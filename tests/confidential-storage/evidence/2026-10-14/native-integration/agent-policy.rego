package agent_policy
import rego.v1

# Workload-specific values are generated from the public publisher descriptor.
workload := {"args": ["/bin/sh", "-c", "set -eu; id; grep -E '^(CapEff|NoNewPrivs):' /proc/self/status; grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status; grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status; test ! -e /dev/encrypted-data; test ! -e /dev/mapper/control; test ! -e /run/confidential-containers/cdh.sock; cd /models; printf '%s\\n' '832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e  LICENSE' | sha256sum -c -; printf '%s\\n' '660db3b73d788119c04535e48cf9be5f55bc3100841a718637ae695b442f27dd  config.json' | sha256sum -c -; printf '%s\\n' '2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2  generation_config.json' | sha256sum -c -; printf '%s\\n' '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5  merges.txt' | sha256sum -c -; printf '%s\\n' 'f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b  model.safetensors' | sha256sum -c -; printf '%s\\n' 'd5945b210faf8c6c869fb72de03acd0e386507dae493107fed818ea9bb6948d8  publisher-source.json' | sha256sum -c -; printf '%s\\n' 'aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4  tokenizer.json' | sha256sum -c -; printf '%s\\n' 'd5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101  tokenizer_config.json' | sha256sum -c -; printf '%s\\n' 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910  vocab.json' | sha256sum -c -; if touch /models/should-not-write 2>/dev/null; then exit 1; fi; echo STORAGE_MODEL_RO_HASHES_PASS; sleep 120"], "environment": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM=xterm", "HOSTNAME=storage-model-ro", "NSS_SDB_USE_CACHE=no", "container=oci", "KUBERNETES_PORT_443_TCP_ADDR=172.30.0.1", "KUBERNETES_SERVICE_HOST=172.30.0.1", "KUBERNETES_SERVICE_PORT=443", "KUBERNETES_SERVICE_PORT_HTTPS=443", "KUBERNETES_PORT=tcp://172.30.0.1:443", "KUBERNETES_PORT_443_TCP=tcp://172.30.0.1:443", "KUBERNETES_PORT_443_TCP_PROTO=tcp", "KUBERNETES_PORT_443_TCP_PORT=443"], "image": "registry.access.redhat.com/ubi9/ubi-minimal@sha256:b966f21589d418d1e02159cb8e03e261fc8c4ee9061258893f5e4e33986aac4c"}
default AllowRequestsFailingPolicy := false
default CreateContainerRequest := false
default CreateSandboxRequest := false
default ExecProcessRequest := false
default CopyFileRequest := false
default SetPolicyRequest := false
default WriteStreamRequest := false
default AddSwapRequest := false
default AddSwapPathRequest := false
default SetGuestDateTimeRequest := false
default ReseedRandomDevRequest := false
default UpdateContainerRequest := false
default UpdateEphemeralMountsRequest := false
default GetDiagnosticDataRequest := false

# These calls cannot introduce processes, files or storage. Native lifecycle
# guards restrict start/remove to admitted container identities.
default StartContainerRequest := true
default RemoveContainerRequest := true
default DestroySandboxRequest := true
default SignalProcessRequest := true
default WaitProcessRequest := true
default ReadStreamRequest := true
default CloseStdinRequest := true
default StatsContainerRequest := true
default GuestDetailsRequest := true
default GetOOMEventRequest := true
default GetMetricsRequest := true
default UpdateInterfaceRequest := true
default UpdateRoutesRequest := true
default AddARPNeighborsRequest := true
default ListInterfacesRequest := true
default ListRoutesRequest := true
default OnlineCPUMemRequest := true

CreateSandboxRequest if {
    input.guest_hook_path == ""
    count(input.kernel_modules) == 0
    regex.match("^[a-f0-9]{64}$", input.sandbox_id)
}

common if {
    regex.match("^[a-f0-9]{64}$", input.container_id)
    input.OCI.Root.Path == sprintf("/run/kata-containers/%s/rootfs", [input.container_id])
    input.OCI.Root.Readonly == true
    input.OCI.Hooks == null
    input.OCI.Process.Terminal == false
    count(input.OCI.Linux.Namespaces) == 3
    {ns.Type | some ns in input.OCI.Linux.Namespaces} == {"ipc", "uts", "mount"}
    every ns in input.OCI.Linux.Namespaces { ns.Path == "" }
    count(input.shared_mounts) == 0
    count(input.storages) == 1
    input.storages[0].driver == "image_guest_pull"
    input.storages[0].mount_point == input.OCI.Root.Path
    input.storages[0].fstype == "overlay"
    count(input.storages[0].options) == 0
    input.storages[0].shared == false
    input.OCI.Annotations["io.kubernetes.pod.namespace"] == "confidential-storage-test"
}

# The native agent replaces this process with its measured in-image pause
# configuration. It removes all host file bind mounts before native creation.
CreateContainerRequest if {
    common
    input.OCI.Annotations["io.kubernetes.container.name"] == "POD"
    input.OCI.Annotations["io.katacontainers.pkg.oci.container_type"] == "pod_sandbox"
    input.storages[0].source == "pause"
    input.OCI.Process.Args == ["/usr/bin/pod"]
    count(input.devices) == 0
    count(input.OCI.Linux.Devices) == 0
}

CreateContainerRequest if {
    common
    input.OCI.Annotations["io.kubernetes.container.name"] == "model"
    input.OCI.Annotations["io.katacontainers.pkg.oci.container_type"] == "pod_container"
    input.storages[0].source == workload.image
    input.OCI.Process.Args == workload.args
    input.OCI.Process.Cwd == "/"
    input.OCI.Process.User.UID == 1000
    input.OCI.Process.User.GID == 1000
    input.OCI.Process.NoNewPrivileges == true
    every environment in input.OCI.Process.Env {
        environment in workload.environment
    }
    count(input.devices) == 1
    input.devices[0].type_ == "scsi"
    input.devices[0].container_path == "/dev/encrypted-data"
}
