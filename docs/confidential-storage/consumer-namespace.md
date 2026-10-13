# Application namespace bind manager

The guest library now owns the filesystem bind in a separate application mount
namespace, in addition to the private staging mount and encrypted mapping. This
is a tested Linux primitive and lifecycle integration, **not an installed Kata
adapter**. The production activation gate remains closed.

[`consumer.rs`](../../components/confidential-storage-guest/src/consumer.rs) is
private to the library. The trusted agent calls
`LinuxBackend::attach_consumer(grant, namespace_fd, root_fd, container_name)` after
successful activation and before application startup. It must call controller
close after any attachment failure and must never start the workload on failure.

## Descriptor and policy contract

The agent must retain file descriptors for the exact paused container's mount
namespace and root directory, obtained through its own container lifecycle state.
A namespace fd pins that namespace rather than repeatedly resolving a PID. The
library has no API accepting an application-provided PID or namespace pathname.
The agent must still prevent PID reuse/mismatched handles while acquiring those
descriptors and bind container identity to measured policy.

The manager checks the signed container name and currently supports only `rw` at
`/data`. It rejects a non-mount namespace descriptor and the service's own mount
namespace. Both input descriptors must be close-on-exec to prevent inheritance by application
exec. In the target namespace, the root descriptor must refer to a private
mount. Root and target directories must be root-owned and not group/other writable;
the target must be empty, not a symlink and not already a separate mount.

These local checks complement policy; a root-directory fd and a matching name are
not independent attestation of a container. The production agent must supply the
right pair, authorize the image/process/capabilities and serialize startup/removal.
No host RPC or application socket exposes this library.

## Attach and detach flow

1. The backend obtains a directory descriptor from its verified ext4 staging
   mount. Arbitrary caller-provided sources are not accepted by its public method.
2. `open_tree` creates a non-recursive detached clone of that filesystem. Before
   attachment, `mount_setattr` sets `nosuid,nodev,noexec` and private propagation.
   The application never receives the staging descriptor, mapper or key.
3. A dedicated short-lived thread unshares `CLONE_FS`, then joins only the retained
   mount namespace. Tokio/service threads keep their original filesystem state.
   Mount identity reads use `/proc/thread-self`, not the process leader's namespace.
4. The manager records the original target identity and detached mount ID before
   `move_mount` attaches the clone using source/target descriptors. It verifies
   the resulting mount ID, filesystem device/inode, ext4 type, private propagation
   and fixed flags. There is no recursive clone or path-based fallback.
5. Cleanup first invokes the trusted agent's `quiesce_consumers` callback to stop
   application activity and prevent concurrent namespace/startup changes. The
   callback must retain the container root and namespace until bind teardown;
   it must not destroy those targets first. The manager then enters the retained
   namespace and removes exactly its recorded
   bind with ordinary unmount. Busy or replaced targets fail closed.
6. Only after the original target is visible and the recorded mount ID is absent
   can cleanup drop namespace/root handles, unmount staging, close the mapper
   stack and acknowledge authority release.

The descriptor mount operations follow the Linux interfaces documented in
[open_tree(2)](https://man7.org/linux/man-pages/man2/open_tree.2.html),
[move_mount(2)](https://man7.org/linux/man-pages/man2/move_mount.2.html),
[mount_setattr(2)](https://man7.org/linux/man-pages/man2/mount_setattr.2.html) and
[setns(2)](https://man7.org/linux/man-pages/man2/setns.2.html).

Attachment is one-shot per activation. Cleanup admission closes further attach
attempts, including when quiescence fails. Namespace/mount effects are recorded
before the move; failures retain the handle for explicit teardown. An unrelated
overmount is never removed to reach the underlying bind. No force/lazy unmount,
automatic formatting or authority-release shortcut exists.

## Namespace creation order

Create the application's private namespace **before mounting decrypted storage**,
or explicitly remove inherited guest staging mounts before admitting it. Cloning
the service namespace after mounting storage could copy private plaintext mounts
even though propagation is disabled. Private propagation prevents later events
from spreading; it does not remove mounts already present when a namespace is
cloned. The production adapter must enforce this ordering and its namespace view.
The manager also rejects attachment if the target namespace already lists any
mount of the source filesystem device. Its negative test retains an open file
through an inherited alias and verifies that mapper close and authority release
stay blocked until that reference is closed. It does not equate an otherwise idle
namespace's lifetime with a persistent open reference to the filesystem.

The lab creates its namespace first, activates storage afterward, then attaches
only the verified filesystem at `/data`. The unprivileged application has no
mount capability. Separate namespaces do not create another hardware boundary
inside the confidential VM; guest services remain trusted.

## Tests and limits

The disposable Linux example now uses the Rust bind manager. Guest-root test code
creates a namespace holder; `nsenter` later launches only the capability-free UID
1000 application there. Shell commands no longer install or remove the successful
application bind. The application checks read/write, noexec, raw-device denial and
private-staging denial. Tests additionally cover wrong identity/namespace,
duplicate attachment, unchanged service namespace, a foreign overmount, busy bind
cleanup, callback failure, release retry and replay tombstones.

Device/key/authority providers remain synthetic. The example is not a Kata
container and its namespace holder is lab infrastructure. Evidence and reproduction
use the existing opener/kernel harness and
`tests/confidential-storage/evidence/2026-10-13/consumer/`.

Production work remains: connect paused-container descriptors and quiescence to
the approved Kata baseline, enforce namespace creation order, integrate measured
device resolution and attested authority/CDH delivery, and test process/VM death,
partial operations and blocked I/O. The pinned CDH prototype's
[ActivateVolume request](https://github.com/manuelh-dev/guest-components/blob/0ab1fb1be6e46293f29600085f929ae035ca3739/protos/protos/confidential-data-hub/api.proto)
still carries only a device ID and manifest URI, without the required profile and
authority session/operation binding. This change does not bypass that gap or
replace the live runtime.
