# Private-volume authority core

This is the first implementation of the authority's durable transition rules.
It is a local Python library, **not a deployed attestation service or a key broker**.
Run its tests with Python 3.9+ and the pinned signing-library dependency:

```sh
cd components/confidential-storage-authority
python3 -m pip install -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
```

`Authority` uses SQLite `BEGIN IMMEDIATE` transactions and `synchronous=FULL`.
The volume record, ownership, consumed operation IDs and event are committed in
one transaction before returning an authorization decision. Parallel callers
cannot obtain two different owners. Use an independently administered, private
directory on durable local storage; sharing the database through an ordinary
workload PVC would invalidate the authority's trust boundary.

| Call | Caller and effect |
| --- | --- |
| `register` | Trusted administrator records immutable volume, policy/profile digests and versioned KBS reference; duplicate registration fails |
| `begin(initialize)` | Verified guest obtains one initialization operation after durable `Initializing` transition |
| Retry of initialization | Same session/operation gets `resume-initialization`, never a new format grant; another session must stop for recovery |
| `commit_ready` | After verified guest completion receipt, records immutable authenticated header digest and `Ready`; conflicting retry fails |
| `begin(reopen)` | Only for `Ready`; returns the recorded header/profile/key reference; never initializes |
| `close` | Same activation/session confirms teardown, then releases ordinary ownership |
| `revoke` | Administrator denies future operations; previously released keys remain usable |

An initialization retry after `Ready` returns `open`, not permission to format.
The same operation ID cannot acquire ownership again after close. There is no
automatic owner expiry, force takeover, database reset, or recovery format API.
Incomplete initialization stays owned until an explicit recovery protocol is
implemented. This intentionally sacrifices availability on ambiguous failure.

`AttestedPeer` contains trusted inputs. Constructing this dataclass does **not**
validate evidence. A future adapter must verify fresh CPU/workload evidence,
any required GPU evidence, and a guest-held channel key before calling this core.
Never deserialize an untrusted HTTP body directly into `AttestedPeer` and treat
it as authenticated. Administrative methods need separate authentication.

Before deployment, implement and test:

- An attestation verifier and authenticated, replay-resistant session transport.
- Signed/channel-bound responses covering every returned field, audience and
  activation; the current dictionary is an internal result, not a bearer token.
- Guest receipts that bind durable initialization phases/header/profile to the
  same activation; this library cannot verify that a guest actually flushed disk.
- Measured guest policy binding target container, destination and device contract.
- Fresh Trustee key retrieval bound to the approved volume/profile. This library
  stores only resource identifiers and never handles key material.
- Independent authority recovery/epoch protection. Restoring an old SQLite
  backup can restore old authorization; SQLite durability does not prevent this.
- Guest mount lifecycle and failure cleanup. `close` trusts the adapter's teardown
  assertion and does not establish fencing against a malicious host.

These unit tests cover transaction/state rules, not cryptography, disk integrity,
attestation, application isolation, writable rollback resistance or hardware E2E.

## Signed reopen receipts

`receipts.py` adds an internal Ed25519 receipt issuer for the
[guest lifecycle core](../confidential-storage-guest/README.md). Install the pinned
`requirements.txt` dependency before running the complete test suite. The original
state core still has no network endpoint or hardware verifier.

New registration may include an immutable `mount_binding` with `manifest_uri`,
`container_name`, `destination` and `access`. For these records, `commit_ready`
requires `manifest_sha256` as well as the header digest. Both digests are immutable
on retries. The issuer refuses legacy/unbound/uninitialized records. Existing
records are not silently upgraded or reset.

The issuer checks the requested binding and profile, commits a nonce-bound reopen
operation, and signs a short-lived receipt containing the session, activation,
resource identities and mount constraints. Changed request nonces cannot reuse
an existing operation; exact retries retain its activation ID. A signing or delivery
failure does not roll back committed ownership. Signing keys are supplied by the
trusted administrator; tests generate them in memory and retain only public
signature fixtures. No real authority key was created or deployed.

This implements signed response construction, not the missing attested transport,
receipt-backed first-use initialization, signing-key distribution/rotation,
private CDH socket, disk backend or privileged guest mount integration. Keep the
Kata activation gate closed until those pieces pass their respective tests.
