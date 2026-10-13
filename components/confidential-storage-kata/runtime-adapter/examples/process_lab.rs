// SPDX-License-Identifier: Apache-2.0
//! Root inside a disposable non-TEE VM only. Never run on the cluster node.
#[path = "../src/process_guard.rs"]
mod process_guard;
use process_guard::Guard;
use std::{fs, path::Path, process::Command, time::Duration};
fn wait(path: &str) {
    for _ in 0..200 {
        if Path::new(path).exists() {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    panic!("lab marker absent: {path}");
}
fn main() {
    assert!(fs::read_to_string("/proc/cmdline")
        .unwrap()
        .split_whitespace()
        .any(|v| v == "coco.storage.lab=1"));
    assert_eq!(
        fs::read_to_string("/sys/block/vda/serial").unwrap().trim(),
        "coco-scratch-only"
    );
    assert!(Command::new("/bin/mount")
        .args(["-t", "cgroup2", "none", "/sys/fs/cgroup"])
        .status()
        .unwrap()
        .success());
    let group = Path::new("/sys/fs/cgroup/coco-adapter-lab");
    fs::create_dir(group).unwrap();
    assert!(Command::new("/bin/mkfifo")
        .arg("/run/runtime-go")
        .status()
        .unwrap()
        .success());
    let mut child=Command::new("/bin/unshare").args(["-m","/bin/sh","-c",r#"
set -eu
mount --make-rprivate /
echo $$ > /sys/fs/cgroup/coco-adapter-lab/cgroup.procs
echo ready > /run/runtime-ready
read go < /run/runtime-go
/bin/sh -c 'echo $$ > /sys/fs/cgroup/coco-adapter-lab/nested/cgroup.procs; echo ready > /run/runtime-descendant; exec sleep 600' &
exec sleep 600
"#]).spawn().unwrap();
    wait("/run/runtime-ready");
    // This process owns the child and does not reap it until after capture.
    assert!(Guard::capture(child.id() as i32, Path::new("/sys/fs/cgroup/wrong")).is_err());
    let mut guard = Guard::capture(child.id() as i32, group).unwrap();
    let (namespace, root) = guard.handles().unwrap();
    guard.check_start().unwrap();
    fs::create_dir("/sys/fs/cgroup/coco-adapter-foreign").unwrap();
    assert!(Command::new("/bin/mount")
        .args([
            "--bind",
            "/sys/fs/cgroup/coco-adapter-foreign",
            "/sys/fs/cgroup/coco-adapter-lab"
        ])
        .status()
        .unwrap()
        .success());
    assert!(guard.check_start().is_err());
    assert!(guard.quiesce().is_err());
    assert!(child.try_wait().unwrap().is_none());
    assert!(Command::new("/bin/umount")
        .arg(group)
        .status()
        .unwrap()
        .success());
    fs::remove_dir("/sys/fs/cgroup/coco-adapter-foreign").unwrap();
    fs::create_dir(group.join("nested")).unwrap();
    fs::write("/run/runtime-go", "go\n").unwrap();
    wait("/run/runtime-descendant");
    assert!(!fs::read_to_string(group.join("nested/cgroup.procs"))
        .unwrap()
        .trim()
        .is_empty());
    guard.quiesce().unwrap();
    guard.quiesce().unwrap();
    child.wait().unwrap();
    assert!(guard.check_start().is_err());
    assert!(namespace.metadata().is_ok() && root.metadata().is_ok());
    assert!(fs::read_to_string(group.join("cgroup.events"))
        .unwrap()
        .contains("populated 0"));
    assert!(guard.removed().is_err());
    fs::remove_dir(group.join("nested")).unwrap();
    fs::remove_dir(group).unwrap();
    guard.removed().unwrap();
    guard.quiesce().unwrap();
    println!("COCO_TEST_PASS:pinned-process-cgroup-subtree-quiescence");
}
