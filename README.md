# Resource allocation team

Actual Google ADK availability, reservation and compensation specialists coordinate a local synthetic shared-resource workspace. The normal path uses real ADK `SequentialAgent`/`LlmAgent`/async `FunctionTool`/`Runner`, real Gemini and Google SDKs, and owned public HTTPX sync/async clients. Each hold, confirmation, release and final role transition consumes its own current source inspection. Compensation inspects even after a reservation claims success and never releases committed or other requests' allocations.

## Run locally

Requires Python3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --group dev
uv run resource-team-state init --db resources.db --fixture fixtures/starter.json
```

Set `GOOGLE_API_KEY` and an explicitly enabled `GEMINI_MODEL`, then run:

```sh
uv run resource-team --db resources.db --request-id REQ-101 \
  --task 'Allocate these exact original requested resources and inspect final compensation state.' \
  --allow-writes --output artifacts/resource-allocation
```

Omit `--allow-writes` to inspect: the actual specialists and private role controls run, while business resources, requests, holds and audit remain byte-for-byte unchanged. Missing keys/models fail before opening source or allocating SDK clients. No provider default model or paid fallback is enabled by these instructions.

REQ-101/REQ-102 compete for the same room/projector. REQ-103 holds a different pair and deliberately fails at commit; actual compensation releases its pending holds. A shared SQLite transaction confirms only with all original required resources. Every role and write needs genuine private current-read authority, an unchanged original selected request plan and independent caller permission. Safe resource/request revisions, current availability, competing holds and complete prior history matter. A changed original plan invalidates that scope. Confirmed allocations survive later technical failure; partial holds remain visible until actual authorized compensation.

## Evidence and lifecycle

Whole original bounded fatal UTF8/JSON, explicit single-candidate `STOP`, exact function name/arguments/types and SDK parsed-content comparison precede tool effects. Missing/null/unfinished/parallel/coerced/malformed/refused or unsupported provider data stops normal execution. Original unknown fields and unknown/zero/invalid usage counters remain distinct from SDK normalization; billing stays unknown. No retry/fallback/redirect, ambient Vertex/account/origin/header/cache/session routing, fake normal model or vendor/global patch is used. Run telemetry explicitly captures no message content; an already configured global tracing provider or ambient exporter/custom-header configuration is unsupported at startup.

Per-run ownership covers both actual supplied HTTPX pools (the Google SDK deliberately leaves custom pools open), SDK facades, actual runner/session, graph task, database/FD, timers/watchers and SIGINT/SIGTERM/SIGHUP. Cancellation revokes scope before draining work. Evidence separately records technical success, known earlier effects, source validity, recording failure and failed cleanup acknowledgments. A model narrative never grants authority or substitutes for compensation's actual final inspection.

```sh
uv run --locked --group dev pytest -q
```

The original six backend/scripted ADK checks and literal starter fixture remain explicit human compatibility seams in `store.py`/`agent.py`; their injected `BaseLlm` objects are separate from normal native execution. Tests run the actual normal ADK/Gemini/Google SDKs against owned synthetic HTTP with synthetic keys/counters. No live Gemini quality, actual billing, real calendar/purchasing/physical booking or new Firedrill persisted integration is qualified. SequentialAgent remains supported in pinned ADK2.12.0 with a documented deprecation warning.

Live enabled models/keys/budget, semantic/billing review, real connectors, new Firedrill release qualification, public publication and license remain external gates. The repository remains private without a license pending that decision. See [PLAN.md](PLAN.md) and the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog).
