package agent_policy
import rego.v1

# Workload-specific values are generated from the public publisher descriptor.
workload := {"args": ["/usr/bin/python3", "-c", "\"\"\"Measured CPU-only Qwen server for the encrypted RO PVC integration test.\n\nNo model downloads, remote code, key APIs, host file mounts or writable model.\nThe policy embeds this exact program and pins its image and environment.\n\"\"\"\n\nimport hashlib\nimport json\nimport os\nfrom pathlib import Path\nimport stat\nimport time\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n\nMODEL = \"qwen3-0.6b-luks\"\nINVENTORY = [{'bytes': 11343, 'path': 'LICENSE', 'sha256': '832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e'}, {'bytes': 726, 'path': 'config.json', 'sha256': '660db3b73d788119c04535e48cf9be5f55bc3100841a718637ae695b442f27dd'}, {'bytes': 239, 'path': 'generation_config.json', 'sha256': '2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2'}, {'bytes': 1671853, 'path': 'merges.txt', 'sha256': '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5'}, {'bytes': 1503300328, 'path': 'model.safetensors', 'sha256': 'f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b'}, {'bytes': 1079, 'path': 'publisher-source.json', 'sha256': 'd5945b210faf8c6c869fb72de03acd0e386507dae493107fed818ea9bb6948d8'}, {'bytes': 11422654, 'path': 'tokenizer.json', 'sha256': 'aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4'}, {'bytes': 9732, 'path': 'tokenizer_config.json', 'sha256': 'd5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101'}, {'bytes': 2776833, 'path': 'vocab.json', 'sha256': 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'}]  # noqa: F821 - replaced by prepare-policy.py\nroot = Path(\"/models\")\nassert os.getuid() == 1000 and os.getgid() == 1000\nstatus = Path(\"/proc/self/status\").read_text()\nfields = dict(line.split(\":\", 1) for line in status.splitlines() if \":\" in line)\nassert int(fields[\"CapEff\"].strip(), 16) == 0 and fields[\"NoNewPrivs\"].strip() == \"1\"\nassert not any(\n    Path(p).exists()\n    for p in (\n        \"/dev/encrypted-data\",\n        \"/dev/mapper/control\",\n        \"/run/confidential-containers/cdh.sock\",\n    )\n)\nassert os.statvfs(root).f_flag & os.ST_RDONLY\nfor item in INVENTORY:\n    fd = os.open(root / item[\"path\"], os.O_RDONLY | os.O_NOFOLLOW)\n    with os.fdopen(fd, \"rb\") as stream:\n        info = os.fstat(stream.fileno())\n        assert stat.S_ISREG(info.st_mode) and info.st_size == item[\"bytes\"]\n        digest = hashlib.file_digest(stream, \"sha256\").hexdigest()\n        assert digest == item[\"sha256\"]\nprint(\n    \"STORAGE_MODEL_SERVER_HASHES_PASS uid=1000 capabilities=0 readonly=true\", flush=True\n)\n\nimport torch  # noqa: E402\nfrom transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402\n\ntorch.set_num_threads(1)\ntorch.set_num_interop_threads(1)\nassert not torch.cuda.is_available()\ntokenizer = AutoTokenizer.from_pretrained(\n    root, local_files_only=True, trust_remote_code=False\n)\nmodel = AutoModelForCausalLM.from_pretrained(\n    root, local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16\n).eval()\nassert all(p.device.type == \"cpu\" for p in model.parameters())\n\n\nclass Handler(BaseHTTPRequestHandler):\n    protocol_version = \"HTTP/1.0\"\n\n    def log_message(self, *_args):\n        pass\n\n    def reply(self, code, body):\n        raw = json.dumps(body, separators=(\",\", \":\")).encode()\n        self.send_response(code)\n        self.send_header(\"Content-Type\", \"application/json\")\n        self.send_header(\"Content-Length\", str(len(raw)))\n        self.send_header(\"Cache-Control\", \"no-store\")\n        self.send_header(\"Connection\", \"close\")\n        self.end_headers()\n        self.wfile.write(raw)\n        self.close_connection = True\n\n    def do_GET(self):\n        if self.path == \"/health\":\n            self.reply(200, {\"status\": \"ready\", \"model\": MODEL, \"device\": \"cpu\"})\n        else:\n            self.reply(404, {\"error\": \"unknown route\"})\n\n    def do_POST(self):\n        if self.path != \"/v1/chat/completions\":\n            self.reply(404, {\"error\": \"unknown route\"})\n            return\n        self.connection.settimeout(5)\n        try:\n            lengths = self.headers.get_all(\"Content-Length\", [])\n            if (\n                len(lengths) != 1\n                or not lengths[0].isdigit()\n                or \"Transfer-Encoding\" in self.headers\n            ):\n                raise ValueError()\n            length = int(lengths[0])\n            if not 0 < length <= 4096:\n                raise ValueError()\n            body = json.loads(self.rfile.read(length))\n            if set(body) - {\"model\", \"messages\", \"max_tokens\", \"temperature\"}:\n                raise ValueError()\n            if body.get(\"model\") != MODEL or body.get(\"temperature\", 0) != 0:\n                raise ValueError()\n            messages = body[\"messages\"]\n            maximum = body.get(\"max_tokens\", 16)\n            if type(maximum) is not int or not 1 <= maximum <= 64:\n                raise ValueError()\n            if not isinstance(messages, list) or not 1 <= len(messages) <= 4:\n                raise ValueError()\n            for m in messages:\n                if (\n                    not isinstance(m, dict)\n                    or set(m) != {\"role\", \"content\"}\n                    or m[\"role\"] not in (\"system\", \"user\", \"assistant\")\n                    or not isinstance(m[\"content\"], str)\n                    or not 0 < len(m[\"content\"]) <= 1024\n                ):\n                    raise ValueError()\n            text = tokenizer.apply_chat_template(\n                messages,\n                tokenize=False,\n                add_generation_prompt=True,\n                enable_thinking=False,\n            )\n            inputs = tokenizer(text, return_tensors=\"pt\")\n            prompt_tokens = inputs[\"input_ids\"].shape[1]\n            if prompt_tokens > 512:\n                raise ValueError()\n        except (ValueError, TypeError, KeyError):\n            self.reply(400, {\"error\": \"invalid bounded chat request\"})\n            return\n        started = time.monotonic()\n        with torch.inference_mode():\n            output = model.generate(\n                **inputs,\n                max_new_tokens=maximum,\n                do_sample=False,\n                pad_token_id=tokenizer.eos_token_id\n            )\n        generated = output[0, prompt_tokens:]\n        completion = tokenizer.decode(generated, skip_special_tokens=True)\n        self.reply(\n            200,\n            {\n                \"model\": MODEL,\n                \"choices\": [\n                    {\n                        \"index\": 0,\n                        \"message\": {\"role\": \"assistant\", \"content\": completion},\n                    }\n                ],\n                \"usage\": {\n                    \"prompt_tokens\": prompt_tokens,\n                    \"completion_tokens\": len(generated),\n                    \"total_tokens\": prompt_tokens + len(generated),\n                },\n                \"test\": {\n                    \"device\": \"cpu\",\n                    \"elapsed_seconds\": round(time.monotonic() - started, 3),\n                },\n            },\n        )\n        print(\"STORAGE_MODEL_INFERENCE_PASS\", flush=True)\n\n\nserver = HTTPServer((\"0.0.0.0\", 8080), Handler)\nprint(\"STORAGE_MODEL_SERVER_READY port=8080 device=cpu\", flush=True)\nserver.serve_forever()\n"], "environment": ["AWS_OFI_NCCL_VERSION=1.14.0", "BASH_ENV=/etc/bash.bashrc", "COCOAPI_VERSION=2.0+nv0.8.1", "CUBLASMP_VERSION=0.5.1.65", "CUBLAS_VERSION=13.0.2.14", "CUDA_ARCH_LIST=7.5 8.0 8.6 9.0 10.0 12.0", "CUDA_DRIVER_VERSION=580.82.07", "CUDA_HOME=/usr/local/cuda", "CUDA_MODULE_LOADING=LAZY", "CUDA_VERSION=13.0.1.012", "CUDA_VISIBLE_DEVICES=", "CUDLA_VERSION=13.0.1.012", "CUDNN_FRONTEND_VERSION=1.14.0", "CUDNN_VERSION=9.13.0.50", "CUFFT_VERSION=12.0.0.61", "CUFILE_VERSION=1.15.1.6", "CURAND_VERSION=10.4.0.35", "CUSOLVER_VERSION=12.0.4.66", "CUSPARSELT_VERSION=0.8.1.1", "CUSPARSE_VERSION=12.6.3.3", "DALI_BUILD=", "DALI_URL_SUFFIX=130", "DALI_VERSION=1.51.2", "DOCA_VERSION=3.1.0", "EFA_VERSION=1.38.1", "ENV=/etc/shinit_v2", "GDRCOPY_VERSION=2.5", "HF_HOME=/dev/shm/hf", "HF_HUB_OFFLINE=1", "HOSTNAME=storage-model-ro", "HPCX_VERSION=2.24.1", "JUPYTER_PORT=8888", "KUBERNETES_PORT=tcp://172.30.0.1:443", "KUBERNETES_PORT_443_TCP=tcp://172.30.0.1:443", "KUBERNETES_PORT_443_TCP_ADDR=172.30.0.1", "KUBERNETES_PORT_443_TCP_PORT=443", "KUBERNETES_PORT_443_TCP_PROTO=tcp", "KUBERNETES_SERVICE_HOST=172.30.0.1", "KUBERNETES_SERVICE_PORT=443", "KUBERNETES_SERVICE_PORT_HTTPS=443", "LC_ALL=C.UTF-8", "LD_LIBRARY_PATH=/usr/local/lib/python3.12/dist-packages/torch/lib:/usr/local/lib/python3.12/dist-packages/torch_tensorrt/lib:/usr/local/cuda/compat/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64", "LIBRARY_PATH=/usr/local/cuda/lib64/stubs:", "MAXSMVER=", "MAX_JOBS=12", "MKL_NUM_THREADS=1", "MODEL_OPT_VERSION=0.33.0", "MOFED_VERSION=5.4-rdmacore56.0", "NCCL_VERSION=2.27.7", "NPP_VERSION=13.0.1.2", "NSIGHT_COMPUTE_VERSION=2025.3.1.4", "NSIGHT_SYSTEMS_VERSION=2025.5.1.121", "NSS_SDB_USE_CACHE=no", "NVFATBIN_VERSION=13.0.85", "NVFUSER_BUILD_VERSION=a71c674", "NVFUSER_VERSION=a71c674", "NVIDIA_BUILD_ID=214638689", "NVIDIA_DRIVER_CAPABILITIES=compute,utility,video", "NVIDIA_PRODUCT_NAME=vLLM", "NVIDIA_PYTORCH_VERSION=25.09", "NVIDIA_REQUIRE_CUDA=cuda>=9.0", "NVIDIA_REQUIRE_JETPACK_HOST_MOUNTS=", "NVIDIA_VISIBLE_DEVICES=void", "NVIDIA_VLLM_VERSION=25.09", "NVJITLINK_VERSION=13.0.88", "NVJPEG_VERSION=13.0.1.86", "NVPL_LAPACK_MATH_MODE=PEDANTIC", "NVPTXCOMPILER_VERSION=13.0.88", "NVRX_VERSION=0.4.1+cuda13", "NVSHMEM_VERSION=3.4.5", "NVVM_VERSION=13.0.88", "OMPI_MCA_coll_hcoll_enable=0", "OMP_NUM_THREADS=1", "OPAL_PREFIX=/opt/hpcx/ompi", "OPENMPI_VERSION=4.1.7", "OPENUCX_VERSION=1.19.0", "PATH=/usr/local/lib/python3.12/dist-packages/torch_tensorrt/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/mpi/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/ucx/bin:/opt/amazon/efa/bin:/opt/tensorrt/bin", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "PIP_BREAK_SYSTEM_PACKAGES=1", "PIP_CONSTRAINT=/etc/pip/constraint.txt", "PIP_DEFAULT_TIMEOUT=100", "PIP_NO_BUILD_ISOLATION=1", "POLYGRAPHY_VERSION=0.49.26", "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python", "PYTHONDONTWRITEBYTECODE=1", "PYTHONIOENCODING=utf-8", "PYTORCH_BUILD_NUMBER=0", "PYTORCH_BUILD_VERSION=2.9.0a0+50eac81", "PYTORCH_HOME=/opt/pytorch/pytorch", "PYTORCH_TRITON_VERSION=3.4.0+gitc817b9b6", "PYTORCH_VERSION=2.9.0a0+50eac81", "RDMACORE_VERSION=56.0", "SHELL=/bin/bash", "TENSORBOARD_PORT=6006", "TERM=xterm", "TMPDIR=/dev/shm", "TOKENIZERS_PARALLELISM=false", "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE=1", "TORCH_CUDA_ARCH_LIST=7.5 8.0 8.6 9.0 10.0 12.0+PTX", "TORCH_NCCL_USE_COMM_NONBLOCKING=0", "TRANSFORMERS_OFFLINE=1", "TRANSFORMER_ENGINE_VERSION=2.7", "TRITON_CUDACRT_PATH=/usr/local/cuda/include", "TRITON_CUDART_PATH=/usr/local/cuda/include", "TRITON_CUOBJDUMP_PATH=/usr/local/cuda/bin/cuobjdump", "TRITON_CUPTI_PATH=/usr/local/cuda/include", "TRITON_NVDISASM_PATH=/usr/local/cuda/bin/nvdisasm", "TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas", "TRTOSS_VERSION=", "TRT_VERSION=10.13.3.9", "UCC_CL_BASIC_TLS=^sharp", "UCC_EC_CUDA_EXEC_NUM_THREADS=256", "VLLM_VERSION=0.10.1.1+381074ae", "_CUDA_COMPAT_PATH=/usr/local/cuda/compat", "container=oci"], "image": "nvcr.io/nvidia/vllm@sha256:15f380ad9c32f0ac57ac16e4b778c6f733c88b9ffe3a936035d0a59ad17b1aab", "podEnvironment": [{"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"}, {"name": "CUDA_VISIBLE_DEVICES", "value": ""}, {"name": "TMPDIR", "value": "/dev/shm"}, {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}, {"name": "HF_HOME", "value": "/dev/shm/hf"}, {"name": "HF_HUB_OFFLINE", "value": "1"}, {"name": "TRANSFORMERS_OFFLINE", "value": "1"}, {"name": "TOKENIZERS_PARALLELISM", "value": "false"}, {"name": "OMP_NUM_THREADS", "value": "1"}, {"name": "MKL_NUM_THREADS", "value": "1"}]}
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
