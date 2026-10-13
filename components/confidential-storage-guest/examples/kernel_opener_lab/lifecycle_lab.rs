// SPDX-License-Identifier: Apache-2.0
//! Synthetic lab providers only: never ship these with a production guest.
use super::*;
use confidential_storage_guest::{
    guest_mount::MountRoot,
    lifecycle::{Backend, BackendError, BackendResult, Controller, Phase as LifecyclePhase},
    linux_backend::{LinuxBackend, Providers, Resolved, Secret},
};
use std::{
    os::unix::fs::PermissionsExt,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};

#[derive(Clone)]
struct LabProviders {
    manifest: Vec<u8>,
    wrong_key: bool,
    fail_release_once: bool,
    keys: Arc<AtomicUsize>,
    releases: Arc<AtomicUsize>,
    detach_failures: Arc<AtomicUsize>,
    block_fds_before: usize,
}
impl LabProviders {
    fn new(manifest: Vec<u8>) -> Self {
        Self {
            manifest,
            wrong_key: false,
            fail_release_once: false,
            keys: Arc::new(AtomicUsize::new(0)),
            releases: Arc::new(AtomicUsize::new(0)),
            detach_failures: Arc::new(AtomicUsize::new(0)),
            block_fds_before: block_fds(),
        }
    }
}
impl Providers for LabProviders {
    fn resolve(&self, _: &VerifiedGrant, device_id: &str) -> Result<Resolved, BackendError> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/vda")
            .unwrap();
        let m = file.metadata().unwrap();
        let (major, minor) = (libc::major(m.rdev()), libc::minor(m.rdev()));
        assert_eq!(device_id, format!("{major}:{minor}"));
        Ok(Resolved {
            file,
            major,
            minor,
            manifest: self.manifest.clone(),
        })
    }
    fn key(&self, g: &VerifiedGrant, uri: &str) -> Result<Secret, BackendError> {
        assert_eq!(uri, g.claims().credential_uri);
        self.keys.fetch_add(1, Ordering::SeqCst);
        let mut key = [0; 32];
        File::open("/test-key")
            .unwrap()
            .read_exact(&mut key)
            .unwrap();
        if self.wrong_key {
            key[0] ^= 1;
        }
        let result = Secret::new(key);
        key.fill(0);
        Ok(result)
    }
    fn detach_consumers(&self, _: &VerifiedGrant) -> BackendResult {
        // The only lab consumer's namespace process has already exited and was
        // waited for. A real provider must retain and tear down every app bind.
        if self.detach_failures.swap(0, Ordering::SeqCst) != 0 {
            return Err(BackendError("synthetic application detach failure"));
        }
        Ok(())
    }
    fn release(&self, g: &VerifiedGrant) -> BackendResult {
        assert_eq!(
            block_fds(),
            self.block_fds_before,
            "raw device descriptor retained at release"
        );
        assert!(
            !std::path::Path::new(&format!("/run/coco/storage/{}", g.claims().activation_id))
                .exists()
        );
        let names = Command::new("/usr/sbin/dmsetup")
            .args(["info", "-c", "--noheadings", "-o", "name"])
            .output()
            .unwrap();
        assert!(!String::from_utf8(names.stdout).unwrap().contains("coco-"));
        let attempt = self.releases.fetch_add(1, Ordering::SeqCst);
        if self.fail_release_once && attempt == 0 {
            return Err(BackendError("synthetic acknowledgement lost"));
        }
        Ok(())
    }
}
fn block_fds() -> usize {
    fs::read_dir("/proc/self/fd")
        .unwrap()
        .filter_map(Result::ok)
        .filter_map(|e| fs::read_link(e.path()).ok())
        .filter(|p| p == std::path::Path::new("/dev/vda"))
        .count()
}
pub fn run(header: &[u8]) {
    fs::create_dir_all("/run/coco/storage").unwrap();
    fs::set_permissions("/run/coco/storage", fs::Permissions::from_mode(0o777)).unwrap();
    assert!(MountRoot::open().is_err());
    fs::set_permissions("/run/coco/storage", fs::Permissions::from_mode(0o700)).unwrap();
    let root = MountRoot::open().unwrap();
    std::os::unix::fs::symlink("/data", "/run/coco/storage/foreign").unwrap();
    assert!(root.reserve("foreign").is_err());
    assert!(fs::symlink_metadata("/run/coco/storage/foreign")
        .unwrap()
        .file_type()
        .is_symlink());
    fs::remove_file("/run/coco/storage/foreign").unwrap();
    let mut slot = root.reserve("reserved-test").unwrap();
    assert!(root.reserve("reserved-test").is_err());
    slot.unmount().unwrap();
    slot.unmount().unwrap();
    // Propagation out of the guest-private staging mount must be refused.
    super::run("/bin/mount", &["--make-shared", "/run"]);
    assert!(MountRoot::open().is_err());
    super::run("/bin/mount", &["--make-private", "/run"]);
    println!("COCO_TEST_PASS:private-root-permissions-collision-propagation");

    let stat = fs::metadata("/dev/vda").unwrap();
    let device = format!("{}:{}", libc::major(stat.rdev()), libc::minor(stat.rdev()));
    tokio::runtime::Runtime::new().unwrap().block_on(async {
        let (g, mut manifest) = grant_named(header, "mount-bad-manifest");
        manifest[0] ^= 1;
        let providers = LabProviders::new(manifest);
        let backend = Arc::new(LinuxBackend::new(providers.clone()).unwrap());
        let controller = Controller::new(backend, g.claims().session_sha256.clone(), 8);
        assert!(controller.activate(g.clone(), device.clone()).await.is_err());
        assert_eq!(providers.keys.load(Ordering::SeqCst), 0);
        assert_eq!(providers.releases.load(Ordering::SeqCst), 1);
        assert_eq!(controller.phase(&g.claims().activation_id).await.unwrap(), LifecyclePhase::Closed);
        println!("COCO_TEST_PASS:backend-authenticates-before-key");

        let (g, manifest) = grant_named(header, "mount-wrong-key");
        let mut providers = LabProviders::new(manifest);
        providers.wrong_key = true;
        let backend = Arc::new(LinuxBackend::new(providers.clone()).unwrap());
        let controller = Controller::new(backend, g.claims().session_sha256.clone(), 8);
        assert!(controller.activate(g.clone(), device.clone()).await.is_err());
        assert_eq!(controller.phase(&g.claims().activation_id).await.unwrap(), LifecyclePhase::Closed);
        assert_eq!(providers.releases.load(Ordering::SeqCst), 1);
        assert!(controller.activate(g, device.clone()).await.is_err());
        println!("COCO_TEST_PASS:backend-failed-open-cleanup-tombstone");

        let (g, manifest) = grant_named(header, "mount-success");
        let mut providers = LabProviders::new(manifest);
        providers.fail_release_once = true;
        let backend = Arc::new(LinuxBackend::new(providers.clone()).unwrap());
        let controller = Controller::new(backend.clone(), g.claims().session_sha256.clone(), 8);
        let mounted = controller.activate(g.clone(), device.clone()).await.unwrap();
        assert_eq!(controller.activate(g.clone(), device.clone()).await.unwrap(), mounted);
        assert_eq!(providers.keys.load(Ordering::SeqCst), 1);
        assert!(backend.release(&g).await.is_err());
        assert!(backend.close_mapping(&g).await.is_err());
        assert!(backend.unmount(&g, "/data").await.is_err());
        assert_eq!(providers.releases.load(Ordering::SeqCst), 0);
        let names = Command::new("/usr/sbin/dmsetup").args(["info", "-c", "--noheadings", "-o", "name"]).output().unwrap();
        let names = String::from_utf8(names.stdout).unwrap();
        let name = names.lines().map(str::trim).find(|s| s.starts_with("coco-") && !s.ends_with("_dif")).unwrap();
        // Lab-only namespace setup runs as guest root, then execs a cap-free UID.
        // The production Kata namespace/bind adapter is not implemented by this.
        let script = "set -eu; mount --make-rprivate /; mount --bind \"$1\" /data; mount -o remount,bind,nodev,nosuid,noexec /data; /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups --bounding-set=-all --no-new-privs /bin/sh /app-test initialize; umount /data";
        assert!(Command::new("/bin/unshare").args(["-m", "/bin/sh", "-c", script, "lab", &mounted.mount_path])
            .env("COCO_TEST_MAPPER", format!("/dev/mapper/{name}"))
            .env("COCO_TEST_PRIVATE_ROOT", &mounted.mount_path)
            .status().unwrap().success());
        println!("COCO_TEST_PASS:backend-unprivileged-namespace-consumer");
        providers.detach_failures.store(1, Ordering::SeqCst);
        assert!(controller.close(&mounted.activation_id).await.is_err());
        assert_eq!(controller.phase(&mounted.activation_id).await.unwrap(), LifecyclePhase::NeedsUnmount);
        assert!(std::path::Path::new(&format!("{}/probe", mounted.mount_path)).exists());
        assert_eq!(providers.releases.load(Ordering::SeqCst), 0);
        println!("COCO_TEST_PASS:backend-detach-failure-retains-staging");
        let busy = File::open(format!("{}/probe", mounted.mount_path)).unwrap();
        assert!(controller.close(&mounted.activation_id).await.is_err());
        assert_eq!(controller.phase(&mounted.activation_id).await.unwrap(), LifecyclePhase::NeedsUnmount);
        assert_eq!(providers.releases.load(Ordering::SeqCst), 0);
        assert!(controller.activate(g.clone(), device.clone()).await.is_err());
        assert!(busy.metadata().is_ok());
        drop(busy);
        println!("COCO_TEST_PASS:backend-busy-unmount-retains-ownership");
        // First release loses its acknowledgement, after real unmount and close.
        assert!(controller.close(&mounted.activation_id).await.is_err());
        assert_eq!(controller.phase(&mounted.activation_id).await.unwrap(), LifecyclePhase::NeedsRelease);
        assert_eq!(providers.releases.load(Ordering::SeqCst), 1);
        controller.close(&mounted.activation_id).await.unwrap();
        controller.close(&mounted.activation_id).await.unwrap();
        assert_eq!(controller.phase(&mounted.activation_id).await.unwrap(), LifecyclePhase::Closed);
        assert_eq!(providers.releases.load(Ordering::SeqCst), 2);
        assert!(controller.activate(g, device.clone()).await.is_err());
        println!("COCO_TEST_PASS:backend-release-after-teardown-retry-tombstone");
    });
    assert_eq!(fs::read_dir("/run/coco/storage").unwrap().count(), 0);
}
