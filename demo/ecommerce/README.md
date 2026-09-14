# CognitiveOS E-Commerce Backend Demo

A production-quality backend demonstration of CognitiveOS's execution
model — World Construction, Planetary Cycle, Cognitive Reasoning,
Context Propagation, World Mutation, and Adaptive Replanning — in the
e-commerce domain. There is no UI. Every step is a real call to the
same REST APIs an external client would use; nothing here reaches into
runtime internals, bypasses validation, or contains demo-only logic in
the server.

## What it does

```
Bootstrap World → Validate World → Planetary Tick → Customer Prompt
  → Inject Event (Warehouse Fire) → Planetary Tick → Same Prompt Again
  → Observe Different Reasoning → Display Metrics
```

The headline moment: the customer asks the same question twice. Between
the two asks, a warehouse fire evacuates real actors from a real space.
The planner's answer changes — not because the demo scripted a branch,
but because a real LLM reasoned over a genuinely different world state
the second time. See [`expected_output.md`](expected_output.md) for an
actual captured run showing exactly this (`SocietyQuery` becomes
`SocialSourcing` after the fire).

## Design principles (why the code looks the way it does)

- **APIs build the world; prompts reason over it.** `bootstrap.py`
  only ever calls REST endpoints that create/mutate world state
  (geography, societies, actors, merchants, products). It never calls
  `/prompt`. `run_demo.py`'s prompt calls never create entities —
  `/prompt` only reasons over whatever `bootstrap.py` already built.
- **The planetary cycle evolves the world**, not the demo script. World
  mutation from the fire event propagates through a real
  `POST /planet/tick` call, not a Python function the demo calls
  directly.
- **No runtime internals.** Everything in `bootstrap.py`/`run_demo.py`
  is an `httpx` call to a live server. No imports from
  `src.monkey_brain.*`, no constructing `PlanetaryRuntime` in-process.
- **Society-Scoped Interactive Execution.** An interactive request
  (`/prompt`) executes within the smallest valid governance scope — the
  requesting actor's own effective societies — not a full planetary
  traversal. Space determines where the actor is; society determines
  who coordinates the request; the actor performs cognition.
  `/planet/tick` remains the only operation that advances the entire
  simulated world. `Execution Scope` in the transcript is the visible
  proof: 1 space / 1 society / 1 actor for this demo's single-society
  customer, not all 8 actors across 7 societies.

## Prerequisites

- The server running and healthy:
  ```bash
  WORLD_VALIDATION_GATE_EXECUTE=false WORLD_VALIDATION_GATE_SAVE=false \
    KEYSTORE_SECRET=<a Fernet key> \
    ./scripts/start_server.sh 8031 1 false info
  ```
  (`WORLD_VALIDATION_GATE_EXECUTE=false` isn't required for the demo
  itself — the demo's own world passes real validation — but this
  matches how this repo's dev server is normally run, and avoids the
  gate tripping on unrelated pre-existing state if you're running this
  against a non-fresh server.)
- A reachable LLM backend for the planner (Ollama locally, or whichever
  provider `MODEL_BACKEND` resolves to — see
  `src/monkey_brain/kernel/execute/provider/model_backend.py`). Prompt
  reasoning is real; there is no mock backend in this demo.
- Python deps: `httpx` (already in `pyproject.toml`'s dependencies).

**Run against a fresh environment for a clean, fast, reproducible
transcript.** A server that already has many actors from unrelated
prior activity will be genuinely slower (planetary cycle latency scales
with total actor count — see "What to expect" below) and may fail
world validation on pre-existing, unrelated data. If you need to reset:

```bash
./scripts/stop_server.sh
redis-cli FLUSHDB
mongosh --quiet --eval "db.getSiblingDB('agentos').dropDatabase()"
# then start_server.sh again
```

**Always restart the server as part of resetting — don't just flush
Redis/Mongo and reuse a still-running process.** Confirmed live: doing
that leaves the running server's in-memory actor/geography state stale
relative to the now-empty backing store, which reliably fails bootstrap
with `verify_world` reporting every actor `actor_without_presence` (see
`expected_output.md`'s notes). It looks like a server bug at first
glance; it isn't — it's this exact shortcut.

## Running it

```bash
cd demo/ecommerce
python3 run_demo.py
```

One command, as required. `bootstrap.py` can also be run standalone
(`python3 bootstrap.py`) if you just want to build the world without
the full narrated demo.

Set `DEMO_BASE_URL` to point at a non-default server
(default: `http://localhost:8031/api/v1/agentos`).

## What to expect (honestly)

This is not a fast demo, because it isn't faking anything. Two
`/prompt` calls each run a real LLM planning call plus real execution.
As of the Society-Scoped Interactive Execution fix, `/prompt` only
coordinates the requesting actor's own effective societies (see
`Execution Scope` in the transcript — 1 space / 1 society / 1 actor for
this demo's Alice), so round-trips are now dominated purely by real LLM
planning/execution latency (13-25 seconds each in captured runs) rather
than incidentally ticking the whole 8-actor world. `/planet/tick` is
the one operation that still coordinates every actor by design (50-63
seconds in captured runs, since it's genuinely ticking all of them) —
that cost scaling with total actor count is expected there, not a bug.
`run_demo.py` retries `/planet/tick` if it hits the server's own real
"cycle already running" response (a genuine, documented concurrency
behavior); it also retries a `/prompt` call whose response body
indicates the actor wasn't reached that time, and `LLMPlanner` itself
retries a malformed-JSON plan up to 3 times — all bounded retries
against real, observed transient conditions, not workarounds for a
deterministic bug.

Both prompts reach `Goal Achieved: True` with every action succeeding
— but not on the first several real attempts. Getting there surfaced
and required fixing four genuine production gaps (a dead-end in
`ProductSelection`'s planner-decision contract, plus missing
store/customer/rider data with no production API to supply it), two
LLM-echo edge cases in permission normalization, two transient
reliability issues addressed with bounded retries, and two
architectural issues in the interactive-request path itself: `/prompt`
was triggering a full-world planetary traversal instead of coordinating
only the requesting actor's own societies, and the planetary-cycle
distributed lock had no active release (a completed cycle still
blocked every other tick for up to 5.5 minutes) — the second issue was
latent until the first fix's speed made it visible. All real findings
from running this exact demo against a live server, not anticipated in
advance. The full account, with file references, is in
[`expected_output.md`](expected_output.md)'s "Reading this transcript
honestly" section — worth reading if you want to understand what
"backend-driven, no demo-only logic" actually surfaced in practice.

## What's real, not scripted

- **`Execution Scope` is measured, not asserted.** `spaces_coordinated`/
  `societies_coordinated`/`actors_coordinated` come from
  `execute_actor_request()`'s own real loop over the actor's effective
  societies (`src/monkey_brain/kernel/society/integration.py`) — the
  same numbers a production caller would see, not a demo-side count.
- **Product matches come from the actual catalog** `bootstrap.py`
  seeded via `POST /products` — the planner's `ProductSelection` step
  finds the real "Wireless Gaming Mouse" at $59.99, not a hardcoded
  answer.
- **The fire evacuates real actors** via the same evacuation logic any
  client triggering a `fire`-type event gets (`type` is one of a real,
  documented set: `fire`, `flood`, `structural_failure`,
  `security_incident`, `chemical_spill`, `power_failure`, `strike` —
  see `src/monkey_brain/api/routes/events.py`).
- **Metrics come from `GET /observability`**, the same Lemon-backed
  endpoint (Gate 5) used throughout this codebase's operational
  tooling — not a demo-local counter.

## Files

- `bootstrap.py` — builds geography (Planet → ... → Space), 7
  societies (one per demo role, each hosted at its own Space), 8
  actors, a merchant storefront with a real address, a 4-product
  catalog, a customer delivery address, and a delivery rider, then
  calls `POST /verify/world` and fails loudly if the world isn't valid.
- `run_demo.py` — the narrated end-to-end sequence described above.
  Imports and calls `bootstrap.py`'s `bootstrap_world()` as its first
  step; nothing about world construction is duplicated between the two
  files.
- `expected_output.md` — a real captured transcript, with honest notes
  on what varies run-to-run (exact LLM wording) versus what's
  structurally reproducible (the plan changing shape, real evacuation
  counts, real metrics).

## Mapping to the originally-sketched API list

The task this demo was built from sketched illustrative endpoint names
(`POST /planets`, `POST /countries`, `POST /prompt` with an `actor_id`
body field, etc.). The actual production API differs in specific,
verifiable ways — this demo uses the real ones:

| Sketched | Real |
|---|---|
| `POST /planets`, `/countries`, `/states`, ... | One generic `POST /planet/geo` with `entity_type` in the body (`planet`\|`country`\|`state`\|`county`\|`city`\|`street`\|`building`\|`space`) |
| `POST /societies/{id}/spaces` | `POST /planet/geo/{entity_id}/host` with `society_id` in the body (society hosts at a space, not the reverse) |
| `/prompt` with `actor_id` in the body | The acting actor comes from the `X-User-ID` header (the real auth dependency), not a body field |
| `/planetary/tick` | `/planet/tick` |
| `POST /customers` | Customers are `POST /actors` with `actor_type: "human"` — there's no separate customer entity type |

Every one of these was confirmed against the live server's OpenAPI
spec and route source before being used here, not assumed from the
original sketch.

Two more endpoints didn't exist at all until this demo's checkout flow
genuinely needed them and none did the job — added as real production
API, not demo-only logic:

| Need | Real endpoint added |
|---|---|
| A customer's delivery address in the commerce KG (a separate system from the society-scoped contact record `POST /actors/{id}/addresses` already wrote) | That same route extended to also write the KG entity `DeliveryCapability` reads |
| A delivery rider `DeliveryCapability` can assign | `POST /riders` (new — `kernel/domains/logistics.py::onboard_rider`) |
