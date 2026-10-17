"""Actual shared SQLite/caller/role/read lineage; explicit human-only backend seam."""

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from resource_team.domain import public, resource, selected
from resource_team.managed import Credential, ManagedSource
from resource_team.managed_state import HUMAN_ROLE
from resource_team.strict import canonical

FIXTURE = Path(__file__).parents[1] / "fixtures/starter.json"


def seed(tmp_path):
    return ManagedSource.seed(tmp_path / "resources.db", FIXTURE)


def scope(source, request_id="REQ-101", writes=True):
    return source.begin(
        source.intent(
            request_id, "Allocate these exact fictional requested resources."
        ),
        "Human-only control probe, no native execution.",
        allow_writes=writes,
        execution="human",
    )


def apply(source, cap, kind, resource_id=None, read=None):
    snapshot = source.snapshot()
    role = snapshot["views"][cap.id]["stage"]
    read = source.observe(cap, role) if read is None else read
    args = {"source": deepcopy(read["value"]["source"])}
    if kind == "hold":
        args["resource_id"] = resource_id
    elif kind == "finish_role":
        args.update(role=role, provenance=deepcopy(HUMAN_ROLE))
    return source.commit(
        cap, {"kind": kind, "args": args}, read["observation"]["id"], read["proof"]
    )


def complete(source, cap, fail=False):
    apply(source, cap, "finish_role")
    request_id = source.snapshot()["records"]["operations"][-1]["intent"]["request_id"]
    for ident in selected(source.snapshot()["business"], request_id)["needs"]:
        apply(source, cap, "hold", ident)
    apply(source, cap, "commit")
    apply(source, cap, "finish_role")
    if fail:
        apply(source, cap, "compensate")
    apply(source, cap, "finish_role")


def test_actual_shared_source_confirms_complete_plan_then_compensation_inspects_without_undo(
    tmp_path,
):
    source = seed(tmp_path)
    with source:
        cap = scope(source)
        complete(source, cap)
        value = source.snapshot()
        view = value["views"][cap.id]
        assert (
            view["stage"] is None
            and view["final_inspection"]["request"]["status"] == "confirmed"
        )
        assert [r["role"] for r in view["finished_roles"]] == [
            "availability",
            "reservation",
            "compensation",
        ]
        assert all(h["status"] == "committed" for h in value["business"]["holds"])
        assert [r["event"] for r in value["business"]["audit"]] == [
            "reserved",
            "reserved",
            "confirmed",
        ]
        source.close_operation(cap)
        with pytest.raises(ValueError):
            source.observe(cap, "availability")
    assert source.database_closed and source.descriptor_closed
    with ManagedSource(source.path) as fresh:
        assert fresh.snapshot()["business"] == value["business"]


def test_downstream_failure_remains_until_actual_pending_only_compensation_and_final_read(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source, "REQ-103")
        complete(source, cap, True)
        value = source.snapshot()
        assert (
            value["views"][cap.id]["final_inspection"]["request"]["status"]
            == "compensated"
        )
        assert value["business"]["holds"] == []
        assert [r["event"] for r in value["business"]["audit"]] == [
            "reserved",
            "reserved",
            "commit_failed",
            "compensated",
        ]
        source.close_operation(cap)


def test_readonly_actual_stages_progress_but_business_rows_and_audit_are_literal_unchanged(
    tmp_path,
):
    with seed(tmp_path) as source:
        before = source.database.execute("SELECT document FROM business").fetchone()[0]
        cap = scope(source, writes=False)
        apply(source, cap, "finish_role")
        assert apply(source, cap, "hold", "ROOM-A")["decision"]["status"] == "denied"
        apply(source, cap, "finish_role")
        apply(source, cap, "finish_role")
        assert (
            source.database.execute("SELECT document FROM business").fetchone()[0]
            == before
        )
        assert (
            source.snapshot()["views"][cap.id]["final_inspection"]["request"]["status"]
            == "pending"
        )
        source.close_operation(cap)


def test_compensation_role_must_make_own_current_final_inspection_and_cannot_leave_pending_holds(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        apply(source, cap, "finish_role")
        apply(source, cap, "hold", "ROOM-A")
        reservation_read = source.observe(cap, "reservation")
        apply(source, cap, "finish_role", read=reservation_read)
        with pytest.raises(ValueError):
            apply(source, cap, "finish_role", read=reservation_read)
        with pytest.raises(ValueError, match="pending holds"):
            apply(source, cap, "finish_role")
        apply(source, cap, "compensate")
        apply(source, cap, "finish_role")
        assert (
            source.snapshot()["views"][cap.id]["final_inspection"]["request"]["status"]
            == "compensated"
        )
        source.close_operation(cap)


def test_distinct_live_requests_really_compete_with_atomic_shared_current_reads(
    tmp_path,
):
    with seed(tmp_path) as source:
        a = scope(source)
        b = scope(source, "REQ-102")
        apply(source, a, "finish_role")
        apply(source, b, "finish_role")
        stale_b = source.observe(b, "reservation")
        assert apply(source, a, "hold", "ROOM-A")["result"]["status"] == "held"
        with pytest.raises(ValueError, match="current unused"):
            apply(source, b, "hold", "ROOM-A", read=stale_b)
        assert (
            apply(source, b, "hold", "ROOM-A")["result"]["status"]
            == "resource_unavailable"
        )
        assert apply(source, b, "hold", "PROJECTOR-A")["result"]["status"] == "held"
        apply(source, b, "finish_role")
        apply(source, b, "compensate")
        apply(source, b, "finish_role")
        assert source.snapshot()["business"]["holds"] == [
            {"resource_id": "ROOM-A", "request_id": "REQ-101", "status": "pending"}
        ]
        source.close_operation(a)
        source.close_operation(b)


def test_separate_real_request_plan_revision_invalidates_old_scope_without_refresh(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        apply(source, cap, "finish_role")
        before = source.snapshot()
        request = deepcopy(selected(before["business"], "REQ-101"))
        source.human(
            {
                "kind": "update_request",
                "args": {"source": request, "needs": ["ROOM-A"], "fail_commit": False},
            }
        )
        with pytest.raises(ValueError, match="request_changed"):
            source.observe(cap, "reservation")
        source.close_operation(cap)
        assert source.snapshot()["business"]["holds"] == []


def test_real_resource_closure_and_new_current_read_refuses_hold_after_earlier_role(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        apply(source, cap, "finish_role")
        read = source.observe(cap, "reservation")
        source.human(
            {
                "kind": "update_resource",
                "args": {
                    "source": deepcopy(
                        resource(source.snapshot()["business"], "ROOM-A")
                    ),
                    "available": False,
                },
            }
        )
        with pytest.raises(ValueError):
            apply(source, cap, "hold", "ROOM-A", read=read)
        assert (
            apply(source, cap, "hold", "ROOM-A")["result"]["status"]
            == "resource_unavailable"
        )
        source.close_operation(cap)


@pytest.mark.parametrize(
    "fault", ["guessed", "borrowed", "used", "wrong_role", "wrong_request", "closed"]
)
def test_owned_role_read_cannot_be_guessed_borrowed_reused_or_retargeted(
    tmp_path, fault
):
    with seed(tmp_path) as source:
        cap = scope(source)
        other = scope(source, "REQ-102")
        read = source.observe(cap, "availability")
        action = {
            "kind": "finish_role",
            "args": {
                "role": "availability",
                "source": deepcopy(read["value"]["source"]),
                "provenance": deepcopy(HUMAN_ROLE),
            },
        }
        credential = cap
        proof = read["proof"]
        ident = read["observation"]["id"]
        if fault == "guessed":
            proof = "0" * 64
        elif fault == "borrowed":
            credential = other
        elif fault == "used":
            apply(source, cap, "finish_role", read=read)
        elif fault == "wrong_role":
            action["args"]["role"] = "reservation"
        elif fault == "wrong_request":
            action["args"]["source"]["request"]["id"] = "REQ-102"
        else:
            source.close_operation(cap)
        with pytest.raises(ValueError):
            source.commit(credential, action, ident, proof)
        source.close_operation(cap)
        source.close_operation(other)


def test_native_mode_requires_original_model_plan_and_human_mode_cannot_claim_native_provenance(
    tmp_path,
):
    with seed(tmp_path) as source:
        with pytest.raises(ValueError):
            source.begin(
                source.intent("REQ-101", "Allocate exact resources."),
                "Native.",
                allow_writes=True,
            )
        cap = scope(source)
        read = source.observe(cap, "availability")
        forged = {
            "kind": "owned_native_role",
            "provider": "gemini",
            "model": "gemini-invented",
            "request_sha256": "0" * 64,
            "response_sha256": "1" * 64,
        }
        with pytest.raises(ValueError):
            source.commit(
                cap,
                {
                    "kind": "finish_role",
                    "args": {
                        "role": "availability",
                        "source": read["value"]["source"],
                        "provenance": forged,
                    },
                },
                read["observation"]["id"],
                read["proof"],
            )
        source.close_operation(cap)


def test_live_caller_flag_cannot_be_promoted_by_self_consistent_sql_scope_edit(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source, writes=False)
        value = json.loads(
            source.database.execute("SELECT document FROM operations").fetchone()[0]
        )
        value["allow_writes"] = True
        source.database.execute("UPDATE operations SET document=?", (canonical(value),))
        with pytest.raises(ValueError, match="own active"):
            source.observe(cap, "availability")
        source.close_operation(cap)


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "header",
        "business",
        "duplicate_json",
        "observation",
        "decision",
        "closure",
    ],
)
def test_whole_sql_header_original_bytes_history_and_closure_corruption_refuse_without_repair(
    tmp_path, fault
):
    with seed(tmp_path) as source:
        cap = scope(source)
        apply(source, cap, "finish_role")
        if fault == "schema":
            source.database.execute("CREATE TABLE unexpected (value TEXT)")
        elif fault == "header":
            source.database.execute("PRAGMA user_version=2")
        elif fault == "business":
            source.database.execute("UPDATE business SET document='{}'")
        elif fault == "duplicate_json":
            raw = source.database.execute("SELECT document FROM business").fetchone()[0]
            source.database.execute(
                "UPDATE business SET document=?",
                (raw.replace('"holds":', '"holds":[],"holds":', 1),),
            )
        elif fault == "observation":
            value = json.loads(
                source.database.execute("SELECT document FROM observations").fetchone()[
                    0
                ]
            )
            value["value"]["source"]["request"]["revision"] = True
            source.database.execute(
                "UPDATE observations SET document=?", (canonical(value),)
            )
        elif fault == "decision":
            value = json.loads(
                source.database.execute("SELECT document FROM decisions").fetchone()[0]
            )
            value["result"]["status"] = "confirmed"
            source.database.execute(
                "UPDATE decisions SET document=?", (canonical(value),)
            )
        else:
            source.close_operation(cap)
            value = json.loads(
                source.database.execute("SELECT document FROM operations").fetchone()[0]
            )
            value["closed_observations"] = 0
            source.database.execute(
                "UPDATE operations SET document=?", (canonical(value),)
            )
        raw = source.path.read_bytes()
        with pytest.raises(ValueError):
            source.snapshot()
        assert source.path.read_bytes() == raw


def test_precommit_cancellation_rolls_back_actual_sql_and_control_revocation_ignores_damaged_business(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        read = source.observe(cap, "availability")
        before = source.snapshot()

        def cancel():
            raise RuntimeError("precommit cancellation")

        source.commit_guard = cancel
        with pytest.raises(RuntimeError):
            apply(source, cap, "finish_role", read=read)
        source.commit_guard = None
        assert source.snapshot()["sha256"] == before["sha256"]
        source.database.execute("UPDATE business SET document='{}'")
        assert source.close_operation(cap)["closed"]


def test_real_sql_writer_lock_and_missing_source_do_not_create_or_repair_database(
    tmp_path,
):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        ManagedSource(missing)
    assert not missing.exists()
    with seed(tmp_path) as source:
        cap = scope(source)
        blocker = sqlite3.connect(source.path, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(sqlite3.OperationalError):
                source.observe(cap, "availability")
            with pytest.raises(sqlite3.OperationalError):
                source.close_operation(cap)
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        assert source.close_operation(cap)["closed"]


def test_actual_sqlite_close_failed_ack_is_attempted_once_and_retained_fd_still_closes(
    tmp_path,
):
    source = seed(tmp_path)
    actual = source.database

    class ClosedButFailed:
        calls = 0

        def close(self):
            self.calls += 1
            actual.close()
            raise RuntimeError("actual close acknowledged as failure")

    wrapper = ClosedButFailed()
    source.database = wrapper
    first = source.close()
    second = source.close()
    assert (
        first == second
        and wrapper.calls == 1
        and not first["database_closed"]
        and first["descriptor_closed"]
    )
    with pytest.raises(sqlite3.ProgrammingError):
        actual.execute("SELECT 1")


def test_private_installed_model_free_cli_closes_source_and_returns_no_live_capability(
    tmp_path,
):
    import subprocess, sys

    def call(*args, expected=0):
        out = subprocess.run(
            [sys.executable, "-m", "resource_team.managed_cli", *map(str, args)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert out.returncode == expected, out.stderr + out.stdout
        return json.loads(out.stdout)

    path = tmp_path / "private.db"
    info = call("init", "--db", path, "--fixture", FIXTURE)
    assert info["business"]["requests"][0]["id"] == "REQ-101"
    intent = call(
        "intent",
        "--db",
        path,
        "--request-id",
        "REQ-101",
        "--task",
        "Inspect these exact requested resources.",
    )
    assert intent["source"]["request"]["needs"] == ["ROOM-A", "PROJECTOR-A"]
    row = info["business"]["resources"][0]
    action = {"kind": "update_resource", "args": {"source": row, "available": False}}
    changed = call("human", "--db", path, "--action-json", canonical(action))
    assert changed["result"]["status"] == "updated"
    snapshot = call("inspect", "--db", path)
    assert (
        snapshot["records"]["operations"] == snapshot["records"]["observations"] == []
    )
    assert snapshot["business"]["resources"][0]["available"] is False
    assert "credential" not in json.dumps(intent) and "proof" not in json.dumps(intent)
    call("init", "--db", path, "--fixture", FIXTURE, expected=2)
