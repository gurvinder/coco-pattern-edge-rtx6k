// SPDX-License-Identifier: Apache-2.0
//! Real rustjail callbacks, root ONLY inside the disposable non-TEE kernel lab.
#[path = "../src/start_fifo.rs"]
mod start_fifo;
#[path = "callback_lab/storage.rs"]
mod storage;
use confidential_storage_guest::agent_lifecycle::{Identity, Runtime};
use confidential_storage_kata_adapter::{
    process::{ProcessHandle, Reaper, OUTPUT_LIMIT},
    runtime::KataRuntime,
};
use rustjail::{container::LinuxContainer, process::Process, specconv::CreateOpts};
use slog::{o, Drain, Logger};
use std::{
    fs,
    os::unix::fs::{symlink, PermissionsExt},
    path::Path,
    process::Command,
    time::Duration,
};

struct LabLog;
impl Drain for LabLog {
    type Ok = ();
    type Err = std::convert::Infallible;
    fn log(&self, record: &slog::Record<'_>, _: &slog::OwnedKVList) -> Result<(), Self::Err> {
        eprintln!("rustjail: {}", record.msg());
        Ok(())
    }
}
fn run(tool: &str, args: &[&str]) {
    assert!(Command::new(tool).args(args).status().unwrap().success());
}
fn mkfifo(path: &Path) {
    run("/bin/mkfifo", &[path.to_str().unwrap()]);
}
fn setup(name: &str) -> (KataRuntime, Identity, std::path::PathBuf) {
    setup_with_script(name, None)
}
fn setup_with_script(
    name: &str,
    script: Option<&str>,
) -> (KataRuntime, Identity, std::path::PathBuf) {
    let root = std::path::PathBuf::from(format!("/run/callback-lab/{name}/rootfs"));
    for dir in ["bin", "proc", "dev", "result", "data"] {
        fs::create_dir_all(root.join(dir)).unwrap();
    }
    fs::set_permissions(root.join("result"), fs::Permissions::from_mode(0o777)).unwrap();
    fs::copy("/bin/busybox", root.join("bin/busybox")).unwrap();
    for applet in [
        "sh",
        "sleep",
        "cat",
        "id",
        "grep",
        "sync",
        "cp",
        "chmod",
        "rm",
        "sha256sum",
    ] {
        symlink("busybox", root.join("bin").join(applet)).unwrap();
    }
    let spec = serde_json::from_value(serde_json::json!({
        "ociVersion":"1.0.2", "root":{"path":root},
        "process":{"user":{"uid":1000,"gid":1000}, "args":["/bin/sh","-c",script.unwrap_or(r#"
set -eu
[ "$(id -u)" = 1000 ]
[ ! -e /dev/vda ] && [ ! -e /test-key ]
grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status
for cap in CapInh CapPrm CapEff CapBnd CapAmb; do grep -Eq "^$cap:[[:space:]]+0000000000000000$" /proc/self/status; done
cat /proc/self/status > /result/status
printf 'unprivileged-rustjail-app\n' > /result/started
exec sleep 600
"#)], "env":["PATH=/bin"],"cwd":"/", "noNewPrivileges":true,
        "capabilities":{"bounding":[],"effective":[],"permitted":[],"inheritable":[],"ambient":[]}},
        "mounts":[{"destination":"/proc","type":"proc","source":"proc","options":["nosuid","noexec","nodev"]},
                  {"destination":"/dev","type":"tmpfs","source":"tmpfs","options":["nosuid","strictatime","mode=755","size=65536k"]}],
        "linux":{"cgroupsPath":format!("/callback-{name}"), "rootfsPropagation":"private", "resources":{}, "namespaces":[{"type":"mount"},{"type":"pid"},{"type":"ipc"},{"type":"uts"}]}
    })).unwrap();
    let config = CreateOpts {
        spec: Some(spec),
        container_name: name.into(),
        no_new_keyring: true,
        ..Default::default()
    };
    let logger = Logger::root(LabLog.fuse(), o!());
    let ctr = LinuxContainer::new(name, "/run/callback-runtime", None, config, &logger).unwrap();
    let process = Process::new(
        &logger,
        ctr.config
            .spec
            .as_ref()
            .unwrap()
            .process()
            .as_ref()
            .unwrap(),
        name,
        true,
        0,
        None,
    )
    .unwrap();
    let id = Identity {
        container_id: name.into(),
        container_name: name.into(),
        activation_id: format!("callback-{name}"),
    };
    (
        KataRuntime::new(id.clone(), ctr, process).unwrap(),
        id,
        root,
    )
}
async fn wait_marker(path: &Path) {
    for _ in 0..300 {
        if path.exists() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("application did not start: {}", path.display());
}
struct LabReaper {
    stop: tokio::sync::watch::Sender<bool>,
    task: tokio::task::JoinHandle<Vec<(i32, i32)>>,
}
impl LabReaper {
    fn start(handle: ProcessHandle, global: bool) -> Self {
        let (stop, mut stopped) = tokio::sync::watch::channel(false);
        let task = tokio::spawn(async move {
            let mut unmatched = Vec::new();
            loop {
                {
                    let reaper = Reaper::lock().await;
                    if global {
                        while let Some(event) = reaper.next_exit().unwrap() {
                            if !handle.observe(&event).await.unwrap() {
                                unmatched.push((event.pid(), event.code()));
                            }
                        }
                    } else {
                        reaper.poll_owned(&handle).await.unwrap();
                    }
                }
                if *stopped.borrow() {
                    return unmatched;
                }
                tokio::select! {
                    _ = tokio::time::sleep(Duration::from_millis(5)) => {},
                    _ = stopped.changed() => {},
                }
            }
        });
        Self { stop, task }
    }
    async fn finish(self) -> Vec<(i32, i32)> {
        self.stop.send_replace(true);
        self.task.await.unwrap()
    }
}
fn pipe_targets() -> std::collections::BTreeSet<std::path::PathBuf> {
    fs::read_dir("/proc/self/fd")
        .unwrap()
        .filter_map(|e| fs::read_link(e.ok()?.path()).ok())
        .filter(|p| p.to_string_lossy().starts_with("pipe:["))
        .collect()
}
async fn process_observations() {
    let before_pipes = pipe_targets();
    // More than pipe capacity and output capture limit; a non-draining owner
    // would block forever. EOF on stdin comes from the owned facade.
    let script = r#"set -eu
cat > /result/stdin
printf 'stderr-marker\n' >&2
i=0
while [ "$i" -lt 8192 ]; do printf '0123456789abcdef'; i=$((i+1)); done
exit 42
"#;
    let (runtime, id, root) = setup_with_script("process-exit", Some(script));
    let handle = runtime.process_handle(&id).await.unwrap();
    let mut wrong = id.clone();
    wrong.activation_id.push('x');
    assert!(runtime.process_handle(&wrong).await.is_err());
    // Exercise actual unified dispatch: one unrelated child is routed to its
    // other owner, not confused with the managed init process.
    let helper = Command::new("/bin/sh")
        .args(["-c", "exit 17"])
        .spawn()
        .unwrap();
    let helper_pid = helper.id() as i32;
    let reaper = LabReaper::start(handle.clone(), true);
    let cancelled = {
        let h = handle.clone();
        tokio::spawn(async move { h.wait().await })
    };
    cancelled.abort();
    assert!(cancelled.await.unwrap_err().is_cancelled());
    let waiter1 = {
        let h = handle.clone();
        tokio::spawn(async move { h.wait().await.unwrap() })
    };
    let waiter2 = {
        let h = handle.clone();
        tokio::spawn(async move { h.wait().await.unwrap() })
    };
    let handles = runtime.prepare(&id).await.unwrap();
    let owned_pipes: Vec<_> = pipe_targets().difference(&before_pipes).cloned().collect();
    assert!(owned_pipes.len() >= 3);
    assert!(
        tokio::time::timeout(Duration::from_millis(20), handle.wait())
            .await
            .is_err()
    );
    handle.close_stdin().await.unwrap();
    handle.close_stdin().await.unwrap();
    runtime.start(&id).await.unwrap();
    let (a, b) = tokio::time::timeout(Duration::from_secs(15), async {
        tokio::join!(waiter1, waiter2)
    })
    .await
    .unwrap();
    assert_eq!((a.unwrap(), b.unwrap()), (42, 42));
    let (out, err) = tokio::time::timeout(Duration::from_secs(5), handle.output())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(out.total_bytes, 131072);
    assert_eq!(out.bytes, b"0123456789abcdef".repeat(OUTPUT_LIMIT / 16));
    assert!(out.truncated());
    assert_eq!(err.bytes, b"stderr-marker\n");
    assert!(!err.truncated());
    assert_eq!(fs::read(root.join("result/stdin")).unwrap(), b"");
    assert!(!handle.io_closed().await);
    // Exit reporting alone does not authorize detach or destruction.
    assert!(runtime.destroy(&id).await.is_err());
    runtime.quiesce(&id).await.unwrap();
    runtime.destroy(&id).await.unwrap();
    assert!(handle.io_closed().await);
    let after_pipes = pipe_targets();
    assert!(owned_pipes.iter().all(|p| !after_pipes.contains(p)));
    assert_eq!(handle.wait().await.unwrap(), 42);
    assert_eq!(handle.output().await.unwrap(), (out, err));
    handle.close_stdin().await.unwrap();
    let unmatched = reaper.finish().await;
    assert_eq!(unmatched.len(), 2);
    assert!(unmatched.contains(&(helper_pid, 17)));
    // The pinned rustjail bootstrap also leaves its temporary parent to the
    // subreaper. It is not the managed init; preserve that helper exit route.
    assert!(unmatched
        .iter()
        .any(|(pid, code)| *pid != helper_pid && *code == 0));
    drop((helper, handles));
    println!("COCO_TEST_PASS:rustjail-process-exit-42-concurrent-waiters-bounded-io");
}
async fn delayed_reaper() {
    let (runtime, id, root) = setup("delayed-reaper");
    let observer = runtime.process_handle(&id).await.unwrap();
    let handles = runtime.prepare(&id).await.unwrap();
    runtime.start(&id).await.unwrap();
    wait_marker(&root.join("result/started")).await;
    runtime.quiesce(&id).await.unwrap();
    // The kernel confirms death, but the exit status has not been reaped. The
    // adapter must retain Process/IO and return an error, never invent a status.
    assert!(runtime.destroy(&id).await.is_err());
    assert!(!observer.io_closed().await);
    assert!(Path::new("/run/callback-runtime/delayed-reaper").exists());
    assert!(
        tokio::time::timeout(Duration::from_millis(20), observer.wait())
            .await
            .is_err()
    );
    Reaper::lock().await.poll_owned(&observer).await.unwrap();
    assert_eq!(observer.wait().await.unwrap(), 137);
    runtime.destroy(&id).await.unwrap();
    assert!(observer.io_closed().await);
    assert!(!Path::new("/run/callback-runtime/delayed-reaper").exists());
    drop(handles);
    println!("COCO_TEST_PASS:rustjail-delayed-reaper-retains-ownership-retry");
}
async fn callbacks() {
    for scenario in [
        "normal",
        "blocked-cleanup",
        "dead-prepared",
        "replaced-fifo",
        "cancel-prepared",
    ] {
        let (runtime, id, root) = setup(scenario);
        let observer = runtime.process_handle(&id).await.unwrap();
        let reaper = LabReaper::start(observer.clone(), false);
        assert!(runtime.start(&id).await.is_err());
        assert!(runtime.destroy(&id).await.is_err());
        let handles = runtime.prepare(&id).await.unwrap();
        assert!(runtime.prepare(&id).await.is_err());
        assert!(!root.join("result/started").exists());
        let mut wrong = id.clone();
        wrong.activation_id.push_str("-wrong");
        assert!(runtime.start(&wrong).await.is_err());
        assert!(runtime.quiesce(&wrong).await.is_err());
        assert!(runtime.destroy(&wrong).await.is_err());
        let fifo = std::path::PathBuf::from(format!("/run/callback-runtime/{scenario}/exec.fifo"));
        match scenario {
            "normal" | "blocked-cleanup" => {
                runtime.start(&id).await.unwrap();
                wait_marker(&root.join("result/started")).await;
                assert_eq!(
                    fs::read_to_string(root.join("result/started")).unwrap(),
                    "unprivileged-rustjail-app\n"
                );
                assert!(runtime.start(&id).await.is_err());
                for line in fs::read_to_string(root.join("result/status"))
                    .unwrap()
                    .lines()
                    .filter(|s| {
                        s.starts_with("Uid:")
                            || s.starts_with("Cap")
                            || s.starts_with("NoNewPrivs:")
                    })
                {
                    println!("APP:{line}");
                }
            }
            "dead-prepared" => {
                fs::write(
                    format!("/sys/fs/cgroup/callback-{scenario}/cgroup.kill"),
                    "1",
                )
                .unwrap();
                for _ in 0..300 {
                    if fs::read_to_string(format!(
                        "/sys/fs/cgroup/callback-{scenario}/cgroup.events"
                    ))
                    .unwrap()
                    .contains("populated 0")
                    {
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
                assert!(runtime.start(&id).await.is_err());
            }
            "replaced-fifo" => {
                fs::rename(&fifo, fifo.with_extension("saved")).unwrap();
                mkfifo(&fifo);
                assert!(runtime.start(&id).await.is_err());
            }
            "cancel-prepared" => {}
            _ => unreachable!(),
        }
        if scenario == "blocked-cleanup" {
            fs::create_dir("/sys/fs/cgroup/callback-foreign").unwrap();
            let target = format!("/sys/fs/cgroup/callback-{scenario}");
            run(
                "/bin/mount",
                &["--bind", "/sys/fs/cgroup/callback-foreign", &target],
            );
            assert!(runtime.quiesce(&id).await.is_err());
            assert!(runtime.destroy(&id).await.is_err());
            assert!(handles.namespace.metadata().is_ok() && handles.root.metadata().is_ok());
            run("/bin/umount", &[&target]);
            fs::remove_dir("/sys/fs/cgroup/callback-foreign").unwrap();
        }
        runtime.quiesce(&id).await.unwrap();
        runtime.quiesce(&id).await.unwrap();
        assert!(handles.namespace.metadata().is_ok() && handles.root.metadata().is_ok());
        assert!(runtime.start(&id).await.is_err());
        // Namespace handles are deliberately kept through destroy, matching the
        // storage coordinator's requirement to detach after stopping processes.
        runtime.destroy(&id).await.unwrap();
        runtime.destroy(&id).await.unwrap();
        runtime.quiesce(&id).await.unwrap();
        assert!(!Path::new(&format!("/sys/fs/cgroup/callback-{scenario}")).exists());
        assert!(!Path::new(&format!("/run/callback-runtime/{scenario}")).exists());
        if scenario != "normal" && scenario != "blocked-cleanup" {
            assert!(!root.join("result/started").exists());
        }
        assert_eq!(observer.wait().await.unwrap(), 137);
        assert!(observer.io_closed().await);
        assert!(reaper.finish().await.is_empty());
        drop(handles);
        println!("COCO_TEST_PASS:rustjail-{scenario}");
    }
}
fn fifo_without_child() {
    // Exact boundary missed by a pre-start liveness check: the child reader is
    // absent. The anchor prevents both open blocking and SIGPIPE without ever
    // reading/consuming the one byte intended for a live child.
    let dir = Path::new("/run/callback-fifo");
    fs::create_dir(dir).unwrap();
    let path = dir.join("exec.fifo");
    mkfifo(&path);
    let fifo = start_fifo::StartFifo::capture(dir).unwrap();
    let start = std::time::Instant::now();
    assert!(fifo.arm().unwrap().is_none()); // never fabricate child readiness
    assert!(start.elapsed() < Duration::from_secs(1));
    let child_path = path.clone();
    let child = std::thread::spawn(move || {
        drop(fs::File::open(child_path).unwrap());
    });
    let armed = loop {
        if let Some(armed) = fifo.arm().unwrap() {
            break armed;
        }
        assert!(start.elapsed() < Duration::from_secs(2));
        std::thread::sleep(Duration::from_millis(1));
    };
    child.join().unwrap(); // real reader dies after handshake, before write
    fs::write(&path, [0]).unwrap();
    assert!(start.elapsed() < Duration::from_secs(2));
    assert!(fifo.arm().is_err()); // queued byte cannot be started twice
    drop(armed);
    drop(fifo);
    fs::remove_file(&path).unwrap();
    fs::write(&path, b"not a FIFO").unwrap();
    assert!(start_fifo::StartFifo::capture(dir).is_err());
    fs::remove_file(&path).unwrap();
    symlink("/dev/null", &path).unwrap();
    assert!(start_fifo::StartFifo::capture(dir).is_err());
    println!("COCO_TEST_PASS:rustjail-fifo-no-child-nonblocking");
}
fn main() {
    if std::env::args().nth(1).as_deref() == Some("init") {
        rustjail::container::init_child();
        return;
    }
    assert!(fs::read_to_string("/proc/cmdline")
        .unwrap()
        .split_whitespace()
        .any(|s| s == "coco.storage.lab=1"));
    assert_eq!(
        fs::read_to_string("/sys/block/vda/serial").unwrap().trim(),
        "coco-scratch-only"
    );
    assert_eq!(
        unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) },
        0
    );
    if !Path::new("/sys/fs/cgroup/cgroup.controllers").exists() {
        run("/bin/mount", &["-t", "cgroup2", "none", "/sys/fs/cgroup"]);
    }
    // pivot_root cannot operate on the kernel's original initramfs rootfs.
    // Put the lab agent on a separate tmpfs root, retaining just its tools and
    // kernel API mounts. This changes only this disposable guest's test setup.
    assert_eq!(unsafe { libc::unshare(libc::CLONE_NEWNS) }, 0);
    run("/bin/mount", &["--make-rprivate", "/"]);
    fs::create_dir_all("/callback-agent-root").unwrap();
    run(
        "/bin/mount",
        &[
            "-t",
            "tmpfs",
            "-o",
            "mode=0755",
            "tmpfs",
            "/callback-agent-root",
        ],
    );
    for dir in [
        "bin", "sbin", "usr", "lib", "lib64", "etc", "proc", "sys", "dev", "run",
    ] {
        if Path::new(&format!("/{dir}")).exists() {
            let dest = format!("/callback-agent-root/{dir}");
            fs::create_dir(&dest).unwrap();
            run("/bin/mount", &["--rbind", &format!("/{dir}"), &dest]);
        }
    }
    let storage_mode = std::env::args().nth(1).filter(|s| s.starts_with("storage"));
    if storage_mode.is_some() {
        fs::copy("/test-key", "/callback-agent-root/test-key").unwrap();
        fs::set_permissions(
            "/callback-agent-root/test-key",
            fs::Permissions::from_mode(0o600),
        )
        .unwrap();
    }
    std::env::set_current_dir("/callback-agent-root").unwrap();
    assert_eq!(unsafe { libc::chroot(c".".as_ptr()) }, 0);
    std::env::set_current_dir("/").unwrap();
    fs::create_dir_all("/run/callback-runtime").unwrap();
    if storage_mode.is_none() {
        fifo_without_child();
    }
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap()
        .block_on(async {
            if let Some(mode) = storage_mode {
                storage::test(&mode).await;
            } else {
                process_observations().await;
                delayed_reaper().await;
                callbacks().await;
            }
        });
    println!("COCO_TEST_PASS:rustjail-callback-lifecycle");
}
