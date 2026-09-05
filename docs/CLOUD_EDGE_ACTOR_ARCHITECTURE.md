# CognitiveOS Cloud/Edge Actor Convergence

Companion to `docs/ACTOR_SCHEDULER.md` (placement), `docs/HORIZONTAL_
SCHEDULER_SCALING.md` (scale), and `docs/ACTOR_ARTIFACT.md` (the
deployable-binary model). This document is about ONE thing: **is there
one Actor abstraction, or two** — and if there were two, what closed the
gap.

## 1. Unified Actor abstraction

There is one: `CognitiveActor` (`kernel/compile/cognitive_actor.py`) —
identity, belief, policy, goals, capabilities, affiliations, the full
Observe→Believe→Plan→Predict→Execute→Learn→Commit cognitive cycle,
delegating to the canonical `CognitiveRuntime`/`BeliefFormation` engine.
This is the SAME class regardless of whether the hosting process is
configured as `ACTOR_NODE_CLASS=cloud`, `edge`, `device`, or `robot` —
node class is a `ExecutionNode` scheduling label
(`kernel/society/actor_scheduler.py::NodeClass`), never a branch in
`CognitiveActor`'s own code. `src/monkey_brain/actor_runtime.py` (the
Actor Runtime — see `docs/ACTOR_ARTIFACT.md`) is what makes this concrete:
the exact same module boots the exact same `CognitiveActor`, through the
exact same `PlanetaryRuntime.register_actor`/`ActorLifecycleController.
reconcile` path, whether `ACTOR_NODE_CLASS=cloud` or `edge`.

**What is NOT unified, deliberately left alone:** `src/sync/edge_actor.py`
(`EdgeActor`) and its surrounding "Thesis 14" cluster
(`edge_node.py`/`cloud_aggregator.py`/`edge_cloud_sync.py`,
`src/sync/edge_server.py`) are a standalone, tabular-RL-only prototype
that predates the real Actor Registry/Scheduler/Lifecycle Controller —
its `actor_id` is a bare string, tied to no `ActorIdentity`, no
governance, no capabilities, no NATS. On inspection this was never a
second *semantic* Actor implementation competing with `CognitiveActor` in
the same sense a rewrite would imply — it's a self-contained research
demonstration with its own test suite (`tests/unit/test_edge_cloud.py`).
Per this task's own instruction ("do not break callers unnecessarily...
prefer EdgeActor → compatibility wrapper / runtime configuration rather
than maintaining a second semantic Actor implementation, or use the
repository's naming if cleaner") — the cleanest solution *was* to leave
it named and shaped exactly as it is, unchanged, with a clear status note
in both files pointing at `actor_runtime.py` as the real path (added this
pass) — because turning it INTO a wrapper around `CognitiveActor` would
either break its own existing tests (which assert on `EdgeActor`'s
specific tabular belief/policy shape) or require rewriting those tests
for no architectural benefit, since nothing in the real system actually
depends on `EdgeActor` for anything CognitiveOS-governed.

## 2. Actor vs. Runtime

Already drawn, before this pass, by `ActorRuntimeState`
(`kernel/society/runtime.py`) vs. `CognitiveActor`:

- **Actor** (`CognitiveActor`): identity, cognition, beliefs, memory,
  goals, capabilities, affiliations — everything that must survive a
  process restart with byte-for-byte the same meaning.
- **Runtime** (`ActorRuntimeState` + `PlanetaryRuntime` +
  `src/monkey_brain/actor_runtime.py`, new this pass): process
  lifecycle, health, node registration, checkpoint triggers,
  connectivity — everything that is disposable and reconstructible.

This pass makes the boundary a literal file boundary for the first time:
`actor_runtime.py` contains ZERO cognition — every real operation
(`restore_actor_belief`, `activate_actor`, `tick_one_actor`) is a call
into infrastructure that already existed. See `docs/ACTOR_ARTIFACT.md`
Section "Actor vs. Runtime" for the full table.

## 3. Cloud runtime

Unchanged: `kernel.py`'s full boot (`deploy/k8s/deployment.yaml`) —
persistent Mongo/Redis/Neo4j/NATS connections, ~295-agent boot, shared
Redis-backed execution engine (`vertical_router.py::build_execution_engine`).
Nothing about this pass makes cloud execution semantically special; it
is simply `ACTOR_NODE_CLASS=cloud` in the same `actor_runtime.py`
(`deploy/k8s/actor-deployment.yaml`) or the pre-existing full multi-actor
cloud deployment.

## 4. Edge runtime

New this pass: `src/monkey_brain/actor_runtime.py` with `ACTOR_NODE_CLASS=edge`.
Genuinely lighter than the cloud boot — it constructs one
`PlanetaryRuntime`, hosts exactly one `actor_id` (via
`ACTOR_NODE_CAPACITY=1`, matching `edge-actor-deployment.yaml`'s
established one-actor-per-process precedent), and tolerates the same
already-existing fail-soft dependencies every other part of this
codebase already tolerates (Mongo down → `ActorStateStore` degrades,
NATS down → `connect_nats()` logs and continues, per their own existing
docstrings — nothing new was added to make these fail-soft, they already
were). What edge conditions genuinely change: an edge/device/robot
runtime **defaults to enforcing** the offline-safety capability gate
(`OFFLINE_SAFETY_GATE_ENABLED` defaults to `true` when `ACTOR_NODE_CLASS`
is edge/device/robot, unless explicitly overridden) — see Section 12
below. Actor identity, authority, cognitive model, and capability
semantics are unchanged.

## 5. Device/robot runtime

Same `actor_runtime.py`, `ACTOR_NODE_CLASS=device` or `robot`. No
`RobotActor`/`DeviceActor` class exists or was created. A device's
hardware-specific interfaces (motor, gripper, navigation) would be
exposed as ordinary capabilities on the `CapabilityBus`
(`kernel/domains/*.py`), reaching the actor through the SAME governed
path (`ActionExecutor`→`TransitionGate`→capability `.handle()`) every
other capability already uses — no capability bypass was introduced, and
none is needed: this pass added a classification layer
(`offline_safety.py`) that runs BEFORE dispatch, not a new dispatch path
that skips governance.

## 6. Actor identity

Unchanged from `docs/ACTOR_SCHEDULER.md`'s central invariant: `actor_id`
is permanent, assigned once at `PlanetaryRuntime.register_actor()`, never
re-derived from process/container/node identity. `actor_runtime.py`
enforces this at the boundary: it refuses to start unless `ACTOR_ID`
already has a registry record (`ReadinessState.NOT_FOUND`), and only
creates one when `ACTOR_BOOTSTRAP_IF_MISSING=true` is explicitly set —
see `docs/ACTOR_ARTIFACT.md` for the full identity-establishment model.

## 7. Persistent state

Unchanged: belief persists via `ActorStateStore`/Mongo
(`checkpoint_actor_belief`/`restore_actor_belief`), lifecycle/placement
via the Redis-backed Actor/Node Registries. `actor_runtime.py` never
invents a second persistence path — its own shutdown hook calls
`checkpoint_actor_belief` directly, its own startup reaches READY only
after `ActorLifecycleController.reconcile()` (which itself calls
`restore_actor_belief`) confirms the actor is genuinely ACTIVE.

## 8. Registry / 9. Scheduler / 10. Lifecycle Controller

Unchanged — all built earlier this session, documented in
`docs/ACTOR_SCHEDULER.md`/`DEPLOYMENT_ARCHITECTURE.md`. This pass's only
addition to the Registry: `ActorRegistryEntry.artifact_version`/
`runtime_version` (pure metadata — see `docs/ACTOR_ARTIFACT.md`).

## 11. Communication

Already location-independent (`AskActorCapability`'s `locate_actor()`
fallback, built earlier this session) — verified in this pass
specifically ACROSS node classes, not just across processes
(`tests/scenarios/test_actor_runtime_artifact.py::test_05`): an actor on
an `EDGE`-class node resolves correctly from a `CLOUD`-class caller
purely via the registry, never a node address.

## 12. Offline semantics

New this pass: `kernel/pipeline/offline_safety.py`. Classifies every
capability into `SAFE_OFFLINE` / `REQUIRES_WORLD_STATE` /
`REQUIRES_AUTHORITY` / `REQUIRES_SYNC`, and assesses this process's
current `ConnectivityStatus` (`CONNECTED`/`DEGRADED`/`DISCONNECTED`,
based on Redis + NATS reachability). Wired into `ActionExecutor` as an
optional gate (`connectivity_check`), evaluated BEFORE the negotiation
gate, refusing a capability call outright — never invoking `.handle()` —
when connectivity is insufficient for that capability's class. Produces
exactly the vocabulary Section 31 of the originating task asked for:
`WAITING_FOR_WORLD_STATE`, `WAITING_FOR_AUTHORITY`, `DISCONNECTED`. An
unclassified capability defaults to the conservative bucket
(`REQUIRES_AUTHORITY`) — never assumed safe by omission.

**Deliberately opt-in, not universal:** wiring this unconditionally into
every `PlanetaryRuntime` (`OFFLINE_SAFETY_GATE_ENABLED=true` always) would
have gated every `REQUIRES_AUTHORITY`/`REQUIRES_WORLD_STATE` capability on
Redis reachability, breaking any test or lightweight deployment that
currently runs with `self._redis is None` — a well-supported, intentional
configuration throughout this codebase, not a degraded state. Default:
off for cloud, **on by default** for edge/device/robot (`actor_runtime.py`
sets it unless the operator overrides), matching where this actually
matters.

## 13. Migration

Unchanged, `docs/ACTOR_SCHEDULER.md`'s safe checkpoint-and-restart model
— exercised in this pass specifically across node CLASSES
(`test_03_migration_cloud_to_edge_preserves_identity`): an actor
migrates `CLOUD`→`EDGE`, same `actor_id`, checkpoint→suspend on the cloud
side, resume from the same checkpoint on the edge side.

## 14. Failure recovery

Unchanged mechanism (`docs/ACTOR_SCHEDULER.md`'s staleness-based
recovery, `docs/HORIZONTAL_SCHEDULER_SCALING.md`'s failure model),
exercised across node classes
(`test_04_edge_node_failure_recovers_actor_on_cloud`): an EDGE node dies,
a CLOUD node recovers the same actor_id with no duplicate registry entry.

## 15. Governance

Unchanged and untouched by this pass: `TransitionGate`/
`domain_security.py` remain the sole authority decision point, evaluated
identically regardless of `ACTOR_NODE_CLASS`. The offline-safety gate
(Section 12) is a *connectivity* precondition ("can we safely reach
authority right now"), never an authority decision itself — it either
lets a call reach `TransitionGate` normally or refuses it before
`TransitionGate` is even consulted; it never grants or overrides a
governance verdict.

## 16. Capability model

Unchanged: capabilities differ by what's registered on the
`CapabilityBus` for a given vertical, never by node class. No
edge-specific capability bypass exists; Section 12's gate is the only
edge-specific *addition*, and it is strictly a refusal mechanism, never a
grant.

## 17. Kubernetes mapping

Same table as `docs/ACTOR_SCHEDULER.md`, restated for this specific
question: **container replacement ≠ Actor replacement.** A Pod is
disposable; `actor_runtime.py`'s own restart behavior proves this in code
— `tests/scenarios/test_actor_runtime_artifact.py::test_18` kills a
runtime process (graceful shutdown) and boots a fresh one with the same
`ACTOR_ID`, asserting exactly one registry entry exists afterward. See
`docs/ACTOR_ARTIFACT.md` for the full artifact/container/binary mapping.

## 18. Edge-local governance, delegation, sync transport, and ROS boundary
(gap-closure pass)

A later pass built out `kernel/edge/` into a full local state +
governance layer, then closed specific functional gaps identified in it.
Distinguishing what is proven today from what merely cannot crash from
what is genuinely unvalidated matters more here than anywhere else in
this document — do not read "implemented" as "validated against real
hardware or a real network."

**Proven today** (real code, real tests, real dependent infrastructure
where available — Redis/Neo4j/Elasticsearch/Ollama/a real local OPA
server/MongoDB were all exercised live during this work, never mocked
where a real instance was reachable):

- Local governance (`edge/local_governance.py`): a signed, TTL-bounded
  `EdgePolicyCache` snapshot plus, optionally, a delegation chain,
  evaluated with zero live OPA round trips — proven by a test that fails
  the suite outright if `_authorize` is ever called
  (`tests/security/test_edge_local_governance.py`,
  `tests/unit/test_edge_hot_path_zero_round_trips.py`).
- Portable delegation verification (`kernel/delegation.py`, unchanged) —
  signature, attenuation, expiry, audience, revocation, and depth are all
  independently re-verified at every use, never trusted from a prior
  check or from message content.
- Live delegation wiring: a delegation chain riding on an inbound
  NATS agent-to-agent message
  (`kernel/domains/grocery.py::subscribe_actor_inbox`) is extracted and
  verified at the message boundary
  (`kernel/edge/delegation_message.py`) before it can influence either
  the central OPA path (`context["verified_delegation"]`) or the local
  path (`context["delegation_chain"]` →
  `LocalGovernanceEvaluator.evaluate`) — never read from agent-claimed
  content, never a second verifier.
- Explicit, separate freshness dimensions
  (`edge/decision_state.py::EdgeExecutionAssessment`) — connectivity,
  policy freshness, world-state freshness, and authority freshness are
  four independent fields; "CONNECTED + STALE_POLICY" is a real,
  correctly-non-healthy state a test asserts on directly.
- Explicit `EdgeDecisionState` vocabulary
  (`LOCAL_ALLOW`/`LOCAL_DENY`/`LOCAL_HUMAN_APPROVAL_REQUIRED`/
  `ESCALATE_POLICY`/`ESCALATE_AUTHORITY`/`ESCALATE_FRESHNESS`/
  `ESCALATE_COORDINATION`/`OFFLINE_DENY`) attached to every
  `LocalGovernanceOutcome`.
- The transport boundary for edge↔control-plane sync is explicit
  (`edge/sync_transport.py`: `SyncTransport` Protocol,
  `InProcessSyncTransport`, `NetworkSyncTransport`) —
  `EdgeSyncClient`'s own idempotency/epoch/reconciliation logic
  (`edge/sync.py`) is untouched and requires zero changes to consume
  either transport. `NetworkSyncTransport`'s retry/timeout/auth-failure/
  malformed-response handling is real code, tested against
  `httpx.MockTransport` (a real HTTP client exercising real retry logic
  against a fake server, not a mocked class).
- The ROS execution boundary (`edge/ros_integration.py`) is a real
  dependency seam: `FakeRosExecutionAdapter` (in-memory, always runs in
  CI) and `RclpyRosExecutionAdapter` (real rclpy service-call
  implementation, lazily imported) both satisfy the same
  `RosExecutionAdapter` Protocol and the same contract test suite
  (`tests/unit/test_ros_integration_contract.py`). Every path into it —
  fake or real — is forced through `ensure_governed` via
  `run_ros_action_if_governed`; the adapter itself contains no
  authorization logic (checked structurally in the contract tests, not
  just asserted in a comment).
- `build_ros_execution_adapter()`'s startup behavior: the normal
  CognitiveOS runtime (`require_real=False`, the default) never crashes
  because ROS is not installed. A robot deployment that explicitly opts
  into `require_real=True` gets a clear `RosUnavailableError`, not a
  silent fallback to a fake adapter that would misrepresent itself as
  hardware.

**MossDB scope decision.** A later request asked to integrate "MossDB" as
the general edge persistence substrate — replacing `edge/local_store.py`'s
SQLite backend for policy/delegation/execution/idempotency/world-state.
Investigation found: no package or product named "MossDB" exists; the
closest real match is `moss` (PyPI, docs.usemoss.dev) — a cloud-backed
semantic-search SaaS (`MossClient(project_id, project_key)` against
`service.usemoss.dev`), document/embedding-shaped (`add_docs`/`query`/
`push_index`), with no documented transaction or atomicity guarantees.
Using it as the general persistence substrate would have meant either
fabricating an atomicity guarantee it does not provide (directly
violating this pass's own "atomic security state" requirement) or
introducing a new external cloud dependency and credential into a layer
whose entire purpose is reducing central round trips. Neither was
acceptable, so the scope was narrowed, with the user's explicit sign-off,
to the one place Moss's real shape actually fits: semantic retrieval.
`edge/local_store.py` is **unchanged** and remains the sole production
persistence backend for everything else.

- `kernel/edge/moss_retrieval.py::MossSemanticMemory` satisfies the exact
  `semantic_memory.query(query) -> dict` contract
  `kernel/knowledge/sittingface_retrieval.py::SittingFaceKnowledgeRetriever`
  already depends on (the same contract `kernel/semantic_memory.py::
  SemanticMemory` implements against Elasticsearch+Ollama) — a drop-in
  alternative backend requiring zero changes to `sittingface_retrieval.py`
  itself, verified end-to-end with a real `SittingFaceKnowledgeRetriever`
  instance and a fake Moss client (real credentials are not configured in
  this environment).
- Every result Moss returns is tagged `retrieval_method="vector"`
  (honest — Moss has no keyword-only code path), and any Moss-side
  failure (auth, network, no session yet) degrades to `{"results": []}`
  rather than raising, classified as `FailureMode.MOSS_UNAVAILABLE` →
  `LOCAL_DEGRADE` in `kernel/edge/failure_modes.py` — costs retrieval
  quality only, never correctness or security.
- `build_moss_semantic_memory()` returns `None` when
  `MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY` are not configured — the normal
  CognitiveOS runtime never depends on Moss and never crashes because it
  is absent, matching `build_ros_execution_adapter()`'s own convention.
- Honest limitation: this module implements the query side of the
  contract only. No pipeline exists that populates a Moss index from
  CognitiveOS's actual knowledge charts — `index_documents()` is real but
  unused by anything yet; constructing `MossSemanticMemory` does not, by
  itself, give the edge any indexed knowledge to retrieve.
- No real Moss credentials exist in this environment — the real-Moss
  integration test in `tests/unit/test_edge_moss_retrieval.py` is
  `pytest.mark.skipif`-gated on `MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY` being
  set and reports "skipped," never a fabricated "passed."

**Implemented but requiring environment-specific validation** — real
code exists and passes its own tests, but the specific claim below has
NOT been exercised in this environment and must not be read as proven
until it is:

- `RclpyRosExecutionAdapter` has never run against a real ROS 2
  installation or physical/simulated hardware — this development
  environment has no ROS 2 distribution installed. Its contract test is
  `pytest.mark.skipif`-gated on `rclpy` being importable and reports
  "skipped," never a fabricated "passed."
- `NetworkSyncTransport` and `build_mtls_httpx_client_from_workload_identity`
  have never been exercised against a real control-plane sync HTTP
  endpoint or a real SPIRE agent socket — both require infrastructure
  this environment does not run. What is proven is the transport's own
  retry/timeout/auth/malformed-response handling (against a fake HTTP
  server), not an end-to-end sync against a real deployment.
- The intended full agent-to-agent delegation round trip (Agent A issues
  D1 over a live NATS message to Agent B, who attenuates it to D2 and
  forwards to Agent C) has been proven at the RECEIVING boundary
  (`extract_and_verify_delegation` against real, cryptographically signed
  chains) and through the real `ActionExecutor`/`LocalGovernanceEvaluator`
  path, but `DelegateTaskCapability` (the SENDING side) does not yet
  construct or attach a `delegation_chain` to its own outbound messages —
  see Remaining limitations below.

**Remaining limitations** (genuinely not done, not merely unvalidated):

1. **`DelegateTaskCapability` never issues or attaches an outbound
   delegation** — an actor delegating a task to another actor today
   forwards only `tasks`/`shared_budget_id`, never a signed
   `DelegationCredential` chain, even though the receiving boundary is
   fully wired to consume one if present. Closing this needs a real
   product decision (when should a delegator actually grant sub-authority
   vs. simply ask another actor to run its own already-existing
   authority?), not just plumbing.
2. **`ESCALATE_COORDINATION` is defined but nothing currently returns
   it** — `kernel/edge/negotiation.py`'s `NegotiationError` (a forbidden
   term key) is the natural trigger, documented in `decision_state.py`'s
   own docstring, but no code path classifies a `NegotiationError` into
   this specific enum value yet.
3. **No real control-plane HTTP sync server exists to test
   `NetworkSyncTransport` end-to-end** — see the validation-required note
   above; this is an infrastructure gap, not a code gap.
4. **`EdgeActor` was not touched or migrated** — a deliberate choice
   (Section 1), not an oversight; it remains a disconnected prototype.
5. **Offline classification (`offline_safety.py`) covers only the
   capabilities explicitly listed** — an unlisted capability defaults
   safely (REQUIRES_AUTHORITY) but is not individually verified; keeping
   this list current as new capabilities are added is a manual,
   unenforced discipline, not a compile-time check.
6. **No real edge hardware, no real intermittent-connectivity network
   simulation** was tested — `ConnectivityStatus` assessment (Redis/NATS
   ping) was verified via a fake Redis, not a real flaky network.
7. **Consequential-action-non-replay across migration/restart** relies
   entirely on the pre-existing `execution_checkpoint_store.py`/
   `resume_execution_id` mechanism, unmodified by this pass — verified by
   inspection (the mechanism is unconditionally consulted by
   `ActionExecutor`, independent of node class), not by a new end-to-end
   test with a real LLM-driven payment capability (no LLM was invoked in
   this session's testing, per its own conventions).

## 19. Actor data: registry vs. runtime snapshot vs. belief checkpoint

Society architecture review (Phase 2): "the registry" is not one store.
Tracing every real call site (`kernel/society/integration.py`) rather
than assuming from a docstring, there are two physical backends and
three conceptually distinct reads/writes:

**Redis hash `monkeybrain:actors:hash`** (one key: `self._ACTORS_HASH_KEY`)
holds BOTH of these — they are different VIEWS of the same entries, not
separate stores:

- `ActorRegistryEntry` (`locate_actor()`/`list_registry()`) — the cheap,
  read-only existence/location/status projection: `actor_id`,
  `actor_type`, `society_id`, `status`, `node_id`, `updated_at`,
  `artifact_version`, `runtime_version`. One `HGET`, no actor
  reconstruction.
- The full actor runtime snapshot (`_save_actor()`/`_load_actors()`) —
  richer per-actor JSON (profile, capabilities, constraints, metadata)
  written on every `register_actor()` call (`_save_actor()`, O(1)) and
  read back at boot (`_load_actors()`).

**MongoDB, via `persistence/actor_state_store.py::ActorStateStore`**
(`checkpoint_actor_belief()`/`restore_actor_belief()`) is the genuinely
separate backend: belief_state/bellman_policy/phi_compiled/memory_kv —
the actor's actual cognition, never written to the Redis hash above.

**A fourth path exists but is disaster recovery, not a competing
registry:** `locate_actor()`/`list_registry()` first try
`_list_registry_from_mongodb()`, which uses
`kernel/society/redis_index_reconstruction.py::RedisIndexReconstructor`
to rebuild registry-shaped entries by scanning `ActorStateStore`'s Mongo
collection — for recovering the Redis index after Redis data loss, not a
second source of truth consulted in normal operation. One edge case
worth a closer look outside this review's scope: when Mongo is reachable
but returns zero actor documents (e.g. a fresh Mongo), `locate_actor()`
currently treats that empty result as "Mongo is the registry of record"
and logs a warning that a populated Redis entry was ignored — verify this
is the intended behavior before relying on it in a fresh-Mongo deployment.

**What is authoritative for what**, restated precisely (correcting the
earlier, looser "three actor-data stores" framing from this review's own
initial pass — same conclusion, more precise mechanism):

| Data | Backend | Authoritative for |
|---|---|---|
| Existence/location/status | Redis hash (Mongo-reconstructable) | "does this actor exist, where, in what lifecycle status" |
| Profile/capabilities/constraints | Redis hash (same key, richer view) | actor specification as last registered |
| Belief/bellman/Φ/memory | MongoDB (`ActorStateStore`) | actor cognition — what this actor believes |
| Lease/fence | Redis (`_ACTOR_LEASE_KEY_PREFIX`/`_ACTOR_FENCE_KEY_PREFIX`) | who currently owns the next tick for this actor_id |

**Split-brain protection already exists and is already tested** (a
correction to this review's own first pass, which claimed no direct test
existed): `checkpoint_actor_belief()` refuses to write
(`integration.py:3427`) when the live Redis fence has advanced past the
fence this process last acquired — proven by
`tests/unit/test_multi_replica_safety.py::TestLeaseFenceCheckpoint::
test_checkpoint_skipped_when_fence_superseded`, which exercises the real
`checkpoint_actor_belief()` method end-to-end, not a reimplementation of
its logic.
