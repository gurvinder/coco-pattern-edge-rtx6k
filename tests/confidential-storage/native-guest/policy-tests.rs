// Run as src/agent/tests/storage_policy.rs against the pinned native source.
use kata_agent_policy::policy::AgentPolicy;
use protocols::{agent, oci};
use serde_json::{json, Value};

async fn allowed(policy: &mut AgentPolicy, endpoint: &str, value: &Value) -> bool {
    policy
        .allow_request(endpoint, &value.to_string())
        .await
        .unwrap()
        .0
}
#[tokio::test]
async fn exact_image_command_environment_and_no_policy_bypass() {
    let path = std::env::var("STORAGE_POLICY_TEST_DIR").unwrap();
    let raw = std::fs::read_to_string(format!("{path}/policy.rego")).unwrap();
    let workload: Value =
        serde_json::from_str(&std::fs::read_to_string(format!("{path}/workload.json")).unwrap())
            .unwrap();
    let mut policy = AgentPolicy::new();
    policy.set_policy(&raw).await.unwrap();
    let cid = "a".repeat(64);
    let mut req = agent::CreateContainerRequest::new();
    req.container_id = cid.clone();
    let mut spec = oci::Spec::new();
    let mut root = oci::Root::new();
    root.Path = format!("/run/kata-containers/{cid}/rootfs");
    root.Readonly = true;
    spec.Root = Some(root).into();
    let mut process = oci::Process::new();
    process.Args = serde_json::from_value(workload["args"].clone()).unwrap();
    process.Env = serde_json::from_value(workload["environment"].clone()).unwrap();
    process.Cwd = "/".into();
    process.NoNewPrivileges = true;
    let mut user = oci::User::new();
    user.UID = 1000;
    user.GID = 1000;
    process.User = Some(user).into();
    spec.Process = Some(process).into();
    for (k, v) in [
        (
            "io.kubernetes.cri.sandbox-namespace",
            "confidential-storage-test",
        ),
        ("io.kubernetes.cri-o.ContainerName", "model"),
        ("io.katacontainers.pkg.oci.container_type", "pod_container"),
    ] {
        spec.Annotations.insert(k.into(), v.into());
    }
    let mut linux = oci::Linux::new();
    for kind in ["ipc", "uts", "mount"] {
        let mut namespace = oci::LinuxNamespace::new();
        namespace.Type = kind.into();
        linux.Namespaces.push(namespace);
    }
    spec.Linux = Some(linux).into();
    req.OCI = Some(spec).into();
    let mut storage = agent::Storage::new();
    storage.driver = "image_guest_pull".into();
    storage.source = workload["image"].as_str().unwrap().into();
    storage.fstype = "overlay".into();
    storage.mount_point = format!("/run/kata-containers/{cid}/rootfs");
    req.storages.push(storage);
    let mut device = agent::Device::new();
    device.type_ = "scsi".into();
    device.container_path = "/dev/encrypted-data".into();
    device.id = "0:1".into();
    req.devices.push(device);
    let base = serde_json::to_value(&req).unwrap();
    assert!(
        allowed(&mut policy, "CreateContainerRequest", &base).await,
        "real protobuf serialization must admit the exact test workload"
    );
    for (pointer, value) in [
        ("/storages/0/source", json!("untrusted:latest")),
        ("/OCI/Process/Args", json!(["/bin/sh"])),
        ("/OCI/Process/Env", json!(["LD_PRELOAD=/host/lib.so"])),
        ("/OCI/Process/User/UID", json!(0)),
        ("/OCI/Root/Readonly", json!(false)),
        ("/OCI/Linux/Namespaces", json!([])),
        ("/OCI/Linux/Namespaces/0/Path", json!("/proc/1/ns/ipc")),
        ("/devices/0/container_path", json!("/dev/sda")),
    ] {
        let mut bad = base.clone();
        *bad.pointer_mut(pointer).unwrap() = value;
        assert!(
            !allowed(&mut policy, "CreateContainerRequest", &bad).await,
            "{}",
            pointer
        );
    }
    for endpoint in [
        "ExecProcessRequest",
        "CopyFileRequest",
        "SetPolicyRequest",
        "AddSwapRequest",
        "SetGuestDateTimeRequest",
        "WriteStreamRequest",
        "AllowRequestsFailingPolicy",
    ] {
        assert!(
            !allowed(&mut policy, endpoint, &json!({})).await,
            "{}",
            endpoint
        );
    }
    let mut pause = base;
    pause["devices"] = json!([]);
    pause["storages"][0]["source"] = json!("pause");
    pause["OCI"]["Annotations"]["io.kubernetes.cri-o.ContainerName"] = json!("POD");
    pause["OCI"]["Annotations"]["io.katacontainers.pkg.oci.container_type"] = json!("pod_sandbox");
    pause["OCI"]["Process"]["Args"] = json!(["/usr/bin/pod"]);
    assert!(allowed(&mut policy, "CreateContainerRequest", &pause).await);
    pause["storages"][0]["source"] = json!("evil-pause:latest");
    assert!(!allowed(&mut policy, "CreateContainerRequest", &pause).await);
}
