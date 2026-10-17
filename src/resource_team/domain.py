"""Exact synthetic shared-resource decisions; native caller/role authority is separate."""

from copy import deepcopy

from .strict import digest, identifier, integer, keys, next_revision, text

STATUSES = ("pending", "holding", "needs_compensation", "confirmed", "compensated")


def listed(value, maximum):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("bounded complete resource list required")
    return value


def selected(value, request_id):
    identifier(request_id)
    row = next((r for r in value["requests"] if r["id"] == request_id), None)
    if row is None:
        raise ValueError("unknown allocation request")
    return row


def resource(value, resource_id):
    identifier(resource_id)
    row = next((r for r in value["resources"] if r["id"] == resource_id), None)
    if row is None:
        raise ValueError("unknown allocation resource")
    return row


def public(value, request_id):
    row = selected(value, request_id)
    return {
        "request": deepcopy(row),
        "resources": [deepcopy(resource(value, ident)) for ident in row["needs"]],
        "holds": [
            deepcopy(h) for h in value["holds"] if h["resource_id"] in row["needs"]
        ],
    }


def effect(value):
    return {k: deepcopy(value[k]) for k in ("resources", "requests", "holds")}


def validate(value):
    keys(value, {"resources", "requests", "holds", "audit"})
    resources = {}
    for row in listed(value["resources"], 256):
        keys(row, {"id", "kind", "available", "revision"})
        identifier(row["id"])
        integer(row["revision"], 1)
        if (
            row["id"] in resources
            or row["kind"] not in ("room", "equipment")
            or type(row["available"]) is not bool
        ):
            raise ValueError("exact unique resource/kind/availability required")
        resources[row["id"]] = row
    if not resources:
        raise ValueError("nonempty synthetic resource workspace required")
    requests = {}
    for row in listed(value["requests"], 256):
        keys(row, {"id", "needs", "status", "fail_commit", "revision"})
        identifier(row["id"])
        integer(row["revision"], 1)
        needs = listed(row["needs"], 16)
        if (
            not needs
            or any(type(i) is not str or i not in resources for i in needs)
            or len(set(needs)) != len(needs)
        ):
            raise ValueError(
                "complete nonempty unique existing requested resources required"
            )
        if (
            row["id"] in requests
            or row["status"] not in STATUSES
            or type(row["fail_commit"]) is not bool
        ):
            raise ValueError(
                "exact unique allocation request/status/boolean fail_commit required"
            )
        requests[row["id"]] = row
    if not requests:
        raise ValueError("nonempty allocation requests required")
    held = {}
    for row in listed(value["holds"], 256):
        keys(row, {"resource_id", "request_id", "status"})
        if (
            row["resource_id"] not in resources
            or row["request_id"] not in requests
            or row["resource_id"] in held
            or row["status"] not in ("pending", "committed")
        ):
            raise ValueError("unique existing resource/request hold required")
        request = requests[row["request_id"]]
        if row["resource_id"] not in request["needs"] or (
            row["status"] == "committed"
        ) != (request["status"] == "confirmed"):
            raise ValueError("hold cannot contradict original request needs/status")
        held[row["resource_id"]] = row
    for row in requests.values():
        own = [h for h in held.values() if h["request_id"] == row["id"]]
        if row["status"] == "confirmed":
            if {h["resource_id"] for h in own} != set(row["needs"]) or any(
                h["status"] != "committed" for h in own
            ):
                raise ValueError(
                    "confirmation requires every original needed committed resource"
                )
        elif row["status"] in ("holding", "needs_compensation"):
            if not own or any(h["status"] != "pending" for h in own):
                raise ValueError("pending allocation requires actual pending own holds")
        elif own:
            raise ValueError("pending/compensated request cannot have holds")
    for ordinal, row in enumerate(listed(value["audit"], 4096), 1):
        keys(
            row,
            {
                "id",
                "event",
                "request_id",
                "resource_ids",
                "before_sha256",
                "after_sha256",
                "request_revision",
                "resource_revisions",
            },
        )
        if (
            type(row["id"]) is not int
            or row["id"] != ordinal
            or row["event"]
            not in (
                "reserved",
                "confirmed",
                "commit_failed",
                "compensated",
                "request_changed",
                "resource_changed",
            )
        ):
            raise ValueError("complete ordered resource audit required")
        if row["request_id"] is not None and row["request_id"] not in requests:
            raise ValueError("audit request missing")
        if (
            type(row["resource_ids"]) is not list
            or len(set(row["resource_ids"])) != len(row["resource_ids"])
            or any(i not in resources for i in row["resource_ids"])
        ):
            raise ValueError("audit resource relationship invalid")
        for field in ("before_sha256", "after_sha256"):
            v = row[field]
            if (
                type(v) is not str
                or len(v) != 64
                or any(c not in "0123456789abcdef" for c in v)
            ):
                raise ValueError("exact audit SHA256 required")
        if row["before_sha256"] == row["after_sha256"]:
            raise ValueError("audit cannot claim a no-op effect")
        if row["request_revision"] is not None:
            integer(row["request_revision"], 1)
        keys(row["resource_revisions"], set(row["resource_ids"]))
        for revision in row["resource_revisions"].values():
            integer(revision, 1)
    if value["audit"]:
        if value["audit"][-1]["after_sha256"] != digest(effect(value)):
            raise ValueError("current allocation differs from last recorded effect")
        for earlier, later in zip(value["audit"], value["audit"][1:]):
            if earlier["after_sha256"] != later["before_sha256"]:
                raise ValueError("complete contiguous allocation audit required")
    return value


def initial(value):
    if type(value) is dict and set(value) == {"resources", "requests"}:
        rows = []
        for r in listed(value["resources"], 256):
            keys(r, {"id", "kind"})
            rows.append({**deepcopy(r), "available": True, "revision": 1})
        requests = []
        for r in listed(value["requests"], 256):
            keys(r, {"id", "needs", "fail_commit"})
            requests.append({**deepcopy(r), "status": "pending", "revision": 1})
        result = {"resources": rows, "requests": requests, "holds": [], "audit": []}
    else:
        keys(value, {"baselineVersion", "resources", "requests", "holds", "audit"})
        if type(value["baselineVersion"]) is not int or value["baselineVersion"] != 1:
            raise ValueError("explicit resource baselineVersion1 required")
        result = {
            k: deepcopy(value[k]) for k in ("resources", "requests", "holds", "audit")
        }
        if (
            result["holds"]
            or result["audit"]
            or any(r["status"] != "pending" for r in result["requests"])
        ):
            raise ValueError(
                "baseline cannot fabricate prior allocations/holds/history"
            )
    validate(result)
    return result


def transition(value, action):
    validate(value)
    keys(action, {"kind", "args"})
    kind, args = action["kind"], action["args"]
    out = deepcopy(value)
    changed = []
    request = None
    if kind in ("hold", "commit", "compensate"):
        keys(args, {"source", "resource_id"} if kind == "hold" else {"source"})
        supplied = keys(args["source"], {"request", "resources", "holds"})
        request = selected(out, supplied["request"]["id"])
        if digest(supplied) != digest(public(value, request["id"])):
            return out, {"status": "source_changed"}
        if kind == "hold":
            ident = identifier(args["resource_id"])
            r = resource(out, ident)
            if ident not in request["needs"]:
                return out, {"status": "resource_not_requested"}
            if request["status"] not in ("pending", "holding"):
                return out, {"status": "request_not_accepting_holds"}
            existing = next(
                (h for h in out["holds"] if h["resource_id"] == ident), None
            )
            if existing is not None:
                return out, {
                    "status": (
                        "already_held"
                        if existing["request_id"] == request["id"]
                        else "resource_unavailable"
                    )
                }
            if not r["available"]:
                return out, {"status": "resource_unavailable"}
            r["revision"] = next_revision(r["revision"])
            request["revision"] = next_revision(request["revision"])
            request["status"] = "holding"
            out["holds"].append(
                {"resource_id": ident, "request_id": request["id"], "status": "pending"}
            )
            changed = [ident]
            event = "reserved"
            result = {
                "status": "held",
                "request_id": request["id"],
                "resource_id": ident,
            }
        else:
            own = [h for h in out["holds"] if h["request_id"] == request["id"]]
            if request["status"] == "confirmed":
                return out, {
                    "status": (
                        "already_confirmed"
                        if kind == "commit"
                        else "confirmed_allocation_preserved"
                    )
                }
            if kind == "commit":
                if (
                    request["status"] != "holding"
                    or {h["resource_id"] for h in own} != set(request["needs"])
                    or any(h["status"] != "pending" for h in own)
                ):
                    return out, {"status": "missing_required_pending_resources"}
                if any(
                    not resource(out, ident)["available"] for ident in request["needs"]
                ):
                    return out, {"status": "resource_unavailable"}
                request["revision"] = next_revision(request["revision"])
                if request["fail_commit"]:
                    request["status"] = "needs_compensation"
                    event = "commit_failed"
                    result = {
                        "status": "needs_compensation",
                        "request_id": request["id"],
                    }
                else:
                    changed = list(request["needs"])
                    for ident in changed:
                        resource(out, ident)["revision"] = next_revision(
                            resource(out, ident)["revision"]
                        )
                    for h in own:
                        h["status"] = "committed"
                    request["status"] = "confirmed"
                    event = "confirmed"
                    result = {
                        "status": "confirmed",
                        "request_id": request["id"],
                        "resources": list(request["needs"]),
                    }
            else:
                if (
                    request["status"] not in ("holding", "needs_compensation")
                    or not own
                ):
                    return out, {"status": "nothing_compensatable"}
                if any(h["status"] != "pending" for h in own):
                    raise ValueError("compensation cannot release committed holds")
                changed = [h["resource_id"] for h in own]
                for ident in changed:
                    resource(out, ident)["revision"] = next_revision(
                        resource(out, ident)["revision"]
                    )
                out["holds"] = [
                    h for h in out["holds"] if h["request_id"] != request["id"]
                ]
                request["revision"] = next_revision(request["revision"])
                request["status"] = "compensated"
                event = "compensated"
                result = {
                    "status": "compensated",
                    "request_id": request["id"],
                    "released": changed,
                }
    elif kind == "update_request":
        keys(args, {"source", "needs", "fail_commit"})
        request = selected(out, args["source"]["id"])
        if digest(args["source"]) != digest(request):
            return out, {"status": "source_changed"}
        if request["status"] not in ("pending", "holding") or any(
            h["request_id"] == request["id"] for h in out["holds"]
        ):
            return out, {"status": "request_has_allocation_or_holds"}
        listed(args["needs"], 16)
        if (
            not args["needs"]
            or len(set(args["needs"])) != len(args["needs"])
            or any(
                type(i) is not str or not any(r["id"] == i for r in out["resources"])
                for i in args["needs"]
            )
            or type(args["fail_commit"]) is not bool
        ):
            raise ValueError("exact new request needs/boolean required")
        if (
            request["needs"] == args["needs"]
            and request["fail_commit"] == args["fail_commit"]
        ):
            return out, {"status": "unchanged"}
        request.update(
            needs=deepcopy(args["needs"]),
            fail_commit=args["fail_commit"],
            revision=next_revision(request["revision"]),
        )
        event = "request_changed"
        result = {"status": "updated", "request_id": request["id"]}
    elif kind == "update_resource":
        keys(args, {"source", "available"})
        r = resource(out, args["source"]["id"])
        if digest(r) != digest(args["source"]):
            return out, {"status": "source_changed"}
        if type(args["available"]) is not bool:
            raise ValueError("exact availability boolean required")
        if r["available"] == args["available"]:
            return out, {"status": "unchanged"}
        r.update(available=args["available"], revision=next_revision(r["revision"]))
        changed = [r["id"]]
        event = "resource_changed"
        result = {"status": "updated", "resource_id": r["id"]}
    else:
        raise ValueError("unsupported allocation action")
    out["audit"].append(
        {
            "id": len(out["audit"]) + 1,
            "event": event,
            "request_id": request["id"] if request else None,
            "resource_ids": changed,
            "before_sha256": digest(effect(value)),
            "after_sha256": digest(effect(out)),
            "request_revision": request["revision"] if request else None,
            "resource_revisions": {i: resource(out, i)["revision"] for i in changed},
        }
    )
    validate(out)
    return out, result
