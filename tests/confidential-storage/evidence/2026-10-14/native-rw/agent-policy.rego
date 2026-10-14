package agent_policy
import rego.v1

# Workload-specific values are generated from the public publisher descriptor.
workload := {"args": ["/bin/sh", "-c", "set -eu\nid\ngrep -E '^(CapEff|NoNewPrivs):' /proc/self/status\ngrep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status\ngrep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status\ntest ! -e /dev/encrypted-data\ntest ! -e /dev/mapper/control\ntest ! -e /run/confidential-containers/cdh.sock\ntest \"$(stat -c %u /data)\" = 1000\ntest \"$(stat -c %g /data)\" = 1000\numask 077\ncd /data\nif test -e sentinel; then\n    printf '%s\\n' '48872dd56b80947bb8dc617632d0efe8544589f244d824ee77deb6ad9d504921  sentinel' | sha256sum -c -\n    case \"$(cat reopen-count)\" in 1|2) ;; *) exit 1;; esac\n    printf '2\\n' > reopen-count\n    sync -f reopen-count\n    sync -f /data\n    echo STORAGE_RW_REOPEN_PASS\nelse\n    test ! -e reopen-count\n    printf '%s\\n' 'confidential-storage-public-rw-persistence-canary-v1' > sentinel\n    printf '1\\n' > reopen-count\n    sync -f sentinel\n    sync -f reopen-count\n    sync -f /data\n    echo STORAGE_RW_INITIAL_WRITE_PASS\nfi\nprintf '%s\\n' '48872dd56b80947bb8dc617632d0efe8544589f244d824ee77deb6ad9d504921  sentinel' | sha256sum -c -\necho STORAGE_RW_UNPRIVILEGED_PASS\nsleep 120\n"], "container": "app", "environment": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM=xterm", "HOSTNAME=storage-rw", "NSS_SDB_USE_CACHE=no", "container=oci", "KUBERNETES_PORT_443_TCP_ADDR=172.30.0.1", "KUBERNETES_SERVICE_HOST=172.30.0.1", "KUBERNETES_SERVICE_PORT=443", "KUBERNETES_SERVICE_PORT_HTTPS=443", "KUBERNETES_PORT=tcp://172.30.0.1:443", "KUBERNETES_PORT_443_TCP=tcp://172.30.0.1:443", "KUBERNETES_PORT_443_TCP_PROTO=tcp", "KUBERNETES_PORT_443_TCP_PORT=443"], "image": "registry.access.redhat.com/ubi9/ubi-minimal@sha256:b966f21589d418d1e02159cb8e03e261fc8c4ee9061258893f5e4e33986aac4c"}
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
    input.OCI.Annotations["io.kubernetes.container.name"] == "app"
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
