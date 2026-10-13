"""Transactional authority core; no network, verifier, key delivery or disk I/O.

Only a trusted adapter may call this module after verifying peer attestation and
channel binding. AttestedPeer is a trusted input type, NOT attestation evidence.
Never expose these methods directly as unauthenticated HTTP/RPC endpoints.
The database must be outside the workload cluster's rollback domain.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


class Denied(Exception):
    """Rejected transition; callers must not perform the requested disk action."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", value
    ):
        raise Denied("invalid identifier")
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise Denied("expected a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class AttestedPeer:
    """Verified policy and fresh channel identity supplied by a future verifier."""

    policy_sha256: str
    session_sha256: str

    def validate(self):
        digest(self.policy_sha256)
        digest(self.session_sha256)


class Authority:
    """Single-authority SQLite backend, serialized across processes.

    No timeout-based takeover: a missing Close leaves the volume owned until a
    separately reviewed recovery procedure establishes that the old guest stopped.
    There is intentionally no reset/reinitialize or force-takeover method.
    """

    def __init__(self, path):
        supplied = Path(path).absolute()
        self.path = supplied.parent.resolve() / supplied.name
        # Require an administrator-owned private directory. This is a trusted
        # configuration path, never a caller-supplied per-volume filesystem path.
        if self.path.parent.stat().st_mode & 0o077:
            raise ValueError("authority database directory must be private (0700)")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError("authority database must be private (0600)")
        with self._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS volumes "
                "(id TEXT PRIMARY KEY, record TEXT NOT NULL)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS operations (
                volume_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                PRIMARY KEY (volume_id, operation_id))"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, volume_id TEXT NOT NULL,
                action TEXT NOT NULL, record TEXT NOT NULL)"""
            )
        # Persist the initial directory entry as well as SQLite's committed data.
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _load(db, volume_id):
        row = db.execute(
            "SELECT record FROM volumes WHERE id=?", (identifier(volume_id),)
        ).fetchone()
        if row is None:
            raise Denied("unknown volume")
        return json.loads(row[0])

    @staticmethod
    def _save(db, record, action):
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        db.execute(
            "UPDATE volumes SET record=? WHERE id=?", (encoded, record["volume_id"])
        )
        db.execute(
            "INSERT INTO events(volume_id,action,record) VALUES (?,?,?)",
            (record["volume_id"], action, encoded),
        )

    def register(
        self,
        volume_id,
        policy_sha256,
        profile_sha256,
        credential_uri,
        *,
        mount_binding=None
    ):
        """Trusted administrator operation. Contains a credential reference only."""
        identifier(volume_id)
        digest(policy_sha256)
        digest(profile_sha256)
        if (
            not isinstance(credential_uri, str)
            or not re.fullmatch(
                r"kbs:///[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+",
                credential_uri,
            )
            or any(part in (".", "..") for part in credential_uri[7:].split("/"))
        ):
            raise Denied("expected a versioned KBS resource reference")
        if mount_binding is not None:
            from receipts import validate_binding

            mount_binding = validate_binding(mount_binding)
        record = dict(
            mount_binding=mount_binding,
            manifest_sha256=None,
            volume_id=volume_id,
            policy_sha256=policy_sha256,
            profile_sha256=profile_sha256,
            credential_uri=credential_uri,
            generation=1,
            state="Uninitialized",
            header_sha256=None,
            owner=None,
            last_closed=None,
            revoked=False,
        )
        with self._transaction() as db:
            try:
                db.execute(
                    "INSERT INTO volumes VALUES (?,?)", (volume_id, json.dumps(record))
                )
            except sqlite3.IntegrityError as exc:
                raise Denied("volume already registered; reset is forbidden") from exc
            self._save(db, record, "register")
        return record

    def begin(
        self,
        volume_id,
        peer,
        operation_id,
        operation,
        expected_generation,
        *,
        request_sha256=None
    ):
        """Commit ownership and initialization intent BEFORE returning permission."""
        peer.validate()
        if request_sha256 is not None:
            digest(request_sha256)
        identifier(operation_id)
        if (
            operation not in ("initialize", "reopen")
            or type(expected_generation) is not int
        ):
            raise Denied("invalid operation or generation")
        with self._transaction() as db:
            record = self._load(db, volume_id)
            if record["revoked"] or peer.policy_sha256 != record["policy_sha256"]:
                raise Denied("volume or workload not authorized")
            if expected_generation != record["generation"]:
                raise Denied("stale generation")
            owner = record["owner"]
            if owner:
                if (
                    owner["session_sha256"],
                    owner["operation_id"],
                    owner["operation"],
                    owner.get("request_sha256"),
                ) != (peer.session_sha256, operation_id, operation, request_sha256):
                    raise Denied(
                        "previous owner not confirmed closed; recovery required"
                    )
                # Retry returns recorded state. Ready is never a fresh format grant.
                return self._grant(record)
            expected_state = "Uninitialized" if operation == "initialize" else "Ready"
            if record["state"] != expected_state:
                raise Denied("operation incompatible with authoritative volume state")
            try:
                db.execute(
                    "INSERT INTO operations VALUES (?,?)", (volume_id, operation_id)
                )
            except sqlite3.IntegrityError as exc:
                raise Denied("operation replay") from exc
            record["owner"] = dict(
                session_sha256=peer.session_sha256,
                operation_id=operation_id,
                operation=operation,
                activation_id=str(uuid.uuid4()),
                request_sha256=request_sha256,
            )
            if operation == "initialize":
                record["state"] = "Initializing"
            self._save(db, record, "begin-" + operation)
            grant = self._grant(record)
            if operation == "initialize":
                grant["action"] = "initialize"
            return grant

    @staticmethod
    def _grant(record):
        # This is an internal result, not a signed bearer token or KBS capability.
        result = dict(record)
        result["action"] = (
            "resume-initialization" if record["state"] == "Initializing" else "open"
        )
        return result

    @staticmethod
    def _check_owner(record, peer, activation_id):
        peer.validate()
        owner = record["owner"]
        if (
            peer.policy_sha256 != record["policy_sha256"]
            or not owner
            or (
                owner["session_sha256"] != peer.session_sha256
                or owner["activation_id"] != activation_id
            )
        ):
            raise Denied("activation owner mismatch")

    def commit_ready(
        self, volume_id, peer, activation_id, header_sha256, *, manifest_sha256=None
    ):
        """Trusted adapter calls after verifying a durable initialization receipt.

        This core cannot verify disk writes, journal state or receipt attestation.
        The adapter and guest recovery protocol are required before deployment.
        """
        digest(header_sha256)
        if manifest_sha256 is not None:
            digest(manifest_sha256)
        with self._transaction() as db:
            record = self._load(db, volume_id)
            self._check_owner(record, peer, activation_id)
            if record["revoked"] or record["owner"]["operation"] != "initialize":
                raise Denied("initialization commit not authorized")
            if record.get("mount_binding") is not None and manifest_sha256 is None:
                raise Denied("bound mounts require an authenticated manifest digest")
            if record["state"] == "Ready":
                if header_sha256 != record[
                    "header_sha256"
                ] or manifest_sha256 != record.get("manifest_sha256"):
                    raise Denied("header identity cannot change on retry")
                return dict(record)
            if record["state"] != "Initializing":
                raise Denied("not initializing")
            record["state"] = "Ready"
            record["header_sha256"] = header_sha256
            record["manifest_sha256"] = manifest_sha256
            self._save(db, record, "commit-ready")
            return dict(record)

    def close(self, volume_id, peer, activation_id):
        """Release ownership after trusted teardown; this does not provide fencing."""
        with self._transaction() as db:
            record = self._load(db, volume_id)
            peer.validate()
            previous = record.get("last_closed")
            if (
                record["owner"] is None
                and previous
                and peer.policy_sha256 == record["policy_sha256"]
                and previous["session_sha256"] == peer.session_sha256
                and previous["activation_id"] == activation_id
            ):
                return
            self._check_owner(record, peer, activation_id)
            if record["state"] != "Ready":
                raise Denied("interrupted initialization requires explicit recovery")
            record["last_closed"] = record["owner"]
            record["owner"] = None
            self._save(db, record, "close")

    def revoke(self, volume_id):
        """Administrative denial of new operations; does not revoke existing keys."""
        with self._transaction() as db:
            record = self._load(db, volume_id)
            record["revoked"] = True
            self._save(db, record, "revoke")

    def inspect(self, volume_id):
        with self._transaction() as db:
            return self._load(db, volume_id)
