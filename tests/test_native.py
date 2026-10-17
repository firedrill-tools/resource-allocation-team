"""Actual ADK/Gemini/Google SDKs with public owned HTTP; no fake native model."""

import asyncio
import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from resource_team.owned_runtime import run_owned_team
from test_managed import seed


@contextmanager
def provider(mutate=None, before=None):
    captured = []
    counts = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            instruction = body["systemInstruction"]["parts"][0]["text"]
            role = next(
                r
                for r in ("availability", "reservation", "compensation")
                if "You are the " + r + " specialist" in instruction
            )
            step = counts.get(role, 0)
            counts[role] = step + 1
            captured.append(
                {
                    "role": role,
                    "body": body,
                    "headers": dict(self.headers),
                    "path": self.path,
                }
            )
            if before:
                before(role, step, captured)
            responses = [
                p["functionResponse"]
                for c in body["contents"]
                for p in c.get("parts", [])
                if "functionResponse" in p
            ]
            inspections = [
                p["response"] for p in responses if p["name"] == "inspect_request"
            ]
            current = inspections[-1] if inspections else None
            if current is not None and "result" in current:
                current = current["result"]
            name, args = None, {}
            if role == "availability":
                if step == 0:
                    name = "inspect_request"
            elif role == "reservation":
                needs = (
                    current["request"]["needs"]
                    if current
                    else ["ROOM-A", "PROJECTOR-A"]
                )
                count = len(needs)
                if step % 2 == 0 and step <= 2 * count + 2:
                    name = "inspect_request"
                elif step < 2 * count:
                    ident = needs[step // 2]
                    row = next(r for r in current["resources"] if r["id"] == ident)
                    name, args = "hold_resource", {
                        "resource_id": ident,
                        "expected_revision": row["revision"],
                    }
                elif step == 2 * count + 1:
                    name = "commit_allocation"
            else:
                # Whether compensation is needed is read from real tool state.
                if step == 0 or step == 2:
                    name = "inspect_request"
                elif step == 1 and current["request"]["status"] in (
                    "holding",
                    "needs_compensation",
                ):
                    name = "release_holds"
            part = (
                {"functionCall": {"name": name, "args": args}}
                if name
                else {
                    "text": "The synthetic specialist reports only the inspected committed state."
                }
            )
            value = {
                "responseId": "owned-" + str(len(captured)),
                "modelVersion": "gemini-owned-native",
                "candidates": [
                    {
                        "index": 0,
                        "finishReason": "STOP",
                        "content": {"role": "model", "parts": [part]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 12,
                },
            }
            raw = None
            status = 200
            if mutate:
                returned = mutate(role, step, value, captured)
                if type(returned) is bytes:
                    raw = returned
                elif type(returned) is int:
                    status = returned
            raw = json.dumps(value).encode() if raw is None else raw
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def options(tmp_path, base, request_id="REQ-101"):
    source = seed(tmp_path)
    path = source.path
    source.close()
    return {
        "database": path,
        "request_id": request_id,
        "task": "Allocate these exact original fictional room and equipment resources and inspect final compensation state.",
        "allow_writes": True,
        "api_key": "owned-resource-key",
        "model": "gemini-owned-native",
        "base_url": base,
        "output_root": tmp_path / "receipts",
    }


def evidence(result):
    return json.loads(Path(result["receipt_path"]).read_bytes())


def test_actual_normal_adk_specialists_google_sdk_confirm_all_original_resources_and_inspect_final_compensation(
    tmp_path,
):
    with provider() as (base, captured):
        result = asyncio.run(run_owned_team(**options(tmp_path, base)))
    ev = evidence(result)
    assert len(captured) == 12 and all(
        c["sdk_verified"] and c["response_closed"] for c in ev["calls"]
    )
    assert (
        result["business"]["allocation_complete"]
        and result["business"]["specialist_stages_complete"]
    )
    assert (
        result["business"]["final_compensation_inspection"]["request"]["status"]
        == "confirmed"
    )
    assert len(ev["acknowledged_effects"]) == 3 and result["cleanup_confirmed"]
    assert (
        ev["cleanup"]["runner_closed"]
        and ev["cleanup"]["session_deleted"]
        and ev["cleanup"]["provider_closed"]
    )
    assert "owned-resource-key" not in json.dumps(ev)


import pytest


@pytest.mark.parametrize(
    "request_id,compensated", [("REQ-101", False), ("REQ-103", True)]
)
def test_actual_confirmed_or_compensated_native_business_matches_final_inspection(
    tmp_path, request_id, compensated
):
    with provider() as (base, captured):
        result = asyncio.run(run_owned_team(**options(tmp_path, base, request_id)))
    ev = evidence(result)
    assert len(captured) == (14 if compensated else 12)
    assert result["business"]["compensation_complete"] == compensated
    assert result["business"]["allocation_complete"] != compensated
    assert (
        result["business"]["workflow_complete"]
        and result["business"]["specialist_stages_complete"]
    )
    assert (
        len(ev["acknowledged_effects"]) == (4 if compensated else 3)
        and result["cleanup_confirmed"]
    )


def test_actual_native_readonly_denials_keep_business_and_holds_literal_unchanged(
    tmp_path,
):
    from resource_team.managed import ManagedSource

    with provider() as (base, captured):
        opts = options(tmp_path, base)
        opts["allow_writes"] = False
        with ManagedSource(opts["database"]) as source:
            before = source.database.execute(
                "SELECT document FROM business"
            ).fetchone()[0]
        result = asyncio.run(run_owned_team(**opts))
    with ManagedSource(opts["database"]) as source:
        assert (
            source.database.execute("SELECT document FROM business").fetchone()[0]
            == before
        )
    assert (
        not result["business"]["workflow_complete"]
        and result["business"]["specialist_stages_complete"]
    )
    assert evidence(result)["acknowledged_effects"] == [] and len(captured) == 12


@pytest.mark.parametrize(
    "fault",
    [
        "missing_finish",
        "null_finish",
        "max_tokens",
        "multi_candidate",
        "duplicate",
        "nonfinite",
        "wrong_role",
        "mixed",
        "parallel",
        "wrong_tool",
        "coerced_revision",
        "extra_arg",
        "partial",
        "status",
        "oversize",
    ],
)
def test_original_gemini_hold_response_must_be_whole_strict_complete_before_any_effect(
    tmp_path, fault
):
    def mutate(role, step, value, captured):
        if role != "reservation" or step != 1:
            return
        candidate = value["candidates"][0]
        part = candidate["content"]["parts"][0]
        if fault == "missing_finish":
            candidate.pop("finishReason")
        elif fault == "null_finish":
            candidate["finishReason"] = None
        elif fault == "max_tokens":
            candidate["finishReason"] = "MAX_TOKENS"
        elif fault == "multi_candidate":
            value["candidates"].append(deepcopy(candidate))
        elif fault == "duplicate":
            return (
                json.dumps(value)
                .replace('"finishReason":', '"finishReason":"STOP","finishReason":')
                .encode()
            )
        elif fault == "nonfinite":
            return (
                json.dumps(value)
                .replace('"promptTokenCount": 10', '"promptTokenCount": NaN')
                .encode()
            )
        elif fault == "wrong_role":
            candidate["content"]["role"] = "user"
        elif fault == "mixed":
            part["text"] = "unapproved mixture"
        elif fault == "parallel":
            candidate["content"]["parts"].append(deepcopy(part))
        elif fault == "wrong_tool":
            part["functionCall"]["name"] = "release_holds"
        elif fault == "coerced_revision":
            part["functionCall"]["args"]["expected_revision"] = True
        elif fault == "extra_arg":
            part["functionCall"]["args"]["request_id"] = "REQ-102"
        elif fault == "partial":
            return b'{"candidates":'
        elif fault == "status":
            return 429
        elif fault == "oversize":
            return b" " * (1024 * 1024 + 1)

    with provider(mutate) as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_team(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert (
        len(captured) == 4
        and ev["acknowledged_effects"] == []
        and ev["cleanup_confirmed"]
    )
    assert (
        not ev["observed_business"]["allocation_complete"]
        and ev["observed_business"]["state"]["holds"] == []
    )


@pytest.mark.parametrize(
    "role,step,prior_effects", [("reservation", 3, 1), ("compensation", 1, 3)]
)
def test_unfinished_later_actual_sdk_response_retains_known_pending_or_confirmed_state(
    tmp_path, role, step, prior_effects
):
    def mutate(current, n, value, captured):
        if current == role and n == step:
            value["candidates"][0]["finishReason"] = None

    with provider(mutate) as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_team(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert len(ev["acknowledged_effects"]) == prior_effects and ev["cleanup_confirmed"]
    assert ev["observed_business"]["allocation_complete"] == (role == "compensation")
    assert not ev["observed_business"]["specialist_stages_complete"]
    assert len(ev["observed_business"]["state"]["holds"]) == (
        2 if role == "compensation" else 1
    )


def test_actual_final_compensation_cannot_skip_own_inspection_even_after_confirmed_reservation(
    tmp_path,
):
    def mutate(role, step, value, captured):
        if role == "compensation" and step == 0:
            value["candidates"][0]["content"]["parts"] = [
                {
                    "text": "The previous role claimed success, so no inspection is needed."
                }
            ]

    with provider(mutate) as (base, captured):
        with pytest.raises(ValueError, match="own actual current role read") as exc:
            asyncio.run(run_owned_team(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert (
        ev["observed_business"]["allocation_complete"]
        and len(ev["acknowledged_effects"]) == 3
    )
    assert (
        ev["observed_business"]["final_compensation_inspection"] is None
        and ev["cleanup_confirmed"]
    )


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0},
        {"promptTokenCount": True, "candidatesTokenCount": -1},
    ],
)
def test_original_usage_unknown_zero_invalid_and_unknown_fields_survive_sdk_normalization(
    tmp_path, usage
):
    def mutate(role, step, value, captured):
        if role == "availability" and step == 0:
            if usage is None:
                value.pop("usageMetadata")
            else:
                value["usageMetadata"] = usage
            value["original_unknown"] = {"owned": "retained"}

    with provider(mutate) as (base, captured):
        result = asyncio.run(run_owned_team(**options(tmp_path, base)))
    call = evidence(result)["calls"][0]
    assert call["response"]["original_unknown"] == {"owned": "retained"}
    expected = (
        "unknown"
        if not usage
        else ("invalid" if type(usage["promptTokenCount"]) is bool else "observed")
    )
    assert (
        call["reported_usage"]["promptTokenCount"]["status"] == expected
        and call["reported_usage"]["cost_usd"] is None
    )
    assert result["cost_usd"] is None


def test_real_original_plan_change_during_first_reservation_http_invalidates_scope_before_hold(
    tmp_path,
):
    from resource_team.managed import ManagedSource

    opts = None

    def before(role, step, captured):
        if role == "reservation" and step == 1:
            with ManagedSource(opts["database"]) as source:
                row = deepcopy(source.snapshot()["business"]["requests"][0])
                source.human(
                    {
                        "kind": "update_request",
                        "args": {
                            "source": row,
                            "needs": ["ROOM-A"],
                            "fail_commit": False,
                        },
                    }
                )

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        with pytest.raises(ValueError, match="request_changed") as exc:
            asyncio.run(run_owned_team(**opts))
    ev = exc.value.evidence
    assert (
        len(captured) == 4
        and ev["acknowledged_effects"] == []
        and not ev["observed_business"]["plan_current"]
    )
    assert ev["cleanup_confirmed"] and ev["observed_business"]["state"]["holds"] == []


def test_real_competing_request_holds_after_availability_are_seen_and_partial_own_holds_are_compensated(
    tmp_path,
):
    from resource_team.managed import ManagedSource
    from resource_team.domain import public

    opts = None

    def before(role, step, captured):
        if role == "reservation" and step == 0:
            with ManagedSource(opts["database"]) as source:
                source.human(
                    {
                        "kind": "hold",
                        "args": {
                            "source": public(source.snapshot()["business"], "REQ-102"),
                            "resource_id": "ROOM-A",
                        },
                    }
                )

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        result = asyncio.run(run_owned_team(**opts))
    assert (
        result["business"]["compensation_complete"]
        and not result["business"]["allocation_complete"]
    )
    assert result["business"]["state"]["holds"] == [
        {"resource_id": "ROOM-A", "request_id": "REQ-102", "status": "pending"}
    ]
    assert len(evidence(result)["acknowledged_effects"]) == 2 and len(captured) == 14


def test_actual_caller_options_frozen_before_http_and_ambient_vertex_origin_key_routing_is_ignored(
    tmp_path, monkeypatch
):
    opts = None
    for name, value in {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "unowned-project",
        "GOOGLE_CLOUD_LOCATION": "unowned-region",
        "GOOGLE_GEMINI_BASE_URL": "http://127.0.0.1:1",
        "GOOGLE_VERTEX_BASE_URL": "http://127.0.0.1:1",
        "GEMINI_API_KEY": "unowned-other-key",
        "GOOGLE_GENAI_API_VERSION": "unowned-version",
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "true",
    }.items():
        monkeypatch.setenv(name, value)

    def before(role, step, captured):
        if len(captured) == 1:
            opts["allow_writes"] = False
            opts["model"] = "gemini-changed"
            opts["task"] = "changed"
            opts["request_id"] = "REQ-103"

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        result = asyncio.run(run_owned_team(**opts))
    assert (
        result["business"]["allocation_complete"]
        and result["business"]["state"]["request"]["id"] == "REQ-101"
    )
    assert all(
        c["path"] == "/v1beta/models/gemini-owned-native:generateContent"
        for c in captured
    )
    assert "unowned-" not in json.dumps(evidence(result))


def test_all_required_key_model_configuration_fails_before_source_and_provider_allocation(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with provider() as (base, captured):
        opts = options(tmp_path, base)
        opts["api_key"] = None
        with pytest.raises(ValueError, match="GOOGLE_API_KEY") as exc:
            asyncio.run(run_owned_team(**opts))
    assert captured == [] and exc.value.evidence["allocation"] == {
        "source": False,
        "scope": False,
        "provider": False,
    }
    assert exc.value.evidence["cleanup_confirmed"]


def test_unsupported_provider_rejected_after_prepared_receipt_before_copying_object(
    tmp_path,
):
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise AssertionError("unsupported object must not be copied")

    with pytest.raises(ValueError, match="unsupported") as exc:
        asyncio.run(
            run_owned_team(output_root=tmp_path / "receipts", provider=Uncopyable())
        )
    assert exc.value.receipt_path and not exc.value.evidence["allocation"]["source"]


@pytest.mark.parametrize("kind", ["caller", "parent", "deadline", "direct"])
def test_actual_pending_gemini_cancel_revokes_before_drain_and_retains_first_pending_hold(
    tmp_path, kind
):
    entered, release = threading.Event(), threading.Event()

    def before(role, step, captured):
        if role == "reservation" and step == 2:
            entered.set()
            release.wait(3)

    async def run(opts):
        stop, parent = asyncio.Event(), asyncio.Event()
        opts.update(stop_event=stop, parent_event=parent)
        if kind == "deadline":
            opts["timeout_ms"] = 700
        task = asyncio.create_task(run_owned_team(**opts))
        while not entered.is_set() and not task.done():
            await asyncio.sleep(0.005)
        assert entered.is_set()
        if kind == "caller":
            stop.set()
        elif kind == "parent":
            parent.set()
        elif kind == "direct":
            task.cancel()
        try:
            with pytest.raises(Exception) as exc:
                await task
            return exc.value.evidence
        finally:
            release.set()

    with provider(before=before) as (base, captured):
        ev = asyncio.run(run(options(tmp_path, base)))
    assert len(ev["acknowledged_effects"]) == 1 and ev["cleanup_confirmed"]
    assert (
        len(ev["observed_business"]["pending_holds"]) == 1
        and not ev["observed_business"]["allocation_complete"]
    )
    canceled = [x for x in ev["lifecycle"] if x["kind"] == "flow_cancel_requested"]
    assert canceled and all(x["scope_closed"] for x in canceled)


def test_partial_actual_google_client_constructor_still_closes_both_supplied_pools(
    tmp_path, monkeypatch
):
    from google import genai

    actual = genai.Client
    allocated = []

    def fail(**kwargs):
        allocated.append((actual(**kwargs), kwargs["http_options"]))
        raise RuntimeError(
            "actual Google client constructed but startup acknowledgment failed"
        )

    monkeypatch.setattr(genai, "Client", fail)
    with provider() as (base, captured):
        with pytest.raises(RuntimeError, match="startup acknowledgment") as exc:
            asyncio.run(run_owned_team(**options(tmp_path, base)))
    assert captured == [] and exc.value.evidence["cleanup_confirmed"]
    assert (
        allocated[0][1].httpx_client.is_closed
        and allocated[0][1].httpx_async_client.is_closed
    )


@pytest.mark.parametrize(
    "resource", ["provider", "source", "runner", "session", "recording"]
)
def test_late_actual_close_or_recording_failure_retains_confirmed_allocation_and_failed_ack(
    tmp_path, monkeypatch, resource
):
    if resource == "provider":
        from resource_team.native import Native

        actual = Native.close

        async def close(value):
            await actual(value)
            raise RuntimeError("actual Google pools closed but acknowledgment failed")

        monkeypatch.setattr(Native, "close", close)
    elif resource == "source":
        from resource_team.managed import ManagedSource

        actual = ManagedSource.close

        def close(value):
            result = actual(value)
            if value.commit_guard is not None:
                raise RuntimeError("actual source closed but acknowledgment failed")
            return result

        monkeypatch.setattr(ManagedSource, "close", close)
    elif resource == "runner":
        from google.adk.runners import Runner

        actual = Runner.close

        async def close(value):
            await actual(value)
            raise RuntimeError("actual runner closed but acknowledgment failed")

        monkeypatch.setattr(Runner, "close", close)
    elif resource == "session":
        from google.adk.sessions import InMemorySessionService

        actual = InMemorySessionService.delete_session

        async def close(value, **kwargs):
            await actual(value, **kwargs)
            raise RuntimeError("actual session deleted but acknowledgment failed")

        monkeypatch.setattr(InMemorySessionService, "delete_session", close)
    else:
        from resource_team.evidence import Receipt

        actual = Receipt.save

        def save(value):
            if value.data["observed_business"].get("allocation_complete"):
                import shutil

                if value.path.parent.exists():
                    shutil.rmtree(value.path.parent)
            return actual(value)

        monkeypatch.setattr(Receipt, "save", save)
    with provider() as (base, captured):
        with pytest.raises(Exception) as exc:
            asyncio.run(run_owned_team(**options(tmp_path, base)))
    ev = exc.value.evidence
    assert (
        len(ev["acknowledged_effects"]) == 3
        and ev["observed_business"]["allocation_complete"]
    )
    if resource == "recording":
        assert ev["recording_status"] == "unknown" and ev["cleanup_confirmed"]
    else:
        assert not ev["cleanup_confirmed"]


@pytest.mark.parametrize("fault", ["business_corrupt", "writer_lock"])
def test_late_real_source_failure_after_first_hold_remains_unknown_or_unconfirmed_without_repair(
    tmp_path, fault
):
    import sqlite3

    opts = None
    blocker = None

    def before(role, step, captured):
        nonlocal blocker
        if role == "reservation" and step == 2:
            blocker = sqlite3.connect(
                opts["database"], isolation_level=None, check_same_thread=False
            )
            if fault == "business_corrupt":
                blocker.execute("UPDATE business SET document='{}'")
            else:
                blocker.execute("BEGIN IMMEDIATE")

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        try:
            with pytest.raises(Exception) as exc:
                asyncio.run(run_owned_team(**opts))
        finally:
            if blocker is not None:
                if blocker.in_transaction:
                    blocker.execute("ROLLBACK")
                blocker.close()
    ev = exc.value.evidence
    assert len(captured) == 5 and len(ev["acknowledged_effects"]) == 1
    if fault == "business_corrupt":
        assert (
            ev["observed_business"]["status"] == "unknown" and ev["cleanup_confirmed"]
        )
        assert len(ev["observed_business"]["last_known"]["pending_holds"]) == 1
    else:
        assert (
            ev["observed_business"]["status"] == "valid" and not ev["cleanup_confirmed"]
        )
        assert (
            len(ev["observed_business"]["pending_holds"]) == 1
            and not ev["cleanup"]["scope_closed"]
        )


def cli_command(opts):
    import sys

    return [
        sys.executable,
        "-m",
        "resource_team.cli",
        "--db",
        str(opts["database"]),
        "--request-id",
        opts["request_id"],
        "--task",
        opts["task"],
        "--allow-writes",
        "--model",
        opts["model"],
        "--base-url",
        opts["base_url"],
        "--output",
        str(opts["output_root"]),
    ]


def cli_env():
    import os

    return {**os.environ, "GOOGLE_API_KEY": "owned-resource-key"}


@pytest.mark.parametrize("incomplete", [False, True])
def test_actual_normal_cli_outside_checkout_completes_or_keeps_confirmed_allocation_after_unfinished_final(
    tmp_path, incomplete
):
    import subprocess

    def mutate(role, step, value, captured):
        if incomplete and role == "compensation" and step == 1:
            value["candidates"][0]["finishReason"] = None

    with provider(mutate) as (base, captured):
        opts = options(tmp_path, base)
        out = subprocess.run(
            cli_command(opts),
            cwd=tmp_path,
            env=cli_env(),
            capture_output=True,
            text=True,
            timeout=15,
        )
    result = json.loads(out.stdout)
    assert (
        out.returncode == int(incomplete)
        and len(captured) == 12
        and result["cleanup_confirmed"]
    )
    assert result["business"]["allocation_complete"]
    assert result["business"]["specialist_stages_complete"] != incomplete
    if incomplete:
        assert len(result["acknowledged_effects"]) == 3


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_real_normal_cli_all_os_signals_after_first_pending_hold_preserves_source_and_closes(
    tmp_path, signal_name
):
    import signal, subprocess

    entered, release = threading.Event(), threading.Event()

    def before(role, step, captured):
        if role == "reservation" and step == 2:
            entered.set()
            release.wait(5)

    with provider(before=before) as (base, captured):
        opts = options(tmp_path, base)
        child = subprocess.Popen(
            cli_command(opts),
            cwd=tmp_path,
            env=cli_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert entered.wait(8)
            child.send_signal(getattr(signal, signal_name))
            stdout, stderr = child.communicate(timeout=8)
        finally:
            release.set()
            if child.poll() is None:
                child.kill()
                child.wait()
    result = json.loads(stdout)
    assert (
        child.returncode == 1
        and result["observed_signal"] == signal_name
        and result["cleanup_confirmed"]
    )
    assert len(captured) == 5 and len(result["acknowledged_effects"]) == 1
    assert (
        len(result["business"]["pending_holds"]) == 1
        and not result["business"]["allocation_complete"]
    )


def test_normal_cli_missing_key_has_prepared_receipt_before_opening_source(tmp_path):
    import subprocess

    env = cli_env()
    env.pop("GOOGLE_API_KEY", None)
    with provider() as (base, captured):
        opts = options(tmp_path, base)
        out = subprocess.run(
            cli_command(opts),
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    result = json.loads(out.stdout)
    assert (
        out.returncode == 1
        and captured == []
        and result["business"]["status"] == "unopened"
    )
    assert result["receipt_path"] and result["cleanup_confirmed"]
