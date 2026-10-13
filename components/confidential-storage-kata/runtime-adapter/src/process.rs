// SPDX-License-Identifier: Apache-2.0
//! Process observations for the sole runtime owner. No mutable Process or
//! LinuxContainer escapes to the RPC/reaper caller.
use crate::runtime::{invalid, State};
use confidential_storage_guest::lifecycle::{BackendError, BackendResult};
use rustjail::{
    container::WAIT_PID_LOCKER,
    process::{Process, StreamType},
};
use std::{io, sync::Arc};
use tokio::{
    io::AsyncReadExt,
    sync::{watch, Mutex, MutexGuard},
};

/// Development capture limit per stream. Excess output is drained and counted,
/// never buffered without bound. This is not Kata's production streaming IO API.
pub const OUTPUT_LIMIT: usize = 64 * 1024;
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Output {
    pub bytes: Vec<u8>,
    pub total_bytes: u64,
}
impl Output {
    pub fn truncated(&self) -> bool {
        self.total_bytes > self.bytes.len() as u64
    }
    fn append(&mut self, bytes: &[u8]) {
        self.total_bytes = self.total_bytes.saturating_add(bytes.len() as u64);
        let n = bytes.len().min(OUTPUT_LIMIT - self.bytes.len());
        self.bytes.extend_from_slice(&bytes[..n]);
    }
}
type OutputResult = Option<Result<Output, BackendError>>;
#[derive(Clone)]
pub(crate) struct Streams {
    stdout: watch::Receiver<OutputResult>,
    stderr: watch::Receiver<OutputResult>,
}
impl Streams {
    pub(crate) fn start(p: &mut Process) -> Result<Self, BackendError> {
        // Ensure both descriptors acquire rustjail PipeStream ownership before
        // cleanup_process_stream: raw parent FDs alone are not closed by it.
        let stdout = p.get_reader(StreamType::ParentStdout).ok_or_else(invalid)?;
        let stderr = p.get_reader(StreamType::ParentStderr).ok_or_else(invalid)?;
        let pump = |reader: Arc<Mutex<tokio::io::ReadHalf<rustjail::pipestream::PipeStream>>>| {
            let (tx, rx) = watch::channel(None);
            tokio::spawn(async move {
                let result = async {
                    let mut output = Output::default();
                    let mut reader = reader.lock().await;
                    let mut buf = [0u8; 8192];
                    loop {
                        let n = reader.read(&mut buf).await.map_err(|_| invalid())?;
                        if n == 0 {
                            break;
                        }
                        output.append(&buf[..n]);
                    }
                    Ok(output)
                }
                .await;
                // Release our stream reference before advertising completion.
                drop(reader);
                tx.send_replace(Some(result));
            });
            rx
        };
        Ok(Self {
            stdout: pump(stdout),
            stderr: pump(stderr),
        })
    }
    pub(crate) async fn finish(&self) -> Result<(Output, Output), BackendError> {
        async fn one(mut rx: watch::Receiver<OutputResult>) -> Result<Output, BackendError> {
            loop {
                if let Some(value) = rx.borrow().clone() {
                    return value;
                }
                rx.changed().await.map_err(|_| invalid())?;
            }
        }
        let (a, b) = tokio::join!(one(self.stdout.clone()), one(self.stderr.clone()));
        Ok((a?, b?))
    }
}

/// Read/exit facade retained by an eventual Sandbox entry and RPC handlers.
/// Waits are cancellation safe and repeatable; they do not remove the process,
/// destroy the container or authorize storage detach.
#[derive(Clone)]
pub struct ProcessHandle {
    pub(crate) state: Arc<Mutex<State>>,
    pub(crate) exit: watch::Receiver<Option<i32>>,
}
impl ProcessHandle {
    pub async fn wait(&self) -> Result<i32, BackendError> {
        let mut rx = self.exit.clone();
        loop {
            if let Some(code) = *rx.borrow() {
                return Ok(code);
            }
            rx.changed().await.map_err(|_| invalid())?;
        }
    }
    pub async fn output(&self) -> Result<(Output, Output), BackendError> {
        let streams = self
            .state
            .lock()
            .await
            .streams
            .clone()
            .ok_or_else(invalid)?;
        streams.finish().await
    }
    pub async fn close_stdin(&self) -> BackendResult {
        let mut s = self.state.lock().await;
        if s.io_closed {
            return Ok(());
        }
        let p = s
            .container
            .processes
            .values_mut()
            .next()
            .ok_or_else(invalid)?;
        p.close_stdin().await;
        Ok(())
    }
    pub async fn io_closed(&self) -> bool {
        self.state.lock().await.io_closed
    }

    /// Dispatch a real wait event while the same reaper exclusion is still held.
    /// Return false for another owner; a unified agent reaper must route that
    /// event to its ordinary Sandbox process map without reaping it again.
    pub async fn observe(&self, event: &ChildExit<'_>) -> Result<bool, BackendError> {
        let mut s = self.state.lock().await;
        Self::record(&mut s, event.pid, event.code)
    }
    fn record(s: &mut State, pid: i32, code: i32) -> Result<bool, BackendError> {
        let Some(p) = s.container.processes.values_mut().find(|p| p.pid == pid) else {
            return Ok(false);
        };
        // A tombstone must not match a subsequently reused numeric PID.
        if s.exit.borrow().is_some() {
            return Ok(false);
        }
        p.exit_code = code;
        p.exit_tx.take();
        p.notify_term_close();
        s.exit.send_replace(Some(code));
        Ok(true)
    }
}

/// Shared lock order: WAIT_PID_LOCKER -> owned runtime state (or ordinary
/// Sandbox). Never call lock() while holding either state lock.
pub struct Reaper(MutexGuard<'static, bool>);
/// Cannot be fabricated or outlive the reaper exclusion that produced it.
pub struct ChildExit<'a> {
    pid: i32,
    code: i32,
    _reaper: &'a Reaper,
}
impl ChildExit<'_> {
    pub fn pid(&self) -> i32 {
        self.pid
    }
    pub fn code(&self) -> i32 {
        self.code
    }
}
fn wait_exit(pid: i32) -> io::Result<Option<(i32, i32)>> {
    loop {
        let mut status = 0;
        let result = unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG | libc::__WALL) };
        if result == 0 {
            return Ok(None);
        }
        if result < 0 {
            let err = io::Error::last_os_error();
            if err.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            return Err(err);
        }
        if let Some(code) = exit_code(status) {
            return Ok(Some((result, code)));
        }
    }
}
fn exit_code(status: i32) -> Option<i32> {
    if libc::WIFEXITED(status) {
        Some(libc::WEXITSTATUS(status))
    } else if libc::WIFSIGNALED(status) {
        Some(128 + libc::WTERMSIG(status))
    } else {
        None
    }
}
impl Reaper {
    pub async fn lock() -> Self {
        Self(WAIT_PID_LOCKER.lock().await)
    }
    /// Intended for a single unified SIGCHLD dispatcher, including ordinary
    /// Kata children. Unknown exits must be dispatched to their other owners.
    pub fn next_exit(&self) -> io::Result<Option<ChildExit<'_>>> {
        let _held = &self.0;
        match wait_exit(-1) {
            Ok(event) => Ok(event.map(|(pid, code)| ChildExit {
                pid,
                code,
                _reaper: self,
            })),
            Err(e) if e.raw_os_error() == Some(libc::ECHILD) => Ok(None),
            Err(e) => Err(e),
        }
    }
    /// Isolated adapter harness: reap only this owned init, leaving unrelated
    /// helper Command children to their own waiters. Do not run alongside a
    /// legacy waitpid(-1) dispatcher that discards managed process exits.
    pub async fn poll_owned(&self, handle: &ProcessHandle) -> BackendResult {
        let mut s = handle.state.lock().await;
        if s.exit.borrow().is_some() {
            return Ok(());
        }
        let Some(p) = s.container.processes.values().next() else {
            return Ok(());
        };
        if p.pid <= 0 {
            return Err(invalid());
        }
        if let Some((pid, code)) = wait_exit(p.pid).map_err(|_| invalid())? {
            if !ProcessHandle::record(&mut s, pid, code)? {
                return Err(invalid());
            }
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn statuses_and_bounded_output() {
        assert_eq!(exit_code(42 << 8), Some(42));
        assert_eq!(exit_code(libc::SIGKILL), Some(137));
        assert_eq!(exit_code((libc::SIGSTOP << 8) | 0x7f), None);
        assert_eq!(exit_code(0xffff), None);
        let mut out = Output::default();
        for _ in 0..40 {
            out.append(&[b'x'; 4096]);
        }
        assert_eq!(out.total_bytes, 163840);
        assert_eq!(out.bytes.len(), OUTPUT_LIMIT);
        assert!(out.truncated());
    }
}
