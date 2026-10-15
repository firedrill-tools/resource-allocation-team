import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from google import genai
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from pydantic import PrivateAttr

from resource_team.agent import build_team, run_team
from resource_team.store import AllocationStore


FIXTURE = Path(__file__).parents[1] / "fixtures" / "starter.json"


def seeded(tmp_path):
    return AllocationStore.seed(tmp_path / "resources.db", FIXTURE)


def test_concurrent_requests_cannot_hold_the_same_resource(tmp_path):
    store = seeded(tmp_path)
    def reserve(request_id):
        try:
            return store.reserve(request_id, "ROOM-A", 1)
        except ValueError as error:
            return {"error": str(error)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ["REQ-101", "REQ-102"]))
    assert sum("error" not in item for item in outcomes) == 1
    assert sum("error" in item for item in outcomes) == 1
    assert len([item for item in store.inspect("REQ-101")["holds"] if item["resource_id"] == "ROOM-A"]) == 1


def test_successful_commit_uses_all_resources_and_blocks_competing_request(tmp_path):
    store = seeded(tmp_path)
    store.reserve("REQ-101", "ROOM-A", 1)
    with pytest.raises(ValueError, match="missing required"):
        store.commit("REQ-101")
    store.reserve("REQ-101", "PROJECTOR-A", 1)
    assert store.commit("REQ-101")["status"] == "confirmed"
    assert store.inspect("REQ-101")["request"]["status"] == "confirmed"
    with pytest.raises(ValueError, match="resource unavailable|stale resource"):
        store.reserve("REQ-102", "ROOM-A", 1)
    with pytest.raises(ValueError, match="nothing compensatable"):
        store.compensate("REQ-101")


def test_partial_allocation_and_failed_commit_compensate_exact_holds(tmp_path):
    store = seeded(tmp_path)
    store.reserve("REQ-102", "ROOM-A", 1)
    store.reserve("REQ-101", "PROJECTOR-A", 1)
    with pytest.raises(ValueError, match="resource unavailable"):
        store.reserve("REQ-101", "ROOM-A", 2)
    released = store.compensate("REQ-101")
    assert released["released"] == ["PROJECTOR-A"]
    assert [item["resource_id"] for item in store.inspect("REQ-102")["holds"]] == ["ROOM-A"]
    store.reserve("REQ-103", "ROOM-B", 1)
    store.reserve("REQ-103", "PROJECTOR-B", 1)
    failed = store.commit("REQ-103")
    assert failed == {"request_id": "REQ-103", "error": "commit_failed", "needs_compensation": True}
    restored = store.compensate("REQ-103")
    assert restored["released"] == ["PROJECTOR-B", "ROOM-B"]
    assert store.inspect("REQ-103")["request"]["status"] == "compensated"
    assert all(item["request_id"] != "REQ-103" for item in store.inspect("REQ-103")["holds"])


class ScriptModel(BaseLlm):
    model: str = "synthetic-resource-model"
    _steps: list = PrivateAttr(default_factory=list)
    _calls: int = PrivateAttr(default=0)

    def __init__(self, steps):
        super().__init__()
        self._steps = list(steps)

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        if not self._steps:
            raise AssertionError("unexpected ADK model call")
        step = self._steps.pop(0)
        if isinstance(step, str):
            part = genai.types.Part.from_text(text=step)
        else:
            part = genai.types.Part.from_function_call(name=step[0], args=step[1])
        yield LlmResponse(content=genai.types.Content(role="model", parts=[part]))


def models_for_success():
    return {
        "availability": ScriptModel([("inspect_request", {}), "ROOM-A and PROJECTOR-A are currently free."]),
        "reservation": ScriptModel([("inspect_request", {}), ("hold_resource", {"resource_id": "ROOM-A", "expected_revision": 1}),
                                    ("hold_resource", {"resource_id": "PROJECTOR-A", "expected_revision": 1}),
                                    ("commit_allocation", {}), "Both resources are committed for REQ-101."]),
        "compensation": ScriptModel([("inspect_request", {}), "REQ-101 is confirmed; no compensation is needed."]),
    }


def test_actual_adk_sequential_team_commits_shared_resources(tmp_path):
    store = seeded(tmp_path); models = models_for_success()
    team = build_team(store, "REQ-101", True, models)
    assert [agent.name for agent in team.sub_agents] == ["availability_specialist", "reservation_specialist", "compensation_specialist"]
    result = asyncio.run(run_team(store, "REQ-101", "Allocate the room and projector", True, models))
    assert result["state"]["request"]["status"] == "confirmed"
    assert [item["event"] for item in result["audit"]] == ["reserved", "reserved", "confirmed"]
    assert all(not model._steps for model in models.values())


def test_adk_team_compensates_downstream_commit_failure(tmp_path):
    store = seeded(tmp_path)
    models = {
        "availability": ScriptModel([("inspect_request", {}), "ROOM-B and PROJECTOR-B appear free."]),
        "reservation": ScriptModel([("hold_resource", {"resource_id": "ROOM-B", "expected_revision": 1}),
                                    ("hold_resource", {"resource_id": "PROJECTOR-B", "expected_revision": 1}),
                                    ("commit_allocation", {}), "Commit failed; compensation is required."]),
        "compensation": ScriptModel([("inspect_request", {}), ("release_holds", {}),
                                     "REQ-103 holds were released after the failed commit."]),
    }
    result = asyncio.run(run_team(store, "REQ-103", "Allocate the resources and compensate failures", True, models))
    assert result["state"]["request"]["status"] == "compensated"
    assert result["state"]["holds"] == []
    assert [item["event"] for item in result["audit"]] == ["reserved", "reserved", "commit_failed", "compensated"]
    assert all(not model._steps for model in models.values())


def test_adk_read_only_team_cannot_create_holds(tmp_path):
    store = seeded(tmp_path)
    models = {
        "availability": ScriptModel([("inspect_request", {}), "The resources are visible."]),
        "reservation": ScriptModel([("hold_resource", {"resource_id": "ROOM-A", "expected_revision": 1}),
                                    "Write authorization is required before reserving."]),
        "compensation": ScriptModel([("inspect_request", {}), "No holds were created."]),
    }
    result = asyncio.run(run_team(store, "REQ-101", "Inspect without writes", False, models))
    assert result["state"]["request"]["status"] == "pending"
    assert result["state"]["holds"] == []
    assert result["audit"] == []
