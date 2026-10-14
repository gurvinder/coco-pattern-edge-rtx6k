"""Synthetic mount consumer in the unchanged vLLM image; no inference claim."""

import errno
import json
import os
import time
from pathlib import Path

shared = Path("/rendezvous")
status = dict(
    line.split(":", 1)
    for line in Path("/proc/self/status").read_text().splitlines()
    if ":" in line
)
assert os.getuid() == 1001 and os.getgid() == 1001
assert int(status["CapEff"].strip(), 16) == 0
assert status["NoNewPrivs"].strip() == "1"
shared.joinpath("application.json").write_text(
    json.dumps({"pid": os.getpid(), "uid": os.getuid()})
)
for _ in range(240):
    if shared.joinpath("mounted.json").exists():
        break
    time.sleep(1)
else:
    raise RuntimeError("sidecar did not publish mounts")
metadata = json.loads(shared.joinpath("mounted.json").read_text())
assert Path("/models/qwen/model.txt").read_text() == "synthetic model, no secrets\n"
try:
    Path("/models/qwen/forbidden").write_text("must fail")
except OSError as error:
    assert error.errno == errno.EROFS, repr(error)
else:
    raise AssertionError("RO filesystem accepted a write")
with open("/data/canary", "w") as handle:
    handle.write("unprivileged writer\n")
    handle.flush()
    os.fsync(handle.fileno())
assert Path("/data/canary").read_text() == "unprivileged writer\n"
for path in [
    "/dev/mapper/control",
    "/dev/test-data",
    f"/proc/{metadata['sidecarPid']}/root/mnt/private/test-key",
]:
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except (PermissionError, FileNotFoundError):
        pass
    else:
        raise AssertionError("application could access private resource: " + path)
mounts = [
    line
    for line in Path("/proc/self/mountinfo").read_text().splitlines()
    if " /models/qwen " in line or " /data " in line
]
result = {
    "result": "PASS",
    "uid": os.getuid(),
    "gid": os.getgid(),
    "capEff": status["CapEff"].strip(),
    "noNewPrivs": status["NoNewPrivs"].strip(),
    "mounts": mounts,
    "roWriteDenied": True,
    "rwFsyncReadback": True,
    "privateResourcesDenied": True,
    "inferenceTested": False,
}
print(json.dumps(result), flush=True)
shared.joinpath("result.json").write_text(json.dumps(result))
for _ in range(60):
    if shared.joinpath("cleanup-done").exists():
        break
    time.sleep(1)
else:
    raise RuntimeError("cleanup timed out")
assert not Path("/models/qwen/model.txt").exists()
assert not Path("/data/canary").exists()
print("APPLICATION_MOUNTS_REMOVED_PASS", flush=True)
