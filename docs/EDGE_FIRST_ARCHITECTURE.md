# Edge-First Architecture

Status: Phase 1 complete (authority activation + core persistence pluggability +
disconnected-operation proof). See "Remaining cloud dependencies" and
"Migration notes" for what's still ahead.

## 1. Architecture diagram

```
                         CLOUD  (supervisory only — never in the execution path)
        ┌─────────────────────────────────────┐
        │ Fleet mgmt · Global identity registry│
        │ Org/tenant mgmt · Global policy dist │   kernel/edge/sync.py's
        │ Model/config distribution            │   ControlPlaneSyncSource is
        │ Deployment mgmt · Audit aggregation   │   the seam; still function-
        │ Telemetry/analytics · Global catalog  │   call-level, no real network
        │ Backup / durable history             │   transport yet (Item 9)
        └────────────────┬────────────────────┘
                    sync / delegation
             ┌───────────▼───────────┐
             │       EDGE SITE       │
             │  ┌───────────────────┐│
             │  │ Society/Actor/    ││  EdgeLocalStore (SQLite) — pluggable
             │  │ World/Presence/   ││  backend alongside Redis, selected by
             │  │ Catalog           ││  ACTOR_NODE_CLASS / explicit env var
             │  └─────────┬─────────┘│
             │  ┌─────────▼─────────┐│
             │  │ Edge Runtime      ││  Planning/ActionExecutor unchanged;
             │  │ Planning/Validate ││  edge_governance now actually wired
             │  │ Policy/Approval   ││  in (was accepted but never
             │  └─────────┬─────────┘│  constructed anywhere in production)
             │       ROS Interface   │
             │  ┌────▼────┐          │  RosExecutionAdapter Protocol — proven
             │  │   ROS   │          │  via FakeRosExecutionAdapter; no real
             │  └─────────┘          │  capability wired to it yet (Item 9)
             └───────────────────────┘
```

## 2. Edge vs. cloud ownership matrix

| Component | Authority | Mechanism |
|---|---|---|
| Actor | **Edge-local** (Redis when reachable, `EdgeLocalStore` otherwise) | `PlanetaryRuntime._load_actors`/`_save_actor(s)`, namespace `"actor"` |
| Society | **Edge-local** | `_load_societies`/`_save_societies`, namespace `"society"` |
| World | **Edge-local** | `_load_world`/`_save_world`, namespace `"world"` |
| Geography | **Edge-local** | `_load_geography`/`_save_geography`, namespace `"geography"` |
| Presence / Membership / Goal / Belief / Execution / Relationship / Activity timelines | **Edge-local** | `kernel/timeline/store.py::_EdgeLocalTimelineBackend`, one namespace per `TimelineKind` |
| Operational catalog (products/stores — KG entities/relationships) | **Edge-local** | `_on_knowledge_graph_change`/`_load_knowledge_graph`, namespaces `"kg_entity"`/`"kg_relationship"` |
| Policy/approval authority | **Edge-local**, cloud-issued signed snapshots | `kernel/edge/policy_cache.py::EdgePolicyCache` + `kernel/edge/local_governance.py::LocalGovernanceEvaluator` — now actually constructed by `PlanetaryRuntime.__init__` and threaded through `ActionExecutor` |
| Actor belief checkpoint | **Edge-local** for `ACTOR_NODE_CLASS in (edge, device, robot)` | `PlanetaryRuntime._get_actor_state_store()` branches to `kernel/edge/actor_state_store.py::EdgeActorStateStore` |
| Delegation | Edge-local (already true, no change needed) | `kernel/delegation.py::verify_delegation_chain` — pure cryptographic verification, no cloud round-trip |
| Execution / commitment | Edge-local (already true) | `kernel/pipeline/action_executor.py::ActionExecutor` runs entirely in-process |
| ROS state | Edge-local (interface proven, no real capability wired yet) | `kernel/edge/ros_integration.py` |
| Fleet registry / global org / analytics / audit aggregation / model distribution | **Cloud** (unchanged) | — |

## 3. Components moved to edge (this pass)

- Actor, Society, World, Geography persistence (`kernel/society/integration.py`)
- Presence/Membership/Goal/Belief/Execution/Relationship/Activity timelines (`kernel/timeline/store.py`)
- Operational catalog / knowledge graph (same file as Actor/Society)
- Policy/approval authority evaluation (activated existing `kernel/edge/*` code — see below)
- Actor belief checkpoint (`_get_actor_state_store`)

None of these required new persistence *design* — `kernel/edge/local_store.py::EdgeLocalStore` (SQLite, generic `(namespace, key)` table) already existed, tested, and unused in production. The work was: construct it in the real boot path, and give each subsystem's existing `_load_*`/`_save_*` pair a fallback branch to it.

## 4. Remaining cloud dependencies (honest, not silently dropped)

- **`EdgeSyncClient` has no real network transport** (`kernel/edge/sync.py`) — `ControlPlaneSyncSource` is a Python Protocol satisfied by direct function calls today; a real edge deployment needs an actual HTTP/NATS implementation. `EdgeSyncClient`'s own reconciliation/epoch logic doesn't change either way.
- **No real ROS-backed capability class** in `kernel/domains/*.py` calls `run_ros_action_if_governed` yet — the governance boundary is proven (`tests/scenarios/test_edge_disconnected_operation.py::test_ros_execution_works_offline`), but nothing invokes it for a real robot action today.
- **Actor/lease/migration locking machinery** (`_PLANETARY_CYCLE_LOCK_KEY`, `_ACTOR_LEASE_KEY_PREFIX`, the pub/sub-based inbox messaging) remains Redis-only. This is deliberate, not an oversight: it exists to arbitrate *multiple processes* racing for the same actor — a genuinely single-actor edge/device/robot Pod (the documented `ACTOR_NODE_CAPACITY=1` deployment model) never needs it. Replicating multi-process locking semantics on top of SQLite would be solving a problem an edge Pod doesn't have.
- **Cloud-side fleet/supervisory API** is unchanged — it already didn't sit in the execution path for anything this pass moved to edge-local, so no code there needed to change.

## 5. `EdgeLocalStore` design (already existed — documented, not reinvented)

File: `src/monkey_brain/kernel/edge/local_store.py`. SQLite, default path `~/.monkeybrain/edge/local_store.db` (override via `EDGE_LOCAL_STORE_PATH`). Two tables:

```sql
cache_entries (namespace TEXT, key TEXT, value TEXT, provenance TEXT, updated_at REAL, PRIMARY KEY (namespace, key))
sync_state    (stream TEXT PRIMARY KEY, last_epoch INTEGER, last_synced_at REAL, cursor TEXT)
```

Namespaces in use after this pass: `actor`, `society`, `world`, `geography`, `kg_entity`, `kg_relationship`, `timeline:{presence,membership,goal,belief,execution,relationship,activity}`, `policy_snapshot`, `actor_state`. One generic table (namespace column, not a table per concept) rather than a bespoke mechanism per subsystem — this was already the store's own design choice; this pass just gave more subsystems a reason to use it.

## 6. Sync protocol

`kernel/edge/sync.py::EdgeSyncClient` — idempotent (upsert-keyed), epoch-compared (`_should_apply` rejects an out-of-order older snapshot even if delivered late), `reconcile_after_partition()` re-runs the same incremental sync regardless of how long a disconnection lasted. **Known limitation, unchanged by this pass**: `ControlPlaneSyncSource` is a same-process Protocol, not a real network client — building the real transport is separate, sizeable work the existing code already flags as deferred.

## 7. Offline/disconnected execution test

`tests/scenarios/test_edge_disconnected_operation.py` — 5 tests, all passing, with Redis genuinely unreachable (`REDIS_PORT` pointed at a port nothing listens on, so `PlanetaryRuntime._init_persistence()`'s real connect+ping fails exactly like a real outage):

1. `test_redis_is_genuinely_unreachable` — sanity-checks the test's own premise.
2. `test_boot_society_actor_world_presence_chain` — boot → register an actor → society/world/geography/presence all persist → a **second**, independent `PlanetaryRuntime()` (simulating a process restart, still disconnected) recovers all of it.
3. `test_catalog_survives_disconnected_restart` — a product listed via `commerce.py::list_product` survives the same restart.
4. `test_approval_works_via_cached_signed_policy_snapshot` — a `REQUIRES_AUTHORITY` capability is refused with no cached authority, then allowed once a signed `SignedPolicySnapshot` is stored in `EdgePolicyCache`.
5. `test_ros_execution_works_offline` — `run_ros_action_if_governed` executes through the same governance boundary using `FakeRosExecutionAdapter` (no real ROS hardware in this environment, consistent with every other ROS test in this repo).

## 8. Security/authority analysis

- `LocalGovernanceEvaluator.evaluate()` (`kernel/edge/local_governance.py`) is fail-closed by construction: it only ever returns `allowed=True` for a cached, Ed25519-verified (`kernel/edge/policy_cache.py::verify_policy_snapshot`), epoch-current, unexpired, `AUTO_APPROVE` snapshot. Anything else — no cache entry, wrong principal, wrong audience, `DENY`, `HUMAN_APPROVAL_REQUIRED`, or a stale epoch — sets `escalate=True`, which `ActionExecutor` (`kernel/pipeline/action_executor.py:386-467`) treats identically to "no edge governance at all": the original unconditional refusal.
- `HUMAN_APPROVAL_REQUIRED` can never be satisfied locally, by design — a cached snapshot is not a human's approval, and the evaluator never fabricates one.
- Delegation chains presented at the edge are independently re-verified via `kernel/delegation.py::verify_delegation_chain` — never trusted because a message boundary already checked them.
- Identity (SPIFFE/SPIRE) is unchanged, per the explicit instruction to preserve it. `kernel/identity.py`'s local `KeyManager` (used by both `issue_policy_snapshot`/`verify_policy_snapshot`) needs no cloud round-trip — confirmed by the disconnected test actually signing and verifying a snapshot with Redis unreachable.

## 9. Migration notes (mapped onto the requested Phase 1–7 strategy)

- **Phase 1 (identify dependencies)** — done via this session's audit of `kernel/society/integration.py`'s `_load_*`/`_save_*` methods and the pre-existing `kernel/edge/` subsystem (20 files, ~3,100 lines, 25+ test files, already built but never instantiated in production).
- **Phase 2 (`EdgeLocalStore`/`EdgeSyncEngine`)** — already existed; this pass did not need to create either.
- **Phase 3 (move Society/Actor/Presence/World/Execution)** — done for Society/Actor/Presence/World this pass. Execution was already edge-local (in-process `ActionExecutor`, no persistence dependency at all).
- **Phase 4 (Policies/Approvals/Delegations/Plans/Commitments/Catalog)** — Policy/Approval authority activated this pass (construction + wiring). Catalog moved this pass. Delegation was already edge-local. Plans/Commitments live in `ActionExecutor`'s in-process execution, not a separate persisted store today.
- **Phase 5 (async cloud sync)** — not started; `EdgeSyncClient` remains function-call-level (see Item 6).
- **Phase 6 (disconnected-edge tests)** — done, `tests/scenarios/test_edge_disconnected_operation.py`.
- **Phase 7 (remove accidental cloud runtime dependencies)** — the one found and fixed: `PlanetaryRuntime._get_actor_state_store()` unconditionally used MongoDB even for edge/device/robot nodes despite `EdgeActorStateStore` already existing; now branches on node class.

## 10. Files changed / tests

See the session's final report for the exact file list and test commands/results.
