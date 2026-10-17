"""Strict literal allocation source, complete human history and pending-only policy."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from resource_team.domain import (
    initial,
    public,
    resource,
    selected,
    transition,
    validate,
)
from resource_team.human import HumanWorkspace
from resource_team.source import apply, parse
from resource_team.strict import MAX_INTEGER, canonical

FIXTURE = Path(__file__).parents[1] / "fixtures/starter.json"


def fresh():
    return parse(FIXTURE.read_bytes())["business"]


def act(state, kind, request_id="REQ-101", resource_id=None):
    args = {"source": public(state, request_id)}
    if kind == "hold":
        args["resource_id"] = resource_id
    return transition(state, {"kind": kind, "args": args})


def held(state, request_id="REQ-101"):
    for ident in selected(state, request_id)["needs"]:
        state, result = act(state, "hold", request_id, ident)
        assert result["status"] == "held"
    return state


def test_complete_original_requested_allocation_confirm_and_no_op_compensation_preserves_committed_holds():
    state = fresh()
    state, result = act(state, "hold", resource_id="ROOM-A")
    state2, result = act(state, "commit")
    assert state2 == state and result["status"] == "missing_required_pending_resources"
    state, result = act(state, "hold", resource_id="PROJECTOR-A")
    state, result = act(state, "commit")
    assert result["status"] == "confirmed" and all(
        h["status"] == "committed" for h in state["holds"]
    )
    final, result = act(state, "compensate")
    assert final == state and result["status"] == "confirmed_allocation_preserved"
    assert [r["event"] for r in state["audit"]] == ["reserved", "reserved", "confirmed"]


def test_downstream_commit_failure_and_partial_compensation_release_only_pending_own_holds():
    state = held(fresh(), "REQ-103")
    state, result = act(state, "commit", "REQ-103")
    assert result["status"] == "needs_compensation"
    state = held(state)
    state, _ = act(state, "commit")
    confirmed = deepcopy([h for h in state["holds"] if h["request_id"] == "REQ-101"])
    state, result = act(state, "compensate", "REQ-103")
    assert result["status"] == "compensated" and set(result["released"]) == {
        "ROOM-B",
        "PROJECTOR-B",
    }
    assert (
        state["holds"] == confirmed
        and selected(state, "REQ-103")["status"] == "compensated"
    )
    assert selected(state, "REQ-101")["status"] == "confirmed"


def test_competing_request_cannot_share_resource_or_release_other_pending_holds():
    state, _ = act(fresh(), "hold", resource_id="ROOM-A")
    same, result = act(state, "hold", "REQ-102", "ROOM-A")
    assert same == state and result["status"] == "resource_unavailable"
    state, _ = act(state, "hold", "REQ-102", "PROJECTOR-A")
    state, result = act(state, "compensate", "REQ-102")
    assert result["released"] == ["PROJECTOR-A"] and state["holds"] == [
        {"resource_id": "ROOM-A", "request_id": "REQ-101", "status": "pending"}
    ]


@pytest.mark.parametrize(
    "field", ["request_revision", "resource_revision", "holds", "needs", "fail_commit"]
)
def test_full_original_read_refuses_stale_or_coerced_values_including_boolean_revision(
    field,
):
    state = fresh()
    source = public(state, "REQ-101")
    if field == "request_revision":
        source["request"]["revision"] = True
    elif field == "resource_revision":
        source["resources"][0]["revision"] = 1.0
    elif field == "holds":
        source["holds"] = [
            {"resource_id": "ROOM-A", "request_id": "REQ-102", "status": "pending"}
        ]
    elif field == "needs":
        source["request"]["needs"] = ["ROOM-A"]
    else:
        source["request"]["fail_commit"] = 0
    after, result = transition(
        state, {"kind": "hold", "args": {"source": source, "resource_id": "ROOM-A"}}
    )
    assert after == state and result["status"] == "source_changed"


@pytest.mark.parametrize(
    "fault",
    [
        "bool_revision",
        "missing",
        "unknown",
        "duplicate_need",
        "empty_need",
        "duplicate_resource",
        "bad_flag",
        "past_status",
        "extra",
        "native",
        "past_hold",
    ],
)
def test_complete_initial_source_types_relationships_and_history_cannot_be_fabricated(
    fault,
):
    state = fresh()
    base = {"baselineVersion": 1, **state}
    if fault == "bool_revision":
        base["resources"][0]["revision"] = True
    elif fault == "missing":
        base["resources"].pop(0)
    elif fault == "unknown":
        base["requests"][0]["needs"] = ["MISSING"]
    elif fault == "duplicate_need":
        base["requests"][0]["needs"] *= 2
    elif fault == "empty_need":
        base["requests"][0]["needs"] = []
    elif fault == "duplicate_resource":
        base["resources"].append(deepcopy(base["resources"][0]))
    elif fault == "bad_flag":
        base["requests"][0]["fail_commit"] = "0"
    elif fault == "past_status":
        base["requests"][0]["status"] = "confirmed"
    elif fault == "extra":
        base["resources"][0]["extra"] = "invented"
    elif fault == "native":
        base["native_model"] = "gemini-forged"
    else:
        base["holds"] = [
            {"resource_id": "ROOM-A", "request_id": "REQ-101", "status": "pending"}
        ]
    with pytest.raises(ValueError):
        parse(canonical(base).encode())


@pytest.mark.parametrize(
    "fault",
    ["duplicate", "utf8", "nan", "negative_zero", "nul", "surrogate", "float_revision"],
)
def test_whole_original_json_before_coercion(fault):
    raw = FIXTURE.read_bytes()
    if fault == "duplicate":
        raw = raw.replace(b'"resources":', b'"resources":[],"resources":', 1)
    elif fault == "utf8":
        raw = b"\xff" + raw
    elif fault == "nan":
        raw = raw.replace(b"false", b"NaN", 1)
    elif fault == "negative_zero":
        raw = raw.replace(b"false", b"-0", 1)
    elif fault == "nul":
        raw = raw.replace(b"room", b"ro\\u0000om", 1)
    elif fault == "surrogate":
        raw = raw.replace(b"room", b"ro\\ud800om", 1)
    else:
        raw = (
            canonical({"baselineVersion": 1, **fresh()})
            .encode()
            .replace(b'"revision":1', b'"revision":1.0', 1)
        )
    with pytest.raises((ValueError, UnicodeError)):
        parse(raw)


def test_safe_revision_exhaustion_rolls_back_whole_transition():
    state = fresh()
    resource(state, "ROOM-A")["revision"] = MAX_INTEGER
    original = deepcopy(state)
    with pytest.raises(ValueError, match="revision_exhausted"):
        act(state, "hold", resource_id="ROOM-A")
    assert state == original


def test_real_human_workspace_full_history_no_op_bytes_and_explicit_close(tmp_path):
    path = tmp_path / "source.json"
    path.write_bytes(FIXTURE.read_bytes())
    workspace = HumanWorkspace(path)
    with workspace:
        before = workspace.inspect()
        first = {
            "kind": "hold",
            "args": {
                "source": public(before["business"], "REQ-101"),
                "resource_id": "ROOM-A",
            },
        }
        workspace.apply(first)
        after = workspace.inspect()
        assert len(after["history"]) == 1
        raw = path.read_bytes()
        workspace.apply(
            {
                "kind": "hold",
                "args": {
                    "source": public(after["business"], "REQ-101"),
                    "resource_id": "ROOM-A",
                },
            }
        )
        assert path.read_bytes() == raw
        assert after["origin"]["provenance"]["prior_model"] is None
    assert workspace.closed


@pytest.mark.parametrize("fault", ["history", "current", "origin", "result", "extra"])
def test_entire_original_human_history_is_replayed_before_current_state(fault):
    before = FIXTURE.read_bytes()
    state = parse(before)["business"]
    value, _ = apply(
        before,
        {
            "kind": "hold",
            "args": {"source": public(state, "REQ-101"), "resource_id": "ROOM-A"},
        },
    )
    if fault == "history":
        value["history"][0]["before_sha256"] = "0" * 64
    elif fault == "current":
        value["business"]["resources"][0]["revision"] += 1
    elif fault == "origin":
        value["origin"]["provenance"]["prior_model"] = "invented"
    elif fault == "result":
        value["history"][0]["result"]["status"] = "confirmed"
    else:
        value["business"]["holds"][0]["credential"] = "forged"
    with pytest.raises(ValueError):
        parse(canonical(value).encode())


def test_real_resource_closure_after_pending_holds_refuses_fresh_commit_and_compensation_still_releases():
    state = held(fresh())
    state, result = transition(
        state,
        {
            "kind": "update_resource",
            "args": {"source": deepcopy(resource(state, "ROOM-A")), "available": False},
        },
    )
    assert result["status"] == "updated"
    same, result = act(state, "commit")
    assert same == state and result["status"] == "resource_unavailable"
    state, result = act(state, "compensate")
    assert result["status"] == "compensated" and state["holds"] == []


def test_explicit_plan_update_only_before_holds_then_old_source_cannot_reserve_original_plan():
    state = fresh()
    old = public(state, "REQ-101")
    state, result = transition(
        state,
        {
            "kind": "update_request",
            "args": {
                "source": deepcopy(selected(state, "REQ-101")),
                "needs": ["ROOM-A"],
                "fail_commit": False,
            },
        },
    )
    assert result["status"] == "updated"
    same, result = transition(
        state, {"kind": "hold", "args": {"source": old, "resource_id": "ROOM-A"}}
    )
    assert same == state and result["status"] == "source_changed"
    state, _ = act(state, "hold", resource_id="ROOM-A")
    same, result = transition(
        state,
        {
            "kind": "update_request",
            "args": {
                "source": deepcopy(selected(state, "REQ-101")),
                "needs": ["PROJECTOR-A"],
                "fail_commit": False,
            },
        },
    )
    assert same == state and result["status"] == "request_has_allocation_or_holds"


@pytest.mark.parametrize(
    "fault", ["symlink", "hardlink", "replace", "missing", "root_move"]
)
def test_actual_human_source_owner_rejects_alias_and_retained_path_identity_changes(
    tmp_path, fault
):
    import os

    path = tmp_path / "original.json"
    path.write_bytes(FIXTURE.read_bytes())
    if fault == "symlink":
        alias = tmp_path / "alias.json"
        alias.symlink_to(path)
        with pytest.raises((ValueError, OSError)):
            HumanWorkspace(alias)
        return
    if fault == "hardlink":
        os.link(path, tmp_path / "hard.json")
        with pytest.raises(ValueError):
            HumanWorkspace(path)
        return
    workspace = HumanWorkspace(path)
    try:
        if fault == "replace":
            new = tmp_path / "new.json"
            new.write_bytes(path.read_bytes())
            os.replace(new, path)
        elif fault == "missing":
            path.unlink()
        else:
            moved = tmp_path.with_name(tmp_path.name + "-moved")
            tmp_path.rename(moved)
            tmp_path.mkdir()
            path.write_bytes(FIXTURE.read_bytes())
        with pytest.raises((ValueError, OSError)):
            workspace.inspect()
    finally:
        workspace.close()
    assert workspace.closed
