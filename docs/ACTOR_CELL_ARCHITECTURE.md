<title>Isolated Actor Cell Architecture — Planning Document</title>

Status: **planning only** — no production code changed. Every claim below is
grounded in a file:line citation gathered from the current repository; where
I could not fully verify something, it's marked "unverified" rather than
assumed.

---

## A. Current architecture (as discovered)

There is no "Actor Cell" object today. What exists instead:

- **`PlanetaryRuntime`** (`kernel/society/integration.py`, 6,228 lines) owns a
  large amount of **shared, process-wide infrastructure**: one Redis client
  (`self._redis`), one `KnowledgeGraph` (`self._knowledge_graph`), one
  `MemoryManager` (`self._memory_manager`), one `ContextConstructionEngine`
  (`self._context_engine`), one execution engine (`self._execution_engine`,
  shared by every `SocietyRuntime` via `_attach_society`), one `SharedWorld`.
  None of these are actor-partitioned by construction — actor scoping, where
  it exists, is enforced by callers passing `actor_id` into otherwise-generic
  APIs, not by the infrastructure itself.
- **`ActorRuntimeState`** (`kernel/society/runtime.py:52`) is the per-actor
  record held inside a `SocietyRuntime` — it carries `belief_state`,
  `profile`, `status`, `actor_runtime`, etc. This is genuinely one object per
  actor, but it's a plain record, not an isolated runtime.
- **`CognitiveActor`** (`kernel/compile/cognitive_actor.py:94-230`) is the
  **closest existing thing to an Actor Cell** — it already holds
  per-instance `belief` (a `SparseTransitionTensor`), `policy =
  PolicyStore(owner_id=entity_id)`, `_goal_queue`/`_current_goal`,
  `_cognitive_state`, and — notably — **its own per-actor `KnowledgeGraph`**
  (`self._knowledge_graph = KnowledgeGraph(person_id=entity_id)`, line 193).
  This per-actor KG exists in the code today and is **not used** by the
  domain capability layer, which instead reads `PlanetaryRuntime`'s one
  shared KG (see Section C, item 4, and the confirmed leak below).
- **`ActorRuntime`** (`src/monkey_brain/actor_runtime.py`) is the per-actor
  *process* entrypoint for the edge/device/robot deployment model — one Pod
  hosts exactly one actor (`ACTOR_NODE_CAPACITY=1` convention). This is
  process-level isolation by deployment convention, not by architecture: the
  same `PlanetaryRuntime` class boots identically whether it's the
  multi-actor cloud control plane or a single-actor edge Pod (confirmed in
  last session's edge-first work).

## B. Actor Cell definition (target)

An Actor Cell owns, exclusively:

| Owned by the Cell | Current state |
|---|---|
| Identity | **Gap** — today's `WorkloadIdentity` is process-scoped (Section J) |
| Runtime | `CognitiveActor` instance — already actor-scoped |
| Beliefs / BeliefState | Already actor-scoped (`kernel/society/belief.py`) |
| Memory (private) | Backing store is shared (Redis vector index + shared KG); access is actor-scoped by default, with an explicit, gated `visibility="shared"` exception (Section C, item 2) |
| Context (per-tick grounding) | Built fresh per call by a shared `ContextConstructionEngine`, not persisted — no leak, but not "owned" by the cell either |
| Perception / observations | Per-actor filtered view (`ObservationProvider.observe(actor_id, filt)`) over the one `SharedWorld` |
| Working memory | **Gap** — `MemoryManager.working_memory` is task_id-keyed on the one shared `MemoryManager` instance, not actor-keyed |
| Execution state | Not persisted anywhere today — `ActionExecutor` is shared and stateless, all actor data flows through a per-call `context` dict |
| Tool/capability state | Shared singleton capability instances, but genuinely stateless (verified: no per-actor instance attributes found) — low risk as-is |
| Local persistence | `EdgeActorStateStore`/`ActorStateStore` already key by `(actor_id, tenant_id)` — actor-scoped |
| Queues | NATS inbox + Redis list already actor_id-keyed — correctly isolated |
| Caches | Mixed — see isolation matrix (D); `semantic_cache.py` is the one unscoped exception |
| Credentials / runtime identity | **Gap** — see Section J |
| ROS interface | One-actor-one-process by convention; no shared ROS state found |

## C. Actor state inventory

| State | Current location | Owner today | Persistence | Sharing today | Target Cell location | Cloud sync |
|---|---|---|---|---|---|---|
| Identity | `WorkloadIdentity` (process SVID) | Process/Pod | SPIRE-managed | **Shared across every actor a process hosts** | Per-actor SPIFFE identity | Federation only |
| BeliefState | `ActorRuntimeState.belief_state` | Actor | `ActorStateStore`/`EdgeActorStateStore`, key `(actor_id, tenant_id)` | None — correctly isolated | Cell (already is) | Async checkpoint |
| Episodic/semantic memory (private) | `MemoryManager` (shared instance), Redis vector index + shared KG | Shared instance, per-record `actor_id` | Redis / KnowledgeGraph | Private by default; explicit `visibility="shared"` + co-membership gate for cross-actor read (`context_engine.py:386-413`) | Cell for private records; the shared/gated subset stays a Society-mediated exchange, not raw sharing | The gated "shared" subset is the sync surface |
| Working memory | `MemoryManager.working_memory: dict[task_id, MemoryNode]` | Shared instance, task_id-keyed (**not** actor_id-keyed) | In-memory only | **Unscoped by actor** — a task_id collision across actors is possible | Cell | None (ephemeral) |
| Context/grounding | `ContextConstructionEngine.build(actor_id, goal, ...)` | Shared instance, stateless per-call | Not persisted | No leak found (per-call, not cached) | N/A — fine as shared+stateless, or trivially become a Cell-local method | None |
| Perception/observations | `ObservationProvider.observe(actor_id, filt)` over `SharedWorld` | Per-actor filtered view | Not persisted | Was leaking `EpisodicTrace`/event entities into every actor's belief; now filtered (`pipeline/observations.py:171,191`) | Cell (the filtered view); `SharedWorld` itself stays cloud | Continuous (it's a live view) |
| Preference/rejection state | `kg entity id=f"pref_reject_{type_keyword}"` (`grocery.py:805-840`) | Shared KG, **no actor_id in key at all** | KnowledgeGraph | **Confirmed real leak**: one actor's rejected-product keywords silently become every other actor's filter (`get_rejected_keywords`, used at `grocery.py:4182,4738,5172,7313`) despite the docstring claiming this is "personal to the actor" | Cell | None needed — this was never meant to be shared |
| Goals / current plan | `CognitiveActor._goal_queue`/`_current_goal` | Actor (CognitiveActor instance) | `current_plan_store.py` (goal-scoped, per actor per memory notes) | None found | Cell (already is) | Commitment reporting only |
| Execution state (in-flight action) | Transient, inside `ActionExecutor.execute()`'s local scope | N/A — never persisted | None | N/A (no queue exists at all, per-actor or otherwise) | Cell, if ever persisted | Result reporting only |
| Tool/capability instances | One shared singleton per capability class (`build_default_capability_bus()`) | Shared, process-global | None (stateless) | Shared object, but verified no actor-keyed instance attributes | Stays shared infra — safe as-is | N/A |
| Inbox / message queue | Redis list `monkeybrain:messages:{actor_id}` + NATS subject `monkeybrain.actor.{actor_id}.inbox` | Actor | Redis | None — correctly keyed | Cell (already is) | N/A (already the sync surface) |
| Lease / fence / desired-state | `monkeybrain:actor:{lease,fence,desired_state}:{actor_id}` | Actor, but semantically cloud-scheduler-owned | Redis | None — correctly keyed per actor | Stays cloud (scheduler-owned, cross-Pod coordination) | N/A |
| context_cache (edge) | `kernel/edge/context_cache.py` | Keyed by `actor_id + goal_hash + ...` | `EdgeLocalStore` (SQLite) | Actor-scoped already | Cell | N/A |
| semantic_cache (edge) | `kernel/edge/semantic_cache.py` | Keyed by `query|cycle_id` — **no actor_id** | `EdgeLocalStore` | **Unscoped** — low-severity today (query text is domain content, not secret), but structurally shared | Cell (needs actor_id added to the key) | N/A |
| policy_cache / delegation_cache (edge) | `kernel/edge/policy_cache.py`, `delegation_cache.py` | Keyed by `principal`/`authenticated_delegate` | `EdgeLocalStore` | Principal-scoped — effectively actor-scoped already | Cell | Cloud-issued, versioned |
| Per-actor KnowledgeGraph | `CognitiveActor._knowledge_graph = KnowledgeGraph(person_id=entity_id)` | Actor — **exists but is unused** | None (in-memory only, never persisted or read by domain capabilities) | N/A — dead code today | Cell (this is exactly the shape a Cell's private KG should have — it just needs to become the one domain capabilities actually read from for actor-private facts) | None needed if truly private |
| ROS interface | `kernel/edge/ros_integration.py::RosExecutionAdapter` | One per Pod (via `build_ros_execution_adapter()`) | N/A | One-actor-one-process by convention, no shared ROS state found | Cell | Execution results only |

## D. Isolation matrix

| Resource | Shared? | Actor-scoped? | Cloud-owned? | Edge-local? | Persistent? |
|---|---|---|---|---|---|
| Society | Yes (by design) | No | Yes | No | Yes |
| World (`SharedWorld`) | Yes (by design) | No | Yes | Filtered view only | Yes |
| Presence | Yes (by design) | Per-actor entries, shared timeline | Yes | No | Yes |
| Policies / signed snapshots | Cloud-issued | Cached per-principal at edge | Yes (authoritative) | Cached copy | Yes (edge cache) |
| Approvals | Cloud-issued | Per-request | Yes | Locally-evaluated cache only | Yes |
| Delegations | Cryptographic, portable | Per-delegate | Verification is stateless | Yes (no round-trip needed) | N/A |
| Plans / Commitments | — | Per-actor (`CognitiveActor._goal_queue`) | Reporting only | Actor-local during execution | Not durably stored today |
| Product Catalog | Yes (by design) | No | Yes | Cached copy | Yes |
| BeliefState | No | **Yes, correctly isolated** | No | Yes | Yes |
| Episodic memory (private) | Backing store shared; access gated | Yes, by default | No | Could be | Yes |
| Episodic memory (`visibility="shared"`) | Yes, deliberately | Cross-actor by explicit opt-in + co-membership | No | Could be | Yes |
| Working memory | **Yes — unscoped bug risk** | No (task_id-keyed, not actor_id) | No | Should be | No |
| Preference/rejection state | **Yes — confirmed leak** | Should be, isn't | No | Should be | Yes (in shared KG) |
| Tool/capability instances | Yes | N/A (stateless) | No | N/A | N/A |
| Inbox/queue | No | Yes, correctly | No | Should be | Yes |
| Workload identity | **Yes — process-scoped** | Should be actor-scoped, isn't | Federation trust root only | Should be per-cell | N/A |
| Internal service token | **Yes — one shared secret for all actors** | Should be, isn't | N/A | N/A | N/A |

## E. Actor ↔ Society interface

`PlanetaryRuntime.execute_actor_request(actor_id, prompt_request)` (`integration.py:4425`) is the real boundary today: resolve space → resolve societies → reserve a per-cycle lock → `_run_actor_tick` → `_finalize_actor_execution` (which is where world/coordination updates happen). The pieces an Actor Cell should call through an explicit interface, mapped to what exists:

```
request world context   → ObservationProvider.observe(actor_id, filt)  [exists]
request policy           → LocalGovernanceEvaluator.evaluate(...) / EdgePolicyCache  [exists, from edge-first work]
submit approval request  → ensure_governed(...) escalation path  [exists]
receive commitment       → ActionOutcome from ActionExecutor.execute()  [exists]
query catalog            → KnowledgeGraph.entities_by_type(ASSET)  [exists, but unscoped access — see D]
report observations      → context_stream.publish(WORLD_UPDATE, ...)  [exists]
report execution result  → _finalize_actor_execution  [exists]
```

None of this needs new plumbing to *express* as a formal interface — it needs the underlying shared reads (KG, working memory) to actually be actor-scoped so the interface's promises hold.

## F. Actor ↔ World interface (World vs. Belief)

**This distinction already exists and is correctly modeled.** `SharedWorld` (`kernel/society/world.py:381`) is the one authoritative, cloud-owned object "all actors observe." `BeliefState` (`kernel/society/belief.py`) is explicitly documented as subjective and non-shared ("No actor should directly modify another actor's beliefs"). The `ObservationProvider` is the sanctioned bridge: it reads `SharedWorld`, applies a per-actor filter, and hands the result to that actor's own belief-fusion step (`BeliefFusion.fuse()`, called per-actor, never merges across actors). The one historical leak here (`EpisodicTrace`/event entities bleeding into every actor's observed world) was already found and patched (`pipeline/observations.py:171,191`) — worth citing as precedent for how the *new* leak (rejection preferences, item C) should be fixed the same way.

## G. Actor ↔ Policy/Approval/Delegation interface (authority model)

From the edge-first work already merged this session: `LocalGovernanceEvaluator.evaluate()` (`kernel/edge/local_governance.py`) only ever returns `allowed=True` for a cached, Ed25519-verified, epoch-current, unexpired `AUTO_APPROVE` snapshot scoped to `(principal, action, resource)`; everything else escalates to the cloud's real `ensure_governed`/OPA decision. `HUMAN_APPROVAL_REQUIRED` can never be satisfied locally. Delegation chains are independently re-verified at the point of use (`kernel/delegation.py::verify_delegation_chain`), never trusted because a message boundary already checked them. This model already correctly distinguishes "the Actor can request/cache authority" from "the Actor IS the authority" — the Actor Cell's job is to hold the *cache and the crypto verification*, never the decision itself.

## H. Actor ↔ Catalog interface

Today: any capability calls `kg.entities_by_type(EntityType.ASSET)` directly on the one shared `PlanetaryRuntime.knowledge_graph` — no actor-scoping exists or is needed for the catalog itself (products/stores are legitimately global, not actor-private), but this is the SAME shared KG object that also holds actor-private memory/preference entities with no type-level separation between "public catalog" and "actor-private fact" beyond attribute duck-typing. The Actor Cell should read catalog data through a narrower query (already namespaced this session as `kg_entity`/`kg_relationship` for edge-local caching) that cannot also return another actor's private KG entities — today it structurally can (see the confirmed leak).

## I. Actor ↔ ROS interface

Already well-designed and already tested (`kernel/edge/ros_integration.py`, confirmed working via `tests/scenarios/test_edge_disconnected_operation.py::test_ros_execution_works_offline` last session): a ROS-backed capability is registered on the same `CapabilityBus` as every other capability and reached through the same `ensure_governed` boundary — `run_ros_action_if_governed()` never calls `adapter.invoke()` except as `ensure_governed`'s own `effect` callable, so governance always runs first. Since a real deployment is one actor per Pod, ROS isolation is currently achieved by **process boundary**, not by any explicit actor-check inside the adapter itself — worth flagging: if two actors were ever co-resident in one process with two ROS adapters, nothing today would stop actor A's committed plan from being handed adapter B's `RosExecutionAdapter` instance by a wiring mistake (no runtime assertion ties an adapter instance to a specific actor_id).

## J. Actor identity model

**This is the largest real gap.** `WorkloadIdentityProvider.get_current_identity()` (`kernel/workload_identity.py:141-172`) fetches one SVID for "THIS process" via the SPIRE Workload API socket — there is no `actor_id` parameter anywhere in that path. A helper `agent_spiffe_id(agent_id)` (`workload_identity.py:63-75`) constructs the URI shape `spiffe://<trust-domain>/agent/<agent_id>` but **has zero callers in the entire codebase** — it's an unused, aspirational naming convention, not a wired minting path. `evidence_from_spiffe()` binds `principal_id = identity.spiffe_id`, i.e. whatever the *process's* SVID says, not an actor identity. Compounding this: `require_internal_service_token` (`api/internal_auth.py:36-50`) checks one shared secret valid for *any* `actor_id` a process hosts — there is no cryptographic binding today between "who is calling" and "which actor they're allowed to act as." In the current one-actor-per-Pod deployment model this is masked (the process's identity happens to correspond to exactly one actor by convention), but it is not enforced by identity itself, and would not hold if two actors ever shared a process.

**Design decision to make later (not now):** whether per-actor identity should come from (a) a distinct SPIRE registration entry per actor_id (a real per-actor SVID, requiring SPIRE server-side selector/entry work), or (b) a process-level SVID plus a *second*, actor-bound credential layered on top (e.g. a short-lived, cell-issued token scoped to one actor_id, checked in addition to the process-level SPIFFE identity). Both are legitimate; this doc does not pick one.

## K. Actor lifecycle

The scheduling/lifecycle machinery already exists and is reasonably mature — `ActorScheduler`/`ActorLifecycleController` (`kernel/society/actor_scheduler.py`, `actor_lifecycle_controller.py`), lease/fence/desired-state Redis keys (all actor_id-scoped, confirmed), `migrate_actor()`, `suspend_actor_for_migration()`. Mapping the user's requested lifecycle onto what exists:

```
Cloud registers Actor         → register_actor() / Actor Registry           [exists]
Actor assigned to edge        → set_actor_desired_node()                    [exists]
Actor Cell provisioned        → GAP — no "cell" concept exists yet to provision
Actor identity provisioned    → GAP — see J
Actor-local state initialized → _load_actors() (loads ALL actors today, or
                                  one when ACTOR_ID scopes it)              [exists, partially]
Actor starts                  → ActorRuntime.start() (actor_runtime.py)     [exists]
Actor receives Society context→ ObservationProvider.observe()               [exists]
Actor reasons/executes        → CognitiveActor tick → ActionExecutor        [exists]
Actor reports state/results   → _finalize_actor_execution / context_stream  [exists]
```

Restart/migration/revocation: `_do_migrate_away` (checkpoint + suspend, confirmed last session to correctly evacuate an actor from its old node), lease fencing prevents split-brain double-execution. Credential rotation and actor deletion have no actor-cell-specific design yet — today "deletion" is `unregister_actor()` (removes from `SocietyRuntime`), with no equivalent "destroy this actor's local cell state" step, since no isolated cell storage exists yet to destroy.

## L. Actor migration between edge nodes

Given the current design (`migrate_actor()` → `suspend_actor_for_migration()` → target node's reconcile loop resumes from checkpoint):

- **Moves (already checkpointed today):** `BeliefState` (via `checkpoint_actor_belief`/`ActorStateStore`).
- **Should move (once a real Cell exists):** the per-actor `CognitiveActor._knowledge_graph` (currently unused, so currently nothing to move), working memory (if it becomes actor-scoped), local cache entries (`context_cache`, `semantic_cache` once actor-scoped).
- **Reconstructed from cloud on the new node, never moved:** Society membership, World observations (re-fetched fresh), Policy/approval cache (re-synced from the control plane's current epoch, never carried forward stale), Product Catalog.
- **Genuinely undecided (flag for the actual design phase):** whether the private "shared-visibility" memory subset (Section C item 2) re-syncs from a cloud copy of record, or whether the edge was ever the sole copy — needs the sync-of-record question answered before migration semantics can be fully specified.

## M. `kernel/society/integration.py` extraction map (condensed — see full method-by-method table in session notes; representative entries below)

| Method | Bucket |
|---|---|
| `register_actor`, `_save_actor`, `restore_actor_belief`, `checkpoint_actor_belief`, `_subscribe_actor_inbox`, `unregister_actor`, `push_actor_message`, `drain_actor_inbox`, `peek_actor_inbox` | **Actor Cell candidate** |
| `_load_actors`, `_save_actors`, `reconcile_actors_from_redis`, `list_registry`, `set_actor_desired_node`, `suspend_actor_for_migration`, `join_society`/`leave_society`, `effective_societies`, `move_actor` (geography — reads/writes shared space state, not actor-own store), `_finalize_actor_execution`, `start_actor_lifecycle_reconciliation` | **Society/cloud candidate** |
| `self._redis`, `self._knowledge_graph`, `self._execution_engine`, `scheduler`/`lifecycle` controller accessors, `acquire_actor_lease`/`release_actor_lease` (cross-Pod coordination, not actor-own state) | **Shared infrastructure** — several "actor-named" methods here are thin wrappers over shared cloud resources, not actual actor-owned state; don't mistake the method name for the bucket |

**The smallest safe seam:** the Actor Cell candidates above already operate on one actor's own data (by `actor_id` parameter or by being called once per actor). None of them currently require touching Society/cloud-bucket code to extract — the real blocker to extraction is not `integration.py`'s structure, it's that **the domain capability layer (`grocery.py`) reads the wrong KnowledgeGraph** (the shared one, not `CognitiveActor`'s own per-actor one) and **`MemoryManager.working_memory` has no actor key** — those two fixes are prerequisite to any extraction, not the extraction itself.

## N. Storage recommendation (actor-local state only)

No new storage engine is needed. `kernel/edge/local_store.py::EdgeLocalStore` (SQLite, generic `(namespace, key)` table, already built and wired into the boot path last session) is already the right shape — its own docstring already anticipated a `"belief"` namespace. The only change an eventual Cell needs is a **keying convention**, not new engineering: every Cell-owned namespace's key should be `actor_id` (or `f"{actor_id}:{sub-key}"`), the same pattern `context_cache.py`/`policy_cache.py` already use correctly and `semantic_cache.py`/working-memory currently don't.

## O. Migration plan (phased, independently testable — not executed here)

1. **Fix the two confirmed leaks first, independent of any Cell work:** scope `record_rejection`/`get_rejected_keywords` (`grocery.py:805-843`) by `actor_id`, and scope `MemoryManager.working_memory` by `actor_id` in addition to `task_id`. Testable today, no architecture change required.
2. **Define the `ActorCell` boundary as a real object** wrapping what `CognitiveActor` already has, formalizing it as the one place identity/belief/memory/context/perception/execution/tool-state/queues/caches are reached through — without moving any cloud-owned data.
3. **Wire `CognitiveActor._knowledge_graph`** (already exists, currently dead) as the actual destination for actor-private facts, replacing the shared-KG writes that currently cause leaks — proves the seam from M without touching Society-bucket code.
4. **Add actor-scoped keying** to `semantic_cache.py` and any other edge cache found unscoped.
5. **Design (not build) the per-actor identity model** (Section J's two options) — this needs a real decision, likely with SPIRE server-side involvement, before any code changes.
6. **Formalize the Actor↔Society interface** (Section E) as an actual typed boundary (even if it's the same underlying calls) so "the Cell can only reach Society through this list" becomes enforceable/testable, not just conventional.
7. **Run the isolation acceptance tests (below)** against the boundary from step 6, on the current architecture, to get a real pass/fail baseline before any extraction begins.

## P. Test strategy (mapping the 10 acceptance tests to what exists today)

| Test | Feasible today against current code? | Notes |
|---|---|---|
| 1. State isolation (memory) | **Would currently FAIL** for the rejection-preference leak; PASS for BeliefState | Good regression test to write immediately (Step 1 above) |
| 2. Belief isolation | Should PASS today | `BeliefFusion.fuse()` never merges across actors — verify with a direct test |
| 3. Credential isolation | **Would currently FAIL** | No per-actor credential exists to test in isolation yet (Section J) — this test can only be written meaningfully after a real per-actor identity decision is made |
| 4. Store isolation | PASS for `ActorStateStore`/`EdgeActorStateStore` (already `(actor_id, tenant_id)`-keyed); **FAILS** for the shared KG/working-memory until Step 1/3 land | |
| 5. Crash isolation | Likely PASS at the request level (`run_actor_tick`'s own try/except, confirmed this session) — worth a direct test of a capability raising for actor A while actor B's own tick, same process, is asserted unaffected | |
| 6. Execution isolation | PASS by construction — no execution queue exists at all today, so there's nothing for actor A to corrupt that actor B reads; worth asserting explicitly since "no queue" is easy to accidentally introduce later | |
| 7. ROS isolation | Currently isolated only by the one-actor-per-process deployment convention, not by any runtime check — a real multi-actor-per-process test would currently FAIL if it existed; today's test suite implicitly assumes the convention holds | |
| 8. Cloud authorization | PASS — `ensure_governed`/OPA path already enforces this per request | |
| 9. Actor migration | Partially testable today via existing `_do_migrate_away`/lease-fence tests; full "no exposure of other actors' state" claim needs Step 2/3 (a real Cell) to be meaningful | |
| 10. Network interruption | **Already proven** — `tests/scenarios/test_edge_disconnected_operation.py` (last session) covers exactly this for Society/Actor/World/Presence/Catalog/Approval/ROS | |

---

## Summary of confirmed gaps (highest priority first)

1. **Confirmed cross-actor leak**: `record_rejection`/`get_rejected_keywords` (`kernel/domains/grocery.py:805-843`) — no `actor_id` in the storage key at all, despite being documented as actor-private.
2. **Unscoped working memory**: `MemoryManager.working_memory` is keyed by `task_id`, not `actor_id` (`kernel/learn/memory/manager.py:55`).
3. **No per-actor identity**: `WorkloadIdentity` is process-scoped; the one helper that constructs a per-actor SPIFFE URI shape (`agent_spiffe_id`) is unused; the internal service token is a single shared secret valid for any `actor_id`.
4. **Dead per-actor KnowledgeGraph**: `CognitiveActor` already constructs `KnowledgeGraph(person_id=entity_id)` but nothing reads or writes to it — domain capabilities use the shared KG instead, which is the root cause of gap #1.
5. **Unscoped `semantic_cache.py`**: keyed by query text + cycle id, no `actor_id`.
6. **No runtime check binding a ROS adapter instance to a specific actor_id** — isolation today is deployment convention (one actor per process), not an enforced invariant.
