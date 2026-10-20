# Resource allocation team with Google ADK and SQLite

Actual Google ADK availability, reservation and compensation specialists coordinate a local synthetic shared-resource workspace. The normal path uses real ADK `SequentialAgent`/`LlmAgent`/async `FunctionTool`/`Runner`, real Gemini and Google SDKs, and owned public HTTPX sync/async clients. Each hold, confirmation, release and final role transition consumes its own current source inspection. Compensation inspects even after a reservation claims success and never releases committed or other requests' allocations.

Simulate this Google ADK AI agent with [Firedrill](https://firedrill.run/), a simulation platform for AI agents. Start with the [Firedrill setup and simulation guide](firedrill/README.md); your coding agent can operate it while this application's runner stays in your repository or CI.

This example repository is publicly viewable. A project license has not been selected.

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

The original six backend/scripted ADK checks and literal starter fixture remain explicit human compatibility seams in `store.py`/`agent.py`; their injected `BaseLlm` objects are separate from normal native execution. Tests run the actual normal ADK/Gemini/Google SDKs against owned synthetic HTTP with synthetic keys/counters. No live Gemini quality, actual billing, real calendar/purchasing/physical booking or authenticated Firedrill persistence is qualified. SequentialAgent remains supported in pinned ADK2.12.0 with a documented deprecation warning.

Live enabled models/keys/budget, semantic/billing review, real connectors, authenticated Firedrill persistence verification, and license selection remain external gates. The repository is publicly viewable; no project license has been selected. See [PLAN.md](PLAN.md) and the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog).

## Installed scenarios and distributions

```sh
uv run resource-team-state init --db default-resources.db
uv run resource-team-cases list
uv run resource-team-cases init --case busy-recovery --root /tmp/resource-busy
uv run resource-team-cases inspect --root /tmp/resource-busy
uv run resource-team-cases reset --root /tmp/resource-busy --new-root /tmp/resource-busy-reset
uv run python scripts/generate_populations.py --check
uv build
uv run --group dev python scripts/check_distribution.py
```

Default initialization uses the exact pinned installed starter. Explicit fixture imports still require original unshared files. Setup/inspection snapshots are private and do not grant runtime capabilities. Three cases each cover starter, busy, ambiguous and boundary conditions; busy populations contain68resources/64requests/96fully replayed human steps with prior confirmed, compensated and partial allocations. Separate reset preserves old source, holds, audit and receipt bytes. See [scenario details](scenarios/README.md) and [qualification boundaries](COMPATIBILITY.md).

Distribution gates create fresh wheel production, sdist production, complete full and complete production environments outside checkout with locked dependency hashes. Production has no pytest. Each runs all12normal actual SDK cases (130owned HTTP requests), private CLI preserving reset, normal installed CLI complete/unfinished final/SIGINT/SIGTERM/SIGHUP after a real pending hold/default init/missing key. Complete source reruns144checks, verifies every source byte/mode/hash and reproduces portable archive bytes. CI uploads its exact source archive for download comparison; immutable revision/evidence goes in the portfolio catalog after success.

## Current Firedrill integration

Released CLI/SDK 2.2.2, current command invocation, remote operator MCP and recovery are documented in [firedrill/README.md](firedrill/README.md). Offline checks preserve actual private-case/native-key behavior and separate caller intent. Authenticated persisted runs and live quality/cost remain separate parked gates.
