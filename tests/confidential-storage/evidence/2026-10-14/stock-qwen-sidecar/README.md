# Stock Kata Qwen sidecar evidence — 14 October 2026

`validation.json` is the final result for add-on build 11. The `chat-*` records
trace NodePort 30081 through the same chatbot to two distinct KServe pod UIDs,
with a live synthetic inference and counter increment in each. Application logs
contain producer model hashes, the preserved data canary, and a CUDA arithmetic
probe. `shutdown.log` and the three authority snapshots establish clean close
followed by fresh ownership without formatting or recovery in the final restart.

`initial-v10-*` and `interrupted-restart-fencing.json` document the earlier build:
inference passed, shutdown failed, and explicit trusted-lab recovery was required.
They must not be interpreted as a successful restart. Build 11 fixes stock Kata's
container-wide TERM interaction in the add-on, without a Kata change.

`stock-measurement.json` was calculated before approval, so its `keysApproved:false`
field describes that preparation stage. `stock-launch-verified-after-restart.json`
compares the final live boot against it. The final identity and live acceptance
are in `validation.json` and the chat/attestation records.

Build input inventories contain public tools, executables and source hashes only.
The scoped Trustee policy tests use synthetic claims; live CPU/GPU appraisal
appears separately in `authority-attestation.log`. No disk keys, private signing
material, authority database, registry credentials or user prompts are included.
