# Guest mount lifecycle backend

The Linux backend now connects the lifecycle controller to the tested opener and
real ext4 staging mounts inside the guest. It is a library for a privileged guest
system service. Applications remain unprivileged and must receive only filesystem
binds. The production Kata namespace adapter and attested providers are still
missing, so the runtime activation gate remains closed.

Sources:

- [`linux_backend.rs`](../../components/confidential-storage-guest/src/linux_backend.rs)
  implements `lifecycle::Backend` and the trusted provider interfaces.
- [`guest_mount.rs`](../../components/confidential-storage-guest/src/guest_mount.rs)
  manages private staging targets with Linux mount/unmount syscalls.
- [The opener](guest-opener.md) authenticates/seals the header and checks the
  effective dm-crypt/dm-integrity stack.

## Request and ownership flow

| Stage | Implemented behavior |
| --- | --- |
| Admission | The controller verifies receipt/session/time binding, reserves the volume/device and retains an activation tombstone. Use one controller/backend per guest service. |
| Resolve | A trusted provider returns an owned block descriptor, local major/minor and manifest bytes. The backend checks those numbers against the reserved device identity; the opener checks the actual descriptor and capacity. |
| Authenticate | Exact signed manifest/header digests and the fixed RW profile must validate before calling the key provider. |
| Key and open | The provider receives the verified grant and authenticated credential URI. The opener is retained before key fetch or kernel effects; the key goes to cryptsetup through a pipe. |
| Mount | Reserve a new private directory, retain the mount handle, then mount ext4 with `nodev,nosuid,noexec`. Verify mount identity, device, filesystem, propagation and flags. |
| Application bind | The namespace manager attaches the verified filesystem using retained namespace/root descriptors. Production Kata acquisition and startup callbacks remain unfinished. |
| Teardown | Require trusted application quiescence, detach the recorded namespace bind, unmount staging, remove the reserved empty directory, close both mapper targets, then acknowledge release. No lazy unmount, force, recursive deletion or formatting. |

`Providers` is an in-process trusted interface, not a network API. The deployed
implementation must resolve guest devices from authenticated policy, retrieve keys
through a session/grant-bound attested CDH channel, and acknowledge release through
the attested authority channel. Its `quiesce_consumers` method must stop the
application and prevent concurrent startup/namespace mutation before bind removal.
The backend now owns the registered bind through the [namespace manager](consumer-namespace.md). Implementing
these methods with host-supplied keys
or ordinary unauthenticated HTTP would not satisfy the contract. Only lab fixture
providers exist today.

The backend serializes blocking operations in Tokio blocking workers. The outer
controller retains service-owned tasks when an RPC waiter disappears. A poisoned
backend lock fails closed and requires recovery; it is not silently reset. Kernel
mount/unmount calls have no artificial cancellation deadline and can block on I/O.
Production supervision and recovery remain necessary.

## Private mount root and identity checks

Trusted bootstrap must create `/run/coco/storage`, owned by root with mode `0700`,
on private guest tmpfs. Opening that root traverses each component using directory
descriptors and `O_NOFOLLOW`; root ownership and non-writable group/other modes are
required. The final directory must be exactly `0700`. The covering mount must be
tmpfs with no shared/slave propagation fields.

Each activation gets a newly created directory beneath the retained root
descriptor. Existing directories or symlinks are refused. The manager pins the
original directory and records the mounted filesystem's kernel mount ID before
post-validation. Mounting uses descriptor paths and fixed ext4 flags. The visible
mount must match the source block device, be private and carry all required flags.

Unmount requires the same mount ID. After ordinary unmount, the underlying target
must match the original directory's device, inode and mount ID before removing
that one empty directory. Changed targets and busy filesystems retain ownership.
A reservation that fails after the attempt was recorded also remains blocked for
explicit recovery; the backend never adopts or deletes an unknown target.

These checks assume a trusted guest mount namespace and a single mount manager.
They are not protection against another privileged process inside the same TEE.
The backend now retains and removes one application bind through the namespace
manager before staging cleanup. The production agent must provide paused-container
descriptors and quiescence callbacks; those Kata integrations remain missing.

## Failure behavior

- Invalid manifest/header: fail before fetching a key; release only after checking
  that this activation has no storage effects.
- Wrong key or partial open: retain the opener and explicitly close it before
  acknowledging release. There is no retry-through-format path.
- Busy unmount: keep `NeedsUnmount`, mappings and exclusive ownership; do not
  contact the authority for release. Retry only after the busy reference is gone.
- Failed application detach: retain the staging mount and mappings without
  attempting their teardown or acknowledging authority release.
- Close or mount identity failure: keep the corresponding cleanup state. Never
  remove an unrelated target or report teardown merely because a path changed.
- Lost release acknowledgement: keep `NeedsRelease` after teardown. Retry the
  idempotent acknowledgement without reopening or remounting storage.
- Successful mapper cleanup drops the retained raw-device descriptor and sealed
  header before release. Closed tombstones do not pin those resources.
- Closed activation: the controller tombstone rejects replay, including after an
  idempotent close. A service restart requires a fresh attested session and a
  recovery protocol; in-memory records are not crash recovery.

The owned `Secret` buffer has no Debug/Clone implementation and is overwritten on
drop using volatile writes. Provider temporaries, register/stack copies, core
dumps, swap and tracing require separate treatment. This is not a complete
service-wide key-memory hardening claim.

## Reproduce and evidence

Use the existing disposable compiler and the opener guide's build/run commands.
`kernel_opener_lab` now runs the lifecycle scenarios after its primitive tests.
The test uses fresh scratch storage, synthetic signatures, an ephemeral fixture
key and a deliberately fallible local release provider. No production PVC, GPU,
Trustee policy or runtime artifact is involved.

The namespace holder is created by lab-only guest-root code; the library installs
and removes its application bind. The application then runs with UID
1000, zero capabilities and `NoNewPrivs=1`. It verifies filesystem read/write,
`noexec`, inability to read the mapper/backing device and inability to traverse
the private staging directory. This is a Linux namespace test, not a Kata E2E.

The fixture filesystem was already assigned to UID/GID 1000 by the earlier lab
setup. The reopen backend does not chown existing data or apply host-provided
`fsGroup` recursively. Production initialization must prepare the approved
workload UID/GID or writable subdirectory through authenticated policy; ordinary
Unix permissions still apply after decrypting and mounting the filesystem.

Evidence is recorded under
`tests/confidential-storage/evidence/2026-10-13/mount-backend/`. Export only reviewed
logs and inventories; the scratch disk, initramfs and fixture keys must remain in
the disposable builder and be deleted with it.

## Next work

Implement the measured guest device resolver and the attested authority/CDH
providers, then connect the namespace manager to Kata's paused-container/startup
and quiescence callbacks. Test partial mount
effects, namespace teardown, service/VM death, blocked I/O and recovery against
the approved guest kernel. Keep the separate first-use initialization protocol;
this backend only reopens initialized RW volumes. Model RO/verity and populator
integration remain separate work. No production encrypted-storage acceptance
gate has passed.
