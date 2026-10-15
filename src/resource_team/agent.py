"""Google ADK specialist workflow over the transactional resource store."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from google import genai
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import InMemoryRunner

from .store import AllocationStore


def build_team(store: AllocationStore, request_id: str, allow_writes: bool,
               models: str | dict[str, str | BaseLlm] | None = None) -> SequentialAgent:
    selected = models or os.getenv("GEMINI_MODEL")
    if not selected:
        raise RuntimeError("GEMINI_MODEL must name an enabled model")
    if not isinstance(selected, dict) and not isinstance(selected, BaseLlm) and not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY required for a live ADK team run")
    if isinstance(selected, dict) and not all(isinstance(value, BaseLlm) for value in selected.values()):
        raise RuntimeError("injected test models must be ADK BaseLlm instances")
    store.inspect(request_id)

    def model_for(name: str):
        return selected[name] if isinstance(selected, dict) else selected

    def inspect_request() -> dict:
        """Read the exact request, resource revisions and all current holds from shared state."""
        return store.inspect(request_id)

    def hold_resource(resource_id: str, expected_revision: int) -> dict:
        """Reserve one required resource using its current revision; conflicts are rejected atomically."""
        if not allow_writes:
            return {"error": "writes_not_authorized"}
        try:
            return store.reserve(request_id, resource_id, expected_revision)
        except ValueError as error:
            return {"error": str(error)}

    def commit_allocation() -> dict:
        """Confirm only when every required resource is held; a downstream failure needs compensation."""
        if not allow_writes:
            return {"error": "writes_not_authorized"}
        try:
            return store.commit(request_id)
        except ValueError as error:
            return {"error": str(error)}

    def release_holds() -> dict:
        """Compensate a partial or failed request by releasing its uncommitted holds."""
        if not allow_writes:
            return {"error": "writes_not_authorized"}
        try:
            return store.compensate(request_id)
        except ValueError as error:
            return {"error": str(error)}

    availability = LlmAgent(
        name="availability_specialist", model=model_for("availability"),
        instruction=(f"You inspect synthetic allocation request {request_id}. Use inspect_request to identify "
                     "every required resource, its current revision and any competing hold. Report exact evidence "
                     "for the reservation specialist. Do not claim a reservation exists."),
        tools=[inspect_request], output_key="availability_report",
    )
    reservation = LlmAgent(
        name="reservation_specialist", model=model_for("reservation"),
        instruction=(f"You allocate request {request_id}. Reread inspect_request, reserve each required "
                     "resource with its current revision, then call commit_allocation only when all are held. "
                     "If any hold or commit fails, stop and leave cleanup to compensation_specialist. "
                     "Do not claim confirmation before the committed state proves it."),
        tools=[inspect_request, hold_resource, commit_allocation], output_key="reservation_report",
    )
    compensation = LlmAgent(
        name="compensation_specialist", model=model_for("compensation"),
        instruction=(f"You inspect final state for request {request_id}. If it is holding or "
                     "needs_compensation, call release_holds and verify released resources. If it is confirmed, "
                     "do not release. Report the exact committed or compensated outcome."),
        tools=[inspect_request, release_holds], output_key="compensation_report",
    )
    return SequentialAgent(name="resource_allocation_team",
                           sub_agents=[availability, reservation, compensation])


async def run_team(store: AllocationStore, request_id: str, task: str, allow_writes: bool = False,
                   models: str | dict[str, str | BaseLlm] | None = None) -> dict:
    team = build_team(store, request_id, allow_writes, models)
    runner = InMemoryRunner(app=App(name="resource_allocation", root_agent=team))
    session_id = str(uuid4())
    await runner.session_service.create_session(app_name="resource_allocation", user_id="operator", session_id=session_id)
    message = genai.types.Content(role="user", parts=[genai.types.Part.from_text(text=task)])
    texts = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    usage_seen = False
    async for event in runner.run_async(user_id="operator", session_id=session_id, new_message=message):
        if event.content:
            texts.extend(part.text for part in event.content.parts if part.text)
        if event.usage_metadata:
            usage_seen = True
            usage["input_tokens"] += event.usage_metadata.prompt_token_count or 0
            usage["output_tokens"] += event.usage_metadata.candidates_token_count or 0
            usage["total_tokens"] += event.usage_metadata.total_token_count or 0
    return {"response": texts[-1] if texts else "", "state": store.inspect(request_id),
            "audit": store.audit(request_id), "usage": usage if usage_seen else None}


def run_team_sync(store: AllocationStore, request_id: str, task: str, allow_writes: bool = False,
                  models: str | dict[str, str | BaseLlm] | None = None) -> dict:
    return asyncio.run(run_team(store, request_id, task, allow_writes, models))
