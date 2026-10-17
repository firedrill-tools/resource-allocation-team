"""Owned normal actual ADK specialist workflow and lifecycle; all effects are local synthetic state."""

import asyncio
import time
from copy import deepcopy
from datetime import datetime, timezone

from .domain import public
from .evidence import Receipt, error_info
from .managed import ManagedSource
from .native import Native, config
from .strict import digest

TASK = "Allocate the exact original synthetic resource plan through actual specialists and inspect final compensation state."


class Run:
    def __init__(self, receipt, timeout_ms):
        self.receipt = receipt
        self.deadline = time.monotonic() + timeout_ms / 1000
        self.source = self.scope = self.native = self.job = self.intent = None
        self.failure = None
        self.closing = self.scope_close_attempted = self.scope_closed = False
        self.flow_drained = True
        self.source_attempted = False
        self.stop_events = []
        self.active_role = "availability"
        self.reads, self.agents = {}, {}
        self.allow_writes = False
        self.runner = self.session_service = self.session_id = None
        self.runner_close_attempted = self.runner_closed = False
        self.session_delete_attempted = self.session_deleted = False
        self.active_tool = None

    def fail(self, error):
        if self.failure is None:
            self.failure = error

    def save(self):
        try:
            self.receipt.save()
        except BaseException as error:
            self.fail(error)
            raise

    def basic(self):
        if self.failure is not None:
            raise self.failure
        if self.closing:
            raise RuntimeError("resource allocation operation is closing")
        if any(event.is_set() for event in self.stop_events):
            raise RuntimeError("resource allocation cancellation requested")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("resource allocation shared deadline exceeded")
        if self.receipt.error is not None:
            raise self.receipt.error

    def check(self):
        self.basic()
        if self.source is not None and self.scope is not None:
            from .managed_state import current_read

            snapshot = self.source.snapshot()
            operation = self.source._operation(snapshot, self.scope)
            current_read(
                snapshot["business"], operation, snapshot["views"][self.scope.id]
            )

    def close_scope(self):
        if self.scope_close_attempted:
            return
        if self.scope is None:
            self.scope_closed = True
            return
        self.scope_close_attempted = True
        try:
            result = self.source.close_operation(self.scope)
            self.scope_closed = result["closed"]
            self.receipt.data["scope_close"] = result
            self.receipt.data["lifecycle"].append(
                {"kind": "scope_close_returned", "closed": self.scope_closed}
            )
        except BaseException as error:
            self.receipt.data["scope_close_error"] = error_info(error)
            self.fail(error)

    def cancel(self, error):
        if self.closing:
            self.fail(error)
            return
        self.closing = True
        self.fail(error)
        self.close_scope()
        if self.job is not None and not self.job.done():
            self.receipt.data["lifecycle"].append(
                {"kind": "flow_cancel_requested", "scope_closed": self.scope_closed}
            )
            self.job.cancel()

    def read(self):
        self.check()
        try:
            read = self.source.observe(self.scope, self.active_role)
        except BaseException as error:
            self.fail(error)
            raise
        self.reads[self.active_role] = read
        snapshot = self.source.snapshot()
        self.receipt.data["actions"].append(
            {
                "kind": "own_current_role_read",
                "role": self.active_role,
                "observation": deepcopy(read["observation"]),
                "source": self.receipt.source(snapshot),
            }
        )
        self.save()
        return deepcopy(read["value"]["source"])

    def apply(self, kind, resource_id=None, expected_revision=None, provenance=None):
        self.check()
        read = self.reads.get(self.active_role)
        if read is None:
            raise ValueError(
                "own actual current role read required before action or final"
            )
        args = {"source": deepcopy(read["value"]["source"])}
        if kind == "hold":
            if type(expected_revision) is not int or not any(
                r["id"] == resource_id and r["revision"] == expected_revision
                for r in args["source"]["resources"]
            ):
                raise ValueError(
                    "exact original current held resource revision required"
                )
            args["resource_id"] = resource_id
        elif kind == "finish_role":
            args.update(role=self.active_role, provenance=deepcopy(provenance))
        try:
            returned = self.source.commit(
                self.scope,
                {"kind": kind, "args": args},
                read["observation"]["id"],
                read["proof"],
            )
        except BaseException as error:
            self.fail(error)
            raise
        self.reads.pop(self.active_role, None)
        self.receipt.data["acknowledged_decisions"].append(deepcopy(returned))
        if returned["decision"]["status"] == "applied":
            self.receipt.data["acknowledged_effects"].append(deepcopy(returned))
        snapshot = self.source.snapshot()
        self.receipt.data["observed_business"] = observed(
            snapshot, self.intent, self.scope
        )
        self.receipt.data["actions"].append(
            {
                "kind": "action_returned",
                "role": self.active_role,
                "action": kind,
                "returned": deepcopy(returned),
                "source": self.receipt.source(snapshot),
            }
        )
        self.save()
        return deepcopy(returned["result"])

    async def close_services(self):
        confirmed = True
        if (
            self.session_service is not None
            and self.session_id is not None
            and not self.session_delete_attempted
        ):
            self.session_delete_attempted = True
            try:
                await self.session_service.delete_session(
                    app_name="resource_allocation",
                    user_id="operator",
                    session_id=self.session_id,
                )
                self.session_deleted = True
            except BaseException as error:
                confirmed = False
                self.fail(error)
                self.receipt.data["session_delete_error"] = error_info(error)
        if self.runner is not None and not self.runner_close_attempted:
            self.runner_close_attempted = True
            try:
                await self.runner.close()
                self.runner_closed = True
            except BaseException as error:
                confirmed = False
                self.fail(error)
                self.receipt.data["runner_close_error"] = error_info(error)
        return (
            confirmed
            and (
                self.session_service is None
                or self.session_id is None
                or self.session_deleted
            )
            and (self.runner is None or self.runner_closed)
        )


def observed(source, intent, scope=None):
    state = public(source["business"], intent["request_id"])
    original = intent["source"]["request"]
    row = state["request"]
    plan_current = all(row[k] == original[k] for k in ("id", "needs", "fail_commit"))
    own = [h for h in state["holds"] if h["request_id"] == original["id"]]
    allocation = (
        plan_current
        and row["status"] == "confirmed"
        and {h["resource_id"] for h in own if h["status"] == "committed"}
        == set(original["needs"])
    )
    compensated = row["status"] == "compensated" and not own
    view = source["views"].get(scope.id) if scope is not None else None
    return {
        "status": "valid",
        "as_observed_context_sha256": source["context_sha256"],
        "state": state,
        "original_request": deepcopy(original),
        "plan_current": plan_current,
        "allocation_complete": allocation,
        "compensation_complete": compensated,
        "pending_holds": [h for h in own if h["status"] == "pending"],
        "workflow_complete": bool(plan_current and (allocation or compensated)),
        "specialist_stages_complete": bool(view and view["stage"] is None),
        "final_compensation_inspection": (
            deepcopy(view["final_inspection"]) if view else None
        ),
        "actual_external_effect": None,
    }


def reported_usage(calls):
    return {
        "source": "original Gemini JSON counters per actual HTTP call; no estimate or billing proof",
        "calls": [
            {"role": c["role"], "usage": deepcopy(c.get("reported_usage"))}
            for c in calls
        ],
        "cost_usd": None,
    }


async def run_owned_team(**options):
    allowed = {
        "database",
        "request_id",
        "task",
        "allow_writes",
        "intent",
        "api_key",
        "model",
        "base_url",
        "output_root",
        "timeout_ms",
        "stop_event",
        "parent_event",
    }
    signals = [options.get("stop_event"), options.get("parent_event")]
    receipt, owner, timer, watchers, result = None, None, None, [], None
    try:
        receipt = Receipt(
            options.get("output_root") or "artifacts/resource-allocation",
            {
                "execution": "actual_owned_ADK_Gemini_Google_SDK",
                "allocation": {"source": False, "scope": False, "provider": False},
                "observed_business": {
                    "status": "unopened",
                    "actual_external_effect": None,
                },
            },
        )
        if set(options) - allowed:
            raise ValueError("unsupported model/client/provider or execution option")
        if options.get("model") is not None and type(options["model"]) is not str:
            raise ValueError(
                "explicit model identifier required; model objects unsupported"
            )
        frozen = {
            k: deepcopy(v)
            for k, v in options.items()
            if k not in ("stop_event", "parent_event")
        }
        timeout_ms = frozen.get("timeout_ms", 120000)
        if type(timeout_ms) is not int or not 10 <= timeout_ms <= 300000:
            raise ValueError("exact bounded timeout_ms10..300000 required")
        owner = Run(receipt, timeout_ms)
        if type(frozen.get("allow_writes", False)) is not bool:
            raise ValueError("exact independent allow_writes required")
        owner.allow_writes = frozen.get("allow_writes", False)
        for event in signals:
            if event is not None:
                if not isinstance(event, asyncio.Event):
                    raise ValueError("actual asyncio cancellation Event required")
                owner.stop_events.append(event)
        owner.basic()
        configuration = config(
            frozen.get("api_key"), frozen.get("model"), frozen.get("base_url")
        )
        owner.basic()
        loop = asyncio.get_running_loop()
        timer = loop.call_later(
            timeout_ms / 1000,
            lambda: owner.cancel(TimeoutError("allocation shared deadline exceeded")),
        )
        for event in owner.stop_events:

            async def watch(signal=event):
                await signal.wait()
                owner.cancel(RuntimeError("allocation cancellation requested"))

            watchers.append(asyncio.create_task(watch()))
        owner.source_attempted = True
        try:
            owner.source = ManagedSource(
                frozen.get("database"), commit_guard=owner.basic
            )
        except BaseException as error:
            owner.source = getattr(error, "managed_source", None)
            receipt.data["allocation"]["source"] = owner.source is not None and (
                owner.source.database is not None or owner.source.descriptor is not None
            )
            raise
        receipt.data["allocation"]["source"] = True
        initial = owner.source.snapshot()
        receipt.data["initial_source"] = receipt.source(initial)
        owner.intent = owner.source.intent(frozen.get("request_id"), frozen.get("task"))
        if frozen.get("intent") is not None and digest(frozen["intent"]) != digest(
            owner.intent
        ):
            raise ValueError("complete current original allocation intent required")
        receipt.data["caller_intent"] = deepcopy(owner.intent)
        receipt.data["observed_business"] = observed(initial, owner.intent)
        owner.scope = owner.source.begin(
            owner.intent,
            TASK,
            allow_writes=owner.allow_writes,
            model=configuration.target(),
        )
        receipt.data["allocation"]["scope"] = True
        try:
            owner.native = Native(owner, configuration)
        except BaseException as error:
            owner.native = getattr(error, "native_owner", None)
            receipt.data["allocation"]["provider"] = owner.native is not None
            raise
        receipt.data["allocation"]["provider"] = True
        from .owned_team import run_team

        owner.job = asyncio.create_task(run_team(owner))
        owner.flow_drained = False
        result = await asyncio.shield(owner.job)
        owner.flow_drained = True
        owner.check()
        receipt.data["sdk_final"] = deepcopy(result)
        if not receipt.data["observed_business"]["specialist_stages_complete"]:
            raise ValueError(
                "actual final compensation inspection and three role completions required"
            )
        owner.save()
    except BaseException as error:
        if receipt is None:
            receipt = getattr(error, "receipt", None)
        if owner is None and receipt is not None:
            owner = Run(receipt, 120000)
        if owner is None:
            raise
        owner.fail(error)
    finally:
        if owner is not None:
            owner.closing = True
            owner.close_scope()

            async def finish():
                owner.close_scope()
                if owner.job is not None:
                    if not owner.job.done():
                        receipt.data["lifecycle"].append(
                            {
                                "kind": "flow_cancel_requested",
                                "scope_closed": owner.scope_closed,
                            }
                        )
                        owner.job.cancel()
                    await asyncio.gather(owner.job, return_exceptions=True)
                    owner.flow_drained = True
                services_confirmed = await owner.close_services()
                provider_confirmed = owner.native is None
                if owner.native is not None:
                    try:
                        await owner.native.close()
                        provider_confirmed = owner.native.closed
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["provider_close_error"] = error_info(error)
                source_confirmed, cleanup = owner.source is None, None
                if owner.source is not None:
                    try:
                        snapshot = owner.source.snapshot()
                        receipt.data["final_source"] = receipt.source(snapshot)
                        if owner.intent is not None:
                            receipt.data["observed_business"] = observed(
                                snapshot, owner.intent, owner.scope
                            )
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["observed_business"] = {
                            "status": "unknown",
                            "last_known": receipt.data["observed_business"],
                            "error": error_info(error),
                            "actual_external_effect": None,
                        }
                    try:
                        cleanup = owner.source.close()
                        source_confirmed = (
                            cleanup["database_closed"]
                            or not cleanup["database_close_attempted"]
                        ) and (
                            cleanup["descriptor_closed"]
                            or not cleanup["descriptor_close_attempted"]
                        )
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["source_close_error"] = error_info(error)
                        cleanup = {
                            "database_close_attempted": owner.source.database_close_attempted,
                            "database_closed": owner.source.database_closed,
                            "descriptor_close_attempted": owner.source.descriptor_close_attempted,
                            "descriptor_closed": owner.source.descriptor_closed,
                            "close_errors": deepcopy(owner.source.close_errors),
                        }
                for task in watchers:
                    task.cancel()
                if watchers:
                    await asyncio.gather(*watchers, return_exceptions=True)
                if timer is not None:
                    timer.cancel()
                if time.monotonic() >= owner.deadline:
                    owner.fail(
                        TimeoutError("allocation deadline exceeded during cleanup")
                    )
                receipt.data["cleanup"] = {
                    "runner_close_attempted": owner.runner_close_attempted,
                    "runner_closed": owner.runner_closed,
                    "session_delete_attempted": owner.session_delete_attempted,
                    "session_deleted": owner.session_deleted,
                    "services_close_confirmed": services_confirmed,
                    "scope_close_attempted": owner.scope_close_attempted,
                    "scope_closed": owner.scope_closed,
                    "flow_drained": owner.flow_drained,
                    "provider_close_attempted": (
                        owner.native.close_attempted if owner.native else False
                    ),
                    "provider_closed": owner.native.closed if owner.native else True,
                    "provider_close_confirmed": provider_confirmed,
                    "source_close_confirmed": source_confirmed,
                    "source": cleanup,
                }
                receipt.data["cleanup_confirmed"] = (
                    owner.scope_closed
                    and owner.flow_drained
                    and services_confirmed
                    and provider_confirmed
                    and source_confirmed
                )
                if not receipt.data["cleanup_confirmed"]:
                    owner.fail(RuntimeError("allocation cleanup unconfirmed"))
                receipt.data["source_attempted"] = owner.source_attempted
                receipt.data["finished_at"] = datetime.now(timezone.utc).isoformat()
                receipt.data["status"] = "failed" if owner.failure else "completed"
                receipt.data["error"] = (
                    error_info(owner.failure) if owner.failure else None
                )
                receipt.data["result"] = (
                    {
                        "status": "completed" if owner.allow_writes else "read_only",
                        "response": receipt.data["sdk_final"]["response"],
                        "business": receipt.data["observed_business"],
                        "usage": reported_usage(receipt.data["calls"]),
                        "cost_usd": None,
                        "receipt_path": str(receipt.path),
                        "cleanup_confirmed": receipt.data["cleanup_confirmed"],
                        "actual_external_effect": None,
                    }
                    if owner.failure is None
                    else None
                )
                try:
                    owner.save()
                    if time.monotonic() >= owner.deadline:
                        raise TimeoutError(
                            "allocation deadline exceeded during final recording"
                        )
                except BaseException as error:
                    owner.fail(error)
                    receipt.data["status"] = "failed"
                    receipt.data["result"] = None
                    receipt.data["error"] = error_info(owner.failure)
                    if receipt.error is None:
                        try:
                            receipt.save()
                        except BaseException:
                            pass

            cleanup_task = asyncio.create_task(finish())
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    owner.fail(error)
            cleanup_task.result()
    if owner.failure is not None:
        error = (
            RuntimeError("allocation caller cancelled")
            if isinstance(owner.failure, asyncio.CancelledError)
            else owner.failure
        )
        error.evidence = receipt.data
        error.receipt_path = str(receipt.path) if receipt.path else None
        if error is not owner.failure:
            raise error from owner.failure
        raise error
    return receipt.data["result"]
