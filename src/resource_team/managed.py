"""Owned current SQLite allocation source, caller scopes and atomic outbox decisions."""

from __future__ import annotations

import base64
import hmac
import os
import secrets
import sqlite3
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from . import managed_state as state
from .domain import transition, public, selected
from .source import PROVENANCE, parse
from .strict import (
    MAX_BYTES,
    canonical,
    digest,
    integer,
    keys,
    read_original,
    sha256,
    text,
)


@dataclass(frozen=True)
class Credential:
    id: str
    token: str
    authority_sha256: str | None = None


class ManagedSource:
    def __init__(self, path, commit_guard=None):
        supplied = Path(path).absolute()
        self.path = supplied.parent.resolve() / supplied.name
        self.commit_guard = commit_guard
        self.database = None
        self.descriptor = None
        self.database_close_attempted = False
        self.database_closed = False
        self.descriptor_close_attempted = False
        self.descriptor_closed = False
        self.close_errors = []
        self.instance_id = None
        self.original_header_sha256 = None
        self.root_identity = self._physical(self.path.parent.stat())
        try:
            before = self.path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or not 4096 <= before.st_size <= state.MAX_DATABASE_BYTES
            ):
                raise ValueError(
                    "bounded regular unshared nonsymlink allocation database required"
                )
            self.descriptor = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            self.identity = self._physical(os.fstat(self.descriptor))
            if self.identity != self._physical(before):
                raise ValueError("original allocation database changed before opening")
            self.database = sqlite3.connect(
                self.path.as_uri() + "?mode=rw",
                uri=True,
                timeout=0.25,
                isolation_level=None,
            )
            self.database.execute("PRAGMA trusted_schema=OFF")
            self.database.execute("PRAGMA synchronous=FULL")
            self.database.execute("PRAGMA busy_timeout=250")
            self._guard()
            snapshot = self.snapshot()
            self.instance_id = snapshot["source"]["instance_id"]
            self.original_header_sha256 = digest(snapshot["source"])
        except BaseException as error:
            self.close()
            error.managed_source = self
            raise

    @staticmethod
    def _physical(st):
        return f"{st.st_dev}:{st.st_ino}"

    def _guard(self):
        if (
            self.database_close_attempted
            or self.descriptor_close_attempted
            or self.descriptor is None
        ):
            raise ValueError("allocation source owner is closing or closed")
        st = self.path.lstat()
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or st.st_size > state.MAX_DATABASE_BYTES
            or self._physical(st) != self.identity
            or self._physical(os.fstat(self.descriptor)) != self.identity
            or self._physical(self.path.parent.stat()) != self.root_identity
        ):
            raise ValueError("original allocation database physical identity differs")

    @classmethod
    def seed(cls, path, fixture):
        raw, _ = read_original(fixture)
        return cls.seed_bytes(path, raw)

    @classmethod
    def seed_bytes(cls, path, raw):
        initial = parse(raw)["business"]
        supplied = Path(path).absolute()
        supplied.parent.mkdir(parents=True, exist_ok=True)
        target = supplied.parent.resolve() / supplied.name
        fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        db = None
        try:
            physical = cls._physical(os.fstat(fd))
            db = sqlite3.connect(
                target.as_uri() + "?mode=rw", uri=True, isolation_level=None
            )
            db.execute("PRAGMA page_size=4096")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA application_id=" + str(state.APPLICATION_ID))
            db.execute("PRAGMA user_version=" + str(state.SOURCE_VERSION))
            db.execute("BEGIN IMMEDIATE")
            for sql in state.SCHEMA.values():
                db.execute(sql)
            header = {
                "schemaVersion": state.SOURCE_VERSION,
                "kind": "managed_synthetic_resource_source",
                "instance_id": str(uuid4()),
                "physical_identity": physical,
                "fixture_base64": base64.b64encode(raw).decode(),
                "fixture_sha256": sha256(raw),
                "provenance": deepcopy(PROVENANCE),
                "creation": "fixture",
                "population": None,
            }
            db.execute("INSERT INTO metadata VALUES (1,?)", (canonical(header),))
            db.execute("INSERT INTO business VALUES (1,?)", (canonical(initial),))
            state.snapshot(db, physical, header["instance_id"])
            if (
                cls._physical(target.lstat()) != physical
                or target.lstat().st_nlink != 1
            ):
                raise ValueError("new allocation source physical identity differs")
            db.execute("COMMIT")
        finally:
            try:
                if db is not None:
                    try:
                        if db.in_transaction:
                            db.execute("ROLLBACK")
                    finally:
                        db.close()
            finally:
                os.close(fd)
        return cls(target)

    def _snapshot(self):
        self._guard()
        value = state.snapshot(self.database, self.identity, self.instance_id)
        if (
            self.original_header_sha256 is not None
            and digest(value["source"]) != self.original_header_sha256
        ):
            raise ValueError("pinned original allocation header differs")
        self._guard()
        return value

    def snapshot(self):
        self._guard()
        if self.database.in_transaction:
            return self._snapshot()
        self.database.execute("BEGIN")
        try:
            value = self._snapshot()
            self.database.execute("COMMIT")
            return value
        except BaseException:
            if self.database.in_transaction:
                self.database.execute("ROLLBACK")
            raise

    def _transaction(self, callback, guard=True):
        self._guard()
        self.database.execute("BEGIN IMMEDIATE")
        try:
            result = callback()
            self._guard()
            if (
                self.database.execute("PRAGMA page_count").fetchone()[0] * 4096
                > state.MAX_DATABASE_BYTES
            ):
                raise ValueError("pending allocation source exceeds64MiB")
            if guard and self.commit_guard is not None:
                self.commit_guard()
            self._guard()
            self.database.execute("COMMIT")
            return result
        except BaseException:
            if self.database.in_transaction:
                self.database.execute("ROLLBACK")
            raise

    @staticmethod
    def _insert(db, table, id, value):
        db.execute("INSERT INTO " + table + " VALUES (?,?)", (id, canonical(value)))

    def intent(self, request_id, task):
        snapshot = self.snapshot()
        return state.caller_intent(
            snapshot["business"], request_id, task, snapshot["context_sha256"]
        )

    def begin(
        self, intent, task, *, allow_writes=False, execution="native", model=None
    ):
        text(task, 1, 8192)
        if type(allow_writes) is not bool or execution not in ("native", "human"):
            raise ValueError(
                "exact independent allocation write/execution mode required"
            )
        if execution == "native":
            state.model_target(model)
        elif model is not None:
            raise ValueError("human allocation control cannot claim native model")

        def commit():
            source = self._snapshot()
            keys(intent, {"request_id", "source", "task", "context_sha256"})
            if digest(intent) != digest(
                state.caller_intent(
                    source["business"],
                    intent["request_id"],
                    intent["task"],
                    source["context_sha256"],
                )
            ):
                raise ValueError("complete current original allocation intent required")
            if any(
                o["status"] == "active"
                and o["intent"]["request_id"] == intent["request_id"]
                for o in source["records"]["operations"]
            ):
                raise ValueError(
                    "selected request already has an active allocation scope"
                )
            credential = Credential(str(uuid4()), secrets.token_hex(32))
            operation = {
                "id": credential.id,
                "token_sha256": sha256(credential.token.encode()),
                "task_sha256": sha256(task.encode("utf-8", errors="strict")),
                "intent": deepcopy(intent),
                "execution": execution,
                "model": deepcopy(model),
                "initial_frontier": len(source["records"]["decisions"]),
                "allow_writes": allow_writes,
                "status": "active",
                "closed_frontier": None,
                "closed_observations": None,
            }
            credential = Credential(
                credential.id, credential.token, self._authority(operation)
            )
            self._insert(self.database, "operations", credential.id, operation)
            self._snapshot()
            return credential

        return self._transaction(commit)

    def observe(self, credential, role):
        def commit():
            source = self._snapshot()
            operation = self._operation(source, credential)
            value = state.current_read(
                source["business"], operation, source["views"][operation["id"]]
            )
            if role not in state.ROLES or value["role"] != role:
                raise ValueError("own actual current allocation role required")
            proof = secrets.token_hex(32)
            observation = {
                "id": len(source["records"]["observations"]) + 1,
                "operation_id": operation["id"],
                "frontier": len(source["records"]["decisions"]),
                "context_sha256": source["context_sha256"],
                "role": role,
                "value": value,
                "proof_sha256": sha256(proof.encode()),
            }
            self._insert(self.database, "observations", observation["id"], observation)
            self._snapshot()
            return {
                "observation": deepcopy(observation),
                "proof": proof,
                "value": deepcopy(value),
            }

        return self._transaction(commit)

    def commit(self, credential, action, observation_id, proof):
        integer(observation_id, 1)
        if type(proof) is not str:
            raise ValueError("genuine own allocation read proof required")

        def commit():
            source = self._snapshot()
            operation = self._operation(source, credential)
            read = next(
                (
                    r
                    for r in source["records"]["observations"]
                    if r["id"] == observation_id
                ),
                None,
            )
            if (
                read is None
                or read["operation_id"] != operation["id"]
                or not hmac.compare_digest(read["proof_sha256"], sha256(proof.encode()))
                or read["context_sha256"] != source["context_sha256"]
                or read["frontier"] != len(source["records"]["decisions"])
                or any(
                    d["operation_id"] is not None and d["read_id"] == observation_id
                    for d in source["records"]["decisions"]
                )
            ):
                raise ValueError("genuine own current unused allocation read required")
            after, result, status, _view = state.evaluate(
                source["business"],
                operation,
                source["views"][operation["id"]],
                action,
                read,
            )
            return self._decision(
                source,
                action,
                after,
                result,
                status,
                operation["id"],
                observation_id,
                "managed_caller",
            )

        return self._transaction(commit)

    def _decision(
        self, source, action, after, result, status, operation_id, read_id, provenance
    ):
        ordinal = len(source["records"]["decisions"]) + 1
        row = {
            "id": ordinal,
            "action": deepcopy(action),
            "operation_id": operation_id,
            "read_id": read_id,
            "before_context_sha256": source["context_sha256"],
            "after_context_sha256": state.context(source["source"], ordinal, after)[1],
            "status": status,
            "result": deepcopy(result),
            "provenance": provenance,
        }
        if digest(after) != digest(source["business"]):
            self.database.execute(
                "UPDATE business SET document=? WHERE id=1", (canonical(after),)
            )
        self._insert(self.database, "decisions", ordinal, row)
        final = self._snapshot()
        return {
            "decision": deepcopy(row),
            "result": deepcopy(result),
            "context_sha256": final["context_sha256"],
        }

    def human(self, action):
        """Separate explicit synthetic-human boundary; never a native model tool."""
        if type(action) is not dict or action.get("kind") not in (
            "hold",
            "commit",
            "compensate",
            "update_request",
            "update_resource",
        ):
            raise ValueError("explicit human allocation action required")

        def commit():
            before = self._snapshot()
            after, result = transition(before["business"], action)
            return self._decision(
                before,
                action,
                after,
                result,
                (
                    "applied"
                    if digest(after) != digest(before["business"])
                    else "unchanged"
                ),
                None,
                None,
                "explicit_synthetic_human_allocation",
            )

        return self._transaction(commit)

    @staticmethod
    def _authority(operation):
        return digest(
            {
                k: v
                for k, v in operation.items()
                if k not in ("status", "closed_frontier", "closed_observations")
            }
        )

    @staticmethod
    def _operation(source, credential):
        if type(credential) is not Credential or type(credential.token) is not str:
            raise ValueError("owned allocation credential required")
        operation = next(
            (o for o in source["records"]["operations"] if o["id"] == credential.id),
            None,
        )
        if (
            operation is None
            or operation["status"] != "active"
            or not hmac.compare_digest(
                operation["token_sha256"], sha256(credential.token.encode())
            )
            or credential.authority_sha256 != ManagedSource._authority(operation)
        ):
            raise ValueError("own active allocation operation required")
        return operation

    def close_operation(self, credential):
        """Control-only revocation bypasses cancellation and damaged business replay."""
        if type(credential) is not Credential or type(credential.token) is not str:
            raise ValueError("owned allocation credential required")

        def commit():
            state.schema(self.database)
            _, metadata = state.rows(self.database, "metadata")
            if [r[0] for r in metadata] != [1]:
                raise ValueError("original allocation metadata required")
            state.header(metadata[0][1], self.identity, self.instance_id)
            if digest(metadata[0][1]) != self.original_header_sha256:
                raise ValueError("pinned original allocation control header differs")
            _, operations = state.rows(self.database, "operations")
            operation = next(
                (o for identity, o in operations if identity == credential.id), None
            )
            if operation is None:
                raise ValueError("unknown allocation operation")
            state.check_operation(operation)
            if not hmac.compare_digest(
                operation["token_sha256"], sha256(credential.token.encode())
            ):
                raise ValueError("own allocation scope token required")
            if operation["status"] == "closed":
                return {"closed": True, "already_closed": True}
            operation["status"] = "closed"
            operation["closed_frontier"] = self.database.execute(
                "SELECT count(*) FROM decisions"
            ).fetchone()[0]
            operation["closed_observations"] = self.database.execute(
                "SELECT count(*) FROM observations"
            ).fetchone()[0]
            self.database.execute(
                "UPDATE operations SET document=? WHERE id=?",
                (canonical(operation), credential.id),
            )
            return {"closed": True, "already_closed": False}

        return self._transaction(commit, guard=False)

    def close(self):
        if self.database is not None and not self.database_close_attempted:
            self.database_close_attempted = True
            try:
                self.database.close()
                self.database_closed = True
            except BaseException as error:
                self.close_errors.append({"resource": "database", "error": str(error)})
        if self.descriptor is not None and not self.descriptor_close_attempted:
            self.descriptor_close_attempted = True
            try:
                os.close(self.descriptor)
                self.descriptor_closed = True
            except BaseException as error:
                self.close_errors.append(
                    {"resource": "descriptor", "error": str(error)}
                )
        return {
            "database_close_attempted": self.database_close_attempted,
            "database_closed": self.database_closed,
            "descriptor_close_attempted": self.descriptor_close_attempted,
            "descriptor_closed": self.descriptor_closed,
            "errors": deepcopy(self.close_errors),
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
