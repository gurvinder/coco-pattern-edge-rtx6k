# Private guest storage service (development)

This component connects the tested authority client, private SNP KBS client and
read-only LUKS/dm-verity opener. It is **not yet installed in the live guest** and
is not evidence of either completed PVC E2E. Its initial configuration rejects RW
volumes: first-use formatting still needs integration with the signed journal.

The fixed, measured `/etc/confidential-storage/service.json` contains only public
trust roots, receipt verification key, endpoint pins, mount binding and an
approved manifest. It contains no storage key. Runtime session key, nonce and
operation ID are generated inside the guest. A signed authority grant permits
only the configured resource; the copied LUKS header and manifest are validated
before the private client asks Trustee for a fresh attested, encrypted key reply.
The service decrypts/mounts the device and sends only the mount path to the agent.

The daemon accepts framed `activate` and `close` messages on a root-owned 0600
Unix socket under a 0700 directory. It checks root peer credentials and the peer
executable inode against the measured agent. This is additional enforcement
inside the trusted VM-root boundary, not a defense against arbitrary malicious
VM root or PID reuse. Applications must stay non-root, without capabilities,
private socket mounts or a public AA/CDH signing/key endpoint.

One container/device reservation lasts for the VM lifetime. Activation failures,
disconnects and uncertain effects retain ownership. Close unmounts normally,
closes the kernel mapping, then closes the authority owner; busy or failed cleanup
never becomes a force-unmount or lease takeover. The agent must destroy the
consumer first. Closed reservations cannot reactivate. Service state is currently
in-memory; guest loss requires conservative authority recovery, not ownership
expiry or automatic reformatting.

## Build and test

Use the pinned guest-components checkout recorded by the KBS wrapper:

```sh
python3 components/confidential-storage-kbs/prepare.py \
  --upstream /scratch/guest-components --output /scratch/storage-service \
  --with-service
cd /scratch/storage-service/components/confidential-storage-service
cargo +1.94.1 test --locked
cargo +1.94.1 build --locked --release
```

Three protocol tests pass: framing/truncation bounds, arbitrary path/schema
rejection and reservation/replay behavior. These do not substitute for live
service/agent/Trustee/PVC and failure-recovery testing. The release build requires
Linux glibc through 2.34, compatible with the recorded RHEL 10 guest base.

See the [native lifecycle experiment](../confidential-storage-kata/native-agent/README.md)
and [private guest packaging](../../tests/confidential-storage/native-guest/README.md).
