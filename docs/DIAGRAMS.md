# CognitiveOS — Architecture and Sequence Diagrams

Visual reference for deployment topology, the cognitive loop, and key runtime flows.
Diagrams use [Mermaid](https://mermaid.js.org/). Syntax targets **GitHub's renderer**.

**GitHub compatibility rules used in this file:**

- Do not use participant id `Loop` (conflicts with the `loop` keyword; GitHub treats it case-insensitively).
- Do not use `classDef loop` in flowcharts (same keyword conflict).
- Avoid nested `loop` / `alt` blocks inside sequence diagrams.
- Avoid self-messages (`X-->>X`) when the participant id could be parsed as a keyword.
- Keep message text plain ASCII (no quotes, slashes, or parentheses in arrow labels).

**Canonical sources:**

| Topic | Document |
|-------|----------|
| Deployment audit and target model | [`DEPLOYMENT_ARCHITECTURE.md`](../DEPLOYMENT_ARCHITECTURE.md) |
| Consolidated architecture | [`COGNITIVEOS_FINAL_ARCHITECTURE.md`](COGNITIVEOS_FINAL_ARCHITECTURE.md) |
| Runtime layering and pipeline | [`architecture.md`](architecture.md) |
| Actor lifecycle | [`ACTOR_LIFECYCLE.md`](ACTOR_LIFECYCLE.md) |
| Actor scheduler | [`ACTOR_SCHEDULER.md`](ACTOR_SCHEDULER.md) |
| Actor artifact and boot | [`ACTOR_ARTIFACT.md`](ACTOR_ARTIFACT.md) |
| Horizontal scaling | [`HORIZONTAL_SCHEDULER_SCALING.md`](HORIZONTAL_SCHEDULER_SCALING.md) |
| SittingFace knowledge retrieval | [`SITTINGFACE_KNOWLEDGE_RETRIEVAL.md`](SITTINGFACE_KNOWLEDGE_RETRIEVAL.md) |
| Interactive UI diagram | living-world-explorer, Architecture tab |

---

## Table of contents

1. [Cognitive loop](#1-cognitive-loop-per-actor-tick)
2. [API to Kernel to Persistence](#2-api-to-kernel-to-persistence)
3. [World interaction and security](#3-world-interaction-and-security-boundary)
4. [Docker Compose (local)](#4-docker-compose-local)
5. [Kubernetes](#5-kubernetes)
6. [Current vs target deployment](#6-current-vs-target-deployment)
7. [Control plane and actor artifact](#7-control-plane-and-actor-artifact)
8. [Horizontal scaling shape](#8-horizontal-scaling-shape)
9. [Sequence: POST /prompt (grocery)](#9-sequence-post-prompt-grocery-purchase)
10. [Sequence: UPI payment (two-phase)](#10-sequence-upi-payment-two-phase)
11. [Sequence: Actor identity at boot](#11-sequence-actor-identity-at-boot)
12. [Actor runtime startup](#12-actor-runtime-startup-state-machine)
13. [Sequence: Lifecycle reconciliation](#13-sequence-lifecycle-reconciliation)
14. [Sequence: Actor migration](#14-sequence-actor-migration)
15. [Sequence: Node failure recovery](#15-sequence-node-failure-reschedule)
16. [Sequence: Rolling artifact upgrade](#16-sequence-rolling-artifact-upgrade)
17. [Sequence: cogctl apply](#17-sequence-cogctl-apply)
18. [Actor lifecycle states](#18-actor-lifecycle-states)
19. [SittingFace knowledge retrieval](#19-sittingface-knowledge-retrieval)
20. [Runtime governance pipeline](#20-runtime-governance-pipeline-ensure_governed)
21. [Portable delegation and attenuation](#21-portable-delegation-and-attenuation)
22. [Edge-local state and governance layer](#22-edge-local-state-and-governance-layer)
23. [Sequence: edge action execution](#23-sequence-edge-action-execution-local-vs-escalated)
24. [Sequence: live delegation message wiring](#24-sequence-live-delegation-message-wiring)
25. [Edge synchronization transport](#25-edge-synchronization-transport)
26. [ROS execution boundary and optional retrieval backend](#26-ros-execution-boundary-and-optional-retrieval-backend)
27. [Geography vs Society](#geography-vs-society-structural-axes)

---

## 1. Cognitive loop (per-actor tick)

Stage order:
`observe -> believe -> plan -> predict -> decide -> execute -> observe_outcome -> compare -> learn -> learn_transitions -> compile_phi -> commit`

Before **plan**, `ContextConstructionEngine` retrieves external knowledge from SittingFace
(keyword chart search plus optional vector via SemanticMemory). Retrieved facts are injected
into the LLM prompt — they inform reasoning but do not mutate authoritative world state.

Inside **execute**, per action: `TransitionGate -> Negotiation (if required) -> Commit`.

```mermaid
flowchart TD
    G[Goal] --> W[World State]
    W --> O[Observe]
    O --> B[Believe]
    B --> CCE[ContextConstructionEngine]
    SF[SittingFace SomaticCompiler] -.->|keyword and vector| CCE
    CCE --> P[Plan with external knowledge]
    P --> PR[Predict]
    PR --> D[Decide]

    D -->|keep| E[Execute]
    D -->|stale or invalid| RP[Replan]
    RP --> CCE

    E --> TG[TransitionGate]
    TG -->|negotiation required| N[Negotiation]
    TG -->|no negotiation| C[World Commit]
    N --> C

    C --> OO[Observe Outcome]
    OO --> CMP[Compare]
    CMP --> L[Learn]
    L --> LT[LearnTransitions]

    LT --> NEXT[Next Cognitive Cycle]
    NEXT --> O

    PR -.->|predicted outcome| CMP
    OO -.->|actual outcome| CMP

    SEC[Security and Policy] -.->|governs| E
    SEC -.->|governs| TG
    SEC -.->|governs| C
```

---

## 2. API to Kernel to Persistence

SittingFace loads `somatic/charts/` at AgentOS boot (`init_sittingface`). The
`SittingFaceKnowledgeRetriever` serves chart knowledge to planning and ETASS compile
paths. Neo4j remains authoritative world state; SittingFace is external reference knowledge.

```mermaid
flowchart TB
    subgraph apiLayer["API layer port 8031"]
        PROMPT["POST /prompt"]
        ACTORS["/actors"]
        PAY["/payments"]
        SFAPI["/sittingface charts"]
    end

    subgraph sfLayer["SittingFace knowledge layer"]
        SC["SomaticCompiler"]
        RET["SittingFaceKnowledgeRetriever"]
        CHARTS[("somatic/charts")]
        KP[("somatic/knowledge_packs")]
        SM["SemanticMemory optional ES"]
    end

    subgraph kernelLayer["Kernel"]
        PR["PlanetaryRuntime"]
        SR["SocietyRuntime"]
        CCE["ContextConstructionEngine"]
        PIPE["pipeline cognitive loop"]
        LLM["LLMPlanner"]
        EXEC["ActionExecutor"]
        CAP["domains grocery finance"]
        PC["PromptCompilerAgent ETASS"]
    end

    subgraph persistLayer["Persistence"]
        MONGO[("MongoDB belief")]
        REDIS[("Redis registry leases")]
        NEO[("Neo4j KnowledgeGraph world state")]
    end

    PROMPT --> PR
    SFAPI --> SC
    CHARTS --> SC
    KP --> SC
    SC --> RET
    SM --> RET
    PR --> SR
    SR --> PIPE
    PIPE --> CCE
    RET --> CCE
    CCE --> LLM
    LLM --> PIPE
    PIPE --> EXEC
    EXEC --> CAP
    PC --> RET
    PR --> MONGO
    PR --> REDIS
    PR --> NEO
    CAP --> NEO
    SM -.->|indexes chart text| CHARTS
```

---

## 3. World interaction and security boundary

SittingFace external knowledge informs LLM reasoning only. It does not write to
the KnowledgeGraph or replace authoritative world state.

```mermaid
flowchart TB
    Actor["ACTOR"]
    Bus["SOCIETY BUS NATS and Redis inbox"]
    Cap["GOVERNED CAPABILITY via ensure_governed see section 20"]
    Soc["SOCIETY"]
    CCE["ContextConstructionEngine"]
    SF["SittingFace charts read-only"]
    LLM["LLM prompt context"]
    WAPI["WORLD API and KnowledgeGraph"]
    Reality["REALITY orders payments inventory"]

    Actor --> Bus
    Actor --> Cap
    Actor --> CCE
    SF -.->|external knowledge| CCE
    CCE --> LLM
    LLM -.->|informs plan only| Actor
    Bus --> Soc
    Cap --> WAPI
    WAPI --> Reality
```

Every arrow into `WORLD API and KnowledgeGraph` from a capability now passes
through the canonical governance boundary (`ensure_governed`,
`kernel/security_boundary.py`) rather than calling `capability.handle()`
directly — see section 20 for the pipeline itself, and section 21 for how a
delegated (not the caller's own) authority is verified before it reaches OPA.

---

## 4. Docker Compose (local)

```mermaid
flowchart TB
    subgraph clientsGrp["Clients"]
        UI["explorer cogctl curl"]
    end

    subgraph gatewayGrp["Gateway port 8000"]
        KONG["Kong API Gateway"]
    end

    subgraph controlGrp["Society Control Plane port 8031"]
        AGENTOS["agentos FastAPI PlanetaryRuntime"]
        SFBOOT["init_sittingface SomaticCompiler"]
    end

    subgraph chartsGrp["SittingFace charts read-only"]
        SOMATIC[("somatic/charts")]
        PACKS[("somatic/knowledge_packs")]
    end

    subgraph domainGrp["Manufacturing REST services"]
        AUTH["auth orders inventory"]
    end

    subgraph infraGrp["Shared infrastructure"]
        MONGO[("MongoDB")]
        REDIS[("Redis")]
        NEO[("Neo4j")]
        NATS[("NATS")]
        OPA[("OPA")]
    end

    subgraph actorsGrp["docker-compose.actors.yml optional"]
        A1["actor-a port 8051"]
        A2["actor-b port 8052"]
    end

    UI --> KONG
    KONG --> AGENTOS
    SOMATIC --> SFBOOT
    PACKS --> SFBOOT
    SFBOOT --> AGENTOS
    KONG --> AUTH
    AGENTOS --> MONGO
    AGENTOS --> REDIS
    AGENTOS --> NEO
    AGENTOS --> NATS
    AGENTOS --> OPA
    A1 --> MONGO
    A1 --> REDIS
    A1 --> NEO
    A1 --> NATS
    A2 --> MONGO
    A2 --> REDIS
    A2 --> NEO
    A2 --> NATS
    A1 -.->|depends on| AGENTOS
    A2 -.->|depends on| AGENTOS
```

**Bring up:**

```bash
docker compose up agentos
docker compose up kong
docker compose -f docker-compose.yml -f docker-compose.actors.yml up -d
```

---

## 5. Kubernetes

```mermaid
flowchart TB
    subgraph nsGrp["namespace monkeybrain"]
        KONG["kong port 8000"]
        AGENTOS["agentos replicas 1"]
        SF["SomaticCompiler at boot"]
        CHARTS[("somatic charts ConfigMap or volume")]
        REDIS[("redis")]
        MONGO[("mongodb")]
        NEO[("neo4j")]
        NATS[("nats")]
        OPA[("opa")]
        ES[("elasticsearch optional vector")]
    end

    subgraph templatesGrp["Per-actor templates envsubst"]
        POD["actor-deployment.yaml"]
        EDGE["edge-actor-deployment.yaml legacy"]
    end

    EXT["Clients"] --> KONG
    KONG --> AGENTOS
    CHARTS --> SF
    SF --> AGENTOS
    AGENTOS --> REDIS
    AGENTOS --> MONGO
    AGENTOS --> NEO
    AGENTOS --> NATS
    AGENTOS --> OPA
    AGENTOS -.->|optional vector index| ES
    POD --> REDIS
    POD --> MONGO
    POD --> NATS
```

**Per-actor deploy:**

```bash
ACTOR_ID=alice ACTOR_NODE_CLASS=cloud \
  envsubst < deploy/k8s/actor-deployment.yaml | kubectl apply -f -
```

---

## 6. Current vs target deployment

### Current (monolithic cloud plus optional edge pods)

```mermaid
flowchart TB
    subgraph cloudGrp["Cloud Process agentos replicas 1"]
        API["FastAPI"]
        PR["PlanetaryRuntime"]
        SF["SomaticCompiler and Retriever"]
        SR["SocietyRuntime"]
        CCE["ContextConstructionEngine"]
        ACTORS["_actors in-process"]
        A1["CognitiveActor Alice"]
        A2["CognitiveActor Bob"]
        API --> PR
        PR --> SF
        PR --> SR
        SR --> CCE
        SF --> CCE
        SR --> ACTORS
        ACTORS -.-> A1
        ACTORS -.-> A2
    end

    CHARTS[("somatic/charts repo mount")]
    CHARTS --> SF

    subgraph edgeGrp["Edge Pod optional"]
        ES["edge_server.py"]
        EA["EdgeActor tabular RL prototype"]
        ES --> EA
    end

    NATS[("NATS")]
    MONGO[("MongoDB")]
    NEO[("Neo4j")]
    REDIS[("Redis")]
    OPA[("OPA")]

    PR --> NATS
    PR --> MONGO
    PR --> NEO
    PR --> REDIS
    API --> OPA
    ES -->|POST sync| API
```

### Target (Actor as Pod, shared control plane)

```mermaid
flowchart TB
    subgraph cpGrp["CognitiveOS Control Plane"]
        REG["Actor Registry"]
        SCHED["Actor Scheduler"]
        CTRL["Lifecycle Controller"]
        GOV["Governance TransitionGate"]
    end

    subgraph swiGrp["Shared World Infrastructure"]
        SW["KnowledgeGraph Neo4j world state"]
        SFREF["SittingFace charts external knowledge"]
        PERSIST["ActorStateStore Mongo"]
        FABRIC["NATS and Redis inbox"]
    end

    subgraph en1Grp["Execution Node Cloud"]
        ACTOR_A["Actor Alice"]
    end

    subgraph en2Grp["Execution Node Edge"]
        ACTOR_B["Actor Carol"]
    end

    CTRL -.->|reconciles| ACTOR_A
    CTRL -.->|reconciles| ACTOR_B
    SCHED -.->|places| ACTOR_A
    SCHED -.->|places| ACTOR_B
    REG -.->|tracks| ACTOR_A
    REG -.->|tracks| ACTOR_B
    ACTOR_A --> FABRIC
    ACTOR_B --> FABRIC
    ACTOR_A --> SW
    ACTOR_B --> SW
    ACTOR_A -.->|read-only| SFREF
    ACTOR_B -.->|read-only| SFREF
    ACTOR_A --> PERSIST
    ACTOR_B --> PERSIST
    ACTOR_A -.->|governed| GOV
    ACTOR_B -.->|governed| GOV
```

**Core mapping:** Actor is analogous to Pod. Identity must survive placement changes
(actor identity is not actor location).

---

## 7. Control plane and actor artifact

```mermaid
flowchart TB
    subgraph socGrp["COGNITIVEOS SOCIETY"]
        Reg["Registry"]
        Sched["Scheduler"]
        LC["Lifecycle Controller"]
        SF["SittingFace chart registry"]
    end

    Bus["Society Bus NATS and Redis"]
    Spec["Actor Specification"]
    Place["placement"]
    Cloud["CLOUD"]
    Edge["EDGE"]
    Device["DEVICE or ROBOT"]
    RT1["Actor Runtime"]
    RT2["Actor Runtime"]
    RT3["Actor Runtime"]
    ActA["Actor A"]
    ActB["Actor B"]
    ActC["Actor C"]

    Reg --> Bus
    Sched --> Bus
    LC --> Bus
    SF -.->|external knowledge| RT1
    SF -.->|external knowledge| RT2
    SF -.->|external knowledge| RT3
    Bus --> Spec
    Spec --> Place
    Place --> Cloud
    Place --> Edge
    Place --> Device
    Cloud --> RT1
    Edge --> RT2
    Device --> RT3
    RT1 --> ActA
    RT2 --> ActB
    RT3 --> ActC
```

**One image, many placements:**

Charts (`somatic/charts`) mount on the control plane — not inside each actor image.

```mermaid
flowchart LR
    Art["ACTOR ARTIFACT agentos image"]
    Charts["somatic charts volume"]
    D["Docker"]
    K["Kubernetes"]
    E["Edge"]
    RT["Runtime"]
    Same["SAME ACTOR MODEL CognitiveActor"]

    Charts -.->|read-only| RT
    Art --> D
    Art --> K
    Art --> E
    D --> RT
    K --> RT
    E --> RT
    RT --> Same
```

**Kubernetes placement:**

```mermaid
flowchart LR
    Spec["ActorSpecification"] --> Sched["CognitiveOS Scheduler"]
    Sched --> K8s["Kubernetes"]
    K8s --> Pod["Pod"]
    Charts["somatic charts ConfigMap"] -.->|boot| CP["AgentOS control plane"]
    Pod --> RT["Actor Runtime"]
    RT --> A["Actor"]
    CP -.->|external knowledge| RT
```

---

## 8. Horizontal scaling shape

```mermaid
flowchart TB
    SOC["SOCIETY"]
    CP["CONTROL PLANE Scheduler Lifecycle Registry"]
    SF["SittingFace charts shared read-only"]
    BUS["SERVICE BUS NATS and Redis"]
    A["Actor A runtime Edge"]
    B["Actor B runtime Cloud"]
    N["Actor N runtime Robot"]

    SOC --> CP
    SOC --> BUS
    SF -.->|knowledge retrieval| A
    SF -.->|knowledge retrieval| B
    SF -.->|knowledge retrieval| N
    CP --> BUS
    BUS --> A
    BUS --> B
    BUS --> N
```

---

## 9. Sequence: POST /prompt (grocery purchase)

Knowledge-seeking questions also trigger SittingFace retrieval before planning.
Transactional grocery goals skip external retrieval by policy.

```mermaid
sequenceDiagram
    participant Client
    participant Kong
    participant API as agentos API
    participant PR as PlanetaryRuntime
    participant SR as SocietyRuntime
    participant Cog as CognitiveActor
    participant CCE as ContextConstructionEngine
    participant SF as SittingFace Retriever
    participant SC as SomaticCompiler
    participant LLM as LLMPlanner
    participant Pipe as Cognitive pipeline
    participant KG as Neo4j KG
    participant Redis as Redis
    participant Mongo as MongoDB

    Client->>Kong: POST prompt buy milk
    Kong->>API: proxy with X-User-ID
    API->>PR: restore_actor_belief
    API->>PR: execute_actor_request
    PR->>Redis: acquire_actor_lease
    PR->>SR: tick_one_actor
    SR->>Cog: tick

    Cog->>CCE: build_async planning context
    CCE->>SF: retrieve goal text
    SF->>SC: keyword search charts
    Note over SF: skip for short transactional goals
    SF-->>CCE: external knowledge items
    CCE->>LLM: plan with external knowledge section
    LLM-->>Cog: plan steps

    Cog->>Pipe: predict and decide
    Note over Pipe: TransitionModel gate

    loop each ActionExecutor step
        Pipe->>Pipe: ensure_governed force_authorize true
        Note over Pipe: AUTH AUTHZ OPA APPROVAL see section 20
        Pipe->>KG: capability handle
    end
    Note over Pipe: HouseholdCognition ProductSelection OrderCreation PaymentConfirmation Payment OrderConfirmation Delivery

    Note over Pipe: compare learn commit
    Cog-->>SR: tick result
    SR-->>PR: actor result
    PR->>Mongo: checkpoint_actor_belief
    PR->>Redis: release_actor_lease
    API-->>Client: PromptResponse
```

Confirmed live end to end as Priya Sharma buying 1 liter of milk (7 of 7 steps,
goal achieved): `HouseholdCognition` (pantry check) to `ProductSelection` to
`OrderCreation` to `PaymentConfirmation` to `Payment` (debit wallet) to
`OrderConfirmation` to `Delivery` (rider assignment). Each step above is
individually authorized — a DENY or HUMAN_APPROVAL_REQUIRED on any one step
stops only that step with a clean failure result; sibling steps in the same
batch still run.

**Local demo:** `scripts/run_clean_grocery_pass.py`, `scripts/seed_world.py demo`

---

## 10. Sequence: UPI payment (two-phase)

Payment execution uses KnowledgeGraph world state only — no SittingFace retrieval on this path.

```mermaid
sequenceDiagram
    participant Pay as Payment step
    participant KG as KnowledgeGraph
    participant PSP as RazorpayUPI
    participant Redis as PendingPayment
    participant Hook as Payment webhook

    Pay->>KG: PaymentConfirmation
    Pay->>PSP: reserve funds
    PSP-->>Pay: reservation_id pending
    Pay->>Redis: save PendingPayment
    Note over Pay: tick pauses awaiting approval

    Hook->>PSP: authorize and capture
    Hook->>Redis: resolve pending payment
    Hook->>Pay: resume execution

    Pay->>PSP: capture
    Pay->>KG: debit wallet and credit store
    Note over Pay: payment success
```

---

## 11. Sequence: Actor identity at boot

AgentOS kernel boot loads SittingFace charts via `init_sittingface` before actors tick.
Actor pods inherit the shared chart registry from the control plane — they do not own it.

```mermaid
sequenceDiagram
    participant Op as Operator
    participant Bin as Actor Runtime
    participant Reg as Actor Registry

    Note over Op,Reg: actor_id already registered
    Op->>Bin: ACTOR_ID=alice run
    Bin->>Reg: locate_actor alice
    alt found
        Reg-->>Bin: ActorRegistryEntry
        Note over Bin: reconcile restore activate
        Note over Bin: same actor_id
    else not found
        Reg-->>Bin: None
        Note over Bin: NOT_FOUND refuse start
    end
```

---

## 12. Actor runtime startup (state machine)

Kernel boot phase SittingFace loads `somatic/charts` into `SomaticCompiler` before
PlanetaryRuntime serves `/prompt` requests.

```mermaid
flowchart TD
    A[process starts] --> B[load config]
    B --> C[register_self_as_node]
    C --> D{actor_id in Registry}
    D -->|no bootstrap| E[NOT_FOUND]
    D -->|bootstrap dev| F[register_actor]
    D -->|yes| G[lifecycle reconcile]
    F --> G
    G --> H{result}
    H -->|unschedulable| I[UNSCHEDULABLE]
    H -->|elsewhere| J[SCHEDULED_ELSEWHERE]
    H -->|resident ACTIVE| K[READY]
    K --> L[start_auto_tick]
```

**AgentOS boot (separate from actor pod):**

```mermaid
flowchart LR
    BOOT[Kernel boot] --> SF[init_sittingface]
    SF --> SC[SomaticCompiler load charts]
    SC --> REG[register ETASS agents]
    REG --> PR[PlanetaryRuntime ready]
```

| Endpoint | Meaning |
|----------|---------|
| `GET /live` | Process alive (liveness probe) |
| `GET /ready` | 503 unless READY (readiness probe) |
| `GET /status` | Full readiness and placement debug |
| `GET /artifact` | actor_id, version, node_id, node_class |

---

## 13. Sequence: Lifecycle reconciliation

Lifecycle reconciliation does not reload SittingFace charts — chart registry is
owned by AgentOS boot, not per-actor reconcile sweeps.

```mermaid
sequenceDiagram
    participant Sweep as Background sweep
    participant Ctrl as LifecycleController
    participant Reg as Actor Registry
    participant Lease as Actor Lease
    participant Runtime as Actor Runtime

    Sweep->>Ctrl: reconcile_all
    Ctrl->>Reg: get desired state
    Ctrl->>Reg: observe actor
    alt desired matches observed
        Ctrl-->>Sweep: no action
    else action required
        Ctrl->>Lease: acquire lease
        Ctrl->>Runtime: start resume suspend recover
        Ctrl->>Reg: refresh registry status
        Ctrl->>Lease: release lease
        Ctrl-->>Sweep: action complete
    end
```

---

## 14. Sequence: Actor migration

Actor state migrates via Mongo belief checkpoints. SittingFace charts remain
shared external knowledge on the control plane — not migrated with the actor.

```mermaid
sequenceDiagram
    participant Op as Operator
    participant Sched as ActorScheduler
    participant NodeA as Node A current
    participant Reg as Registry
    participant NodeB as Node B target

    Op->>Reg: set desired node B
    Sched->>Reg: reserve capacity B
    NodeA->>NodeA: checkpoint and SUSPEND
    Note over NodeA: desired_state stays RUNNING
    NodeB->>Reg: reconcile SUSPENDED on B
    NodeB->>NodeB: restore belief activate
    NodeB->>Reg: status ACTIVE on B
    Note over NodeA,NodeB: same actor_id
```

---

## 15. Sequence: Node failure reschedule

```mermaid
sequenceDiagram
    participant NodeA as Node A dies
    participant Reg as Registry
    participant NodeB as Node B survivor

    Note over NodeA: crash no clean shutdown
    Note over Reg: stale record no lease
    NodeB->>Reg: observe is_stale
    NodeB->>Reg: decide RECOVER
    NodeB->>Reg: schedule on Node B
    NodeB->>NodeB: restore belief activate
    Note over NodeB: same actor_id one entry
```

---

## 16. Sequence: Rolling artifact upgrade

Rolling upgrades replace actor runtime images. SittingFace chart content updates
require AgentOS redeploy or chart volume refresh — not the actor artifact alone.

```mermaid
sequenceDiagram
    participant V1 as Actor v1.4
    participant Ctrl as Lifecycle Controller
    participant V2 as Actor v1.5

    Note over V1: RUNNING
    V1->>Ctrl: checkpoint on SIGTERM
    V1->>Ctrl: deregister_node
    Note over V2: new pod same ACTOR_ID
    V2->>Ctrl: reconcile RESUME
    Ctrl->>V2: restore_actor_belief
    Note over V2: READY same state
```

---

## 17. Sequence: cogctl apply

```mermaid
sequenceDiagram
    participant Op as Operator
    participant Cog as cogctl
    participant API as actors apply API
    participant PR as PlanetaryRuntime
    participant Sched as ActorScheduler
    participant RT as Actor Runtime

    Op->>Cog: cogctl apply actor.yaml
    Cog->>API: ActorSpecification
    API->>PR: register or update
    API->>Sched: set placement
    API->>PR: enqueue reconcile
    API-->>Cog: accepted
    Note over RT: async reconcile to READY
```

---

## 18. Actor lifecycle states

```mermaid
stateDiagram-v2
    [*] --> CREATE
    CREATE --> SCHEDULE
    SCHEDULE --> START
    START --> READY
    READY --> RUNNING
    RUNNING --> SUSPEND
    SUSPEND --> RESUME
    RESUME --> RUNNING
    RUNNING --> RESTART
    RESTART --> START
    RUNNING --> TERMINATE
    SUSPEND --> TERMINATE
    TERMINATE --> [*]
```

---

## 19. SittingFace knowledge retrieval

End-to-end path from chart registry to LLM prompt. External knowledge is distinct
from Neo4j world state and from actor belief checkpoints.

```mermaid
flowchart TB
    subgraph sources["Knowledge sources"]
        CHARTS[("somatic/charts")]
        PACKS[("somatic/knowledge_packs")]
        ES[("Elasticsearch optional")]
    end

    subgraph boot["AgentOS boot"]
        INIT["init_sittingface"]
        SC["SomaticCompiler"]
        SM["SemanticMemory"]
    end

    subgraph retrieval["Retrieval layer"]
        RET["SittingFaceKnowledgeRetriever"]
        POL["retrieval policy"]
        CACHE["per-cycle cache"]
    end

    subgraph paths["Consumption paths"]
        CCE["ContextConstructionEngine build_async"]
        PC["PromptCompilerAgent ETASS"]
        HYB["HybridRouter RetrievalHandler"]
    end

    subgraph output["LLM input"]
        CTX["PlanningContext relevant_external_knowledge"]
        PROMPT["LLMPlanner External knowledge section"]
        IR["StructuredPromptIR compiled_prompt"]
    end

    CHARTS --> INIT
    PACKS --> INIT
    INIT --> SC
    SC --> RET
    CHARTS --> SM
    SM --> RET
    ES -.-> SM
    POL --> RET
    CACHE --> RET
    RET --> CCE
    RET --> PC
    RET --> HYB
    CCE --> CTX
    CTX --> PROMPT
    PC --> IR
```

**Retrieval policy (deterministic):**

| Condition | Retrieval |
|-----------|-----------|
| `meta.include_external_knowledge` | Always |
| `meta.skip_external_knowledge` | Never |
| Knowledge-seeking query patterns | Yes |
| Short transactional goal e.g. buy milk | No |
| Vector backend unavailable | Keyword fallback |

**Sequence: knowledge question via POST /prompt**

```mermaid
sequenceDiagram
    participant Client
    participant API as agentos API
    participant PR as PlanetaryRuntime
    participant CCE as ContextConstructionEngine
    participant RET as SittingFace Retriever
    participant SC as SomaticCompiler
    participant SM as SemanticMemory
    participant LLM as LLMPlanner

    Client->>API: POST prompt What is CAPA
    API->>PR: execute_actor_request
    PR->>CCE: build_async
    CCE->>RET: retrieve query
    RET->>SC: keyword search
    RET->>SM: vector search optional
    SM-->>RET: embedding hits or empty
    SC-->>RET: chart snippets
    RET-->>CCE: ExternalKnowledgeItem list
    CCE->>LLM: plan with external knowledge
    Note over LLM: retrieved facts in prompt text
    LLM-->>API: plan and response
```

See [`SITTINGFACE_KNOWLEDGE_RETRIEVAL.md`](SITTINGFACE_KNOWLEDGE_RETRIEVAL.md) for implementation detail.

---

## 20. Runtime governance pipeline (ensure_governed)

The canonical boundary every capability call and mutating operation goes
through (`kernel/security_boundary.py::ensure_governed`). `ActionExecutor`
(the real grocery and plan execution engine) calls this per capability with
`force_authorize=true`, so a batch-level commitment further up the call stack
never silently skips a specific capability's own authorization — a real gap
this closed (Live Capability Governance Closure): the execution path
previously reached `capability.handle()` directly, bypassing OPA entirely.

```mermaid
flowchart TD
    Start["capability request action resource extra"] --> Auth["AUTH TrustedAuthEvidence valid MFA satisfied"]
    Auth -->|fail closed| DenyAuth["SecurityBoundaryDenied stage AUTH"]
    Auth --> Authz["AUTHZ OPA evaluate build_opa_input"]
    Note1["OPA input carries auth delegation see section 21 capability parameters. Agent supplied claims for these keys are stripped before this point"]
    Authz -.-> Note1
    Authz -->|unreachable or errors| DenyAuthz["fail closed DENY not silently allow"]
    Authz --> Artifact["APPROVAL_ARTIFACT_CREATED ApprovalMode from policy"]
    Artifact --> Mode{approval mode}
    Mode -->|DENY| DenyMode["SecurityBoundaryDenied stage APPROVAL clean ActionOutcome"]
    Mode -->|HUMAN_APPROVAL_REQUIRED| Human["HumanApprovalRequired approval_id returned no capability call"]
    Mode -->|AUTO_APPROVE| Idem["IDEMPOTENCY SecurityOperation ledger dedupe by operation_id"]
    Idem --> Intent["AUDIT_INTENT durable fail closed on persist error"]
    Intent --> Validated["APPROVAL_VALIDATED re-check immediately before effect"]
    Validated --> Mutation["MUTATION capability handle"]
    Mutation --> Result["AUDIT_RESULT durable"]
    Result --> Done["clean ActionOutcome success true"]
```

Sibling steps in the same batch are unaffected by one step's DENY or
HUMAN_APPROVAL_REQUIRED — each capability call is its own governed decision,
not a single all-or-nothing gate over the whole plan.

A delegation is never itself an approval. `verified_delegation` (section 21)
only narrows what OPA is allowed to evaluate as "requested"; the
AUTO_APPROVE / HUMAN_APPROVAL_REQUIRED / DENY decision above remains
exclusively OPA's and GovernanceEngine's.

Negative-path tests exercising the real (non-mocked) `ActionExecutor` against
this exact pipeline: `tests/security/test_live_capability_governance_closure.py`.

---

## 21. Portable delegation and attenuation

Cryptographically verifiable, attenuable, chainable transfer of bounded
authority between authenticated agents — independent of ApprovalArtifact
(a delegation never itself constitutes human approval) and independent of
transferring any identity or private key material.

```mermaid
flowchart LR
    A["Agent A issuer authenticated"] -->|issues D1 grocery.purchase max 10000| B["Agent B delegate of D1 issuer of D2"]
    B -->|attenuates issues D2 grocery.purchase max 5000| C["Agent C delegate of D2"]
    C -->|presents chain D1 D2| Verify["verify_delegation_chain proof attenuation expiry revocation audience"]
    Verify --> OPAG["OPA delegation capability check section 20 AUTHZ"]
    OPAG --> Exec["Execution Governance capability handle"]
```

Invariant enforced at every hop: `Authority(D2) subset of Authority(D1)` —
capabilities, scope, and constraints only ever narrow going down a chain,
and a child can never outlive its parent. `issuer == delegate` (self
delegation) is rejected at construction; capabilities matching human
approval, MFA, or operator identity can never be delegated by an agent,
regardless of what the issuing agent claims to hold.

**Proof mechanism:** Ed25519 via `kernel/identity.py`'s existing
`KeyManager`/`sign_bytes`/`verify_bytes` — the same primitive this codebase
already uses to sign proposals, checkpoints, and execution graphs. Not
`sign_payload`/`verify_signed_payload` (those bundle a nonce and timestamp
for single-use anti-replay envelopes; a delegation is reusable authority,
not a one-shot token). Signed fields cover every security-relevant column
(`delegation_id`, `issuer`, `delegate`, `parent_delegation_id`, `scope`,
`capabilities`, `constraints`, `issued_at`, `expires_at`, `audience`,
`delegation_depth`) — altering any one after issuance invalidates the proof.

**Where it plugs into the runtime governance pipeline (section 20):** a
verified chain's leaf becomes `build_opa_input`'s `verified_delegation`
parameter — the same trusted-only injection pattern already used for
`recipient_spiffe_id` — never populated from agent-supplied `extra`.
`ActionExecutor` reads a pre-verified delegation off
`context["verified_delegation"]` when present and threads it through
`ensure_governed`. `kernel/edge/delegation_message.py` is what populates
it from a live inbound agent-to-agent NATS message (section 24) —
extraction and verification happen at the message boundary itself, never
inside `ActionExecutor`.

**Revoking a parent invalidates every descendant:** `DelegationStore`
tracks parent/child links and cascades `revoke()` down the chain; a
verifier also independently re-checks every ancestor's own
expiry/revocation on each use, so a descendant is never trusted purely
because it was valid once.

Core module: `kernel/delegation.py`. Security invariant tests (forged
delegation, wrong delegate, privilege/capability/constraint escalation,
broken chain, excessive depth, self-delegation, SPIFFE identity mismatch,
OPA unavailable, delegation reaching real capability execution):
`tests/security/test_portable_delegation.py`.

---

## 22. Edge-local state and governance layer

`kernel/edge/` — a durable local cache/state layer plus a local
authorization evaluator for an actor running at the edge, on a device, or
on a robot. Central authority is cached and re-verified locally; it is
never re-derived or manufactured on the edge. Neo4j/Mongo/Redis/OPA
remain authoritative — this layer only reduces how often they need to be
reached synchronously.

```mermaid
flowchart TD
    subgraph WorkingSet["Actor working set in process"]
        Caches["BoundedTTLCache backed caches context delegation semantic"]
    end
    Caches -->|miss| Local["EdgeLocalStore SQLite namespace partitioned"]
    Local -->|policy_snapshot| PolicyCache["EdgePolicyCache signed TTL bounded"]
    Local -->|delegation| DelCache["Verified delegation cache never past own expiry"]
    Local -->|world_projection| Fresh["classify_freshness FRESH STALE_BUT_USABLE STALE_MUST_REFRESH"]
    PolicyCache --> Gov["LocalGovernanceEvaluator allow deny escalate"]
    DelCache --> Gov
    Fresh --> Gov
    Gov --> Exec["ActionExecutor ensure_governed"]
    Local <-->|sync| Transport["EdgeSyncClient over SyncTransport section 25"]
    Transport <--> ControlPlane["Control plane OPA Neo4j Mongo Redis"]
```

Reads and writes to `EdgeLocalStore` never touch governance directly —
`LocalGovernanceEvaluator` is the only consumer authorized to turn cached
state into an allow/deny/escalate outcome, and only when the outer
connectivity gate (`kernel/pipeline/offline_safety.py`) has already
determined the control plane is not reachable for this call.

Core modules: `kernel/edge/local_store.py`, `freshness.py`,
`policy_cache.py`, `local_governance.py`, `local_cache.py`,
`delegation_cache.py`, `context_cache.py`, `semantic_cache.py`,
`moss_retrieval.py` (optional, retrieval only — see section 26).

---

## 23. Sequence: edge action execution (local vs escalated)

The intended shape — local authority executes locally; only genuinely
insufficient authority reaches the control plane. `EdgeDecisionState`
(`kernel/edge/decision_state.py`) names WHY a decision landed where it
did, reusing `GovernanceOrigin`'s LOCAL/CENTRAL/ESCALATED distinction
rather than inventing a second one.

```mermaid
flowchart TD
    Tick["Inbound tick or message"] --> Connect["offline_safety connectivity gate"]
    Connect -->|CONNECTED| Central["Live _authorize OPA section 20"]
    Connect -->|refused DISCONNECTED DEGRADED| Edge["LocalGovernanceEvaluator evaluate"]
    Edge --> Deleg{delegation_chain supplied}
    Deleg -->|yes fails verify| LocalDeny["LOCAL_DENY confident no escalation"]
    Deleg -->|yes verifies, or none supplied| Snap{policy snapshot cached valid}
    Snap -->|none| EscPolicy["ESCALATE_POLICY contact control plane"]
    Snap -->|DENY| LocalDeny2["LOCAL_DENY"]
    Snap -->|HUMAN_APPROVAL_REQUIRED| EscApproval["LOCAL_HUMAN_APPROVAL_REQUIRED never satisfiable locally"]
    Snap -->|AUTO_APPROVE stale but usable| EscFresh["ESCALATE_FRESHNESS"]
    Snap -->|AUTO_APPROVE fresh| LocalAllow["LOCAL_ALLOW zero central round trips"]
    LocalAllow --> Executor["ActionExecutor capability handle"]
    Central --> Executor
    EscPolicy --> Central
    EscFresh --> Central
    EscApproval --> Central
```

Measured (real `ActionExecutor`, N=30, `tests/unit/test_edge_hot_path_zero_round_trips.py`):
local governance decision p50 ≈ 1ms; a test that fails the whole suite if
`_authorize` (the live OPA call) is ever reached proves the zero-round-trip
claim structurally, not by timing alone.

---

## 24. Sequence: live delegation message wiring

Closes the gap `kernel/edge/local_governance.py`'s own delegation support
had no live producer for: a signed delegation chain riding on an inbound
NATS agent-to-agent message is extracted and verified at the narrowest
trusted boundary, never read from agent-claimed content.

```mermaid
sequenceDiagram
    participant A as Agent A issuer
    participant B as Agent B delegate of D1 issuer of D2
    participant C as Agent C delegate of D2
    participant Inbox as subscribe_actor_inbox on_message
    participant Extract as delegation_message extract_and_verify
    participant Gov as LocalGovernanceEvaluator
    participant Exec as ActionExecutor ensure_governed

    A->>B: issues D1 signed
    B->>C: attenuates issues D2 signed forwards delegated_task plus chain
    C->>Inbox: NATS message delegation_chain D1 D2
    Inbox->>Inbox: bind trusted identity SPIFFE or service evidence
    Inbox->>Extract: extract_and_verify_delegation authenticated_delegate equals bound identity
    Extract->>Extract: verify_delegation_chain signature attenuation expiry audience revocation depth
    Extract-->>Inbox: verified_delegation and raw chain, or denial reason
    Inbox->>Exec: context verified_delegation and delegation_chain
    Exec->>Gov: evaluate delegation_chain re-verified independently
    Gov-->>Exec: allow deny or escalate
    Exec->>Exec: capability handle only if allowed
```

`authenticated_delegate` always comes from the identity this handler
already bound for itself, never from the message body — a message
carrying `{"delegate": "mallory"}` alongside a real chain for `"C"` is
verified as `"C"`, not `"mallory"`. A malformed or failed chain is
rejected outright when one was supplied; no chain at all is the normal,
non-delegated case and falls through unchanged.

Core modules: `kernel/edge/delegation_message.py`,
`kernel/domains/grocery.py::subscribe_actor_inbox`/`_run_delegated_tasks`.
Tests: `tests/security/test_edge_delegation_message_wiring.py` (14 cases:
valid chain, attenuated chain, wrong delegate, wrong audience, expired
parent, revoked parent, privilege escalation despite a valid signature,
malformed chain, excessive depth, identity-binding never read from the
message, plus two real-`ActionExecutor` end-to-end cases).

---

## 25. Edge synchronization transport

The transport boundary is explicit so a real network client can be
swapped in without touching `EdgeSyncClient`'s own idempotency/epoch/
reconciliation logic at all.

```mermaid
flowchart LR
    subgraph Sync["EdgeSyncClient kernel edge sync.py unchanged"]
        Idem["epoch comparison never apply an older snapshot over a newer one"]
    end
    Sync --> Source["ControlPlaneSyncSource Protocol"]
    Source --> Adapter["TransportSyncSource"]
    Adapter --> Transport{SyncTransport}
    Transport --> InProc["InProcessSyncTransport direct calls tests single process"]
    Transport --> Net["NetworkSyncTransport real httpx retries timeouts auth typing"]
    Net -->|mTLS SVID| WI["kernel workload_identity existing SPIFFE reused not reinvented"]
    Net --> CP["Control plane edge sync endpoints"]
```

`NetworkSyncTransport` classifies failures explicitly: 401/403 raises
`SyncTransportAuthenticationError` (never silently retried like a
transient error), malformed JSON or a wrong response shape raises
`SyncTransportMalformedResponseError` (never partially applied — section
3's own invariant, "the edge must never interpret an unverified remote
update as authoritative"), and exhausted retries raise
`SyncTransportUnavailableError` (the transport-level classification of a
network partition). Tested against `httpx.MockTransport` — a real HTTP
client exercising real retry logic against a fake server, not a mocked
class. No live control-plane sync endpoint exists in this environment to
validate against end-to-end; that requires a real deployment.

Core module: `kernel/edge/sync_transport.py`. Tests:
`tests/unit/test_edge_sync_transport.py`.

---

## 26. ROS execution boundary and optional retrieval backend

Two intentionally narrow dependency seams, both lazily imported so the
normal CognitiveOS runtime never crashes because an optional package
isn't installed.

```mermaid
flowchart TD
    Plan["Committed plan capability call"] --> Exec["ActionExecutor ensure_governed"]
    Exec -->|force_authorize true| RunGoverned["run_ros_action_if_governed"]
    RunGoverned --> Protocol{RosExecutionAdapter Protocol}
    Protocol --> Fake["FakeRosExecutionAdapter in memory always in CI"]
    Protocol --> Real["RclpyRosExecutionAdapter lazy import real ROS2 service call"]
    Real -.->|rclpy not installed| Unavail["RosUnavailableError only if require_real true"]

    Retrieval["SittingFaceKnowledgeRetriever semantic_memory"] --> Backend{semantic_memory}
    Backend --> ES["SemanticMemory Elasticsearch Ollama default"]
    Backend --> Moss["MossSemanticMemory optional narrow scope retrieval only"]
    Moss -.->|not configured| NoneBackend["build_moss_semantic_memory returns None falls back"]
```

The adapter itself never contains authorization logic — checked
structurally in `tests/unit/test_ros_integration_contract.py`, not just
asserted in a comment. `build_ros_execution_adapter(require_real=False)`
(the default, normal runtime) never crashes when ROS is absent;
`require_real=True` (a robot deployment's own explicit startup path)
raises a clear `RosUnavailableError` instead of silently degrading to a
fake adapter that would misrepresent itself as hardware. Neither the ROS
contract's real-`rclpy` half nor Moss's real-credentials half has been
exercised in this environment — both honestly `pytest.mark.skipif`, never
fabricate a pass. Moss is retrieval-only, narrowed from an original
"replace SQLite" proposal — see
[`CLOUD_EDGE_ACTOR_ARCHITECTURE.md`](../CLOUD_EDGE_ACTOR_ARCHITECTURE.md)
section 18's MossDB scope decision for the full reasoning; `EdgeLocalStore`
remains SQLite-backed for policy/delegation/execution/idempotency/world-state.

Core modules: `kernel/edge/ros_integration.py`, `moss_retrieval.py`.

---

## Geography vs Society (structural axes)

```mermaid
flowchart LR
    subgraph geoGrp["Geography where"]
        P[Planet] --> Co[Country] --> Ci[City] --> Sp[Space]
    end

    subgraph socGrp2["Society who governs"]
        Soc[Society] --> T[Team] --> Act[Actor]
    end

    subgraph knowGrp["SittingFace what reference"]
        SF[Somatic charts] -.->|read-only| Act
    end

    Sp -.->|hosts| Soc
```

---

## OPA vs in-world governance

| Layer | Mechanism | Question answered |
|-------|-----------|-------------------|
| Workload identity | SPIFFE/SPIRE (`kernel/workload_identity.py`) | WHO is actually making this call? |
| Portable delegation | `kernel/delegation.py` (section 21) | WHO GRANTED WHAT bounded authority TO WHOM? |
| Infrastructure authZ | OPA (`kernel/governance.py`, section 20) | IS THIS SPECIFIC capability/action/resource ALLOWED right now? |
| Human authorization | `ApprovalArtifact`/`ApprovalMode` (section 20) | Does THIS operation additionally require a human decision? |
| Execution boundary | `ensure_governed` (section 20) | CAN the side effect actually happen — audited, idempotent? |
| In-world authority | TransitionGate and KG delegations (`kernel/domains/domain_security.py`) | Domain-specific (e.g. household grocery) grant, unrelated to the above |
| External reference | SittingFace charts and knowledge packs | What documented facts inform the LLM prompt? |

Kubernetes RBAC is not a substitute for in-world actor authority.
SittingFace knowledge informs reasoning but does not mutate authoritative world state.
SPIFFE proves identity; delegation proves granted authority; OPA decides if
it's allowed now; approval decides if a human must additionally sign off;
execution governance decides if the side effect may proceed. Each stays a
separate responsibility — none of them substitutes for another.
