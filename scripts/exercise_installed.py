"""Fresh installed actual CLI/SDK, retained allocations, all signals and preserving reset."""

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import resource_team

BASE = Path(sys.argv[1])
BASE.mkdir()
assert Path(resource_team.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
if "--production-only" in sys.argv:
    assert importlib.util.find_spec("pytest") is None
spec = importlib.util.spec_from_file_location(
    "resource_owned_http", Path(__file__).with_name("owned_http.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BIN = Path(sys.executable).parent
ENV = {
    k: os.environ[k]
    for k in ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "SystemRoot", "WINDIR")
    if k in os.environ
}


def execute(command, expected=0, env=ENV, timeout=120):
    out = subprocess.run(
        list(map(str, command)),
        cwd=BASE,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert out.returncode == expected, (out.stdout[-6000:], out.stderr[-5000:])
    return json.loads(out.stdout)


probe = execute(
    [
        sys.executable,
        "-I",
        Path(__file__).with_name("qualify_cases.py"),
        "--root",
        BASE / "populations",
    ],
    timeout=900,
)
assert probe["count"] == 12 and probe["model_requests"] == 130
launcher = BASE / "resource-team"
launcher.symlink_to(BIN / "resource-team")
proof = []
for mode in ("completed", "unfinished-final", "SIGINT", "SIGTERM", "SIGHUP"):
    case = execute(
        [
            BIN / "resource-team-cases",
            "init",
            "--case",
            "starter-confirm",
            "--root",
            BASE / mode,
        ]
    )
    entered, release = threading.Event(), threading.Event()

    def before(role, step, captured):
        if mode.startswith("SIG") and role == "reservation" and step == 2:
            entered.set()
            release.wait(35)

    def mutate(role, step, value, captured):
        if mode == "unfinished-final" and role == "compensation" and step == 1:
            value["candidates"][0]["finishReason"] = None

    with module.provider(mutate, before) as (base, requests):
        command = [
            launcher,
            "--db",
            case["database"],
            "--request-id",
            "REQ-101",
            "--task",
            "Allocate these exact fictional requested resources and inspect final compensation state.",
            "--allow-writes",
            "--model",
            "gemini-owned-native",
            "--base-url",
            base,
            "--output",
            BASE / mode / "receipts",
        ]
        child = subprocess.Popen(
            list(map(str, command)),
            cwd=BASE,
            env={**ENV, "GOOGLE_API_KEY": "owned-resource-key"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if mode.startswith("SIG"):
                assert entered.wait(20)
                child.send_signal(getattr(signal, mode))
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == (0 if mode == "completed" else 1), (
                stdout[-5000:],
                stderr[-5000:],
            )
            result = json.loads(stdout)
            path = Path(result["receipt_path"])
            ev = json.loads(path.read_bytes())
            expected_effects = 1 if mode.startswith("SIG") else 3
            expected_calls = 5 if mode.startswith("SIG") else 12
            assert (
                ev["cleanup_confirmed"]
                and len(ev["acknowledged_effects"]) == expected_effects
                and len(requests) == expected_calls
            )
            assert ev["observed_business"]["allocation_complete"] == (
                not mode.startswith("SIG")
            )
            assert ev["observed_business"]["specialist_stages_complete"] == (
                mode == "completed"
            )
            assert len(ev["observed_business"]["pending_holds"]) == int(
                mode.startswith("SIG")
            )
            assert "owned-resource-key" not in json.dumps(ev)
            if mode.startswith("SIG"):
                assert result["observed_signal"] == mode
            final = execute(
                [BIN / "resource-team-cases", "inspect", "--root", case["root"]]
            )
            assert all(
                o["status"] == "closed"
                for o in final["source"]["records"]["operations"]
            )
            source_bytes, evidence_bytes = (
                Path(case["database"]).read_bytes(),
                path.read_bytes(),
            )
            reset = execute(
                [
                    BIN / "resource-team-cases",
                    "reset",
                    "--root",
                    case["root"],
                    "--new-root",
                    BASE / (mode + "-reset"),
                ]
            )
            assert (
                reset["instance_id"] != case["instance_id"] and reset["generation"] == 2
            )
            assert reset["source"]["business"] == case["source"]["business"]
            assert (
                source_bytes == Path(case["database"]).read_bytes()
                and evidence_bytes == path.read_bytes()
            )
            proof.append(
                {
                    "mode": mode,
                    "requests": expected_calls,
                    "acknowledged_effects": expected_effects,
                    "prior_allocations_or_pending_holds_retained": True,
                    "cleanup_confirmed": True,
                    "preserving_reset": True,
                }
            )
        finally:
            release.set()
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

default = execute([BIN / "resource-team-state", "init", "--db", BASE / "default.db"])
assert (
    len(default["business"]["requests"]) == 3
    and default["source"]["creation"] == "fixture"
)
missing = execute(
    [
        launcher,
        "--db",
        BASE / "absent.db",
        "--request-id",
        "REQ-101",
        "--task",
        "Inspect only.",
        "--model",
        "gemini-owned-native",
        "--output",
        BASE / "missing-key",
    ],
    expected=1,
)
assert not (BASE / "absent.db").exists()
ev = json.loads(Path(missing["receipt_path"]).read_bytes())
assert ev["allocation"] == {"source": False, "scope": False, "provider": False}
print(
    json.dumps(
        {
            "cases": 12,
            "case_model_requests": probe["model_requests"],
            "normal_sdk_cli": proof,
            "default_seed": True,
            "missing_key_before_source": True,
            "production_only": "--production-only" in sys.argv,
            "real_provider_or_external_effect": False,
        }
    )
)
