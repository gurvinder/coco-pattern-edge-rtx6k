// SPDX-License-Identifier: Apache-2.0
//! Owns one prepared rustjail container for one sandbox lifetime. Not registered
//! with Kata's RPC server or reaper yet; do not install this development adapter.
use crate::process_guard::Guard;
use async_trait::async_trait;
use confidential_storage_guest::{
    agent_lifecycle::{Identity, Runtime},
    lifecycle::{BackendError, BackendResult},
    linux_backend::ConsumerHandles,
};
use oci_spec::runtime::{LinuxNamespaceType, Spec};
use runtime_spec::ContainerState;
use rustjail::{
    container::{BaseContainer, LinuxContainer, WAIT_PID_LOCKER},
    process::Process,
};
use std::{
    path::Path,
    sync::{Arc, Mutex as StdMutex},
};
use tokio::sync::Mutex;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    Fresh,
    Preparing,
    Prepared,
    Starting,
    Running,
    Quiesced,
    Destroying,
    Destroyed,
}
struct State {
    container: LinuxContainer,
    process: Option<Process>,
    guard: Option<Arc<StdMutex<Guard>>>,
    phase: Phase,
}
pub struct KataRuntime {
    identity: Identity,
    state: Mutex<State>,
}
fn invalid() -> BackendError {
    BackendError("invalid or uncertain Kata runtime state")
}
fn validate_spec(spec: &Spec) -> BackendResult {
    let p = spec.process().as_ref().ok_or_else(invalid)?;
    if p.user().uid() == 0
        || !p.no_new_privileges().unwrap_or(false)
        || p.terminal().unwrap_or(false)
        || spec.hooks().is_some()
    {
        return Err(invalid());
    }
    let c = p.capabilities().as_ref().ok_or_else(invalid)?;
    if [
        c.bounding(),
        c.effective(),
        c.permitted(),
        c.inheritable(),
        c.ambient(),
    ]
    .iter()
    .any(|v| v.as_ref().is_none_or(|s| !s.is_empty()))
    {
        return Err(invalid());
    }
    let namespaces = spec
        .linux()
        .as_ref()
        .and_then(|l| l.namespaces().as_ref())
        .ok_or_else(invalid)?;
    for kind in [LinuxNamespaceType::Mount, LinuxNamespaceType::Pid] {
        let selected: Vec<_> = namespaces.iter().filter(|ns| ns.typ() == kind).collect();
        if selected.len() != 1 || selected[0].path().is_some() {
            return Err(invalid());
        }
    }
    Ok(())
}
impl KataRuntime {
    /// Call only after measured policy/preflight and identity binding. Takes
    /// ownership: the container cannot simultaneously live in Sandbox.containers.
    /// Caller must not hold the sandbox lock (SIGCHLD lock order is reaper first).
    pub fn new(
        identity: Identity,
        container: LinuxContainer,
        process: Process,
    ) -> Result<Self, BackendError> {
        let mut subreaper = 0i32;
        // The shared waitpid lock is useful only if this agent is the process
        // reaper. An unrelated parent must not be able to recycle our child PID.
        if unsafe { libc::getpid() } != 1
            && (unsafe { libc::prctl(libc::PR_GET_CHILD_SUBREAPER, &mut subreaper) } != 0
                || subreaper != 1)
        {
            return Err(invalid());
        }
        let spec = container.config.spec.as_ref().ok_or_else(invalid)?;
        validate_spec(spec)?;
        if identity.container_id != container.id
            || identity.container_name != container.config.container_name
            || container.status() != ContainerState::Created
            || container.init_process_pid != -1
            || !container.processes.is_empty()
            || !process.init
            || process.exec_id != container.id
            || spec.process().as_ref() != Some(&process.oci)
            || container.config.use_systemd_cgroup
            || container.config.rootless_euid
            || container.config.rootless_cgroup
            || container.config.no_pivot_root
        {
            return Err(invalid());
        }
        Ok(Self {
            identity,
            state: Mutex::new(State {
                container,
                process: Some(process),
                guard: None,
                phase: Phase::Fresh,
            }),
        })
    }
    fn check(&self, id: &Identity) -> BackendResult {
        if id != &self.identity {
            return Err(invalid());
        }
        Ok(())
    }
}
#[async_trait]
impl Runtime for KataRuntime {
    type Consumer = ConsumerHandles;
    async fn prepare(&self, id: &Identity) -> Result<ConsumerHandles, BackendError> {
        self.check(id)?;
        let mut s = self.state.lock().await;
        if s.phase != Phase::Fresh {
            return Err(invalid());
        }
        s.phase = Phase::Preparing;
        // The shared reaper cannot recycle this PID between child creation and
        // pidfd/proc handle acquisition. Do not call while holding sandbox.lock.
        let _reaper = WAIT_PID_LOCKER.lock().await;
        let process = s.process.take().ok_or_else(invalid)?;
        s.container.start(process).await.map_err(|_| invalid())?;
        let path = s
            .container
            .cgroup_manager
            .get_cgroup_path("memory")
            .map_err(|_| invalid())?;
        let guard = Guard::capture(s.container.init_process_pid, Path::new(&path))
            .map_err(|_| invalid())?;
        let (namespace, root) = guard.handles().map_err(|_| invalid())?;
        s.guard = Some(Arc::new(StdMutex::new(guard)));
        s.phase = Phase::Prepared;
        Ok(ConsumerHandles {
            namespace,
            root,
            container_name: id.container_name.clone(),
        })
    }
    async fn start(&self, id: &Identity) -> BackendResult {
        self.check(id)?;
        let mut s = self.state.lock().await;
        if s.phase != Phase::Prepared {
            return Err(invalid());
        }
        s.guard
            .as_ref()
            .ok_or_else(invalid)?
            .lock()
            .map_err(|_| invalid())?
            .check_start()
            .map_err(|_| invalid())?;
        s.phase = Phase::Starting;
        s.container.exec().await.map_err(|_| invalid())?;
        s.phase = Phase::Running;
        Ok(())
    }
    async fn quiesce(&self, id: &Identity) -> BackendResult {
        self.check(id)?;
        let mut s = self.state.lock().await;
        if s.phase == Phase::Destroyed {
            return Ok(());
        }
        // A partial rustjail create without captured identity is deliberately
        // unrecoverable here: never claim that unknown child effects are gone.
        let guard = s.guard.as_ref().ok_or_else(invalid)?.clone();
        tokio::task::spawn_blocking(move || {
            guard
                .lock()
                .map_err(|_| invalid())?
                .quiesce()
                .map_err(|_| invalid())
        })
        .await
        .map_err(|_| invalid())??;
        s.phase = Phase::Quiesced;
        Ok(())
    }
    async fn destroy(&self, id: &Identity) -> BackendResult {
        self.check(id)?;
        let mut s = self.state.lock().await;
        if s.phase == Phase::Destroyed {
            return Ok(());
        }
        if !matches!(s.phase, Phase::Quiesced | Phase::Destroying) {
            return Err(invalid());
        }
        s.guard
            .as_ref()
            .ok_or_else(invalid)?
            .lock()
            .map_err(|_| invalid())?
            .quiesce()
            .map_err(|_| invalid())?;
        s.phase = Phase::Destroying;
        // cgroup.kill + populated=0 already stopped all tasks. rustjail's normal
        // destroy signals cached numeric PIDs; suppress that stale-PID path while
        // preserving Process objects for the eventual agent IO/reaper integration.
        let processes = std::mem::take(&mut s.container.processes);
        let result = s.container.destroy().await;
        s.container.processes = processes;
        result.map_err(|_| invalid())?;
        // rustjail's cgroupfs destroy currently logs deletion errors and returns
        // success. Require the pinned group to be absent before acknowledging.
        s.guard
            .as_ref()
            .ok_or_else(invalid)?
            .lock()
            .map_err(|_| invalid())?
            .removed()
            .map_err(|_| invalid())?;
        s.guard = None;
        s.phase = Phase::Destroyed;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_root_spec_is_not_an_authorized_consumer() {
        assert!(validate_spec(&Spec::default()).is_err());
    }
    #[test]
    fn rejects_privilege_hooks_and_inherited_namespaces() {
        let valid = serde_json::json!({"ociVersion":"1.0.2","root":{"path":"/rootfs"},"process":{"user":{"uid":1000,"gid":1000},"args":["/app"],"cwd":"/","noNewPrivileges":true,"capabilities":{"bounding":[],"effective":[],"permitted":[],"inheritable":[],"ambient":[]}},"linux":{"namespaces":[{"type":"mount"},{"type":"pid"}]}});
        assert!(validate_spec(&serde_json::from_value(valid.clone()).unwrap()).is_ok());
        for (pointer, value) in [
            ("/process/user/uid", serde_json::json!(0)),
            ("/process/noNewPrivileges", serde_json::json!(false)),
            (
                "/linux/namespaces",
                serde_json::json!([{"type":"mount","path":"/proc/1/ns/mnt"},{"type":"pid"}]),
            ),
            ("/linux/namespaces", serde_json::json!([{"type":"pid"}])),
        ] {
            let mut v = valid.clone();
            *v.pointer_mut(pointer).unwrap() = value;
            assert!(
                validate_spec(&serde_json::from_value(v).unwrap()).is_err(),
                "{pointer}"
            );
        }
        let mut absent = valid.clone();
        absent["process"]
            .as_object_mut()
            .unwrap()
            .remove("capabilities");
        assert!(validate_spec(&serde_json::from_value(absent).unwrap()).is_err());
        for field in [
            "bounding",
            "effective",
            "permitted",
            "inheritable",
            "ambient",
        ] {
            let mut missing = valid.clone();
            missing["process"]["capabilities"]
                .as_object_mut()
                .unwrap()
                .remove(field);
            assert!(
                validate_spec(&serde_json::from_value(missing).unwrap()).is_err(),
                "{field}"
            );
        }
        let mut hooks = valid.clone();
        hooks["hooks"] = serde_json::json!({});
        assert!(validate_spec(&serde_json::from_value(hooks).unwrap()).is_err());
        let mut caps = valid.clone();
        caps["process"]["capabilities"] = serde_json::json!({"bounding":["CAP_SYS_ADMIN"]});
        assert!(validate_spec(&serde_json::from_value(caps).unwrap()).is_err());
        let mut terminal = valid;
        terminal["process"]["terminal"] = serde_json::json!(true);
        assert!(validate_spec(&serde_json::from_value(terminal).unwrap()).is_err());
    }
}
