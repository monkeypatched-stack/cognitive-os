# Book 6 — Verification

## Chapter 1: Verification Pipeline

### Overview

The Verification Pipeline checks a system artifact against a set of typed verifiers before it is committed to the epistemic state. Each verifier covers exactly one concern, mapped to one EPA loss term.

```
artifact + context
       │
       ▼
┌─────────────────────────────────────────────────────┐
│             VerificationPipeline                    │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ Epistemic    │  │ Capability   │  ...            │
│  │ Verifier     │  │ Verifier     │                 │
│  │ (L_B)        │  │ (L_A)        │                 │
│  └──────────────┘  └──────────────┘                 │
└─────────────────────────────────────────────────────┘
       │
       ▼
 list[VerificationResult]  →  passed? / aggregate_score
```

### Core Types

**`VerificationResult`**

```python
@dataclass
class VerificationResult:
    passed: bool
    violations: list[str]  # human-readable failure reasons
    evidence: dict[str, Any]  # diagnostic data
    score: float  # [0, 1] — 1.0 = fully passing
```

**`IVerifier`**

```python
class IVerifier(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def verify(self, artifact: Any, context: dict) -> VerificationResult: ...
```

**`VerificationPipeline`**

```python
pipeline = VerificationPipeline([EpistemicVerifier(), CapabilityVerifier()])
results = await pipeline.run(epa_state, context={"world_state": ws})
ok = pipeline.passed(results)
score = pipeline.aggregate_score(results)
```

### Concrete Verifiers

| Verifier | EPA Term | What It Checks |
|---|---|---|
| `EpistemicVerifier` | L_B | `B.confidence ∈ [0,1]`, `B.uncertainty.total ≤ 1`, `B.loss() ≤ threshold` |
| `CapabilityVerifier` | L_A | All preconditions of invoked capabilities satisfied in world_state |
| `SimulationVerifier` | L_S | `SimulationLoss.total` or `L_E` below configurable threshold |
| `MeshVerifier` | L_M | No capability gaps: `required_capabilities ⊆ affordances ∪ active_capabilities` |

### Usage

```python
from cerebellum.verification import (
    VerificationPipeline,
    EpistemicVerifier,
    CapabilityVerifier,
    SimulationVerifier,
    MeshVerifier,
)

pipeline = VerificationPipeline(
    [
        EpistemicVerifier(belief_loss_threshold=0.70),
        CapabilityVerifier(),
        SimulationVerifier(loss_threshold=0.50),
        MeshVerifier(),
    ]
)

# epa_state is EpistemicPredictiveState from cortex.epa
results = await pipeline.run(epa_state, context={"world_state": ws})

if not pipeline.passed(results):
    for r in results:
        if not r.passed:
            print(r.violations)
```

### Invariants

1. Each verifier is independent — failures in one do not prevent others from running.
2. Verifiers run with bounded concurrency (`asyncio.Semaphore(4)` default).
3. A verifier that throws an exception produces a failing `VerificationResult` rather than propagating.
4. `aggregate_score` is the arithmetic mean of individual scores — not a pass/fail gate.
5. The pipeline does NOT mutate the artifact or context.

### Adding a Custom Verifier

```python
class MyVerifier(IVerifier):
    @property
    def name(self) -> str:
        return "my_check"

    async def verify(self, artifact, context) -> VerificationResult:
        # ... your logic ...
        return VerificationResult(passed=True, score=1.0)
```

Register it:

```python
pipeline = VerificationPipeline([..., MyVerifier()])
```

### Relationship to EPA Loss

The four verifiers map exactly to the four EPA loss terms:

```
L_E = L_S        +    L_B           +    L_A              +    L_M
      ↕                ↕                  ↕                    ↕
SimulationVerifier  EpistemicVerifier  CapabilityVerifier  MeshVerifier
```

When all four pass, `L_E` is expected to be below acceptable threshold. The pipeline is a pre-commit gate — it runs before `epa_transition` commits `E_{t+1}`.
