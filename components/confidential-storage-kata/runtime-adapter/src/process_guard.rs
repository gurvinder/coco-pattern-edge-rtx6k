// SPDX-License-Identifier: Apache-2.0
//! Capture only while the trusted child is unreaped and runtime mutation is
//! excluded. pidfd_open alone cannot authenticate a PID supplied by a host.
use std::{
    ffi::CString,
    fs::{File, OpenOptions},
    io::{self, Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::fs::{MetadataExt, OpenOptionsExt},
    },
    path::{Component, Path},
    thread,
    time::{Duration, Instant},
};
fn invalid() -> io::Error {
    io::Error::other("invalid retained process/cgroup identity")
}
fn open_at(dir: &File, name: &str, flags: i32) -> io::Result<File> {
    let name = CString::new(name).map_err(|_| invalid())?;
    // SAFETY: live directory and NUL-terminated path; result uniquely owned.
    let fd = unsafe {
        libc::openat(
            dir.as_raw_fd(),
            name.as_ptr(),
            flags | libc::O_CLOEXEC | libc::O_NOFOLLOW,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}
fn read_at(dir: &File, name: &str) -> io::Result<String> {
    let mut text = String::new();
    open_at(dir, name, libc::O_RDONLY)?
        .take(65537)
        .read_to_string(&mut text)?;
    if text.len() > 65536 {
        return Err(invalid());
    }
    Ok(text)
}
fn membership(text: &str) -> io::Result<&str> {
    let mut lines = text.lines();
    let path = lines
        .next()
        .and_then(|l| l.strip_prefix("0::"))
        .ok_or_else(invalid)?;
    if lines.next().is_some()
        || path == "/"
        || !path.starts_with('/')
        || path.ends_with('/')
        || path.contains("//")
        || path
            .split('/')
            .skip(1)
            .any(|s| s.is_empty() || s == "." || s == "..")
    {
        return Err(invalid());
    }
    Ok(path)
}
fn populated(text: &str) -> io::Result<bool> {
    let values: Vec<_> = text
        .lines()
        .filter_map(|l| l.strip_prefix("populated "))
        .collect();
    match values.as_slice() {
        ["0"] => Ok(false),
        ["1"] => Ok(true),
        _ => Err(invalid()),
    }
}
fn open_group(member: &str) -> io::Result<File> {
    let mut group = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open("/sys/fs/cgroup")?;
    for part in member.trim_start_matches('/').split('/') {
        group = open_at(&group, part, libc::O_RDONLY | libc::O_DIRECTORY)?;
    }
    Ok(group)
}
pub(crate) struct Guard {
    pidfd: File,
    proc: File,
    namespace: File,
    root: File,
    cgroup: File,
    membership: String,
    quiesced: bool,
}
impl Guard {
    /// Caller must hold Kata's WAIT_PID_LOCKER from before child creation until
    /// capture returns. It must own this child and prevent all runtime mutations.
    pub(crate) fn capture(pid: i32, runtime_cgroup: &Path) -> io::Result<Self> {
        if pid <= 1 {
            return Err(invalid());
        }
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) } as i32;
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let pidfd = unsafe { File::from_raw_fd(fd) };
        let proc = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(format!("/proc/{pid}"))?;
        let cg_text = read_at(&proc, "cgroup")?;
        let member = membership(&cg_text)?.to_owned();
        let expected = Path::new("/sys/fs/cgroup").join(member.trim_start_matches('/'));
        if runtime_cgroup != expected
            || runtime_cgroup
                .components()
                .any(|c| !matches!(c, Component::RootDir | Component::Normal(_)))
        {
            return Err(invalid());
        }
        let own = std::fs::read_to_string("/proc/thread-self/cgroup")?;
        let own_path = own.trim().strip_prefix("0::").ok_or_else(invalid)?;
        if own_path == member || own_path.starts_with(&format!("{member}/")) {
            return Err(invalid());
        }
        let cgroup = open_group(&member)?;
        if std::fs::read_dir(format!("/proc/self/fd/{}", cgroup.as_raw_fd()))?
            .any(|e| e.and_then(|e| e.file_type()).map_or(true, |t| t.is_dir()))
        {
            return Err(invalid());
        }
        let mut stat = std::mem::MaybeUninit::<libc::statfs>::uninit();
        if unsafe { libc::fstatfs(cgroup.as_raw_fd(), stat.as_mut_ptr()) } != 0 {
            return Err(io::Error::last_os_error());
        }
        if unsafe { stat.assume_init() }.f_type != 0x63677270
            || read_at(&cgroup, "cgroup.type")?.trim() != "domain"
        {
            return Err(invalid());
        }
        // Initially exclusive: no unrelated process may already share the group.
        if read_at(&cgroup, "cgroup.procs")?.trim() != pid.to_string() {
            return Err(invalid());
        }
        // The two magic links intentionally refer to this pinned proc directory.
        let magic = |name: &str| -> io::Result<File> {
            let path = CString::new(name).unwrap();
            let fd = unsafe {
                libc::openat(
                    proc.as_raw_fd(),
                    path.as_ptr(),
                    libc::O_RDONLY | libc::O_CLOEXEC,
                )
            };
            if fd < 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(unsafe { File::from_raw_fd(fd) })
            }
        };
        let namespace = magic("ns/mnt")?;
        let root = magic("root")?;
        let g = Self {
            pidfd,
            proc,
            namespace,
            root,
            cgroup,
            membership: member,
            quiesced: false,
        };
        g.check_start()?;
        Ok(g)
    }
    fn alive(&self) -> io::Result<bool> {
        let mut p = libc::pollfd {
            fd: self.pidfd.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let rc = unsafe { libc::poll(&mut p, 1, 0) };
        if rc < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(rc == 0)
    }
    pub(crate) fn handles(&self) -> io::Result<(File, File)> {
        self.check_start()?;
        Ok((self.namespace.try_clone()?, self.root.try_clone()?))
    }
    pub(crate) fn check_start(&self) -> io::Result<()> {
        if self.quiesced
            || !self.same_group()?
            || !self.alive()?
            || membership(&read_at(&self.proc, "cgroup")?)? != self.membership
            || !populated(&read_at(&self.cgroup, "cgroup.events")?)?
        {
            return Err(invalid());
        }
        Ok(())
    }
    fn same_group(&self) -> io::Result<bool> {
        let current = match open_group(&self.membership) {
            Ok(g) => g,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(e) => return Err(e),
        };
        let a = current.metadata()?;
        let b = self.cgroup.metadata()?;
        if a.dev() != b.dev() || a.ino() != b.ino() {
            return Err(invalid());
        }
        Ok(true)
    }
    pub(crate) fn removed(&self) -> io::Result<()> {
        if !self.quiesced || self.alive()? || self.same_group()? {
            return Err(invalid());
        }
        Ok(())
    }
    pub(crate) fn quiesce(&mut self) -> io::Result<()> {
        // cgroup.kill handles descendants and fork races atomically. Never signal
        // a cached numeric PID, and never infer termination from signal delivery.
        if !self.same_group()? {
            return if self.quiesced && !self.alive()? {
                Ok(())
            } else {
                Err(invalid())
            };
        }
        if !self.quiesced {
            open_at(&self.cgroup, "cgroup.kill", libc::O_WRONLY)?.write_all(b"1")?;
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if !populated(&read_at(&self.cgroup, "cgroup.events")?)? && !self.alive()? {
                self.quiesced = true;
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "cgroup still populated; retain storage ownership",
                ));
            }
            thread::sleep(Duration::from_millis(10));
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reject_ambiguous_or_root_membership() {
        for v in [
            "0::/\n",
            "1:memory:/x\n",
            "0::/a\n0::/b\n",
            "0::/a/../b\n",
            "0::/a//b\n",
            "0::/a/\n",
            "0::/a/./b\n",
        ] {
            assert!(membership(v).is_err(), "{v}");
        }
        assert_eq!(membership("0::/kata/app\n").unwrap(), "/kata/app");
    }
    #[test]
    fn require_unambiguous_population_evidence() {
        assert!(populated("populated 1\nfrozen 0\n").unwrap());
        assert!(!populated("populated 0\nfrozen 0\n").unwrap());
        for v in ["", "populated 2", "populated 0\npopulated 1"] {
            assert!(populated(v).is_err());
        }
    }
    #[test]
    fn reject_service_or_invalid_pid() {
        assert!(Guard::capture(0, Path::new("/sys/fs/cgroup/test")).is_err());
        assert!(
            Guard::capture(std::process::id() as i32, Path::new("/sys/fs/cgroup/test")).is_err()
        );
    }
}
