"""Exact owned SQLite resource source and replay of live caller/role/read decisions."""

from __future__ import annotations
import base64
import re
from copy import deepcopy
from uuid import UUID
from urllib.parse import urlsplit
from .domain import selected, public, transition
from .source import PROVENANCE, parse
from .strict import canonical, digest, integer, keys, parse_json, sha256, text

ROLES = ("availability", "reservation", "compensation")
HUMAN_ROLE = {
    "kind": "explicit_synthetic_human_role",
    "model": None,
    "authentication": None,
}
APPLICATION_ID = 0x46445241
SOURCE_VERSION = 1
MAX_DATABASE_BYTES = 64 * 1024 * 1024
SCHEMA = {
    "metadata": "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
    "business": "CREATE TABLE business (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
    "operations": "CREATE TABLE operations (id TEXT PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "observations": "CREATE TABLE observations (id INTEGER PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "decisions": "CREATE TABLE decisions (id INTEGER PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "seed_state": "CREATE TABLE seed_state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
}
LIMITS = {
    "metadata": 1,
    "business": 1,
    "operations": 128,
    "observations": 1024,
    "decisions": 512,
    "seed_state": 1,
}


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value or UUID(value).version != 4:
        raise ValueError("original UUID4 allocation identity required")
    return value


def hex_digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("original SHA256 required")
    return value


def schema(db):
    if (
        db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or db.execute("PRAGMA user_version").fetchone()[0] != SOURCE_VERSION
        or db.execute("PRAGMA page_size").fetchone()[0] != 4096
        or db.execute("PRAGMA encoding").fetchone()[0] != "UTF-8"
        or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
    ):
        raise ValueError("original allocation database header differs")
    if db.execute("PRAGMA page_count").fetchone()[0] * 4096 > MAX_DATABASE_BYTES:
        raise ValueError("allocation database exceeds64MiB")
    actual = [
        tuple(row)
        for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    ]
    expected = sorted(
        [("table", name, name, sql) for name, sql in SCHEMA.items()]
        + [("index", "sqlite_autoindex_operations_1", "operations", None)]
    )
    if actual != expected or [
        tuple(r) for r in db.execute("PRAGMA quick_check(1)")
    ] != [("ok",)]:
        raise ValueError("exact original allocation SQL schema/integrity required")
    return actual


def rows(db, name):
    count = db.execute("SELECT count(*) FROM " + name).fetchone()[0]
    if count > LIMITS[name]:
        raise ValueError("bounded complete allocation SQL rows required")
    original = []
    values = []
    for identity, raw in db.execute(
        "SELECT id,CAST(document AS BLOB) FROM " + name + " ORDER BY id"
    ):
        if type(raw) is not bytes:
            raise ValueError("original allocation SQL text bytes required")
        value = parse_json(raw)
        if raw.decode("utf-8", errors="strict").encode("utf-8", errors="strict") != raw:
            raise ValueError("original allocation SQL UTF8 differs")
        original.append(
            {
                "id": identity,
                "document_base64": base64.b64encode(raw).decode(),
                "document_sha256": sha256(raw),
            }
        )
        values.append((identity, value))
    return original, values


def header(value, physical, instance_id=None):
    keys(
        value,
        {
            "schemaVersion",
            "kind",
            "instance_id",
            "physical_identity",
            "fixture_base64",
            "fixture_sha256",
            "provenance",
            "creation",
            "population",
        },
    )
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != SOURCE_VERSION
        or value["kind"] != "managed_synthetic_resource_source"
        or value["physical_identity"] != physical
        or value["provenance"] != PROVENANCE
        or value["creation"] not in ("fixture", "population")
        or (value["creation"] == "fixture" and value["population"] is not None)
    ):
        raise ValueError(
            "original allocation source header identity/provenance differs"
        )
    uuid(value["instance_id"])
    if instance_id is not None and value["instance_id"] != instance_id:
        raise ValueError("original allocation source UUID differs")
    if type(value["fixture_base64"]) is not str:
        raise ValueError("literal allocation initial fixture required")
    raw = base64.b64decode(value["fixture_base64"], validate=True)
    if (
        base64.b64encode(raw).decode() != value["fixture_base64"]
        or sha256(raw) != value["fixture_sha256"]
    ):
        raise ValueError("original allocation fixture hash/encoding differs")
    if value["creation"] == "population":
        from .populations import baseline

        return baseline(value["population"], raw)
    return parse(raw)["business"]


def context(source, frontier, business):
    value = {
        "source_sha256": digest(source),
        "frontier": frontier,
        "business_sha256": digest(business),
    }
    return value, digest(value)


def caller_intent(business, request_id, task, context_sha256):
    text(task, 1, 8192)
    if not task.strip():
        raise ValueError("nonblank original allocation task required")
    return {
        "request_id": request_id,
        "source": public(business, request_id),
        "task": task,
        "context_sha256": hex_digest(context_sha256),
    }


def model_target(value):
    keys(value, {"provider", "model", "base_url", "credential_sha256"})
    if value["provider"] != "gemini":
        raise ValueError("original Gemini model target required")
    text(value["model"], 1, 200)
    hex_digest(value["credential_sha256"])
    text(value["base_url"], 1, 500)
    p = urlsplit(value["base_url"])
    if (
        p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in ("", "/")
        or p.scheme not in ("http", "https")
        or p.hostname is None
        or p.scheme == "http"
        and (p.hostname != "127.0.0.1" or p.port is None)
    ):
        raise ValueError("HTTPS or owned loopback Gemini origin required")


def check_operation(value):
    keys(
        value,
        {
            "id",
            "token_sha256",
            "task_sha256",
            "intent",
            "execution",
            "model",
            "initial_frontier",
            "allow_writes",
            "status",
            "closed_frontier",
            "closed_observations",
        },
    )
    uuid(value["id"])
    hex_digest(value["token_sha256"])
    hex_digest(value["task_sha256"])
    integer(value["initial_frontier"])
    intent = keys(value["intent"], {"request_id", "source", "task", "context_sha256"})
    text(intent["task"], 1, 8192)
    hex_digest(intent["context_sha256"])
    keys(intent["source"], {"request", "resources", "holds"})
    if (
        intent["source"]["request"]["id"] != intent["request_id"]
        or type(value["allow_writes"]) is not bool
        or value["execution"] not in ("human", "native")
    ):
        raise ValueError(
            "exact original allocation caller/execution/write mode required"
        )
    if value["execution"] == "native":
        model_target(value["model"])
    elif value["model"] is not None:
        raise ValueError("human control cannot claim native target")
    if value["status"] == "active":
        if (
            value["closed_frontier"] is not None
            or value["closed_observations"] is not None
        ):
            raise ValueError("active allocation has closure cut")
    elif value["status"] == "closed":
        integer(value["closed_frontier"])
        integer(value["closed_observations"])
        if value["closed_frontier"] < value["initial_frontier"]:
            raise ValueError("allocation closure precedes caller")
    else:
        raise ValueError("unknown allocation operation status")


def initial_view(operation):
    return {
        "stage": "availability",
        "expected_request": deepcopy(operation["intent"]["source"]["request"]),
        "finished_roles": [],
        "final_inspection": None,
    }


def current_read(business, operation, view):
    value = public(business, operation["intent"]["request_id"])
    if digest(value["request"]) != digest(view["expected_request"]):
        raise ValueError(
            "request_changed; original allocation plan cannot be refreshed under this scope"
        )
    return {"role": view["stage"], "source": value}


def role_provenance(value, operation):
    if operation["execution"] == "human":
        if value != HUMAN_ROLE:
            raise ValueError("explicit human role provenance required")
    else:
        keys(value, {"kind", "provider", "model", "request_sha256", "response_sha256"})
        if (
            value["kind"] != "owned_native_role"
            or value["provider"] != "gemini"
            or value["model"] != operation["model"]["model"]
        ):
            raise ValueError("original configured owned native role required")
        hex_digest(value["request_sha256"])
        hex_digest(value["response_sha256"])


def evaluate(business, operation, view, action, observation):
    keys(action, {"kind", "args"})
    read = observation["value"]
    if (
        read != current_read(business, operation, view)
        or read["role"] is None
        or observation["role"] != view["stage"]
    ):
        raise ValueError("own exact current role/source read required")
    after_view = deepcopy(view)
    if action["kind"] == "finish_role":
        args = keys(action["args"], {"role", "source", "provenance"})
        if args["role"] != view["stage"] or digest(args["source"]) != digest(
            read["source"]
        ):
            raise ValueError("own current final role inspection required")
        role_provenance(args["provenance"], operation)
        if (
            args["role"] == "compensation"
            and operation["allow_writes"]
            and read["source"]["request"]["status"] in ("holding", "needs_compensation")
        ):
            raise ValueError(
                "pending holds require compensation before final role completion"
            )
        after_view["finished_roles"].append(
            {
                "role": args["role"],
                "source": deepcopy(read["source"]),
                "provenance": deepcopy(args["provenance"]),
            }
        )
        index = ROLES.index(args["role"]) + 1
        after_view["stage"] = ROLES[index] if index < len(ROLES) else None
        if args["role"] == "compensation":
            after_view["final_inspection"] = deepcopy(read["source"])
        return (
            business,
            {"status": "role_finished", "role": args["role"]},
            "control",
            after_view,
        )
    if action["kind"] not in ("hold", "commit", "compensate") or (
        view["stage"] != "reservation"
        if action["kind"] in ("hold", "commit")
        else view["stage"] != "compensation"
    ):
        raise ValueError(
            "only the actual reservation/compensation role may perform this action"
        )
    if digest(action["args"].get("source")) != digest(read["source"]):
        raise ValueError("actual own fresh full source required")
    after, result = transition(business, action)
    if not operation["allow_writes"]:
        return (
            business,
            {"status": "write_permission_required", "requested_action": action["kind"]},
            "denied",
            after_view,
        )
    after_view["expected_request"] = deepcopy(
        selected(after, operation["intent"]["request_id"])
    )
    return after, result, "applied" if after != business else "unchanged", after_view


def snapshot(db, physical, instance_id=None):
    original_schema = schema(db)
    raw, table = {}, {}
    for name in SCHEMA:
        raw[name], table[name] = rows(db, name)
    if [r[0] for r in table["metadata"]] != [1] or [
        r[0] for r in table["business"]
    ] != [1]:
        raise ValueError("original allocation singleton/fixture seed required")
    source = table["metadata"][0][1]
    current = header(source, physical, instance_id)
    if source["creation"] == "population":
        from .populations import validate_seed

        if [r[0] for r in table["seed_state"]] != [1]:
            raise ValueError("original resource population seed required")
        validate_seed(source, original_schema, raw, table["seed_state"][0][1])
    elif table["seed_state"]:
        raise ValueError("fixture source cannot import population seed")
    operations = {}
    for identity, op in table["operations"]:
        check_operation(op)
        if identity != op["id"]:
            raise ValueError("exact allocation SQL operation ID required")
        operations[identity] = op
    active = [
        o["intent"]["request_id"]
        for o in operations.values()
        if o["status"] == "active"
    ]
    if len(active) != len(set(active)):
        raise ValueError("only one active scope per selected request required")
    observations = {}
    for ordinal, (identity, row) in enumerate(table["observations"], 1):
        keys(
            row,
            {
                "id",
                "operation_id",
                "frontier",
                "context_sha256",
                "role",
                "value",
                "proof_sha256",
            },
        )
        if (
            type(identity) is not int
            or type(row["id"]) is not int
            or identity != ordinal
            or row["id"] != ordinal
            or row["operation_id"] not in operations
        ):
            raise ValueError("complete own allocation observation identity required")
        integer(row["frontier"])
        hex_digest(row["context_sha256"])
        hex_digest(row["proof_sha256"])
        if row["role"] not in ROLES:
            raise ValueError("exact original observed role required")
        observations[identity] = row
    frontiers = [deepcopy(current)]
    view_frontiers = [{ident: initial_view(op) for ident, op in operations.items()}]
    used = set()
    decisions = []
    for ordinal, (identity, row) in enumerate(table["decisions"], 1):
        keys(
            row,
            {
                "id",
                "action",
                "operation_id",
                "read_id",
                "before_context_sha256",
                "after_context_sha256",
                "status",
                "result",
                "provenance",
            },
        )
        if (
            type(identity) is not int
            or type(row["id"]) is not int
            or identity != ordinal
            or row["id"] != ordinal
        ):
            raise ValueError("complete original allocation decision frontier required")
        before = context(source, ordinal - 1, current)[1]
        if row["before_context_sha256"] != before:
            raise ValueError("allocation before context differs")
        views = deepcopy(view_frontiers[-1])
        if row["operation_id"] is None:
            if (
                row["read_id"] is not None
                or row["provenance"] != "explicit_synthetic_human_allocation"
                or row["action"].get("kind")
                not in (
                    "hold",
                    "commit",
                    "compensate",
                    "update_request",
                    "update_resource",
                )
            ):
                raise ValueError("exact separate human allocation boundary required")
            after, result = transition(current, row["action"])
            status = "applied" if after != current else "unchanged"
        else:
            op = operations.get(row["operation_id"])
            read = observations.get(row["read_id"])
            if (
                op is None
                or read is None
                or row["read_id"] in used
                or read["operation_id"] != op["id"]
                or read["frontier"] != ordinal - 1
                or read["context_sha256"] != before
                or op["initial_frontier"] > ordinal - 1
                or row["provenance"] != "managed_caller"
                or op["status"] == "closed"
                and ordinal > op["closed_frontier"]
            ):
                raise ValueError(
                    "genuine own current unused allocation observation required"
                )
            used.add(row["read_id"])
            after, result, status, views[op["id"]] = evaluate(
                current, op, views[op["id"]], row["action"], read
            )
        if (
            digest(result) != digest(row["result"])
            or status != row["status"]
            or row["after_context_sha256"] != context(source, ordinal, after)[1]
        ):
            raise ValueError("whole original allocation decision replay differs")
        current = after
        frontiers.append(deepcopy(current))
        view_frontiers.append(views)
        decisions.append(deepcopy(row))
    for op in operations.values():
        start = op["initial_frontier"]
        if start > len(decisions) or digest(op["intent"]) != digest(
            caller_intent(
                frontiers[start],
                op["intent"]["request_id"],
                op["intent"]["task"],
                context(source, start, frontiers[start])[1],
            )
        ):
            raise ValueError("complete original allocation caller intent differs")
        if op["status"] == "closed" and (
            op["closed_frontier"] > len(decisions)
            or op["closed_observations"] > len(observations)
        ):
            raise ValueError("original allocation closure cut exceeds frontier")
    for read in observations.values():
        op = operations[read["operation_id"]]
        f = read["frontier"]
        if (
            f > len(decisions)
            or f < op["initial_frontier"]
            or op["status"] == "closed"
            and (f > op["closed_frontier"] or read["id"] > op["closed_observations"])
        ):
            raise ValueError("observation outside original live allocation scope")
        if read["context_sha256"] != context(source, f, frontiers[f])[1] or digest(
            read["value"]
        ) != digest(current_read(frontiers[f], op, view_frontiers[f][op["id"]])):
            raise ValueError("complete original allocation observation replay differs")
    if digest(table["business"][0][1]) != digest(current):
        raise ValueError("current allocation differs from whole original replay")
    original = {"schema": original_schema, "tables": raw}
    ctx, ctx_sha = context(source, len(decisions), current)
    return {
        "source": deepcopy(source),
        "business": deepcopy(current),
        "records": {
            "operations": list(operations.values()),
            "observations": list(observations.values()),
            "decisions": decisions,
        },
        "views": view_frontiers[-1],
        "context": ctx,
        "context_sha256": ctx_sha,
        "sha256": digest(original),
        "original": original,
    }
