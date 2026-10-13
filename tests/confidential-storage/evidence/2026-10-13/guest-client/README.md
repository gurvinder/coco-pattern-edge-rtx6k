# Guest authority client interoperability

Real TLS 1.3, Rust ES256 proofs, Python Ed25519 receipts and SQLite state.
Hardware/AS evidence is explicitly synthetic. No model key or disk is involved.

The final 11 tests cover normal reopen/retry/close/replay, wrong CA/hostname,
changed challenge/InitData, wrong receipt signer, weak hardware appraisal,
redirects, unknown fields, oversized response, and ownership retained against a
new guest session after a rejected response. The earlier build log includes the
single Rust canonicalization test and initial eight-case transport run; the
final log contains the complete 11-case suite.

Source/artifact hashes match the committed source. Formatting and strict Clippy
passed. `python-wheels.json` pins the isolated Linux test dependencies. The
compiler manifest is scratch tooling only, not a confidential workload manifest.
No private signing/TLS keys, model keys, generated initrds or tokens are included.

The first compiler was evicted at its 16 GiB EmptyDir limit; the replacement uses
32 GiB. Neither has host access, a PVC, GPU, service-account token or privileges.
Live confidential integration and node-handler approval remain outstanding.
