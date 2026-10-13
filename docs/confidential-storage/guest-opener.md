# Descriptor-based guest storage opener

[`opener.rs`](../../components/confidential-storage-guest/src/opener.rs) adds a real
Linux reopen/close primitive for the privileged **guest system service**. The
application should receive only a filesystem bind mount. There is no formatting
method, public socket, Trustee client or authority-release method. The separate
[Linux lifecycle backend](guest-mount-backend.md) now connects it to private guest
mounts and injected providers; production integration and the Kata activation
gate remain unfinished/closed.

## Authorization and descriptors

1. A trusted guest resolver supplies an owned block-device descriptor and expected
   local major/minor numbers. `BlockDevice::from_file` checks the type, identity and
   kernel-reported capacity via `BLKGETSIZE64`. Regular files and character devices
   are refused. The primitive does not accept a raw host path.
2. `Opener::prepare` requires a signature-verified grant and checks admission time.
   It authenticates the manifest and full 16 MiB header, validates the fixed RW
   profile, and retains a sealed memfd copy through `PreparedHeader`.
3. The trusted caller then obtains `credential_uri()` and retrieves that 32-byte
   credential through CDH. The provider is still unfinished. The opener neither
   reads a Kubernetes credential nor creates a key file.
4. `open` rechecks the grant and a monotonic admission deadline. It derives a
   mapping name from volume/session/activation and refuses existing target names.
   It records NeedsClose before invoking cryptsetup.
5. The child receives the **same sealed header descriptor** and retained device
   descriptor as `/proc/self/fd/N` paths. Only their child-side `FD_CLOEXEC` flags
   are cleared; parent descriptors remain close-on-exec. The key travels through
   an anonymous stdin pipe, never argv, environment or a file.
6. The primitive verifies the effective kernel stack before becoming Open and
   returning a mapper path to the trusted service. Applications must never receive
   that path or opener handle.

The command uses fixed `/usr/sbin/cryptsetup`, fixed options, batch mode, disabled
external tokens and disabled keyring storage. There is no shell expansion,
fallback cipher or format-on-open-failure path. The sealed header cannot be
repaired in place. Child environment is cleared, locale is C, child core dumps
are disabled, stdout capture is bounded and commands have a 30-second timeout.
Public errors are static codes; stderr is suppressed. No dm-crypt table or
`--showkeys` command is queried.

This depends on trusted, measured executables and `/proc`/`/sys` mounts. The future
service must also prevent key disclosure through its own core dumps, swap, tracing
or application access and implement secure key-buffer lifetime/zeroization.
Those service-wide protections are **not established by this primitive**.

## Effective-stack checks

Both targets need the expected cryptsetup UUID, one segment, and no suspension.
The exact kernel dependency chain is:

```text
activation dm-crypt target -> activation dm-integrity target -> retained block device
```

Cryptsetup status must report LUKS2, AES-XTS, a 768-bit combined key (512-bit
encryption plus 256-bit integrity), HMAC-SHA256, 4096-byte sectors and read/write
mode. Duplicate or unknown fields fail closed. Device identity is checked from
sysfs dependencies, not trusted from a printable status pathname.

The integrity table needs offset 32768 sectors, 32-byte tags, mode `J`, and this
exact option set for the tested tool/kernel combination:

```text
block_size:4096 journal_sectors:1848 interleave_sectors:32768
buffer_sectors:128 journal_watermark:50 commit_time:10000 fix_padding
```

Unknown/duplicate options, additional targets, direct/bitmap/recovery modes,
recalculation, discards and unkeyed internal hashing are rejected. This narrow
compatibility envelope was tested with cryptsetup 2.6.1 and the Debian 6.1 lab
kernel. A tool/kernel version or volume geometry that changes these values needs
explicit profile review and tests; do not silently relax checks to make it open.
Sector integrity does not prevent replay of an older valid writable snapshot or
authenticate all journal metadata.

## Failure and close semantics

```text
Prepared -> NeedsClose (before command) -> Open (after stack checks)
                |                          |
                +-------- explicit close --+
                             |
                NeedsClose on failure / Closed on verified absence
```

Failed open cannot be retried on the same handle. `close` verifies both mapping
UUIDs before removing anything. It handles a remaining integrity target after a
partial open and verifies absence of both names afterward. It never uses force or
deferred removal. Busy mappings retain NeedsClose; remove application/guest mounts
before retrying close.

A pre-existing name is not permission to remove someone else's mapping. Even a
never-opened handle refuses successful cleanup while either name exists. Dropping
an opener does **not** close kernel mappings or release ownership; retain it until
cleanup succeeds. The lifecycle controller's exclusive reservations, replay
tombstones and service-owned worker remain necessary. Per-handle state alone
cannot prevent cross-handle replay.

## Real Linux test

The disposable `kernel_opener_lab` example creates synthetic signed grants only.
Its disk and random key are fixtures: no TEE, GPU, Trustee or production PVC.
Initial formatting belongs to the existing harness, not the opener. Tests cover:

- Wrong-key failure and refusal to retry open on the consumed handle.
- Changing an **active encrypted keyslot byte** after preparation: new preparation
  fails authentication, while the prepared opener succeeds through its sealed
  descriptor. The harness restores the byte afterward; this is not a production
  header-repair routine.
- Name collision: open and cleanup fail against the existing mapping while the
  original owner remains usable.
- Ext4 access as UID 1000 with all capabilities removed and `NoNewPrivs=1`.
  The process must see the mapper node but fail to read it, preventing a false
  pass caused by a missing path. This is a process test, not a Kata namespace test.
- Close while mounted fails and retains state. After unmount, close removes both
  targets. Repeated close is harmless; the closed handle cannot reopen.
- A subsequent VM boot recovers the data; existing wrong-key and ciphertext
  corruption tests still pass.

In the pinned disposable compiler pod:

```bash
python3 tests/confidential-storage/kernel-lab/fetch.py --work /build/debian
cd components/confidential-storage-guest
cargo test --locked --all-targets
cargo clippy --locked --all-targets -- -D warnings
cargo build --locked --release --example kernel_opener_lab
cd ../..
python3 tests/confidential-storage/kernel-lab/run.py \
  --tools /build/debian/tools --work /build/opener-suite \
  --opener components/confidential-storage-guest/target/release/examples/kernel_opener_lab
```

Use new work directories. Export only reviewed logs; the initramfs contains the
random key. Never ship this example in a production guest. Evidence lives under
`tests/confidential-storage/evidence/2026-10-13/opener/`.

## Next integration work

The Linux lifecycle backend now retains device/mapping/mount ownership and manages
private guest staging mounts. The [namespace manager](consumer-namespace.md) now
owns the application bind; connect it to production Kata callbacks. Add the real
device resolver, attested authority/CDH transport and key provider. Test partial
opens/cleanup, service crashes and recovery against the approved guest kernel.
The production initialization backend also remains separate from the Python
reference coordinator. No live runtime or encrypted-storage E2E gate is enabled.
