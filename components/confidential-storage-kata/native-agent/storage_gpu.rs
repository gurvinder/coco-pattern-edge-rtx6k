// SPDX-License-Identifier: Apache-2.0
//! Opt-in guest-generated NVIDIA CDI, never host-supplied container edits.
use anyhow::{ensure, Context, Result};
use kata_sys_util::sl;
use oci_spec::runtime as oci;
use protocols::agent::CreateContainerRequest;
use serde_json::Value;
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::Path,
    sync::Arc,
};
use tokio::sync::Mutex;
const PROFILE: &str = "/etc/confidential-storage/gpu-profile";
const PROFILE_VALUE: &[u8] = b"nvidia-single-gpu-guest-cdi/v1\n";
const CACHE: &str = "/run/coco/storage-cdi";

pub fn enabled() -> Result<bool> {
    match read_private(PROFILE, 128, Some(0o600)) {
        Ok(bytes) => {
            ensure!(bytes == PROFILE_VALUE, "unknown measured GPU profile");
            Ok(true)
        }
        Err(e)
            if e.downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound) =>
        {
            Ok(false)
        }
        Err(e) => Err(e),
    }
}
fn read_private(path: &str, limit: u64, mode: Option<u32>) -> Result<Vec<u8>> {
    let mut f = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    let m = f.metadata()?;
    ensure!(
        m.is_file()
            && m.uid() == 0
            && m.mode() & 0o022 == 0
            && mode.is_none_or(|mode| m.mode() & 0o777 == mode)
            && m.len() <= limit,
        "untrusted GPU configuration file"
    );
    let mut bytes = Vec::new();
    (&mut f).take(limit + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 <= limit, "GPU configuration too large");
    Ok(bytes)
}
fn decimal(s: &str) -> bool {
    !s.is_empty() && s.len() <= 5 && s.bytes().all(|b| b.is_ascii_digit())
}
fn pci_option(s: &str) -> bool {
    let Some((host, guest)) = s.split_once('=') else {
        return false;
    };
    let h = host.as_bytes();
    h.len() == 12
        && h[4] == b':'
        && h[7] == b':'
        && h[10] == b'.'
        && h.iter()
            .enumerate()
            .all(|(i, b)| [4, 7, 10].contains(&i) || b.is_ascii_hexdigit())
        && h[11] <= b'7'
        && guest.split('/').count() <= 8
        && guest
            .split('/')
            .all(|p| p.len() == 2 && p.bytes().all(|b| b.is_ascii_hexdigit()))
}
/// Called before any device or key side effect. Storage devices are checked by
/// the separate exact binding validator. VFIO identifiers are locators, not trust.
pub fn validate_request(req: &CreateContainerRequest, spec: &oci::Spec) -> Result<()> {
    let annotations = spec.annotations().as_ref().context("missing annotations")?;
    let cdi: Vec<_> = annotations
        .iter()
        .filter(|(k, _)| k.starts_with("cdi.k8s.io/"))
        .collect();
    ensure!(
        cdi.len() == 1
            && cdi[0]
                .0
                .strip_prefix("cdi.k8s.io/vfio")
                .is_some_and(decimal)
            && cdi[0].1 == "nvidia.com/gpu=0",
        "exact single NVIDIA CDI request required"
    );
    let gpu: Vec<_> = req.devices.iter().filter(|d| d.type_ != "scsi").collect();
    ensure!(gpu.len() <= 1, "at most one guest-kernel GPU transport");
    for d in gpu {
        let suffix = d
            .container_path
            .strip_prefix("/dev/vfio/devices/vfio")
            .or_else(|| d.container_path.strip_prefix("/dev/vfio/"));
        let number = suffix.context("invalid GPU transport path")?;
        ensure!(
            d.type_ == "vfio-pci-gk"
                && decimal(number)
                && (d.id == number || d.id == format!("vfio{number}"))
                && cdi[0].0 == &format!("cdi.k8s.io/vfio{number}")
                && d.options.len() == 1
                && pci_option(&d.options[0]),
            "unexpected GPU transport"
        );
    }
    Ok(())
}
fn nvidia_node(path: &str) -> bool {
    matches!(
        path,
        "/dev/nvidia0"
            | "/dev/nvidiactl"
            | "/dev/nvidia-uvm"
            | "/dev/nvidia-uvm-tools"
            | "/dev/dri/card0"
            | "/dev/dri/renderD128"
    ) || path
        .strip_prefix("/dev/nvidia-caps/nvidia-cap")
        .is_some_and(decimal)
}
fn safe_library_path(path: &str) -> bool {
    let p = Path::new(path);
    p.is_absolute()
        && p.components().all(|c| {
            matches!(
                c,
                std::path::Component::RootDir | std::path::Component::Normal(_)
            )
        })
        && ["/usr/lib/", "/usr/lib64/", "/lib/", "/lib64/", "/usr/bin/"]
            .iter()
            .any(|prefix| path.starts_with(prefix))
        && p.file_name().is_some_and(|n| {
            let n = n.to_string_lossy();
            n.starts_with("libcuda")
                || n.starts_with("libnvidia")
                || n.starts_with("nvidia-")
                || n.starts_with("libnvcuvid")
                || n.starts_with("libnvoptix")
        })
}
/// The measured NVIDIA generator is part of the guest TCB. This additional check
/// prevents its edits from introducing raw disks, writable mounts or app privilege.
fn validate_edits(before: &oci::Spec, after: &oci::Spec) -> Result<()> {
    let mut a = serde_json::to_value(before)?;
    let mut b = serde_json::to_value(after)?;
    let before_mounts = before.mounts().as_ref().context("missing mounts")?;
    let after_mounts = after.mounts().as_ref().context("missing CDI mounts")?;
    ensure!(
        after_mounts.starts_with(before_mounts),
        "CDI changed existing mounts"
    );
    let mut destinations = std::collections::BTreeSet::new();
    for mount in before_mounts {
        destinations.insert(mount.destination());
    }
    for mount in &after_mounts[before_mounts.len()..] {
        let source = mount
            .source()
            .as_ref()
            .and_then(|p| p.to_str())
            .context("missing CDI source")?;
        let target = mount.destination().to_str().context("invalid CDI target")?;
        let options = mount.options().as_ref().context("missing CDI options")?;
        ensure!(
            safe_library_path(source)
                && safe_library_path(target)
                && destinations.insert(mount.destination())
                && mount.typ().as_deref() == Some("bind")
                && options.iter().any(|o| o == "ro")
                && options.iter().any(|o| o == "bind")
                && options
                    .iter()
                    .all(|o| ["ro", "bind", "nosuid", "nodev", "noexec"].contains(&o.as_str())),
            "unsupported NVIDIA library mount: source={source:?} destination={target:?} type={:?} options={options:?}",
            mount.typ()
        );
    }
    let linux = after.linux().as_ref().context("missing CDI linux")?;
    let devices = linux.devices().as_ref().context("missing NVIDIA nodes")?;
    ensure!(
        !devices.is_empty() && devices.len() <= 16,
        "invalid NVIDIA node count"
    );
    let mut nodes = std::collections::BTreeSet::new();
    for device in devices {
        ensure!(
            device.typ() == oci::LinuxDeviceType::C
                && device.path().to_str().is_some_and(nvidia_node)
                && nodes.insert(device.path().clone())
                && device.major() > 0
                && device.minor() >= 0,
            "non-NVIDIA or raw CDI device: {device:?}"
        );
    }
    ensure!(
        nodes.contains(Path::new("/dev/nvidia0")) && nodes.contains(Path::new("/dev/nvidiactl")),
        "missing GPU nodes"
    );
    if let Some(rules) = linux
        .resources()
        .as_ref()
        .and_then(|r| r.devices().as_ref())
    {
        for rule in rules.iter().filter(|d| d.allow()) {
            ensure!(rule.typ() == Some(oci::LinuxDeviceType::C)
                && devices.iter().any(|d| Some(d.major()) == rule.major() && Some(d.minor()) == rule.minor()), "broad CDI device grant");
        }
    }
    if let Some(hooks) = b.get("hooks").and_then(Value::as_object) {
        for (stage, entries) in hooks {
            let entries = entries.as_array().context("invalid CDI hooks")?;
            for hook in entries {
                ensure!(
                    ["createContainer", "startContainer"].contains(&stage.as_str())
                        && hook["path"] == "/usr/bin/nvidia-cdi-hook",
                    "untrusted GPU hook"
                );
            }
        }
    }
    let before_env = a["process"]["env"].as_array().cloned().unwrap_or_default();
    let after_env = b["process"]["env"].as_array().cloned().unwrap_or_default();
    ensure!(
        after_env.starts_with(&before_env)
            && after_env[before_env.len()..]
                .iter()
                .all(|v| v == "NVIDIA_VISIBLE_DEVICES=void"
                    || v == "NVIDIA_CTK_LIBCUDA_DIR=/usr/lib64"),
        "unexpected CDI environment edit"
    );
    for value in [&mut a, &mut b] {
        value.as_object_mut().unwrap().remove("mounts");
        value.as_object_mut().unwrap().remove("hooks");
        value["process"]
            .as_object_mut()
            .context("missing process")?
            .remove("env");
        value["linux"]
            .as_object_mut()
            .context("missing linux")?
            .remove("devices");
        if let Some(resources) = value["linux"]["resources"].as_object_mut() {
            resources.remove("devices");
        }
        if value["linux"]["resources"].is_null()
            || value["linux"]["resources"]
                .as_object()
                .is_some_and(|r| r.values().all(Value::is_null))
        {
            value["linux"].as_object_mut().unwrap().remove("resources");
        }
    }
    ensure!(a == b, "CDI changed a field outside the GPU edit contract");
    Ok(())
}

/// CDI injection edits the in-memory OCI spec; no mount/hook runs here. Keep the
/// persistence daemon and pinned driver firmware private to the guest; normalize readonly library
/// file binds. Resolved source type/path checks still run before container start.
fn restrict_compute_edits(before: &oci::Spec, after: &mut oci::Spec) -> Result<()> {
    let original = before
        .mounts()
        .as_ref()
        .context("missing original mounts")?;
    let edits = after.mounts().as_ref().context("missing CDI mounts")?;
    ensure!(edits.starts_with(original), "CDI changed existing mounts");
    let mut mounts = original.clone();
    for edit in &edits[original.len()..] {
        let source = edit
            .source()
            .as_ref()
            .and_then(|p| p.to_str())
            .unwrap_or("");
        let target = edit.destination().to_str().unwrap_or("");
        // These exact entries belong to guest infrastructure, not CUDA clients.
        // Unknown firmware paths still fail validation rather than becoming mounts.
        if target == source
            && matches!(
                source,
                "/run/nvidia-persistenced/socket"
                    | "/usr/bin/nvidia-persistenced"
                    | "/lib/firmware/nvidia/595.58.03/gsp_ga10x.bin"
                    | "/lib/firmware/nvidia/595.58.03/gsp_tu10x.bin"
            )
        {
            continue;
        }
        let mut mount = edit.clone();
        if let Some(options) = edit.options() {
            if safe_library_path(source)
                && safe_library_path(target)
                && matches!(edit.typ().as_deref(), None | Some("bind"))
                && options.iter().any(|o| o == "ro")
                && options.iter().any(|o| o == "rbind" || o == "bind")
                && options.iter().all(|o| {
                    [
                        "ro", "bind", "rbind", "rprivate", "nosuid", "nodev", "noexec",
                    ]
                    .contains(&o.as_str())
                })
            {
                mount.set_typ(Some("bind".into()));
                mount.set_options(Some(
                    options
                        .iter()
                        .filter(|o| o.as_str() != "rprivate")
                        .map(|o| {
                            if o == "rbind" {
                                "bind".into()
                            } else {
                                o.clone()
                            }
                        })
                        .collect(),
                ));
            }
        }
        mounts.push(mount);
    }
    after.set_mounts(Some(mounts));
    // CUDA/NVML do not need display modesetting. Remove only the observed
    // guest driver node and its exact rule; unknown/broad grants still fail.
    if let Some(linux) = after.linux_mut() {
        if let Some(devices) = linux.devices_mut() {
            devices.retain(|d| {
                !(d.path() == Path::new("/dev/nvidia-modeset")
                    && d.typ() == oci::LinuxDeviceType::C
                    && d.major() == 195
                    && d.minor() == 254)
            });
        }
        if let Some(resources) = linux.resources_mut() {
            if let Some(rules) = resources.devices_mut() {
                rules.retain(|r| {
                    !(r.allow()
                        && r.typ() == Some(oci::LinuxDeviceType::C)
                        && r.major() == Some(195)
                        && r.minor() == Some(254))
                });
            }
        }
    }
    Ok(())
}

pub async fn configure(
    req: &CreateContainerRequest,
    spec: &mut oci::Spec,
    sandbox: &Arc<Mutex<crate::sandbox::Sandbox>>,
) -> Result<()> {
    let directory = fs::symlink_metadata("/run/cdi")?;
    ensure!(
        directory.is_dir() && directory.uid() == 0 && directory.mode() & 0o022 == 0,
        "untrusted guest CDI directory"
    );
    let files: Vec<_> = fs::read_dir("/run/cdi")?.collect::<std::io::Result<Vec<_>>>()?;
    ensure!(
        files.len() == 1 && files[0].file_name() == "nvidia.yaml",
        "unexpected guest CDI files"
    );
    let data = read_private("/run/cdi/nvidia.yaml", 256 * 1024, None)?;
    // Isolate the validated file from cache scanning or later generator writes.
    fs::create_dir(CACHE)?;
    fs::set_permissions(CACHE, fs::Permissions::from_mode(0o700))?;
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(format!("{CACHE}/nvidia.yaml"))?;
    f.write_all(&data)?;
    drop(f);
    let gpu: Vec<_> = req
        .devices
        .iter()
        .filter(|d| d.type_ != "scsi")
        .cloned()
        .collect();
    crate::device::add_devices(&req.container_id, &sl!(), &gpu, spec, sandbox).await?;
    let before = spec.clone();
    crate::device::handle_cdi_devices(&sl!(), spec, CACHE, std::time::Duration::from_secs(0))
        .await?;
    restrict_compute_edits(&before, spec)?;
    validate_edits(&before, spec)?;
    // Only guest regular files may be passed to the application; never sockets,
    // host-shared trees, private AA/CDH paths or entire library directories.
    for mount in &spec.mounts().as_ref().unwrap()[before.mounts().as_ref().unwrap().len()..] {
        let path = fs::canonicalize(mount.source().as_ref().unwrap())?;
        ensure!(
            path.to_str().is_some_and(safe_library_path) && fs::metadata(path)?.is_file(),
            "unsafe resolved NVIDIA library source"
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn specs() -> (oci::Spec, oci::Spec) {
        let a = json!({"ociVersion":"1.0.2","process":{"user":{"uid":1001,"gid":1001},"cwd":"/","args":["vllm"],"noNewPrivileges":true},"mounts":[],"linux":{"devices":[]}});
        let mut b = a.clone();
        b["linux"]["devices"] = json!([{"path":"/dev/nvidia0","type":"c","major":195,"minor":0},{"path":"/dev/nvidiactl","type":"c","major":195,"minor":255}]);
        b["linux"]["resources"] =
            json!({"devices":[{"allow":true,"type":"c","major":195,"minor":0,"access":"rwm"}]});
        b["mounts"] = json!([{"source":"/usr/lib64/libcuda.so.1","destination":"/usr/lib64/libcuda.so.1","type":"bind","options":["bind","ro"]}]);
        b["hooks"] = json!({"createContainer":[{"path":"/usr/bin/nvidia-cdi-hook","args":["nvidia-cdi-hook","update-ldcache"]}]});
        b["process"]["env"] = json!(["NVIDIA_VISIBLE_DEVICES=void"]);
        (
            serde_json::from_value(a).unwrap(),
            serde_json::from_value(b).unwrap(),
        )
    }
    #[test]
    fn accepts_bounded_guest_gpu_edits() {
        let (a, b) = specs();
        validate_edits(&a, &b).unwrap();
    }
    #[test]
    fn accepts_only_fixed_guest_libcuda_environment_addition() {
        let (a, b) = specs();
        let mut value = serde_json::to_value(b).unwrap();
        value["process"]["env"] = json!([
            "NVIDIA_CTK_LIBCUDA_DIR=/usr/lib64",
            "NVIDIA_VISIBLE_DEVICES=void"
        ]);
        validate_edits(&a, &serde_json::from_value(value.clone()).unwrap()).unwrap();
        for bad in [
            "NVIDIA_CTK_LIBCUDA_DIR=/tmp",
            "NVIDIA_VISIBLE_DEVICES=all",
            "LD_LIBRARY_PATH=/tmp",
        ] {
            value["process"]["env"][0] = json!(bad);
            assert!(validate_edits(&a, &serde_json::from_value(value.clone()).unwrap()).is_err());
        }
    }
    #[test]
    fn private_daemon_socket_removed_and_readonly_file_bind_normalized() {
        let (a, b) = specs();
        let mut value = serde_json::to_value(b).unwrap();
        value["mounts"][0].as_object_mut().unwrap().remove("type");
        value["mounts"][0]["options"] = json!(["ro", "rbind", "rprivate", "nosuid", "nodev"]);
        value["mounts"].as_array_mut().unwrap().push(json!({
            "source":"/run/nvidia-persistenced/socket", "destination":"/run/nvidia-persistenced/socket",
            "options":["nosuid","nodev","rbind","rprivate","noexec"]
        }));
        let mut result = serde_json::from_value(value).unwrap();
        restrict_compute_edits(&a, &mut result).unwrap();
        validate_edits(&a, &result).unwrap();
        assert_eq!(result.mounts().as_ref().unwrap().len(), 1);
        assert_eq!(
            result.mounts().as_ref().unwrap()[0].typ().as_deref(),
            Some("bind")
        );
    }
    #[test]
    fn guest_firmware_and_daemon_binary_are_not_exposed() {
        let (a, b) = specs();
        let mut value = serde_json::to_value(b).unwrap();
        for path in [
            "/usr/bin/nvidia-persistenced",
            "/lib/firmware/nvidia/595.58.03/gsp_ga10x.bin",
            "/lib/firmware/nvidia/595.58.03/gsp_tu10x.bin",
        ] {
            value["mounts"].as_array_mut().unwrap().push(json!({
                "source":path, "destination":path,
                "options":["ro","rbind","rprivate","nosuid","nodev"]
            }));
        }
        let mut result = serde_json::from_value(value).unwrap();
        restrict_compute_edits(&a, &mut result).unwrap();
        validate_edits(&a, &result).unwrap();
        assert_eq!(result.mounts().as_ref().unwrap().len(), 1);
    }
    #[test]
    fn modeset_node_and_exact_rule_removed_but_block_node_rejected() {
        let (a, b) = specs();
        for kind in ["c", "b"] {
            let mut value = serde_json::to_value(&b).unwrap();
            value["linux"]["devices"]
                .as_array_mut()
                .unwrap()
                .push(json!({
                    "path":"/dev/nvidia-modeset", "type":kind,"major":195,"minor":254
                }));
            value["linux"]["resources"]["devices"]
                .as_array_mut()
                .unwrap()
                .push(json!({
                    "allow":true,"type":kind,"major":195,"minor":254,"access":"rwm"
                }));
            let mut result = serde_json::from_value(value).unwrap();
            restrict_compute_edits(&a, &mut result).unwrap();
            assert_eq!(validate_edits(&a, &result).is_ok(), kind == "c");
            if kind == "c" {
                assert_eq!(
                    result
                        .linux()
                        .as_ref()
                        .unwrap()
                        .devices()
                        .as_ref()
                        .unwrap()
                        .len(),
                    2
                );
            }
        }
    }
    #[test]
    fn normalization_does_not_admit_writable_files_or_other_sockets() {
        let (a, b) = specs();
        for change in [
            json!({"source":"/lib/firmware/nvidia/595.58.03/other.bin", "destination":"/lib/firmware/nvidia/595.58.03/other.bin", "options":["ro","rbind"]}),
            json!({"source":"/usr/lib64/libcuda.so.1", "destination":"/usr/lib64/libcuda.so.1", "options":["rw","rbind"]}),
            json!({"source":"/run/confidential-containers/cdh.sock", "destination":"/run/nvidia-persistenced/socket", "options":["ro","rbind"]}),
            json!({"source":"/run/nvidia-persistenced/socket", "destination":"/run/other.sock", "options":["ro","rbind"]}),
        ] {
            let mut value = serde_json::to_value(&b).unwrap();
            value["mounts"] = json!([change]);
            let mut result = serde_json::from_value(value).unwrap();
            restrict_compute_edits(&a, &mut result).unwrap();
            assert!(validate_edits(&a, &result).is_err());
        }
    }
    #[test]
    fn rejects_raw_nodes_wildcards_private_mounts_and_privilege() {
        let (a, b) = specs();
        let original = serde_json::to_value(b).unwrap();
        for (pointer, value) in [
            ("/linux/devices/0/type", json!("b")),
            ("/linux/devices/0/path", json!("/dev/mapper/control")),
            ("/linux/resources/devices/0/major", Value::Null),
            (
                "/mounts/0/source",
                json!("/run/confidential-containers/socket"),
            ),
            ("/mounts/0/options", json!(["bind", "rw"])),
            ("/process/user/uid", json!(0)),
            ("/process/noNewPrivileges", json!(false)),
            ("/hooks/createContainer/0/path", json!("/bin/sh")),
            ("/process/env", json!(["LD_PRELOAD=/tmp/bad.so"])),
        ] {
            let mut v = original.clone();
            *v.pointer_mut(pointer).unwrap() = value;
            assert!(
                validate_edits(&a, &serde_json::from_value(v).unwrap()).is_err(),
                "{pointer}"
            );
        }
    }
    #[test]
    fn accepts_only_one_gpu_zero_cdi_annotation() {
        let mut req = CreateContainerRequest::new();
        let spec = serde_json::from_value(
            json!({"ociVersion":"1.0.2","annotations":{"cdi.k8s.io/vfio2":"nvidia.com/gpu=0"}}),
        )
        .unwrap();
        validate_request(&req, &spec).unwrap(); // Cold-plug may already be complete.
        let mut d = protocols::agent::Device::new();
        d.type_ = "vfio-pci-gk".into();
        d.id = "vfio2".into();
        d.container_path = "/dev/vfio/devices/vfio2".into();
        d.options = vec!["0000:41:00.0=02/00".into()];
        req.devices.push(d);
        validate_request(&req, &spec).unwrap();
        req.devices[0].type_ = "vfio-pci".into();
        assert!(validate_request(&req, &spec).is_err());
        for annotation in [
            json!({}),
            json!({"cdi.k8s.io/vfio2":"nvidia.com/gpu=all"}),
            json!({"cdi.k8s.io/vfio2":"nvidia.com/gpu=0","cdi.k8s.io/other":"x/y=1"}),
        ] {
            let s = serde_json::from_value(json!({"ociVersion":"1.0.2","annotations":annotation}))
                .unwrap();
            assert!(validate_request(&CreateContainerRequest::new(), &s).is_err());
        }
    }
    #[test]
    fn rejects_path_traversal_and_bad_pci_locators() {
        for p in [
            "/usr/lib64/../libcuda.so",
            "/usr/lib64",
            "/etc/passwd",
            "/run/coco/storage/libcuda.so",
        ] {
            assert!(!safe_library_path(p));
        }
        for p in [
            "",
            "0000:41:00.0=../../etc",
            "0000:41:00.8=02/00",
            "0000:41:00.0=02/00=x",
        ] {
            assert!(!pci_option(p));
        }
    }
}
