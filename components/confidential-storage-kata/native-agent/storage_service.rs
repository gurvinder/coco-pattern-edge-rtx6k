// SPDX-License-Identifier: Apache-2.0
//! Guest-only adapter for the installed agent's native container lifecycle.
//! The separate private service owns all authority/key/mapping state.
use anyhow::{anyhow, bail, ensure, Context, Result};
use oci_spec::runtime as oci;
use protocols::agent::{CopyFileRequest, CreateContainerRequest, CreateSandboxRequest};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    os::unix::{
        fs::{FileTypeExt, MetadataExt, OpenOptionsExt},
        net::UnixStream,
    },
    path::Path,
    sync::Arc,
    time::Duration,
};
use tokio::sync::{Mutex, OwnedMutexGuard};

const CONFIG: &str = "/etc/confidential-storage/service.json";
const SOCKET: &str = "/run/coco/storage-service/agent.sock";
const DEVICE: &str = "/dev/encrypted-data";
const ROOT: &str = "/run/coco/storage";
const MAX_FRAME: usize = 16384;
const MAX_VOLUMES: usize = 8;
lazy_static! {
    static ref STATE: Arc<Mutex<State>> = Arc::new(Mutex::new(State::default()));
}

/// Runs before the agent accepts container RPCs. No application can use AA or
/// CDH as an alternate attestation/key oracle, even if REST was host-requested.
pub fn protect_guest_components() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    for directory in [
        Path::new("/run/confidential-containers"),
        Path::new("/run/confidential-containers/attestation-agent"),
    ] {
        let info = fs::symlink_metadata(directory)?;
        ensure!(
            info.is_dir() && info.uid() == 0 && info.mode() & 0o022 == 0,
            "private guest-components directory invalid"
        );
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

#[derive(Clone, Deserialize)]
struct Binding {
    container_name: String,
    destination: String,
    access: String,
    #[serde(default = "legacy_id")]
    binding_id: String,
    #[serde(default = "legacy_device")]
    transport_path: String,
}
fn legacy_id() -> String {
    "legacy".into()
}
fn legacy_device() -> String {
    DEVICE.into()
}
#[derive(Clone)]
struct Bindings {
    legacy: bool,
    volumes: Vec<Binding>,
}
#[derive(Deserialize)]
struct VolumeBinding {
    binding_id: String,
    transport_path: String,
    expected: Binding,
}
#[derive(Deserialize)]
struct Config {
    schema_version: u32,
    purpose: String,
    expected: Option<Binding>,
    volumes: Option<Vec<VolumeBinding>>,
}
#[derive(Default)]
pub struct State {
    config: Option<Option<Bindings>>,
    sandbox: Option<String>,
    pause: Option<String>,
    consumer: Option<String>,
    mounted: bool,
    closing: bool,
    closed: bool,
}
pub async fn lock() -> Result<OwnedMutexGuard<State>> {
    let mut state = STATE.clone().lock_owned().await;
    if state.config.is_none() {
        state.config = Some(load()?);
    }
    Ok(state)
}
fn load() -> Result<Option<Bindings>> {
    let mut file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(CONFIG)
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    ensure!(
        cfg!(feature = "agent-policy") && cfg!(feature = "init-data"),
        "measured policy required"
    );
    let info = file.metadata()?;
    ensure!(
        info.is_file()
            && info.uid() == 0
            && info.mode() & 0o777 == 0o600
            && info.len() <= 256 * 1024,
        "invalid fixed storage config"
    );
    let mut bytes = Vec::new();
    (&mut file).take(256 * 1024 + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 256 * 1024, "storage config too large");
    let config: Config = serde_json::from_slice(&bytes)?;
    Ok(Some(parse_bindings(config)?))
}
fn safe_destination(value: &str) -> bool {
    if value.len() > 256 || !value.starts_with('/') {
        return false;
    }
    let parts: Vec<_> = value[1..].split('/').collect();
    !parts.is_empty()
        && parts.len() <= 8
        && ["models", "data", "checkpoints"].contains(&parts[0])
        && parts.iter().all(|p| {
            !p.is_empty()
                && p.len() <= 63
                && p.as_bytes()[0].is_ascii_alphanumeric()
                && p.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        })
}
fn parse_bindings(config: Config) -> Result<Bindings> {
    let (legacy, volumes) = match (
        config.schema_version,
        config.purpose.as_str(),
        config.expected,
        config.volumes,
    ) {
        (1, "isolated-confidential-storage/v1", Some(b), None) => {
            ensure!(
                matches!(
                    (b.access.as_str(), b.destination.as_str()),
                    ("ro", "/models") | ("rw", "/data")
                ) && b.binding_id == "legacy"
                    && b.transport_path == DEVICE,
                "invalid legacy binding"
            );
            (true, vec![b])
        }
        (2, "confidential-storage-guest/v2", None, Some(volumes)) => (
            false,
            volumes
                .into_iter()
                .map(|v| Binding {
                    binding_id: v.binding_id,
                    transport_path: v.transport_path,
                    ..v.expected
                })
                .collect(),
        ),
        _ => bail!("unsupported storage binding schema"),
    };
    ensure!(
        !volumes.is_empty() && volumes.len() <= MAX_VOLUMES,
        "invalid volume count"
    );
    let mut ids = std::collections::BTreeSet::new();
    let mut paths = std::collections::BTreeSet::new();
    for (index, b) in volumes.iter().enumerate() {
        ensure!(
            !b.container_name.is_empty()
                && b.container_name == volumes[0].container_name
                && matches!(b.access.as_str(), "ro" | "rw")
                && safe_destination(&b.destination)
                && !b.binding_id.is_empty()
                && b.binding_id.len() <= 63
                && b.binding_id.as_bytes()[0].is_ascii_lowercase()
                && b.binding_id
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
                && ids.insert(&b.binding_id)
                && paths.insert(&b.transport_path)
                && (legacy
                    || (0..MAX_VOLUMES)
                        .any(|n| b.transport_path == format!("/dev/coco-volume-{n}")))
                && volumes[..index]
                    .iter()
                    .all(|a| !Path::new(&a.destination).starts_with(&b.destination)
                        && !Path::new(&b.destination).starts_with(&a.destination)),
            "invalid or overlapping storage binding"
        );
    }
    Ok(Bindings { legacy, volumes })
}
#[derive(Serialize)]
struct Attachment {
    binding_id: String,
    device_path: String,
    major: u32,
    minor: u32,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MountedVolume {
    binding_id: String,
    source: String,
    destination: String,
    readonly: bool,
}

#[derive(Serialize)]
#[serde(tag = "action", rename_all = "kebab-case")]
enum Request {
    ActivateMany {
        container_id: String,
        devices: Vec<Attachment>,
    },
    Activate {
        container_id: String,
        device_path: String,
        major: u32,
        minor: u32,
    },
    Close {
        container_id: String,
    },
}
#[derive(Deserialize)]
#[serde(tag = "result", rename_all = "kebab-case", deny_unknown_fields)]
enum Response {
    MountedMany {
        mounts: Vec<MountedVolume>,
    },
    Mounted {
        source: String,
        destination: String,
        readonly: bool,
    },
    Closed,
    Denied,
}
async fn call(request: Request) -> Result<Response> {
    // Owned worker completes its exchange even if the RPC caller disappears.
    tokio::task::spawn_blocking(move || {
        let directory = fs::symlink_metadata(Path::new(SOCKET).parent().unwrap())?;
        ensure!(
            directory.is_dir() && directory.uid() == 0 && directory.mode() & 0o777 == 0o700,
            "private service directory invalid"
        );
        let info = fs::symlink_metadata(SOCKET)?;
        ensure!(
            info.file_type().is_socket() && info.uid() == 0 && info.mode() & 0o777 == 0o600,
            "private service socket invalid"
        );
        let mut stream = UnixStream::connect(SOCKET)?;
        stream.set_read_timeout(Some(Duration::from_secs(600)))?;
        stream.set_write_timeout(Some(Duration::from_secs(5)))?;
        let bytes = serde_json::to_vec(&request)?;
        ensure!(bytes.len() <= MAX_FRAME, "private request too large");
        stream.write_all(&(bytes.len() as u32).to_be_bytes())?;
        stream.write_all(&bytes)?;
        let mut size = [0; 4];
        stream.read_exact(&mut size)?;
        let n = u32::from_be_bytes(size) as usize;
        ensure!(n > 0 && n <= MAX_FRAME, "private response too large");
        let mut bytes = vec![0; n];
        stream.read_exact(&mut bytes)?;
        Ok(serde_json::from_slice(&bytes)?)
    })
    .await
    .map_err(|_| anyhow!("private service worker failed; retained state requires cleanup"))?
}
impl State {
    fn binding(&self) -> Option<&Bindings> {
        self.config.as_ref().and_then(|v| v.as_ref())
    }
    pub fn enabled(&self) -> bool {
        self.binding().is_some()
    }
    pub fn admit_sandbox(&mut self, req: &CreateSandboxRequest) -> Result<()> {
        if !self.enabled() {
            return Ok(());
        }
        ensure!(
            self.sandbox.is_none() && self.pause.is_none() && self.consumer.is_none(),
            "storage sandbox cannot be recreated"
        );
        ensure!(
            req.guest_hook_path.is_empty() && req.kernel_modules.is_empty(),
            "host hooks/modules forbidden"
        );
        ensure!(
            req.sandbox_id.len() == 64
                && req
                    .sandbox_id
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
            "invalid sandbox identity"
        );
        for storage in &req.storages {
            ensure!(
                ["9p", "virtio-fs", "ephemeral"].contains(&storage.driver.as_str()),
                "unsupported sandbox storage"
            );
            ensure!(
                beneath(
                    Path::new(&storage.mount_point),
                    Path::new("/run/kata-containers")
                ),
                "sandbox storage outside runtime tree"
            );
        }
        self.sandbox = Some(req.sandbox_id.clone());
        Ok(())
    }
    pub fn consumer(&self, cid: &str) -> bool {
        self.consumer.as_deref() == Some(cid)
    }
    /// Host file mounts are removed, so corresponding runtime copies have no
    /// consumer. Acknowledge bounded shared-file chunks without creating,
    /// following, chmodding or writing any path. All real CopyFile stays denied.
    pub fn discard_host_copy(&self, req: &CopyFileRequest) -> Result<bool> {
        if !self.enabled() {
            return Ok(false);
        }
        ensure!(!self.closing && !self.closed, "storage guest closing");
        ensure!(
            req.path.len() <= 4096
                && beneath(
                    Path::new(&req.path),
                    Path::new("/run/kata-containers/shared/containers")
                ),
            "host copy outside discarded shared tree"
        );
        ensure!(
            req.file_size >= 0
                && req.file_size <= 1024 * 1024
                && req.offset >= 0
                && req.offset <= req.file_size
                && req.data.len() <= 1024 * 1024
                && req.data.len() as i64 <= req.file_size - req.offset,
            "host copy bounds invalid"
        );
        // No file operation occurs here. Host bytes never enter guest storage.
        Ok(true)
    }
    pub fn deny_mutation(&self) -> Result<()> {
        ensure!(
            !self.enabled(),
            "host mutation unavailable in storage guest"
        );
        Ok(())
    }
    pub fn check_process(&self, cid: &str, process: &oci::Process) -> Result<()> {
        if self.consumer(cid) {
            validate_process(process)?;
        }
        Ok(())
    }
    pub fn check_start(&self, cid: &str) -> Result<()> {
        if !self.enabled() {
            return Ok(());
        }
        ensure!(!self.closing, "storage guest closing");
        ensure!(
            self.pause.as_deref() == Some(cid) || (self.consumer(cid) && self.mounted),
            "container not admitted"
        );
        Ok(())
    }
    pub fn begin_remove(&mut self, cid: &str) -> Result<bool> {
        if !self.enabled() {
            return Ok(false);
        }
        if self.consumer(cid) {
            self.closing = true;
            return Ok(true);
        }
        ensure!(
            self.pause.as_deref() == Some(cid) && (self.consumer.is_none() || self.closed),
            "cannot remove pause before consumer cleanup"
        );
        Ok(false)
    }
    pub async fn close(&mut self) -> Result<()> {
        self.closing = true;
        if self.closed || self.consumer.is_none() {
            return Ok(());
        }
        match call(Request::Close {
            container_id: self.consumer.clone().unwrap(),
        })
        .await?
        {
            Response::Closed => {
                self.closed = true;
                self.mounted = false;
                Ok(())
            }
            _ => bail!("private storage cleanup denied; ownership retained"),
        }
    }
    /// Before add_devices, normal rootfs mounting or process creation. The RPC's
    /// measured policy must already have approved the exact image/process spec.
    pub async fn prepare(
        &mut self,
        req: &mut CreateContainerRequest,
        spec: &mut oci::Spec,
        sandbox: &Arc<Mutex<crate::sandbox::Sandbox>>,
    ) -> Result<()> {
        let Some(bindings) = self.binding().cloned() else {
            return Ok(());
        };
        ensure!(!self.closing && !self.closed, "storage guest closed");
        // Initial isolated workload accepts no host-shared file content. Drop
        // bind mounts before any guest mount operation; lexical path checks
        // cannot prevent a malicious shared-file symlink from targeting a
        // private guest socket/header. Use a fresh guest tmpfs for /dev/shm.
        strip_host_binds(spec)?;
        let name = measured_container_name(spec);
        let runtime_sandbox = sandbox.lock().await;
        ensure!(
            runtime_sandbox.hooks.is_none(),
            "guest hooks unavailable in storage VM"
        );
        let sid = runtime_sandbox.id.clone();
        drop(runtime_sandbox);
        ensure!(
            self.sandbox.as_deref() == Some(&sid),
            "sandbox identity mismatch"
        );
        if req.container_id == sid && name == "POD" {
            ensure!(
                self.pause.is_none() && self.consumer.is_none() && req.devices.is_empty(),
                "invalid pause admission"
            );
            // Exact pause image/process/capabilities are required by measured
            // agent policy. Container annotations alone are not authorization.
            self.pause = Some(req.container_id.clone());
            return Ok(());
        }
        ensure!(
            self.pause.as_deref() == Some(&sid) && self.consumer.is_none(),
            "only one measured consumer per guest"
        );
        validate_many(req, spec, &bindings.volumes)?;
        // Reserve before any asynchronous/device/network effect. Never admit a
        // replacement consumer after cancellation or uncertain create failure.
        self.consumer = Some(req.container_id.clone());
        let mut devices = Vec::new();
        for binding in &bindings.volumes {
            let device = req
                .devices
                .iter()
                .find(|d| d.container_path == binding.transport_path)
                .context("missing bound device")?;
            let source =
                crate::device::scsi_device_handler::get_scsi_device_name(sandbox, &device.id)
                    .await?;
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(&source)?;
            let info = file.metadata()?;
            ensure!(
                info.file_type().is_block_device(),
                "resolved attachment is not block storage"
            );
            devices.push(Attachment {
                binding_id: binding.binding_id.clone(),
                device_path: source,
                major: libc::major(info.rdev()),
                minor: libc::minor(info.rdev()),
            });
        }
        let request = if bindings.legacy {
            let d = devices.pop().unwrap();
            Request::Activate {
                container_id: req.container_id.clone(),
                device_path: d.device_path,
                major: d.major,
                minor: d.minor,
            }
        } else {
            Request::ActivateMany {
                container_id: req.container_id.clone(),
                devices,
            }
        };
        let response = call(request).await?;
        let mounts = match response {
            Response::Mounted {
                source,
                destination,
                readonly,
            } if bindings.legacy => vec![MountedVolume {
                binding_id: bindings.volumes[0].binding_id.clone(),
                source,
                destination,
                readonly,
            }],
            Response::MountedMany { mounts } if !bindings.legacy => mounts,
            _ => {
                self.close().await?;
                bail!("private storage activation denied; cleanup completed");
            }
        };
        validate_mounts(&mounts, &bindings.volumes)?;
        // This adapter still admits only storage devices. GPU admission will be
        // added against the actual measured GPU runtime shape, not a wildcard.
        req.devices.clear();
        let linux = spec.linux_mut().as_mut().context("missing linux spec")?;
        linux.set_devices(Some(vec![]));
        if let Some(resources) = linux.resources_mut() {
            if let Some(devices) = resources.devices_mut() {
                devices.retain(|d| {
                    !d.allow() || d.typ().is_some_and(|t| t == oci::LinuxDeviceType::C)
                });
            }
        }
        for mounted in mounts {
            let mount = oci::MountBuilder::default()
                .destination(mounted.destination)
                .typ("bind")
                .source(mounted.source)
                .options(vec![
                    "bind".to_owned(),
                    if mounted.readonly {
                        "ro".into()
                    } else {
                        "rw".into()
                    },
                    "nosuid".into(),
                    "nodev".into(),
                    "noexec".into(),
                ])
                .build()?;
            spec.mounts_mut()
                .as_mut()
                .context("missing mounts")?
                .push(mount);
        }
        self.mounted = true;
        Ok(())
    }
}
fn validate_mounts(mounts: &[MountedVolume], bindings: &[Binding]) -> Result<()> {
    ensure!(
        mounts.len() == bindings.len(),
        "private mount count mismatch"
    );
    let mut ids = std::collections::BTreeSet::new();
    let mut sources = std::collections::BTreeSet::new();
    for mounted in mounts {
        let binding = bindings
            .iter()
            .find(|b| b.binding_id == mounted.binding_id)
            .context("unknown mounted binding")?;
        ensure!(
            ids.insert(&mounted.binding_id)
                && sources.insert(&mounted.source)
                && mounted.destination == binding.destination
                && mounted.readonly == (binding.access == "ro"),
            "private service binding mismatch"
        );
        let suffix = mounted
            .source
            .strip_prefix(&format!("{ROOT}/"))
            .context("invalid private source")?;
        ensure!(
            !suffix.is_empty()
                && suffix.len() <= 128
                && suffix.as_bytes()[0].is_ascii_alphanumeric()
                && suffix
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)),
            "invalid private activation identity"
        );
    }
    Ok(())
}
fn strip_host_binds(spec: &mut oci::Spec) -> Result<()> {
    let mounts = spec.mounts_mut().as_mut().context("missing mounts")?;
    mounts.retain(|m| {
        m.typ().as_deref() != Some("bind")
            && !m
                .options()
                .as_ref()
                .is_some_and(|o| o.iter().any(|s| s == "bind" || s == "rbind"))
    });
    ensure!(
        !mounts
            .iter()
            .any(|m| m.destination() == Path::new("/dev/shm")),
        "unexpected shm mount"
    );
    mounts.push(
        oci::MountBuilder::default()
            .destination("/dev/shm")
            .typ("tmpfs")
            .source("shm")
            .options(vec![
                "nosuid".to_owned(),
                "nodev".into(),
                "noexec".into(),
                "mode=1777".into(),
                "size=65536k".into(),
            ])
            .build()?,
    );
    Ok(())
}

// Use the same exact Kubernetes label as the measured workload policy. Kata's
// generic CRI helper prefers a different, composite name on this CRI-O version.
fn measured_container_name(spec: &oci::Spec) -> &str {
    spec.annotations()
        .as_ref()
        .and_then(|a| a.get("io.kubernetes.container.name"))
        .map(String::as_str)
        .unwrap_or("")
}

#[cfg(test)]
fn validate(req: &CreateContainerRequest, spec: &oci::Spec, binding: &Binding) -> Result<()> {
    validate_many(req, spec, std::slice::from_ref(binding))
}
fn validate_many(
    req: &CreateContainerRequest,
    spec: &oci::Spec,
    bindings: &[Binding],
) -> Result<()> {
    ensure!(
        !bindings.is_empty() && bindings.len() <= MAX_VOLUMES,
        "invalid binding count"
    );
    let binding = &bindings[0];
    ensure!(
        measured_container_name(spec) == binding.container_name,
        "unapproved consumer"
    );
    ensure!(
        req.container_id.len() == 64
            && req
                .container_id
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
        "invalid consumer ID"
    );
    ensure!(
        req.devices.len() == bindings.len(),
        "exact storage attachments required"
    );
    let mut paths = std::collections::BTreeSet::new();
    let mut locators = std::collections::BTreeSet::new();
    for device in &req.devices {
        ensure!(
            device.type_ == "scsi"
                && paths.insert(&device.container_path)
                && locators.insert(&device.id)
                && bindings
                    .iter()
                    .any(|b| b.transport_path == device.container_path),
            "unexpected storage attachment"
        );
        let fields: Vec<_> = device.id.split(':').collect();
        ensure!(
            fields.len() == 2
                && fields
                    .iter()
                    .all(|f| !f.is_empty() && f.bytes().all(|b| b.is_ascii_digit())),
            "invalid SCSI locator"
        );
    }
    validate_process(spec.process().as_ref().context("missing process")?)?;
    ensure!(
        spec.hooks().is_none() && req.shared_mounts.is_empty(),
        "hooks/shared mounts forbidden"
    );
    ensure!(
        spec.root()
            .as_ref()
            .is_some_and(|r| r.readonly() == Some(true)),
        "read-only application root required"
    );
    let linux = spec.linux().as_ref().context("missing linux")?;
    let devices = linux.devices().as_ref().context("missing raw attachment")?;
    let mut raw_paths = std::collections::BTreeSet::new();
    ensure!(
        devices.len() == bindings.len()
            && devices.iter().all(|d| d.typ() == oci::LinuxDeviceType::B
                && raw_paths.insert(d.path())
                && bindings
                    .iter()
                    .any(|b| d.path() == Path::new(&b.transport_path))),
        "unexpected raw devices"
    );
    ensure!(
        linux
            .namespaces()
            .as_ref()
            .is_some_and(|ns| ns.iter().all(|n| n.path().is_none())),
        "caller namespace paths forbidden"
    );
    for mount in spec.mounts().as_ref().context("missing mounts")? {
        ensure!(
            bindings
                .iter()
                .all(|b| !mount.destination().starts_with(&b.destination)
                    && !Path::new(&b.destination).starts_with(mount.destination())),
            "overlapping model destination"
        );
        let typ = mount.typ().as_deref().context("missing mount type")?;
        let bind_option = mount
            .options()
            .as_ref()
            .is_some_and(|options| options.iter().any(|o| o == "bind" || o == "rbind"));
        if typ == "bind" {
            ensure!(bind_option, "explicit bind option required");
            ensure!(
                mount
                    .source()
                    .as_ref()
                    .is_some_and(|s| beneath(s, Path::new("/run/kata-containers"))),
                "unapproved bind source"
            );
        } else {
            ensure!(
                !bind_option
                    && ["proc", "sysfs", "devpts", "tmpfs", "mqueue", "cgroup", "cgroup2"]
                        .contains(&typ),
                "unapproved mount type/options"
            );
        }
    }
    // This first live binding uses only a guest-pulled rootfs. It does not
    // accept generic guest mounts, secondary block storage or writable sharing.
    ensure!(
        req.storages.len() == 1,
        "exactly one guest image storage required"
    );
    let storage = &req.storages[0];
    ensure!(
        storage.driver == "image_guest_pull"
            && storage.mount_point == format!("/run/kata-containers/{}/rootfs", req.container_id),
        "unapproved image storage location"
    );
    Ok(())
}
fn validate_process(process: &oci::Process) -> Result<()> {
    ensure!(
        process.user().uid() != 0
            && process.no_new_privileges() == Some(true)
            && process.terminal() != Some(true),
        "unprivileged process required"
    );
    let caps = process
        .capabilities()
        .as_ref()
        .context("missing capability constraints")?;
    ensure!(
        [
            caps.bounding(),
            caps.effective(),
            caps.permitted(),
            caps.inheritable(),
            caps.ambient()
        ]
        .iter()
        .all(|c| c.as_ref().is_some_and(|s| s.is_empty())),
        "capabilities forbidden"
    );
    Ok(())
}
fn beneath(path: &Path, root: &Path) -> bool {
    path.is_absolute()
        && path != root
        && path.starts_with(root)
        && path.components().all(|p| {
            matches!(
                p,
                std::path::Component::RootDir | std::path::Component::Normal(_)
            )
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    fn binding() -> Binding {
        Binding {
            container_name: "consumer".into(),
            destination: "/models".into(),
            access: "ro".into(),
            binding_id: legacy_id(),
            transport_path: legacy_device(),
        }
    }
    fn state() -> State {
        State {
            config: Some(Some(Bindings {
                legacy: true,
                volumes: vec![binding()],
            })),
            ..State::default()
        }
    }
    fn fixture() -> (CreateContainerRequest, Value) {
        let cid = "a".repeat(64);
        let mut req = CreateContainerRequest::new();
        req.container_id = cid.clone();
        let mut device = protocols::agent::Device::new();
        device.type_ = "scsi".into();
        device.id = "0:1".into();
        device.container_path = DEVICE.into();
        req.devices.push(device);
        let mut storage = protocols::agent::Storage::new();
        storage.driver = "image_guest_pull".into();
        storage.mount_point = format!("/run/kata-containers/{cid}/rootfs");
        req.storages.push(storage);
        let value = json!({"ociVersion":"1.0.2","root":{"path":"rootfs","readonly":true},
            "annotations":{"io.kubernetes.container.name":"consumer", "io.kubernetes.cri-o.ContainerName":"k8s_consumer_storage-model-ro_confidential-storage-test_uid_0"},
            "process":{"user":{"uid":1000,"gid":1000},"cwd":"/","args":["/test"],"noNewPrivileges":true,
                "capabilities":{"bounding":[],"effective":[],"permitted":[],"inheritable":[],"ambient":[]}},
            "mounts":[{"destination":"/proc","type":"proc","source":"proc","options":["nosuid","nodev","noexec"]}],
            "linux":{"devices":[{"path":DEVICE,"type":"b","major":8,"minor":0}],
                "namespaces":[{"type":"ipc"},{"type":"uts"},{"type":"mount"}],"cgroupsPath":"slice:prefix:name"}});
        (req, value)
    }
    fn valid(req: &CreateContainerRequest, value: Value) -> bool {
        validate(req, &serde_json::from_value(value).unwrap(), &binding()).is_ok()
    }
    fn multiple() -> Bindings {
        parse_bindings(serde_json::from_value(json!({"schema_version":2,"purpose":"confidential-storage-guest/v2",
            "volumes":[
                {"binding_id":"model","transport_path":"/dev/coco-volume-0","expected":{"container_name":"consumer","destination":"/models/qwen","access":"ro"}},
                {"binding_id":"data","transport_path":"/dev/coco-volume-1","expected":{"container_name":"consumer","destination":"/data/results","access":"rw"}}
            ]})).unwrap()).unwrap()
    }
    #[test]
    fn multiple_bindings_resolve_by_transport_path_and_reject_extra_devices() {
        let bindings = multiple();
        let (mut req, mut value) = fixture();
        req.devices[0].container_path = bindings.volumes[0].transport_path.clone();
        let mut second = req.devices[0].clone();
        second.container_path = bindings.volumes[1].transport_path.clone();
        second.id = "0:2".into();
        req.devices.push(second);
        value["linux"]["devices"] = json!([
            {"path":"/dev/coco-volume-1","type":"b","major":8,"minor":16},
            {"path":"/dev/coco-volume-0","type":"b","major":8,"minor":0}]);
        let spec: oci::Spec = serde_json::from_value(value).unwrap();
        assert!(validate_many(&req, &spec, &bindings.volumes).is_ok());
        req.devices.reverse();
        assert!(validate_many(&req, &spec, &bindings.volumes).is_ok());
        req.devices[0].id = req.devices[1].id.clone();
        assert!(validate_many(&req, &spec, &bindings.volumes).is_err());
        req.devices[0].id = "0:2".into();
        req.devices[0].type_ = "vfio-pci".into();
        assert!(validate_many(&req, &spec, &bindings.volumes).is_err());
    }
    #[test]
    fn private_response_cannot_swap_mounts_access_or_duplicate_sources() {
        let bindings = multiple();
        let mut mounts = vec![
            MountedVolume {
                binding_id: "model".into(),
                source: format!("{ROOT}/model-activation"),
                destination: "/models/qwen".into(),
                readonly: true,
            },
            MountedVolume {
                binding_id: "data".into(),
                source: format!("{ROOT}/data-activation"),
                destination: "/data/results".into(),
                readonly: false,
            },
        ];
        validate_mounts(&mounts, &bindings.volumes).unwrap();
        mounts.reverse();
        validate_mounts(&mounts, &bindings.volumes).unwrap();
        mounts[0].readonly = true;
        assert!(validate_mounts(&mounts, &bindings.volumes).is_err());
        mounts[0].readonly = false;
        mounts[0].source = mounts[1].source.clone();
        assert!(validate_mounts(&mounts, &bindings.volumes).is_err());
        mounts[0].source = format!("{ROOT}/../private");
        assert!(validate_mounts(&mounts, &bindings.volumes).is_err());
        mounts.pop();
        assert!(validate_mounts(&mounts, &bindings.volumes).is_err());
    }
    #[test]
    fn configuration_rejects_overlapping_destinations_and_mixed_consumers() {
        for path in [
            "/",
            "/proc/key",
            "/data/../models",
            "/data//child",
            "/data/.",
            "/data/é",
        ] {
            assert!(!safe_destination(path));
        }
        for field in ["destination", "container_name"] {
            let mut value = json!({"schema_version":2,"purpose":"confidential-storage-guest/v2","volumes":[
                {"binding_id":"a","transport_path":"/dev/coco-volume-0","expected":{"container_name":"consumer","destination":"/data","access":"rw"}},
                {"binding_id":"b","transport_path":"/dev/coco-volume-1","expected":{"container_name":"consumer","destination":"/models","access":"ro"}}
            ]});
            value["volumes"][1]["expected"][field] = if field == "destination" {
                json!("/data/child")
            } else {
                json!("other")
            };
            assert!(parse_bindings(serde_json::from_value(value).unwrap()).is_err());
        }
    }
    #[test]
    fn measured_name_uses_kubernetes_label_without_cri_fallback() {
        let (req, mut value) = fixture();
        assert!(valid(&req, value.clone()));
        value["annotations"]["io.kubernetes.container.name"] = json!("POD");
        let spec: oci::Spec = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(measured_container_name(&spec), "POD");
        assert!(!valid(&req, value.clone()));
        value["annotations"]
            .as_object_mut()
            .unwrap()
            .remove("io.kubernetes.container.name");
        value["annotations"]["io.kubernetes.cri-o.ContainerName"] = json!("consumer");
        assert!(!valid(&req, value));
    }
    #[test]
    fn rw_binding_still_rejects_caps_and_overlapping_app_mounts() {
        let (req, mut value) = fixture();
        let mut binding = binding();
        binding.access = "rw".into();
        binding.destination = "/data".into();
        assert!(validate(
            &req,
            &serde_json::from_value(value.clone()).unwrap(),
            &binding
        )
        .is_ok());
        value["process"]["capabilities"]["effective"] = json!(["CAP_SYS_ADMIN"]);
        assert!(validate(
            &req,
            &serde_json::from_value(value.clone()).unwrap(),
            &binding
        )
        .is_err());
        value["process"]["capabilities"]["effective"] = json!([]);
        value["mounts"]
            .as_array_mut()
            .unwrap()
            .push(json!({"destination":"/data","type":"tmpfs","source":"tmpfs"}));
        assert!(validate(&req, &serde_json::from_value(value).unwrap(), &binding).is_err());
    }
    #[test]
    fn native_runtime_shape_keeps_systemd_cgroups_without_incoming_pid_namespace() {
        let (req, v) = fixture();
        assert!(valid(&req, v));
    }
    #[test]
    fn unprivileged_boundary_rejects_each_escape_independently() {
        let (req, base) = fixture();
        assert!(valid(&req, base.clone()));
        for (pointer, value) in [
            ("/process/user/uid", json!(0)),
            ("/process/noNewPrivileges", json!(false)),
            ("/process/terminal", json!(true)),
            ("/process/capabilities/effective", json!(["CAP_SYS_ADMIN"])),
            ("/root/readonly", json!(false)),
            ("/linux/devices/0/path", json!("/dev/mem")),
            ("/linux/namespaces/0/path", json!("/proc/1/ns/ipc")),
        ] {
            let mut v = base.clone();
            let (parent, key) = pointer.rsplit_once('/').unwrap();
            v.pointer_mut(parent)
                .unwrap()
                .as_object_mut()
                .unwrap()
                .insert(key.into(), value);
            assert!(!valid(&req, v), "{}", pointer);
        }
        let mut v = base;
        v["hooks"] = json!({"prestart":[]});
        assert!(!valid(&req, v));
    }
    #[test]
    fn mount_options_and_paths_cannot_alias_private_guest_state() {
        let (req, base) = fixture();
        for mount in [
            json!({"destination":"/x","type":"none","source":"/","options":["rbind"]}),
            json!({"destination":"/x","type":"tmpfs","source":"/","options":["bind"]}),
            json!({"destination":"/x","type":"bind","source":"/run/kata-containers/../coco","options":["bind"]}),
            json!({"destination":"/models/child","type":"tmpfs","source":"tmpfs"}),
            json!({"destination":"/","type":"tmpfs","source":"tmpfs"}),
        ] {
            let mut v = base.clone();
            v["mounts"] = json!([mount]);
            assert!(!valid(&req, v));
        }
        for path in [
            "/",
            "/run",
            "/run/coco",
            "/run/kata-containers/../coco",
            "/run/kata-containers/other/rootfs",
        ] {
            let mut changed = req.clone();
            changed.storages[0].mount_point = path.into();
            assert!(!valid(&changed, base.clone()));
        }
    }
    #[test]
    fn host_shared_files_are_removed_instead_of_following_untrusted_symlinks() {
        let (_, mut value) = fixture();
        value["mounts"].as_array_mut().unwrap().extend([
            json!({"destination":"/etc/hosts","type":"bind","source":"/run/kata-containers/shared/containers/host-file","options":["bind"]}),
            json!({"destination":"/dev/shm","type":"none","source":"/run/kata-containers/sandbox/shm","options":["rbind"]}),
        ]);
        let mut spec: oci::Spec = serde_json::from_value(value).unwrap();
        strip_host_binds(&mut spec).unwrap();
        let mounts = spec.mounts().as_ref().unwrap();
        assert_eq!(mounts.len(), 2);
        assert_eq!(mounts[0].destination(), Path::new("/proc"));
        assert_eq!(mounts[1].destination(), Path::new("/dev/shm"));
        assert_eq!(mounts[1].typ().as_deref(), Some("tmpfs"));
    }

    #[test]
    fn bounded_host_copies_are_discarded_without_any_file_operation() {
        let mut req = CopyFileRequest::new();
        req.path = "/run/kata-containers/shared/containers/not-created/hosts".into();
        req.data = b"untrusted host bytes".to_vec();
        req.file_size = req.data.len() as i64;
        assert!(state().discard_host_copy(&req).unwrap());
        for path in [
            "/run/coco/storage/header",
            "/run/confidential-containers/cdh.sock",
            "/run/kata-containers/shared/containers/../../private",
            "/run/kata-containers/shared/containers",
        ] {
            let mut bad = req.clone();
            bad.path = path.into();
            assert!(state().discard_host_copy(&bad).is_err());
        }
        let mut bad = req.clone();
        bad.file_size = 1024 * 1024 + 1;
        assert!(state().discard_host_copy(&bad).is_err());
        bad = req.clone();
        bad.offset = -1;
        assert!(state().discard_host_copy(&bad).is_err());
        bad = req.clone();
        bad.offset = 1;
        assert!(state().discard_host_copy(&bad).is_err());
        let mut closing = state();
        closing.closing = true;
        assert!(closing.discard_host_copy(&req).is_err());
        assert!(!State::default().discard_host_copy(&req).unwrap());
    }

    #[test]
    fn sandbox_hooks_modules_and_recreation_are_rejected() {
        let mut req = CreateSandboxRequest::new();
        req.sandbox_id = "b".repeat(64);
        req.guest_hook_path = "/host-hooks".into();
        assert!(state().admit_sandbox(&req).is_err());
        req.guest_hook_path.clear();
        req.kernel_modules
            .push(protocols::agent::KernelModule::new());
        assert!(state().admit_sandbox(&req).is_err());
        req.kernel_modules.clear();
        let mut s = state();
        s.admit_sandbox(&req).unwrap();
        assert!(s.admit_sandbox(&req).is_err());
    }
    #[test]
    fn failed_create_cleanup_cannot_admit_another_consumer_or_remove_pause() {
        let mut s = state();
        s.pause = Some("pause".into());
        s.consumer = Some("consumer".into());
        assert!(s.check_start("consumer").is_err());
        assert!(s.check_start("other").is_err());
        assert!(s.begin_remove("pause").is_err());
        assert!(s.begin_remove("consumer").unwrap());
        s.mounted = true;
        assert!(s.check_start("consumer").is_err());
        assert!(s.deny_mutation().is_err());
        s.closed = true;
        assert!(!s.begin_remove("pause").unwrap());
    }
}
