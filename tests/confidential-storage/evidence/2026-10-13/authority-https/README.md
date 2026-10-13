# Authority HTTPS checkpoint

Date: 13 October 2026. Implementation branch: `dev/confidential-storage-integration`.

- `tests.log`: all 90 authority tests pass, including 18 additional TLS/AS adapter
  tests. Cryptographic signatures, TLS sockets and SQLite are real; hardware
  attestation claims in these tests are synthetic.
- `checks.json`: tested source hashes and explicit incomplete hardware/activation
  flags. Formatting with Black 24.10.0 and Flake8 7.3.0 passed.
- `diagnostic.json`: the installed `kata-cc` guest's public attestation API is
  disabled. The two isolated non-root diagnostic pods requested no PVC, GPU or
  key and were removed. No SNP appraisal was possible through that API.
- `cluster-after.json`: comparison against the retained E2E baseline reports
  `production_unchanged: true`.

The pinned real Trustee SNP AS and private AA ttRPC client built successfully.
Their inputs and outputs are recorded in the
[build lock](../../../../../components/confidential-storage-authority/attestation-build-lock.json).
The isolated real AS rejected `{}` as SNP evidence with HTTP 500 and
`Deserialize SNP Evidence failed: missing field attestation_report`. This only
confirms rejection of malformed evidence, not acceptance of a genuine quote.

The model's populated PVC and current workloads were preserved. No storage key
has been provisioned to this new authority path. No new guest handler or image is
installed. See [HTTPS/private guest integration](../../../../../docs/confidential-storage/authority-https.md).
