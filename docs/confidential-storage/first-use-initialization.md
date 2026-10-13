# First-use formatting of private writable storage

Yes: the writable-volume design includes initialization of a newly provisioned,
disposable raw-block PVC. The guest system service will create LUKS2, authenticated
integrity metadata and an ext4 filesystem. The application receives only `/data`
and requires no privileged container, raw device or `CAP_SYS_ADMIN`.

This is **not automatic conversion of any non-LUKS disk**. A volume containing
existing plaintext data needs a separately authorized migration/import workflow.
Missing LUKS magic, a damaged header, an invalid key or a failed open never grants
permission to format. The read-only model path differs: the trusted publisher
prepares the encrypted filesystem, and the populator copies ciphertext; consumers
do not format model PVCs.

## Implemented authorization sequence

The local authority library has an explicit approval and step journal. A trusted
in-process coordinator now connects it to the disposable Linux test backend; see
[the executor contract and failure tests](initialization-executor.md). The [signed guest initialization API](guest-initialization-protocol.md) now connects
the Rust client to the TLS gateway and AS-result verifier. Production disk
execution, private CDH key retrieval and hardware attestation validation remain unfinished.

1. The administrator registers the volume, workload policy, crypto profile,
   versioned key URI and fixed `/data` mount binding.
2. `approve_initialization(volume_id, plan)` explicitly authorizes destruction of
   the contents of that **new** disposable volume. The immutable plan identifies
   the provisioning request/PVC UID, exact capacity, chosen LUKS UUID, filesystem
   and layout. Its SHA-256 also covers the registered volume/generation, workload
   policy, crypto profile, key URI and mount binding. The provisioning ID is an
   administrative identity, not hardware storage attestation. A transactional unique
   reservation prevents approving the same provisioning ID under another volume
   name, and the reservation is not released after initialization.
3. A verified guest begins `initialize`. The authority durably records the owner
   and `Initializing` before returning `plan-initialization`. This response does
   not authorize a disk operation by itself.
4. The guest requests each step in order. The authority writes `Started` and a
   unique step ID before returning the sole `execute` decision for that step.
5. The trusted transport adapter validates the guest's completion evidence and
   checks actual header/layout/key-reference and final manifest against the approved
   plan, then records the result against the same activation/session/step. This advances the
   step to `Complete`. Results and evidence digests cannot change on retry.
6. `commit_ready` succeeds only after every step completes and its header/manifest
   digests match the final durability barrier. Normal reopen can then be authorized.

| Step | Intended guest operation | Result recorded by the authority |
| --- | --- | --- |
| `luks-format` | Create the approved LUKS2/integrity layout with the planned UUID and key; no application exposure | Header SHA-256 |
| `integrity-initialize` | Finish/verify required integrity initialization and inspect the effective journaled stack | Same header SHA-256 and digest of verified stack evidence |
| `filesystem-create` | Create the approved ext4 filesystem inside the verified mapping | Same header SHA-256 and filesystem UUID |
| `durability-barrier` | Flush required device/filesystem state and durably publish the authenticated manifest/header identity | Same header SHA-256 and manifest SHA-256 |

These are protocol phases. In the tested Linux lab, cryptsetup performs integrity
initialization during formatting; the second phase verifies its completed result
and effective kernel stack rather than repeating destructive work. That sequence
is now exercised under the coordinator. Power-loss, partially executed commands and
production-kernel behavior still require separate tests.

## Failure timeline

```text
authority commits Started(step-id)
             |
             +-- response delivered -> guest executes at most once in that session
             |                        -> verified completion -> Complete
             |
             +-- response lost / process dies / completion uncertain
                                      -> retry returns reconcile
                                      -> NO second execute decision
```

An exact retry of a completed step returns `complete`; a started step returns
`reconcile`. Neither permits rerunning `luksFormat` or `mkfs`. A verified guest
may acknowledge the original step after it proves completion, but the library
provides no automatic resume/reformat routine. A new session cannot take over an
uncertain owner, and `close` cannot clear incomplete initialization. The conservative
result may be a stranded disposable PVC requiring a separately reviewed recovery
or disposal decision.

The library dictionary containing `execute` is an internal, trusted call result.
The network adapter signs a fresh request/challenge-bound decision, and the Rust
client accepts it as a non-cloneable, expiring execute permit. It must
**not** become a replayable stateless format token. The coordinator serializes local
execution, binds the decision to its context and refuses uncertain retries. The
production guest still needs authenticated session/channel binding and explicit
recovery rules for cancellation, process death and lost replies. The authority's
single decision alone cannot guarantee exactly-once physical disk effects.

Similarly, `evidence_sha256` stores the identity of evidence already verified by
the adapter. A digest or a Python `AttestedPeer` object does not itself prove a
TEE, completed writes, successful journal initialization or a durable flush.
The authority unit suite supplies synthetic assertions. The separate journal/kernel
lab performs real disk operations with a synthetic peer and unauthenticated console.

Old development records without explicit approval and completed step history fail
closed for initialization and signed reopen. There is no automatic migration that
marks them complete. Restoring an older authority database can still restore old
permission: independent durable storage and rollback/epoch protection remain
production prerequisites.

## Next executable milestones

1. **Guest executor and attested transport:** bind the approved plan and one-shot
   step decisions to a verified guest session. Obtain the volume key through CDH
   only for that authorized flow, using a private guest-service interface.
2. **Real disposable-volume backend:** implement formatting plus normal reopen,
   exact sealed-header use, effective dm-crypt/dm-integrity stack verification,
   mount ownership and teardown. Keep the application unprivileged. Finish flush,
   partial-failure and reconciliation rules before enabling the Kata gate.
3. **Isolated guest artifact:** include compatible tools/kernel support, port the
   adapter to the approved runtime baseline and register a separate test runtime.
   Replacing shared runtime files or interrupting KServe needs a concrete maintenance
   change; the current live runtime remains untouched.
4. **RW acceptance tests:** initialize once, write/fsync, replace the pod and
   reopen the same data. Inject failures at each recorded boundary. Verify no
   reformat after corrupted headers, wrong keys, bad tags, busy teardown or lost
   responses. Record the writable snapshot rollback limitation explicitly.
5. **Model path:** complete immutable model publication, ciphertext population and
   read-only activation; then integrate a separately named inference workload.

The authority tests cover the step order, concurrent decisions, lost responses,
restart, transaction failures, conflicting completions, revocation and refusal to
format Ready/read-only/unapproved volumes. They do not satisfy RW01–RW09 by
themselves. See the [handoff acceptance matrix](../confidential-storage-implementation-handoff.md)
and [current implementation status](continuation.md).
