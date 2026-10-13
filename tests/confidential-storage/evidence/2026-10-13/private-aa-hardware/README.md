# Private AA hardware checkpoint

Real CPU-only SNP evidence from the separate diagnostic guest was checked by a
pinned-source isolated Trustee AS. `hardware-validation.json` records signature,
binding and tampering checks. The default appraisal is contraindicated because
approved launch/TCB references are absent. No storage key was released and neither
storage E2E is complete.

The approved CRI-O reload retained its PID but did not activate the new handler.
One separately approved restart activated it; production pods/specifications and
GitOps matched the baseline. The extra RuntimeClass is intentional. `install.log`
and `restart.log` describe the initial v2 install; subsequent diagnostic-only
changes produced v4 (see the probe guide and current build manifest).

Application checks run as UID/GID 1000 with zero capabilities and NoNewPrivs=1,
without the private AA socket or device-mapper control. A live exec attempt was
rejected: `ExecProcessRequest is blocked by policy`. Quotes, chip identifiers,
certificates, EAR tokens and private signers are not included here.
