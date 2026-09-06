# Actor Cell Isolation Architecture

## System Overview

```mermaid
graph TB
    subgraph Society["Society (Cloud-Authoritative)"]
        World["🌍 Shared World<br/>(Cloud Persisted)"]
        Policy["📋 Policy Store<br/>(Cloud)"]
        Approval["✅ Approval Store<br/>(Cloud)"]
        Delegation["🔐 Delegation Store<br/>(Cloud)"]
        Registry["📖 Global Actor Registry<br/>(Cloud)"]
    end

    subgraph ProcessLocal["Process-Local Runtime"]
        subgraph ActorA["Actor A Cell"]
            IdentityA["🔑 Actor A Identity<br/>(Non-Forgeable)"]
            KGA["🧠 A's KnowledgeGraph<br/>(Distinct Instance)"]
            BeliefA["💭 A's Belief Tensor<br/>(actor_id=A)"]
            MemoryA["📝 A's Working Memory<br/>(actor_id,task_id keyed)"]
            RuntimeA["⚙️ A's Runtime State<br/>(Isolated Containers)"]
            ROSA["🤖 A's ROS Adapter<br/>(actor_id=A bound)"]
        end

        subgraph ActorB["Actor B Cell"]
            IdentityB["🔑 Actor B Identity<br/>(Non-Forgeable)"]
            KGB["🧠 B's KnowledgeGraph<br/>(Distinct Instance)"]
            BeliefB["💭 B's Belief Tensor<br/>(actor_id=B)"]
            MemoryB["📝 B's Working Memory<br/>(actor_id,task_id keyed)"]
            RuntimeB["⚙️ B's Runtime State<br/>(Isolated Containers)"]
            ROSB["🤖 B's ROS Adapter<br/>(actor_id=B bound)"]
        end

        Tick["🔄 Independent Tick Cycles<br/>(Crash Isolation)"]
    end

    Society -.->|Cloud Boundary| ProcessLocal
    World -.->|Shared Observation| ActorA
    World -.->|Shared Observation| ActorB
    
    IdentityA -->|Verified Against| Registry
    IdentityB -->|Verified Against| Registry
    
    Tick -->|Isolated| ActorA
    Tick -->|Isolated| ActorB
    
    ROSA -->|Fails if actor_id≠A| ROSB
    
    style ActorA fill:#e1f5ff,stroke:#01579b,stroke-width:3px
    style ActorB fill:#fff3e0,stroke:#e65100,stroke-width:3px
    style Society fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    style ProcessLocal fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
```

---

## Identity Isolation — Fail-Closed

```mermaid
graph LR
    subgraph Auth["Authentication Layer"]
        MintA["mint_actor_cell_identity<br/>actor_id=A"]
        MintB["mint_actor_cell_identity<br/>actor_id=B"]
    end

    subgraph Verification["Verification Layer"]
        VerifyA["verify_actor_cell_identity<br/>delegate=A"]
        VerifyB["verify_actor_cell_identity<br/>delegate=B"]
    end

    subgraph Enforcement["ActorCell Enforcement"]
        CellA["ActorCell<br/>actor_id=A<br/>identity.delegate=A"]
        CellB["ActorCell<br/>actor_id=B<br/>identity.delegate=B"]
    end

    subgraph Attack["Attack Scenario"]
        Spoof["🚨 A attempts to use<br/>B's credential"]
    end

    MintA -->|Non-Forgeable Credential| VerifyA
    MintB -->|Non-Forgeable Credential| VerifyB
    VerifyA -->|delegate=A ✅| CellA
    VerifyB -->|delegate=B ✅| CellB
    
    Spoof -->|A.credential + actor_id=B| VerifyB
    VerifyB -->|❌ DENIED<br/>delegate mismatch| Attack
    
    style VerifyB fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style CellA fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style CellB fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

---

## Knowledge Graph Isolation

```mermaid
graph TB
    subgraph CognitiveA["CognitiveActor A"]
        KGA["_knowledge_graph<br/>person_id=A"]
    end

    subgraph CognitiveB["CognitiveActor B"]
        KGB["_knowledge_graph<br/>person_id=B"]
    end

    subgraph GroceryDomain["Grocery Domain"]
        Record["record_rejection<br/>(kg, actor_id, keyword)"]
        Get["get_rejected_keywords<br/>(kg, actor_id)"]
    end

    subgraph Storage["Storage Keys"]
        StorageA["(A, almond)<br/>(A, apple)"]
        StorageB["(B, broccoli)<br/>(B, beans)"]
    end

    CognitiveA -->|Passed to| Record
    CognitiveB -->|Passed to| Get
    
    Record -->|Write to| StorageA
    Get -->|Read from| StorageB
    
    StorageA -->|Person A's<br/>Rejections| CognitiveA
    StorageB -->|Person B's<br/>Rejections| CognitiveB
    
    style StorageA fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style StorageB fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style KGA fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style KGB fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

---

## Memory Isolation — Tuple-Keyed

```mermaid
graph TB
    subgraph MemoryMgr["MemoryManager.working_memory"]
        TupleA["(actor-A, task-1)<br/>payload={item:A}"]
        TupleB["(actor-A, task-2)<br/>payload={item:A2}"]
        TupleC["(actor-B, task-1)<br/>payload={item:B}"]
        TupleD["(actor-B, task-2)<br/>payload={item:B2}"]
    end

    subgraph AllocA["Actor A Allocation"]
        AllocA1["allocate<br/>actor_id=A<br/>task_id=task-1"]
        AllocA2["allocate<br/>actor_id=A<br/>task_id=task-2"]
    end

    subgraph AllocB["Actor B Allocation"]
        AllocB1["allocate<br/>actor_id=B<br/>task_id=task-1"]
        AllocB2["allocate<br/>actor_id=B<br/>task_id=task-2"]
    end

    AllocA1 --> TupleA
    AllocA2 --> TupleB
    AllocB1 --> TupleC
    AllocB2 --> TupleD
    
    TupleA -.->|No collision| TupleC
    
    style TupleA fill:#bbdefb,stroke:#1565c0,stroke-width:2px
    style TupleC fill:#f8bbd0,stroke:#c2185b,stroke-width:2px
    style MemoryMgr fill:#f1f8e9,stroke:#558b2f,stroke-width:2px
```

---

## ROS Binding — Actor-Specific

```mermaid
graph LR
    subgraph RequestA["Request from Actor A"]
        ReqA["run_ros_action_if_governed<br/>adapter=A.adapter<br/>actor_id=A"]
    end

    subgraph RequestB["Request from Actor B"]
        ReqB["run_ros_action_if_governed<br/>adapter=B.adapter<br/>actor_id=B"]
    end

    subgraph Attack["Cross-Actor Attack"]
        AttackAB["run_ros_action_if_governed<br/>adapter=A.adapter<br/>actor_id=B❌"]
    end

    subgraph Binding["Adapter Binding Check"]
        CheckAB["if adapter.actor_id ≠ actor_id:<br/>raise RosUnavailableError"]
    end

    subgraph Execution["Execution"]
        ExecA["✅ A's ROS action<br/>executed"]
        ExecB["✅ B's ROS action<br/>executed"]
        ExecFail["❌ Error: actor_id<br/>mismatch"]
    end

    ReqA -->|actor_id=A| Binding
    ReqB -->|actor_id=B| Binding
    AttackAB -->|adapter.actor_id=A<br/>but actor_id=B| Binding
    
    Binding -->|A.adapter.actor_id==A ✅| ExecA
    Binding -->|B.adapter.actor_id==B ✅| ExecB
    Binding -->|A.adapter.actor_id≠B ❌| ExecFail
    
    style ExecA fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style ExecB fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style ExecFail fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style CheckAB fill:#fff9c4,stroke:#f57f17,stroke-width:2px
```

---

## Crash Isolation — Independent Tick Cycles

```mermaid
graph TB
    subgraph Time["Time →"]
        T1["T1"]
        T2["T2"]
        T3["T3"]
        T4["T4"]
    end

    subgraph CycleA["Actor A Tick Cycle"]
        A1["tick_one_actor(A)<br/>🔄 Running"]
        A2["🚨 CRASH<br/>RuntimeError"]
        A3["❌ Result: None<br/>(caught)"]
        A4["Next cycle ready"]
    end

    subgraph CycleB["Actor B Tick Cycle"]
        B1["tick_one_actor(B)<br/>🔄 Running"]
        B2["✅ Processing"]
        B3["✅ Result: True"]
        B4["State intact"]
    end

    T1 --> A1
    T1 --> B1
    T2 --> A2
    T2 --> B2
    T3 --> A3
    T3 --> B3
    T4 --> A4
    T4 --> B4
    
    A3 -.->|Does NOT propagate to| B3
    
    style A2 fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style A3 fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style B3 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style CycleA fill:#ffebee,stroke:#b71c1c,stroke-width:1px
    style CycleB fill:#e8f5e9,stroke:#1b5e20,stroke-width:1px
```

---

## Actor Runtime State Isolation

```mermaid
graph TB
    subgraph RuntimeMgr["SocietyRuntime"]
        RegA["register_actor(A)"]
        RegB["register_actor(B)"]
    end

    subgraph StateA["ActorRuntimeState(A)"]
        StagesA["cognitive_stages: {}<br/>(Fresh dict)"]
        LastA["last_tick_result"]
        ContextA["actor-local context"]
    end

    subgraph StateB["ActorRuntimeState(B)"]
        StagesB["cognitive_stages: {}<br/>(Fresh dict)"]
        LastB["last_tick_result"]
        ContextB["actor-local context"]
    end

    subgraph Mutation["Mutation in A"]
        MutA["A.runtime_state<br/>.cognitive_stages<br/>[key]=value"]
    end

    RegA -->|Creates distinct| StateA
    RegB -->|Creates distinct| StateB
    
    MutA -->|Updates A's dict| StagesA
    
    StagesA -.->|No shared ref| StagesB
    
    style StateA fill:#bbdefb,stroke:#1565c0,stroke-width:2px
    style StateB fill:#f8bbd0,stroke:#c2185b,stroke-width:2px
    style MutA fill:#fff9c4,stroke:#f57f17,stroke-width:2px
```

---

## Cloud Boundary — Shared Authority

```mermaid
graph TB
    subgraph Cloud["☁️ Cloud (Authoritative)"]
        CloudSociety["Society<br/>(Lifecycle)"]
        CloudWorld["World<br/>(Shared Observations)"]
        CloudPolicy["Policy<br/>(Governance)"]
        CloudApproval["Approval<br/>(Decisions)"]
        CloudDelegation["Delegation<br/>(Credentials)"]
    end

    subgraph Edge["🔌 Edge Process<br/>(Isolated Actors)"]
        ActorA["Actor A Cell"]
        ActorB["Actor B Cell"]
    end

    subgraph Forbidden["❌ NOT Moved to Edge"]
        Forbidden1["Society<br/>(Still Cloud)"]
        Forbidden2["World<br/>(Still Cloud)"]
        Forbidden3["Policy<br/>(Still Cloud)"]
    end

    CloudSociety -->|Defines| Edge
    CloudWorld -->|Observed by| ActorA
    CloudWorld -->|Observed by| ActorB
    CloudPolicy -->|Governs| ActorA
    CloudPolicy -->|Governs| ActorB
    CloudApproval -->|Artifacts stored| Cloud
    CloudDelegation -->|Verified against| Cloud
    
    style Cloud fill:#f3e5f5,stroke:#4a148c,stroke-width:3px
    style Edge fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    style Forbidden fill:#ffebee,stroke:#b71c1c,stroke-width:2px
```

---

## Construction Paths — Both Create Isolated Cells

```mermaid
graph TB
    subgraph Path1["Path 1: SocietyRuntime.register_actor()"]
        Call1["register_actor<br/>(profile)"]
        Create1A["Create CognitiveActor"]
        Create1B["Create ActorRuntimeState"]
        Create1C["Mint Actor Identity"]
        Create1D["Bind ROS Adapter"]
        Cell1["🔒 Isolated ActorCell"]
    end

    subgraph Path2["Path 2: POST /actors"]
        Call2["HTTP POST<br/>/actors"]
        Create2A["Create CognitiveActor"]
        Create2B["Create ActorRuntimeState"]
        Create2C["Mint Actor Identity"]
        Create2D["Bind ROS Adapter"]
        Cell2["🔒 Isolated ActorCell"]
    end

    Call1 --> Create1A
    Create1A --> Create1B
    Create1B --> Create1C
    Create1C --> Create1D
    Create1D --> Cell1

    Call2 --> Create2A
    Create2A --> Create2B
    Create2B --> Create2C
    Create2C --> Create2D
    Create2D --> Cell2

    Cell1 -->|Same isolation| Cell2

    style Cell1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style Cell2 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

---

## Execution Path — Per-Actor Authorization

```mermaid
sequenceDiagram
    participant Client
    participant API["API Gateway"]
    participant Identity["Identity Module"]
    participant Governance["Governance"]
    participant ActorCell["ActorCell"]
    participant Executor["Executor"]

    Client->>API: POST /execute<br/>actor_id=A<br/>identity_token=T_A
    API->>Identity: verify_actor_cell_identity<br/>(token_A, actor_id=A)
    Identity-->>API: ✅ Verified<br/>delegate=A
    API->>Governance: ensure_governed<br/>verified_delegation=...
    Governance-->>API: ✅ Policy decision
    API->>ActorCell: Execute with verified context
    ActorCell->>ActorCell: Verify actor_id=A
    ActorCell->>Executor: Run with A's KG, belief, memory
    Executor-->>ActorCell: Result
    ActorCell-->>API: ✅ Success
    API-->>Client: Response

    Note over Identity,ActorCell: Actor-scoped verification<br/>at every layer
```

---

## Test Coverage Matrix

```mermaid
graph TB
    subgraph Tests["✅ All 15 Isolation Tests Pass"]
        T1["Identity Isolation (5)"]
        T2["KG Isolation (2)"]
        T3["Memory Isolation (1)"]
        T4["Belief Isolation (2)"]
        T5["Runtime Isolation (1)"]
        T6["ROS Isolation (3)"]
        T7["Crash Isolation (1)"]
    end

    subgraph Verified["Verified Dimensions"]
        V1["Credentials non-forgeable"]
        V2["KG mutations isolated"]
        V3["Memory keyed by tuple"]
        V4["Belief distinct tensors"]
        V5["Runtime no shared containers"]
        V6["ROS adapter actor-bound"]
        V7["Crashes don't propagate"]
    end

    T1 --> V1
    T2 --> V2
    T3 --> V3
    T4 --> V4
    T5 --> V5
    T6 --> V6
    T7 --> V7

    style Tests fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style Verified fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

---

## Architectural Principles

```mermaid
graph TB
    Principle1["🔐 Fail-Closed Identity<br/>No actor can spoof another"]
    Principle2["🏝️ Per-Actor Knowledge<br/>Each actor has own KG instance"]
    Principle3["🧠 Per-Actor Cognition<br/>Distinct belief tensors"]
    Principle4["💾 Per-Actor Memory<br/>Tuple-keyed working memory"]
    Principle5["⚙️ Per-Actor Runtime<br/>Isolated state containers"]
    Principle6["🤖 Per-Actor ROS<br/>Actor-bound adapters"]
    Principle7["☁️ Cloud Authority<br/>Shared domains remain cloud"]
    Principle8["🔄 Independent Cycles<br/>Crash isolation by design"]

    Principle1 --> Outcome1["✅ Identity Isolation"]
    Principle2 --> Outcome2["✅ KG Isolation"]
    Principle3 --> Outcome3["✅ Belief Isolation"]
    Principle4 --> Outcome4["✅ Memory Isolation"]
    Principle5 --> Outcome5["✅ Runtime Isolation"]
    Principle6 --> Outcome6["✅ ROS Isolation"]
    Principle7 --> Outcome7["✅ Cloud Boundaries"]
    Principle8 --> Outcome8["✅ Crash Isolation"]

    Outcome1 --> Result["🎯 True Actor Cell<br/>Isolation Achieved"]
    Outcome2 --> Result
    Outcome3 --> Result
    Outcome4 --> Result
    Outcome5 --> Result
    Outcome6 --> Result
    Outcome7 --> Result
    Outcome8 --> Result

    style Result fill:#a5d6a7,stroke:#1b5e20,stroke-width:3px
```

---

## Verdict: Actor Isolation Is Secure

```mermaid
graph LR
    Question["Can two Actors operate<br/>without sharing state?"]
    Evidence1["✅ Identity Isolated"]
    Evidence2["✅ KG Isolated"]
    Evidence3["✅ Memory Isolated"]
    Evidence4["✅ Runtime Isolated"]
    Evidence5["✅ ROS Isolated"]
    Evidence6["✅ Crashes Isolated"]
    
    Question --> Evidence1
    Question --> Evidence2
    Question --> Evidence3
    Question --> Evidence4
    Question --> Evidence5
    Question --> Evidence6
    
    Evidence1 --> Verdict["🎯 YES — PASS"]
    Evidence2 --> Verdict
    Evidence3 --> Verdict
    Evidence4 --> Verdict
    Evidence5 --> Verdict
    Evidence6 --> Verdict
    
    style Verdict fill:#a5d6a7,stroke:#1b5e20,stroke-width:3px,font-weight:bold
```

