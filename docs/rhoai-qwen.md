# RHOAI confidential Qwen extension

The `rhoai-confidential-qwen` branch extends the normal AMD SNP GPU CoCo pattern
with RHOAI 3.6 EA2, encrypted Qwen3 distribution, raw LUKS storage and a chat
server in the same confidential VM as inference. The base continues to own OSC,
Kata, GPU setup, Trustee and Vault/ESO. The extension introduces no storage
controller, CSI driver or key-release service.

Follow the [installation runbook](https://github.com/pmatouse/confidential-inference-pattern/blob/rhoai-confidential-qwen/docs/rhoai/install.md)
and [architecture](https://github.com/pmatouse/confidential-inference-pattern/blob/rhoai-confidential-qwen/docs/rhoai/architecture.md).
Confidential loading is Early Access / Developer Preview; the small streaming
initializer derivative is a demo image, with its source and base image pinned.

The committed RHOAI overrides describe this lab. A new site must regenerate
its own keys, TLS identity, immutable initializer image, measured guest policy
and approved Trustee binding using the runbook. Do not reuse another site's
measured policy or image-pull credentials. Configure the stock CoCo hardware
profile and independently calculate its launch references before enrollment.

For this branch, optional old inference samples and the separate upstream
KServe owner are disabled. On an existing cluster, orphan the old KServe
Applications before removing them so shared CRDs and RHOAI resources survive.
The new raw model PVC is retained on application deletion.
