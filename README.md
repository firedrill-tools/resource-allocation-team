# Resource allocation team

A Google ADK workflow coordinates three Gemini specialists over a shared synthetic resource store: availability inspection, reservation, and compensation. SQLite transactions protect room and equipment holds when requests compete. A request is confirmed only after it holds every required resource. A partial allocation or downstream commit failure remains visible until the compensation specialist releases its uncommitted holds.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --group dev
uv run resource-team --seed
```

Set `GOOGLE_API_KEY` and an enabled `GEMINI_MODEL`, then inspect without writes:

```sh
uv run resource-team --request-id REQ-101 --task "Inspect the room and projector needed by this request"
```

To allow a synthetic allocation:

```sh
uv run resource-team --request-id REQ-101 --task "Reserve both required resources, confirm only if both holds succeed, and compensate any failure" --allow-writes
```

`--db` changes the local SQLite path, defaulting to `workspace/resources.db`. The ADK `SequentialAgent` runs availability, reservation and compensation specialists in order. Each specialist reads the same committed database. Tool writes require `--allow-writes`; holds use expected resource revisions and `BEGIN IMMEDIATE` transactions. REQ-101 and REQ-102 compete for the same room and projector. REQ-103 deliberately fails at commit after acquiring a different room and projector; compensation releases those holds. The final JSON reports the exact request, resources, holds, audit events and model usage when ADK supplies it.

The sample has no real room calendar, purchasing or physical reservation system. Reset by creating a new database path; seeding refuses to overwrite an existing file. `uv run --group dev pytest` exercises concurrent contenders, complete and partial holds, downstream failure, compensation, a read-only run, and actual ADK runner execution with scripted specialist models. These are offline workflow and state proofs, not live Gemini decision quality. Live Gemini and new Firedrill integration proofs remain pending.

ADK 2.12.0 still supports `SequentialAgent` but warns that it will be replaced by `Workflow` in a future version. This private repository has no license pending portfolio publication decisions.
