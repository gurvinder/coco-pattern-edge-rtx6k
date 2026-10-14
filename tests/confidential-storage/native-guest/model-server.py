"""Measured CPU-only Qwen server for the encrypted RO PVC integration test.

No model downloads, remote code, key APIs, host file mounts or writable model.
The policy embeds this exact program and pins its image and environment.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = "qwen3-0.6b-luks"
INVENTORY = __INVENTORY_JSON__  # noqa: F821 - replaced by prepare-policy.py
root = Path("/models")
assert os.getuid() == 1000 and os.getgid() == 1000
status = Path("/proc/self/status").read_text()
fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
assert int(fields["CapEff"].strip(), 16) == 0 and fields["NoNewPrivs"].strip() == "1"
assert not any(
    Path(p).exists()
    for p in (
        "/dev/encrypted-data",
        "/dev/mapper/control",
        "/run/confidential-containers/cdh.sock",
    )
)
assert os.statvfs(root).f_flag & os.ST_RDONLY
for item in INVENTORY:
    fd = os.open(root / item["path"], os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_size == item["bytes"]
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        assert digest == item["sha256"]
print(
    "STORAGE_MODEL_SERVER_HASHES_PASS uid=1000 capabilities=0 readonly=true", flush=True
)

import torch  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

torch.set_num_threads(1)
torch.set_num_interop_threads(1)
assert not torch.cuda.is_available()
tokenizer = AutoTokenizer.from_pretrained(
    root, local_files_only=True, trust_remote_code=False
)
model = AutoModelForCausalLM.from_pretrained(
    root, local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16
).eval()
assert all(p.device.type == "cpu" for p in model.parameters())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        pass

    def reply(self, code, body):
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ready", "model": MODEL, "device": "cpu"})
        else:
            self.reply(404, {"error": "unknown route"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.reply(404, {"error": "unknown route"})
            return
        self.connection.settimeout(5)
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if (
                len(lengths) != 1
                or not lengths[0].isdigit()
                or "Transfer-Encoding" in self.headers
            ):
                raise ValueError()
            length = int(lengths[0])
            if not 0 < length <= 4096:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if set(body) - {"model", "messages", "max_tokens", "temperature"}:
                raise ValueError()
            if body.get("model") != MODEL or body.get("temperature", 0) != 0:
                raise ValueError()
            messages = body["messages"]
            maximum = body.get("max_tokens", 16)
            if type(maximum) is not int or not 1 <= maximum <= 64:
                raise ValueError()
            if not isinstance(messages, list) or not 1 <= len(messages) <= 4:
                raise ValueError()
            for m in messages:
                if (
                    not isinstance(m, dict)
                    or set(m) != {"role", "content"}
                    or m["role"] not in ("system", "user", "assistant")
                    or not isinstance(m["content"], str)
                    or not 0 < len(m["content"]) <= 1024
                ):
                    raise ValueError()
            text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            inputs = tokenizer(text, return_tensors="pt")
            prompt_tokens = inputs["input_ids"].shape[1]
            if prompt_tokens > 512:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            self.reply(400, {"error": "invalid bounded chat request"})
            return
        started = time.monotonic()
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=maximum,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id
            )
        generated = output[0, prompt_tokens:]
        completion = tokenizer.decode(generated, skip_special_tokens=True)
        self.reply(
            200,
            {
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": completion},
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": len(generated),
                    "total_tokens": prompt_tokens + len(generated),
                },
                "test": {
                    "device": "cpu",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
            },
        )
        print("STORAGE_MODEL_INFERENCE_PASS", flush=True)


server = HTTPServer(("0.0.0.0", 8080), Handler)
print("STORAGE_MODEL_SERVER_READY port=8080 device=cpu", flush=True)
server.serve_forever()
