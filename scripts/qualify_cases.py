"""All installed private cases through actual ADK/Gemini SDK and preserving reset."""

import argparse
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from resource_team.domain import public, resource, selected
from resource_team.managed import ManagedSource
from resource_team.owned_runtime import run_owned_team
from resource_team.populations import definition
from resource_team.strict import sha256

spec = importlib.util.spec_from_file_location(
    "resource_owned_http", Path(__file__).with_name("owned_http.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def cli(*args):
    out = subprocess.run(
        [sys.executable, "-I", "-m", "resource_team.cases", *map(str, args)],
        cwd=tempfile.gettempdir(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, (out.stdout[-5000:], out.stderr[-5000:])
    return json.loads(out.stdout)


def files(root):
    return {
        str(p.relative_to(root)): (p.stat().st_mode & 0o777, sha256(p.read_bytes()))
        for p in root.rglob("*")
        if p.is_file()
    }


def qualify(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    listing = cli("list")
    assert len(listing["cases"]) == 12
    rows = []
    for item in listing["cases"]:
        case = definition(item["id"])
        old = root / case["id"]
        info = cli("init", "--case", case["id"], "--root", old)
        assert cli("inspect", "--root", old)["source_sha256"] == info["source_sha256"]
        changed = False

        def before(role, step, captured):
            nonlocal changed
            event = case["during"]
            if (
                event is None
                or changed
                or event["role"] != role
                or event["step"] != step
            ):
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
                        "text": "The ambiguous task permits inspection only; no new reservation is asserted."
                    }
                ]

        statuses = []
        with module.provider(mutate, before) as (base, requests):
            for caller in case["runs"]:
                opts = {
                    "database": info["database"],
                    **caller,
                    "api_key": "owned-resource-key",
                    "model": "gemini-owned-native",
                    "base_url": base,
                    "output_root": old / "receipts",
                }
                try:
                    result = asyncio.run(run_owned_team(**opts))
                    assert not case["reference"]["technical_failure"]
                    ev = json.loads(Path(result["receipt_path"]).read_bytes())
                    statuses.append("completed")
                except Exception as error:
                    assert case["reference"]["technical_failure"], repr(error)
                    ev = error.evidence
                    statuses.append("failed")
                    assert json.loads(
                        Path(error.receipt_path).read_bytes()
                    ) == json.loads(json.dumps(ev))
                ref = case["reference"]
                business = ev["observed_business"]
                assert business["allocation_complete"] == ref["allocation_complete"]
                assert business["compensation_complete"] == ref["compensation_complete"]
                assert business["specialist_stages_complete"] == ref["stages_complete"]
                assert len(ev["acknowledged_effects"]) == ref["effect_count"]
                assert ev["cleanup_confirmed"] and ev["recording_status"] == "durable"
            assert len(requests) == case["reference"]["expected_calls"], (
                case["id"],
                len(requests),
            )
            wire = json.dumps(requests)
            for private in (
                case["id"],
                case["reference"]["private_canary"],
                info["instance_id"],
                info["seed_sha256"],
                "definition_sha256",
                "dataset_sha256",
                "fixture_base64",
                "token_sha256",
                "private_reference",
                "prior_human_steps",
            ):
                assert private not in wire, private
        final = cli("inspect", "--root", old)
        assert all(
            o["status"] == "closed" for o in final["source"]["records"]["operations"]
        )
        initial = info["source"]["business"]
        current = final["source"]["business"]
        assert current["audit"][: len(initial["audit"])] == initial["audit"]
        assert all(
            h in current["holds"]
            for h in initial["holds"]
            if h["status"] == "committed"
        )
        if not case["runs"][0]["allow_writes"]:
            assert current == initial
        before_files = files(old)
        reset = cli(
            "reset", "--root", old, "--new-root", root / (case["id"] + "-reset")
        )
        assert (
            files(old) == before_files
            and reset["instance_id"] != info["instance_id"]
            and reset["seed_sha256"] != info["seed_sha256"]
        )
        assert reset["generation"] == 2 and reset["source"]["business"] == initial
        assert all(
            reset["source"]["records"][k] == []
            for k in ("operations", "observations", "decisions")
        )
        rows.append(
            {
                "id": case["id"],
                "statuses": statuses,
                "model_requests": len(requests),
                "prior_metrics": info["metrics"],
                "preserving_reset": True,
            }
        )
    return {
        "count": len(rows),
        "cases": rows,
        "model_requests": sum(c["model_requests"] for c in rows),
        "execution": "actual installed ADK/Gemini/Google SDK over owned HTTP; fictional human history",
        "real_provider_or_external_effect": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    parser.add_argument("--report")
    args = parser.parse_args()
    if args.root:
        result = qualify(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="resource-installed-cases-") as root:
            result = qualify(root)
    raw = json.dumps(result, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(raw)
    print(raw)
