"""Twelve private pinned allocation cases and actual SDK/reset evidence."""

import asyncio
import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from resource_team.cases import initialize, inspect_case, reset_case
from resource_team.domain import public, resource, selected
from resource_team.managed import ManagedSource
from resource_team.owned_runtime import run_owned_team
from resource_team.populations import dataset, definition, sealed
from test_native import provider, evidence
from resource_team.strict import canonical, parse_json, sha256


@pytest.mark.parametrize("case_id", [c["id"] for c in dataset()["cases"]])
def test_twelve_actual_native_case_results_private_controls_and_preserving_separate_reset(
    tmp_path, case_id
):
    case = definition(case_id)
    info = initialize(tmp_path / "old", case_id)
    initial = deepcopy(info["source"]["business"])
    changed = False

    def before(role, step, captured):
        nonlocal changed
        event = case["during"]
        if event is None or changed or event["role"] != role or event["step"] != step:
            return
        with ManagedSource(info["database"]) as human:
            state = human.snapshot()["business"]
            if event["kind"] == "contender":
                action = {
                    "kind": "hold",
                    "args": {
                        "source": public(state, event["request_id"]),
                        "resource_id": event["resource_id"],
                    },
                }
            elif event["kind"] == "plan_change":
                action = {
                    "kind": "update_request",
                    "args": {
                        "source": deepcopy(selected(state, event["request_id"])),
                        "needs": ["ROOM-A"],
                        "fail_commit": False,
                    },
                }
            else:
                action = {
                    "kind": "update_resource",
                    "args": {
                        "source": deepcopy(resource(state, event["resource_id"])),
                        "available": False,
                    },
                }
            human.human(action)
        changed = True

    def mutate(role, step, value, captured):
        fault = case["native_fault"]
        if fault is None or fault["role"] != role or fault["step"] != step:
            return
        if fault["kind"] == "null_finish":
            value["candidates"][0]["finishReason"] = None
        else:
            value["candidates"][0]["content"]["parts"] = [
                {
                    "text": "The unclear original task permits inspection only; no new reservation is asserted."
                }
            ]

    with provider(mutate, before) as (base, captured):
        for caller in case["runs"]:
            opts = {
                "database": info["database"],
                **caller,
                "api_key": "owned-resource-key",
                "model": "gemini-owned-native",
                "base_url": base,
                "output_root": tmp_path / "old" / "receipts",
            }
            if case["reference"]["technical_failure"]:
                with pytest.raises(Exception) as exc:
                    asyncio.run(run_owned_team(**opts))
                ev = exc.value.evidence
            else:
                result = asyncio.run(run_owned_team(**opts))
                ev = evidence(result)
            ref = case["reference"]
            business = ev["observed_business"]
            assert business["allocation_complete"] == ref["allocation_complete"]
            assert business["compensation_complete"] == ref["compensation_complete"]
            assert business["specialist_stages_complete"] == ref["stages_complete"]
            assert (
                len(ev["acknowledged_effects"]) == ref["effect_count"]
                and ev["cleanup_confirmed"]
            )
    assert len(captured) == case["reference"]["expected_calls"]
    wire = json.dumps(captured)
    for private in (
        case["id"],
        case["reference"]["private_canary"],
        info["instance_id"],
        info["seed_sha256"],
        "dataset_sha256",
        "private_reference",
        "prior_human_steps",
        "fixture_base64",
    ):
        assert private not in wire
    with ManagedSource(info["database"]) as source:
        final = source.snapshot()
    assert final["business"]["audit"][: len(initial["audit"])] == initial["audit"]
    if case["profile"] == "busy":
        assert (
            len(initial["resources"]) == 68
            and len(initial["requests"]) == 64
            and len(initial["holds"]) == 24
        )
        assert (
            case["reference"]["prior_human_steps"]
            == case["reference"]["prior_audit"]
            == 96
        )
        committed = [h for h in initial["holds"] if h["status"] == "committed"]
        assert all(h in final["business"]["holds"] for h in committed)
    if not case["runs"][0]["allow_writes"]:
        assert final["business"] == initial
    files = {
        str(p.relative_to(tmp_path / "old")): p.read_bytes()
        for p in (tmp_path / "old").rglob("*")
        if p.is_file()
    }
    reset = reset_case(tmp_path / "old", tmp_path / "reset")
    assert reset["instance_id"] != info["instance_id"] and reset["generation"] == 2
    assert reset["source"]["business"] == initial
    assert (
        reset["source"]["records"]["operations"]
        == reset["source"]["records"]["observations"]
        == reset["source"]["records"]["decisions"]
        == []
    )
    assert {
        str(p.relative_to(tmp_path / "old")): p.read_bytes()
        for p in (tmp_path / "old").rglob("*")
        if p.is_file()
    } == files
    assert inspect_case(tmp_path / "old")["source_sha256"] == final["sha256"]


@pytest.mark.parametrize(
    "fault",
    [
        "control_rehash",
        "fixture",
        "seed_strip",
        "seed_rehash",
        "header_strip",
        "copied",
        "symlink",
        "overlap",
        "locked",
    ],
)
def test_private_controls_cannot_grant_imported_or_rehashed_authority_or_destroy_original(
    tmp_path, fault
):
    info = initialize(tmp_path / "old", "starter-confirm")
    root = tmp_path / "old"
    if fault == "control_rehash":
        path = root / "control.json"
        value = parse_json(path.read_bytes())
        value["reference"]["case_id"] = "starter-compensate"
        path.write_text(
            canonical(sealed({k: v for k, v in value.items() if k != "content_sha256"}))
        )
    elif fault == "fixture":
        (root / "fixture.json").write_bytes(b"{}")
    elif fault in ("seed_strip", "seed_rehash", "header_strip"):
        db = sqlite3.connect(info["database"])
        if fault == "seed_strip":
            db.execute("DELETE FROM seed_state")
        elif fault == "seed_rehash":
            value = json.loads(
                db.execute("SELECT document FROM seed_state").fetchone()[0]
            )
            value["original"]["tables"]["operations"] = [
                {
                    "id": "forged",
                    "document_base64": "e30=",
                    "document_sha256": sha256(b"{}"),
                }
            ]
            from resource_team.strict import digest

            value["original_sha256"] = digest(value["original"])
            value = sealed({k: v for k, v in value.items() if k != "content_sha256"})
            db.execute("UPDATE seed_state SET document=?", (canonical(value),))
        else:
            value = json.loads(
                db.execute("SELECT document FROM metadata").fetchone()[0]
            )
            value.update(creation="fixture", population=None)
            db.execute("UPDATE metadata SET document=?", (canonical(value),))
        db.commit()
        db.close()
    elif fault == "copied":
        shutil.copytree(root, tmp_path / "copied")
        root = tmp_path / "copied"
    elif fault == "symlink":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "alias"
    elif fault == "overlap":
        with pytest.raises(ValueError):
            reset_case(root, root / "nested")
        assert not (root / "nested").exists()
        return
    else:
        (root / ".case-control.lock").write_text("other-owner")
    before = {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    with pytest.raises((ValueError, OSError)):
        inspect_case(root)
    assert {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before


def test_package_asset_hardlink_is_allowed_but_mutable_import_requires_unshared_original(
    tmp_path, monkeypatch
):
    import os
    from resource_team import assets

    p = tmp_path / "data"
    p.mkdir()
    for name in assets.MANIFEST:
        os.link(assets.DATA_ROOT / name, p / name)
    monkeypatch.setattr(assets, "DATA_ROOT", p)
    assert assets.read_asset("starter.json")
    from resource_team.strict import read_original

    with pytest.raises(ValueError):
        read_original(p / "starter.json")


def test_installed_default_seed_and_case_cli_outside_checkout(tmp_path):
    import subprocess, sys

    def call(module, *args):
        out = subprocess.run(
            [sys.executable, "-m", module, *map(str, args)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert out.returncode == 0, out.stderr + out.stdout
        return json.loads(out.stdout)

    default = call("resource_team.managed_cli", "init", "--db", tmp_path / "default.db")
    assert (
        len(default["business"]["requests"]) == 3
        and default["source"]["creation"] == "fixture"
    )
    assert len(call("resource_team.cases", "list")["cases"]) == 12
    a = call(
        "resource_team.cases",
        "init",
        "--case",
        "starter-confirm",
        "--root",
        tmp_path / "a",
    )
    b = call(
        "resource_team.cases",
        "reset",
        "--root",
        tmp_path / "a",
        "--new-root",
        tmp_path / "b",
    )
    assert a["instance_id"] != b["instance_id"] and b["generation"] == 2
