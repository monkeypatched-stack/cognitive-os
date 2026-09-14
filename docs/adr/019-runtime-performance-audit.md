# ADR-019: Runtime Performance Audit — Where Planetary Cycle Latency Actually Goes

## Status

Accepted (measurement only — no optimizations implemented)

## Context

`docs/adr/016-performance-gate9.md` found the planetary cycle is
LLM-latency-bound and serial (`GeographicEntityRuntime.tick()` awaits
each present actor one at a time, no `asyncio.gather`), measuring
**~3.0–3.3s/actor** with 10 actors (30.1–32.6s total). Since then, the
same operation has grown to **>300s with 14 actors** — a live-observed
regression this session needed to explain with evidence, not
assumptions, before touching anything.

## Instrumentation Added

`time.perf_counter()` timing was added at two levels, all additive
(default-valued fields, zero behavior change):

**Cycle level** (`kernel/society/integration.py::_run_cycle`): wraps
world-reconciliation (simulated perturb + movement), the Perturbation
Queue drain/reconcile pass, Deja Vu, the scheduler (the entire
`GeographicEntityRuntime(...).tick()` call — which includes every
actor's own tick, since it's serial), and the post-tick cleanup/publish
loop.

**Actor/stage level**: fine timing was threaded through the existing
`state.metrics`/`context.metadata` dicts (the same mechanism
`execution_id`/`_planner_prompt` already use — no new plumbing
invented) from three files:
- `kernel/pipeline/llm_planner.py::plan()` — prompt construction, each
  LLM-call attempt, each parse attempt (per retry).
- `kernel/pipeline/belief_runtime.py::_generate_plan/_execute_plan` —
  grounding (`ContextConstructionEngine.build()`), total planner-call
  time, and `ActionExecutor`'s new `event_publish_ms` field
  (perturbation-publication sub-slice of Execute).
- `kernel/compile/cognitive_actor.py::_CognitiveTickResult` — new
  `stage_timings_ms` field, merging the fine timings above with the
  coarse per-stage buckets `run_stages()` (`kernel/pipeline/
  cognitive_policy.py`) already computed and previously discarded at
  this exact boundary (confirmed: `state.stage_durations` was already
  being thrown away here before this audit — a real, separate,
  pre-existing gap this incidentally fixed for free).

New module `kernel/society/cycle_performance.py` holds
`CyclePerformanceReport`/`ActorPerformanceReport` — pure data + text
formatting, no analysis logic (the analysis below is this document's,
not baked into the runtime).

**A real coverage gap found while measuring**: `_tick_present_actor`
(the per-actor timer for "Total Actor Time") never fired — every
`ActorPerformanceReport.total_ms` came back `0.0`. `GeographicEntityRuntime`
apparently ticks actors through a path other than the
`actor_ticker=self._tick_present_actor` callback in this configuration
(its own docstring already hedged this: "Only called for Actors
GeographicEntityRuntime hasn't already ticked this cycle via their home
Society's own society-wide tick()"). The fine per-stage timings
(grounding/LLM/parse/etc.) are unaffected — they're written from deep
inside the pipeline regardless of dispatch path, and are the real data
this report relies on — but "Total Actor Time" ground-truth and
"Runtime Overhead per actor via wall-clock cross-check" could not be
independently verified this run. `accounted_ms` (sum of every measured
stage) is used below as the total-per-actor figure instead; it is
provably a lower bound, not an exact wall-clock total.

**A second methodology caveat**: this measurement ran `PlanetaryRuntime`
constructed directly (`await pr._run_cycle()`), not through the full
app boot (`kernel.py`'s `Kernel.boot()`). The log shows `"Comparison
failed: ComparatorRuntime is not booted"` on every tick — the Compare
stage was a no-op throughout this run. This does not affect the
dominant costs measured (grounding, LLM, parse — all upstream of
Compare), but Compare/Learn numbers in this report are not
representative of the live server.

## Live Run

One real cycle, 16 registered actors (14 domain actors + 2 actors this
session's own earlier smoke-testing left registered), real Ollama
(`gemma3:latest`), `MODEL_BACKEND=ollama` confirmed loaded from `.env`
before any import. Total wall time: **410.76s (6:51)**.

### Per-Actor Breakdown (sorted by total cost)

```
Actor: Bay Area Central Warehouse
Grounding.................  1983.0 ms
Prompt Build...............   0.02 ms
LLM (1 call)............. 57753.0 ms
Response Parse..............1.02 ms
Predict/Decide/Act..........7.28 ms
Compare/Learn/Commit.........2.55 ms
Total (accounted)........ 59747.7 ms

Actor: Driver
Grounding.................  1875.0 ms
LLM (1 call)............. 45041.5 ms
Response Parse...............0.18 ms
Predict/Decide/Act...........5.92 ms
Compare/Learn/Commit.........4.06 ms
Total (accounted)........ 46926.8 ms

Actor: Jamal Rivera
Grounding.................  1902.4 ms
LLM (1 call)............. 40519.0 ms
Response Parse...............0.20 ms
Predict/Decide/Act...........7.11 ms
Compare/Learn/Commit.........3.16 ms
Total (accounted)........ 42432.7 ms

Actor: Priya Sharma
Grounding.................  1888.7 ms
LLM (2 calls)............ 35918.9 ms   <- 1 parse retry
Response Parse...............0.65 ms
Predict/Decide/Act..........26.03 ms
Compare/Learn/Commit........15.88 ms
Total (accounted)........ 37852.4 ms

Actor: Marcus Chen
Grounding.................  2266.2 ms
LLM (1 call)............. 33389.4 ms
Response Parse...............0.57 ms
Predict/Decide/Act...........6.0 ms
Compare/Learn/Commit.........3.54 ms
Total (accounted)........ 35670.0 ms

Actor: Alexandra Rodrigues
Grounding.................  1931.4 ms
LLM (1 call)............. 31491.0 ms
Response Parse...............0.12 ms
Predict/Decide/Act..........14.98 ms
Compare/Learn/Commit.........5.37 ms
Total (accounted)........ 33444.5 ms

Actor: Trader Joe's
Grounding.................  1855.7 ms
LLM (3 calls)............ 29040.0 ms   <- 3/3 parse attempts FAILED, empty plan
Response Parse...............0.90 ms
Predict/Decide/Act..........13.47 ms
Compare/Learn/Commit.........8.51 ms
Total (accounted)........ 30918.7 ms

Actor: Alice Nguyen
Grounding.................  2088.8 ms
LLM (1 call)............. 26631.0 ms
Response Parse...............0.07 ms
Predict/Decide/Act...........6.98 ms
Compare/Learn/Commit.........3.15 ms
Total (accounted)........ 28730.3 ms

Actor: Safeway
Grounding.................  1939.7 ms
LLM (1 call)............. 21948.9 ms
Response Parse...............0.16 ms
Predict/Decide/Act..........12.88 ms
Compare/Learn/Commit.........4.13 ms
Total (accounted)........ 23906.0 ms

--- 6 actors with NO goal set (Test CRUD Human/Enterprise, Whole Foods
    Market, Cognition Check Human, and this session's own 2 smoke-test
    actors): LLMPlanner.plan() returns an empty Plan immediately on an
    empty goal name, BEFORE building a prompt or calling the backend --
    but grounding already ran unconditionally before that check. ---

Actor: Whole Foods Market       Grounding 1893.1 ms, LLM 0 (no goal), Total 1893.7 ms
Actor: Test CRUD Enterprise     Grounding 1749.0 ms, LLM 0 (no goal), Total 1749.8 ms
Actor: Cognition Check Human    Grounding 1690.3 ms, LLM 0 (no goal), Total 1690.9 ms
Actor: Test CRUD Human          Grounding 1683.9 ms, LLM 0 (no goal), Total 1684.6 ms
Actor: Smoke Test Actor 2       Grounding  286.1 ms, LLM 0 (no goal), Total  287.0 ms
Actor: Smoke Test Actor         Grounding  247.0 ms, LLM 0 (no goal), Total  247.7 ms

Actor: Warehouse Worker          FAILED — "Belief formation timed out after 60s"
                                  (Cognitive tick failed: Timed out after 60.0s)
                                  Contributes ~60,000 ms to the cycle with
                                  ZERO recorded stage data (the 60s cognitive-
                                  tick cap fired before/during its LLM call).
```

(Raw per-field JSON for all 16 actors: `/tmp/perf_audit_raw.json`, not
committed — reproducible any time via the same script pattern, see
Reproduction below.)

### Aggregate Statistics

```
Planetary Cycle

Actors Scheduled: 16
Actors Executed: 15          (1 hard-failed: Warehouse Worker, 60s timeout)
Actors Actually Changed: 15  (belief_updated=True for every actor that ticked)
LLM Calls: 12                (9 actors × 1 call, 1 actor × 2 calls, 1 actor × 3 calls;
                               6 actors made 0 calls -- no goal set)

Scheduler..................... 409893.6 ms  (99.8% of cycle -- includes ALL actor work,
                                              the loop is serial, not parallel)
Perturbation Queue............      0.0 ms  (queue was empty this run)
World Reconciliation..........      0.0 ms  (simulated-noise perturb, negligible)
Deja Vu........................     0.0 ms  (never triggered -- no queued perturbations)
Cleanup........................    16.8 ms
Runtime Overhead...............  89030.9 ms  (21.7% of cycle)
Total LLM Time................. 321732.7 ms  (78.3% of cycle)
Total Cycle..................... 410763.5 ms
```

### Scheduling Analysis

- **16 actors scheduled**, all 16 physically present and eligible.
- **15 actually needed reasoning** in the sense that the pipeline ran
  for them; of those, **only 9 actually had a goal** to reason about.
  The other 6 (37.5% of scheduled actors) are structurally guaranteed
  to produce an empty plan every cycle (no `goal.name`) — real accounts
  left over from earlier CRUD/smoke testing, not part of the domain
  scenario.
- **12 LLM calls occurred** across those 9 goal-bearing actors (3
  actors needed a parse retry: Priya 2 calls, Trader Joe's 3 calls and
  still failed to parse valid JSON on all 3).
- **"Actors Actually Changed" (15) is not a meaningful signal here** —
  `belief_updated` is unconditionally `True` in `_CognitiveTickResult`
  (`cognitive_actor.py:811`, hardcoded, not derived from whether
  anything changed), so this number cannot currently distinguish a real
  belief change from a no-op tick. This is itself a finding: the
  scheduling-efficiency question the audit was asked to answer
  ("actors actually changed" vs. "actors that did nothing") **cannot be
  answered from `_CognitiveTickResult` as it exists today** — the field
  needed for it always reads `True`.
- **Could unaffected actors have been skipped?** Yes, for the 6
  goalless actors specifically: grounding (`ContextConstructionEngine.
  build()`, average ~1.4s each, ~9.1s combined) runs unconditionally
  in `_generate_plan` *before* `LLMPlanner.plan()`'s own goal-emptiness
  check ever runs. That check could gate grounding too, not just the
  LLM call, for a real (if modest — ~2.2% of this cycle) savings. This
  is listed under Recommendations, not implemented.

### Runtime Efficiency

```
Runtime Overhead = Total Cycle − Sum(LLM Time)
                  = 410763.5 ms − 321732.7 ms
                  = 89030.9 ms  (21.7%)
```

Percentage of total cycle time:

| Category | ms | % of cycle |
|---|---:|---:|
| LLM inference | 321,732.7 | 78.3% |
| Runtime overhead (all non-LLM work) | 89,030.9 | 21.7% |
| — of which: one actor's 60s hard timeout | ~60,000.0 | 14.6% |
| — of which: grounding/RAG (all 15 actors) | 25,280.7 | 6.2% |
| — of which: prompt build + parse + predict + decide + act + observe-outcome + compare + learn + compile-Φ + commit (all 15 actors) | ~169.4 | 0.04% |
| — of which: scheduling dispatch overhead (inter-actor loop, cleanup) | ~3,580.7 | 0.9% |
| World updates (perturbation queue + world reconciliation + Deja Vu) | 0.04 | ~0.00001% |
| Synchronization (locks) | not exercised | n/a — see note |

**Synchronization note**: this run called `_run_cycle()` directly, not
through `cycle()`, so it never touched `_tick_lock` or the Redis
distributed lock at all — synchronization cost is genuinely absent
from this measurement, not merely small. It would only appear under
real concurrent load (an auto-tick and a manual `/planet/tick` racing,
which this session's earlier robustness audit — see the lock-ownership/
fail-open fixes — already showed does happen in practice).

**World updates were near-zero because the queue was empty this run**,
not because that subsystem is inherently free — with real perturbations
queued (the `/planet/perturbations` route, or `ReportWorldPerturbation`
capability calls), this cost would include however long `replay_
affected_actors` (Deja Vu) takes, which itself calls the LLM planner
per affected actor — i.e., it inherits the same LLM-latency profile
measured above, not a separate cheap path.

### Identify the Bottleneck

**C — LLM inference dominates runtime.**

78.3% of total cycle time is spent inside `ModelBackend.complete()`
waiting on Ollama, directly measured, not inferred. Every actor that
reached the LLM took between 21.9s and 57.8s for a *single* call — 6.6x
to 17.5x slower than ADR-016's per-actor budget ceiling (`max_ms:
10000`) for the *entire* tick, let alone one call within it. This is
not close to any other category: the next largest cost (a single hard
60s timeout) is itself fundamentally an LLM-latency problem (the
pipeline waited on a call that didn't return in time), and grounding —
the only genuinely separate, non-LLM cost — is 6.2%, more than 12x
smaller than LLM time.

Two secondary, real, independently-fixable factors compound the
primary bottleneck without displacing it as the cause:
- **Serial scheduling amplifies it**: `scheduler_ms` (409,893.6ms) is
  99.8% of the cycle because every actor's LLM wait is summed serially,
  confirming ADR-016's finding still holds architecturally — the
  regression is in *how slow each LLM call now is* (see below), not in
  a newly-introduced scheduling inefficiency.
- **Grounding runs before it's known to be needed** (the 6-goalless-
  actor finding above) — small in absolute terms (6.2%) but 100%
  avoidable for those actors specifically.

### The ADR-016 → Now Regression, Quantified

| | ADR-016 (10 actors) | This audit (16 actors, 9 with a goal) |
|---|---:|---:|
| Per-actor cost | ~3.0–3.3s | median 33.4s (goal-bearing actors only) |
| Fastest observed | ~3.0s | 21.9s |
| Slowest observed | ~3.3s | 57.8s |
| Regression factor | — | **~10x at the median, ~17.5x at the worst case** |

This audit cannot fully explain *why* per-call latency grew ~10x since
ADR-016 without a controlled before/after comparison (same model, same
prompt) — that wasn't run. The most plausible, evidence-consistent
hypothesis, not confirmed here: this session's own testing has added
substantially more world state (purchase logs, warehouse-fire events,
transaction history, affiliation records) that `ContextConstructionEngine`
folds into `relevant_knowledge`/grounding facts, which `LLMPlanner.
_build_prompt()` renders into the prompt — a longer prompt directly
costs more local-inference time. `llm_planner.py` already records
`_prompt_tokens` per call (`context.metadata["_prompt_tokens"]`) but
this audit did not capture it into the timing report; doing so would
let a follow-up confirm or rule this out directly rather than guessing
from a token count nobody looked at this run.

## Recommendations

**Not implemented — this gate is measurement only, per its own
instructions.** In descending order of expected impact, given the
evidence above:

1. **Parallel actor execution.** ADR-016 already identified the serial
   `await` loop; this audit confirms the multiplier effect is now much
   larger (17.5x/actor regression → the same serial-sum architecture
   turns a already-large per-actor cost into a cycle time that reliably
   exceeds the 300s auto-tick interval). `asyncio.gather` over
   independent actors is the direct fix ADR-016 already scoped as "real
   but out of that gate's boundary" — still true here.
2. **Fix (or bound) the `asyncio.to_thread` cancellation gap.** The
   Warehouse Worker's 60s timeout very likely did not actually stop its
   underlying LLM call — a `"planning failed after 3 attempts"` log
   line appears in this run's log with no actor attributable to it in
   the collected data, timing-correlated with the timeout, consistent
   with the orphaned-background-thread behavior this session's separate
   lock/robustness audit already predicted theoretically for `asyncio.
   to_thread`-wrapped blocking calls. A real, load-bearing thread can
   keep running (and consuming a thread-pool slot, plus eventually
   mutating a `state`/`context` object nothing reads anymore) well past
   its own logical deadline.
3. **Skip unaffected/goalless actors before grounding**, not just
   before the LLM call — move (or duplicate, cheaply) `LLMPlanner.
   plan()`'s existing empty-goal short-circuit up into `_generate_plan`,
   ahead of `context_engine.build()`. Small (~2.2% of this cycle) but
   free and zero-risk relative to the other items here.
4. **Investigate the per-call latency regression directly** — capture
   `_prompt_tokens` into future timing reports (the field already
   exists, just wasn't wired into this one), and run one controlled
   comparison (fixed prompt, fixed model, ADR-016-era prompt size vs.
   today's) to confirm or rule out prompt growth as the ~10x cause
   before assuming it.
5. **A tighter per-actor timeout with a genuine cooperative-cancellation
   contract**, rather than the current 60s wrapper that (per #2) may
   not actually stop anything — lowering the wrapper's number alone
   would not help if the underlying work keeps running regardless.
6. **Faster model / smaller model for routine ticks**, reserving the
   current model for cases that need it — not evaluated here (would
   need its own accuracy/behavior comparison, out of scope for a
   latency-only audit).
7. **Ollama batching / true async inference** — would remove the
   thread-pool-exhaustion risk this session's lock/robustness audit
   flagged (repeated timeouts piling up orphaned worker threads), but
   is an infrastructure change (a different Ollama deployment mode or
   client), not a code change in this repo alone.

Deliberately **not** recommending "incremental planetary cycles" or
"Deja Vu pruning" as high-priority here: Deja Vu contributed 0ms this
run (queue was empty) and, per the World Updates note above, would
inherit the same LLM-bound cost as regular ticks whenever it does fire
— pruning it wouldn't address the actual bottleneck.

## Reproduction

```python
# Must load .env BEFORE importing model_backend.py (MODEL_BACKEND is
# read at import time) -- see kernel/kernel.py's own boot sequence.
from dotenv import load_dotenv

load_dotenv(".env")
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
import asyncio

pr = PlanetaryRuntime()
result = asyncio.run(pr._run_cycle())  # bypasses cycle()'s 300s wait_for cap
report = pr._last_cycle_report  # CyclePerformanceReport
print(report.format_summary())
for actor in report.actors:
    print(actor.format())
```

Safe to re-run anytime, same caveats as `scripts/gate9_benchmark.py`
category 4: issues real actor ticks (state-advancing, not read-only),
so treat it the same as a real `/planet/tick` — not a load-generating
loop, but not side-effect-free either.

## Consequences

- The regression is now measured, not assumed: **LLM inference is
  78.3% of cycle time, directly, with per-call latency ~10x worse at
  the median than ADR-016's baseline** — the serial-scheduling
  architecture ADR-016 already flagged is what turns that per-call cost
  into a cycle time that blows through the 300s auto-tick budget, not
  a new inefficiency of its own.
- Two real, previously-unknown gaps surfaced as a side effect of
  instrumenting: `state.stage_durations` was already being computed
  and silently discarded at the `_cognitive_tick()` → `_CognitiveTickResult`
  boundary (now fixed, for free, as part of this audit's plumbing);
  and `belief_updated` in `_CognitiveTickResult` is hardcoded `True`,
  making "did this actor actually change anything" unanswerable from
  that field as it stands today.
- A live, timing-correlated instance of orphaned `asyncio.to_thread`
  work outliving its own cancellation (predicted theoretically in this
  session's separate lock/robustness audit) was observed in this same
  run, strengthening that earlier finding with real evidence rather
  than leaving it as a code-reading inference.
- No code path's behavior changed. Every new field defaults to a
  neutral value (`0.0`/`{}`); every new timer is a pure `perf_counter()`
  wrap around an existing call, not a restructuring of it.

## Addendum: Recommendation #1 Implemented and Verified

Recommendation #1 (parallel actor execution) was implemented:
`GeographicEntityRuntime.tick()`'s per-occupant loop
(`kernel/geography/runtime.py`) now runs every present actor's tick —
plus its temporary-membership reconciliation and lookup, in the same
relative order as before — concurrently via `asyncio.gather`, instead
of a serial `for occupant_id in ...: await self._actor_ticker(...)`.
Extracted into a local `_tick_occupant` closure with identical
branch-for-branch logic to the original (same dedup via
`ticked_actor_ids`, same exception isolation per actor, same
`actors_total`/`active_actor_ids` counting) — safe to parallelize
because every occupant of one Space is physically present in exactly
one place at a time (this module's own invariant), so no two
concurrently-running occupant tasks ever write to the same
`ticked_actor_ids`/`temporary_memberships`/`effective_memberships`
entry; `ticked_actor_ids` itself is only *read* during the gather (the
society-wide bulk-tick pass that populates it already finished before
this loop starts), and adding each occupant's own id happens in the
caller after `gather()` returns, not inside the concurrently-running
coroutines. Sibling-subtree recursion (`children_of`) was deliberately
left serial — a separate, larger question this fix wasn't scoped to
answer.

**Verified live**, same methodology, same 16-actor world, direct
`_run_cycle()` call bypassing the 300s cap:

| | Before (serial) | After (`asyncio.gather`) |
|---|---:|---:|
| Total Cycle | 410.8s | 290.9s |
| Total LLM Time | 321.7s | 200.4s |
| Actors Scheduled / Executed | 16 / 15 | 16 / 15 |
| LLM Calls | 12 | 11 |

**~119.9s saved, a 29.2% reduction (1.41x speedup)** in one direct
before/after comparison. `lsof -i :11434` during the run confirmed 2
simultaneous established connections to Ollama — the concurrency is
real at the client/request level, not just in the code.

**This is a real but partial win, and the gap from "partial" to
"dramatic" is honestly attributable, not mysterious**: if the 9 actors
that made real LLM calls this run had executed in true, unconstrained
parallel, total cycle time would track the *slowest single actor*
(~30s, Trader Joe's 3-attempt retry) plus grounding/overhead — call it
35–40s — not 290.9s. The actual result sits much closer to the fully
serial baseline than to that theoretical floor, which means **Ollama
itself is not processing these concurrent requests in true parallel** —
consistent with `.env`'s `OLLAMA_NUM_PARALLEL=4` not actually being
present in the running `Ollama.app` process's own environment when
checked directly (`ps eww -p <pid>` showed no such variable — the app
was launched via macOS Launch Services, which does not inherit this
repo's `.env`). The code-level bottleneck this ADR identified is fixed;
the next one in line is the LLM backend's own concurrent-request
handling capacity, which is an Ollama deployment/configuration
question, not a code change in this repository.

**One measurement-noise caveat, stated plainly**: individual actors'
LLM-call times varied meaningfully between the two runs even for
identical goals/prompts (e.g. Marcus Chen: 33.4s → 18.3s, Alice Nguyen:
26.6s → 22.1s) — real local-inference variance, not something this fix
controls. The 29.2%/1.41x figures come from one direct comparison, not
a percentile over repeated runs (matching ADR-016's own stated reason
for not looping this exact benchmark — it's a real, state-advancing
operation, not a free synthetic one to run repeatedly). Treat the
*direction and rough magnitude* (real, positive, well short of
Ollama's serial-processing floor) as the reliable takeaway, not the
decimal points.

**Which actor hit the 60s timeout also changed between runs**
(Warehouse Worker → Bay Area Central Warehouse) — further evidence this
is resource contention under concurrent load, not a defect in a
specific actor's own data/prompt.

No new error types appeared in the verification run's log — the same
two pre-existing categories recurred (a JSON parse failure exhausting
all 3 retries for one actor, one hard 60s cognitive-tick timeout for
another), just distributed differently across actors, as expected once
they're genuinely contending for the same backend concurrently instead
of queueing behind each other in the application code.
