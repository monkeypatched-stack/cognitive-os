"""epistemic.py — Knowledge epistemic state for the generalized world model.

Implements the full knowledge layer of the transition function:

    T: (s_t, k_t, a_t) → (s_{t+1}, A_{t+1}, k_{t+1})

where k = epistemic state — what the system knows and how confident it is.

Five sessions:
  Session 1  KnowledgeItem    K = {k_1, …, k_n} with rich attributes
  Session 2  Multimodal       Evidence fusion across document/image/CAD/telemetry/…
  Session 3  Retrieval Policy ΔL_sim > RetrievalCost → only retrieve if it pays off
  Session 4  Evolution        K_t → K_{t+1} — items are reinforced, weakened, or retired
  Session 5  Learning Loop    Knowledge → Proposal → Simulation → Loss → Update → repeat

No external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Session 1 — Epistemic State
# K = {k_1, …, k_n} where each item carries multi-dimensional attributes
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeItem:
    """One knowledge artifact with full epistemic metadata.

    Attributes
    ----------
    id          : unique identifier (e.g. "SOP-123", "schema-v4", "telemetry-2026-07-01")
    content     : summary or pointer to the knowledge (text, URI, hash)
    modality    : evidence type — one of MODALITIES
    provenance  : source authority (ISO standard name, URL, department)
    freshness   : ISO date string of when the knowledge was last verified ("2026-06-01")
    completeness: fraction of required knowledge present [0, 1]
    consistency : internal semantic consistency [0, 1]
    validation  : external audit / peer-review score [0, 1]
    simulation_success : fraction of simulations this item helped predict correctly [0, 1]
    uncertainty : explicit epistemic uncertainty — set high when source conflicts exist [0, 1]
    verification_history : ordered list of {"date", "result", "verifier"} dicts
    """

    id: str
    content: str = ""
    modality: str = "document"
    provenance: str = ""
    freshness: str = ""
    completeness: float = 1.0
    consistency: float = 1.0
    validation: float = 1.0
    simulation_success: float = 1.0
    uncertainty: float = 0.0
    verification_history: list[dict] = field(default_factory=list)

    @property
    def composite_confidence(self) -> float:
        """Weighted geometric mean across confidence dimensions.

        Unified formula used by both kernel.rl and cortex layers.
        Geometric mean penalises low dimensions — a knowledge item with
        freshness=0.01 cannot compensate by having provenance=1.0.
        """
        import math

        prov = _provenance_score(self.provenance) if isinstance(self.provenance, str) else float(self.provenance)
        fresh = _freshness_score(self.freshness) if isinstance(self.freshness, str) else float(self.freshness)
        dims = {
            "provenance": max(prov, 1e-9),
            "freshness": max(fresh, 1e-9),
            "completeness": max(self.completeness, 1e-9),
            "consistency": max(self.consistency, 1e-9),
            "simulation_success": max(self.simulation_success, 1e-9),
            "validation": max(self.validation, 1e-9),
            "certainty": max(1.0 - self.uncertainty, 1e-9),
        }
        weights = {
            "provenance": 0.20,
            "freshness": 0.12,
            "completeness": 0.12,
            "consistency": 0.12,
            "simulation_success": 0.18,
            "validation": 0.12,
            "certainty": 0.14,
        }
        log_sum = sum(weights[k] * math.log(v) for k, v in dims.items())
        return math.exp(log_sum)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "modality": self.modality,
            "provenance": self.provenance,
            "freshness": self.freshness,
            "completeness": self.completeness,
            "consistency": self.consistency,
            "validation": self.validation,
            "simulation_success": self.simulation_success,
            "uncertainty": self.uncertainty,
            "verification_history": self.verification_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeItem":
        return cls(
            id=d.get("id", ""),
            content=d.get("content", ""),
            modality=d.get("modality", "document"),
            provenance=d.get("provenance", ""),
            freshness=d.get("freshness", ""),
            completeness=float(d.get("completeness", 1.0)),
            consistency=float(d.get("consistency", 1.0)),
            validation=float(d.get("validation", 1.0)),
            simulation_success=float(d.get("simulation_success", 1.0)),
            uncertainty=float(d.get("uncertainty", 0.0)),
            verification_history=d.get("verification_history", []),
        )


# ---------------------------------------------------------------------------
# Session 2 — Multimodal Confidence Fusion
#
# Different evidence modalities emphasise different confidence dimensions.
# Telemetry lives and dies by freshness; CAD lives by consistency; regulatory
# documents live by provenance authority.
#
# Fusion uses a harmonic mean (not arithmetic) — one weak source hurts.
# A contradiction penalty applies when items disagree.
# ---------------------------------------------------------------------------

MODALITIES = {
    "document",
    "image",
    "cad",
    "video",
    "telemetry",
    "code",
    "graph",
    "ontology",
    "db",
    "simulation",
}

# Per-modality dimension weights — must sum to ~1.0 per row
_MODALITY_WEIGHTS: dict[str, dict[str, float]] = {
    #               prov    consist  fresh   complet  valid   sim_suc
    "document": {
        "provenance": 0.30,
        "consistency": 0.20,
        "freshness": 0.15,
        "completeness": 0.20,
        "validation": 0.10,
        "simulation_success": 0.05,
    },
    "image": {
        "provenance": 0.10,
        "consistency": 0.20,
        "freshness": 0.35,
        "completeness": 0.12,
        "validation": 0.18,
        "simulation_success": 0.05,
    },
    "cad": {
        "provenance": 0.20,
        "consistency": 0.35,
        "freshness": 0.08,
        "completeness": 0.22,
        "validation": 0.10,
        "simulation_success": 0.05,
    },
    "video": {
        "provenance": 0.08,
        "consistency": 0.18,
        "freshness": 0.42,
        "completeness": 0.12,
        "validation": 0.15,
        "simulation_success": 0.05,
    },
    "telemetry": {
        "provenance": 0.04,
        "consistency": 0.18,
        "freshness": 0.58,
        "completeness": 0.08,
        "validation": 0.07,
        "simulation_success": 0.05,
    },
    "code": {
        "provenance": 0.20,
        "consistency": 0.35,
        "freshness": 0.10,
        "completeness": 0.20,
        "validation": 0.10,
        "simulation_success": 0.05,
    },
    "graph": {
        "provenance": 0.18,
        "consistency": 0.35,
        "freshness": 0.10,
        "completeness": 0.22,
        "validation": 0.10,
        "simulation_success": 0.05,
    },
    "ontology": {
        "provenance": 0.30,
        "consistency": 0.32,
        "freshness": 0.05,
        "completeness": 0.20,
        "validation": 0.08,
        "simulation_success": 0.05,
    },
    "db": {
        "provenance": 0.08,
        "consistency": 0.22,
        "freshness": 0.32,
        "completeness": 0.22,
        "validation": 0.10,
        "simulation_success": 0.06,
    },
    "simulation": {
        "provenance": 0.05,
        "consistency": 0.18,
        "freshness": 0.12,
        "completeness": 0.15,
        "validation": 0.10,
        "simulation_success": 0.40,
    },
}
_DEFAULT_MODALITY_WEIGHTS = {
    "provenance": 0.20,
    "consistency": 0.20,
    "freshness": 0.20,
    "completeness": 0.15,
    "validation": 0.15,
    "simulation_success": 0.10,
}

# Named authority → provenance trust score
_PROVENANCE_SCORES: dict[str, float] = {
    "ISO13485": 1.00,
    "ISO9001": 1.00,
    "ISO27001": 1.00,
    "ISO14971": 1.00,
    "ISO62304": 1.00,
    "ISO45001": 0.98,
    "ISO25010": 0.95,
    "FDA": 0.98,
    "EMA": 0.98,
    "GDPR": 0.97,
    "HIPAA": 0.97,
    "SOC2": 0.95,
    "PCI-DSS": 0.95,
    "FCC": 0.93,
    "NIST": 0.92,
    "IEEE": 0.90,
    "OWASP": 0.90,
    "CIS": 0.88,
    "TOGAF": 0.85,
    "internal-validated": 0.75,
    "internal-draft": 0.60,
    "internal": 0.65,
}


def _provenance_score(provenance: str) -> float:
    if not provenance:
        return 0.50
    key = provenance.split(":")[0].strip()
    if key in _PROVENANCE_SCORES:
        return _PROVENANCE_SCORES[key]
    for k, v in _PROVENANCE_SCORES.items():
        if k.upper() == key.upper():
            return v
    return 0.60


def _freshness_score(freshness: str) -> float:
    if not freshness:
        return 0.70
    try:
        from datetime import date, datetime

        d = datetime.strptime(freshness[:10], "%Y-%m-%d").date()
        age = (date.today() - d).days
        if age < 0:
            return 1.00
        if age < 30:
            return 1.00
        if age < 90:
            return 0.85
        if age < 180:
            return 0.70
        if age < 365:
            return 0.55
        return 0.30
    except (ValueError, TypeError):
        return 0.70


def _item_confidence(item: KnowledgeItem) -> float:
    """C_knowledge for a single item — weighted by modality."""
    weights = _MODALITY_WEIGHTS.get(item.modality, _DEFAULT_MODALITY_WEIGHTS)

    prov = _provenance_score(item.provenance)
    fresh = _freshness_score(item.freshness)

    dims = {
        "provenance": prov,
        "consistency": item.consistency,
        "freshness": fresh,
        "completeness": item.completeness,
        "validation": item.validation,
        "simulation_success": item.simulation_success,
    }
    # Penalise explicit uncertainty regardless of other dimensions
    base = sum(weights.get(k, 0.0) * v for k, v in dims.items())
    return round(max(0.0, base * (1.0 - item.uncertainty)), 4)


def _harmonic_mean(values: list[float]) -> float:
    """Harmonic mean: one very low value pulls the result down strongly."""
    pos = [v for v in values if v > 0]
    if not pos:
        return 0.0
    return len(pos) / sum(1.0 / v for v in pos)


def _contradiction_penalty(items: list[KnowledgeItem]) -> float:
    """Extra loss when items from the same modality disagree significantly."""
    groups: dict[str, list[float]] = {}
    for it in items:
        groups.setdefault(it.modality, []).append(_item_confidence(it))
    penalty = 0.0
    for confs in groups.values():
        if len(confs) < 2:
            continue
        spread = max(confs) - min(confs)
        if spread > 0.30:
            penalty = max(penalty, spread * 0.4)
    return round(min(0.25, penalty), 4)


# ---------------------------------------------------------------------------
# Session 3 — Retrieval Policy
#
# Retrieve only when expected ΔL_sim > retrieval cost.
# This is a value-of-information decision, not a "always fetch" policy.
# ---------------------------------------------------------------------------

# Modality → which loss components it primarily reduces
_MODALITY_RELEVANCE: dict[str, dict[str, float]] = {
    "telemetry": {"state": 0.8, "goal": 0.5},
    "document": {"action": 0.7, "constraint": 0.8},
    "ontology": {"affordance": 0.8, "constraint": 0.7},
    "code": {"constraint": 0.8, "action": 0.5},
    "db": {"state": 0.7, "goal": 0.6},
    "simulation": {"state": 0.6, "affordance": 0.6},
    "graph": {"affordance": 0.7, "action": 0.5},
    "cad": {"state": 0.5, "constraint": 0.6},
    "image": {"state": 0.4},
    "video": {"state": 0.4, "action": 0.4},
}

# Normalised retrieval cost by source type [0, 1]
RETRIEVAL_COSTS: dict[str, float] = {
    "sensor_read": 0.02,
    "db_query": 0.05,
    "file_read": 0.03,
    "vector_search": 0.08,
    "web_search": 0.20,
    "llm_inference": 0.35,
    "external_api": 0.25,
    "simulation_run": 0.50,
}


# ---------------------------------------------------------------------------
# Session 4 — Knowledge Evolution
# ---------------------------------------------------------------------------


@dataclass
class SimulationOutcome:
    """What happened in one G→A→R round — feeds K_t → K_{t+1}."""

    converged: bool = False  # zero adversarial findings
    knowledge_loss: float = 0.0  # L_knowledge from this round
    constraint_violations: int = 0  # count of constraint findings
    confidence_delta: float = 0.0  # positive = design improved
    findings_count: int = 0

    @classmethod
    def from_round(cls, adv_dict: dict, sim_loss_dict: dict) -> "SimulationOutcome":
        return cls(
            converged=adv_dict.get("terminated") == "attacks_exhausted" or not adv_dict.get("findings"),
            knowledge_loss=sim_loss_dict.get("knowledge", 0.0),
            constraint_violations=sum(
                1
                for f in adv_dict.get("findings", [])
                if f.get("finding_type") in ("violated_invariant", "ddd_violation", "constraint_violation")
            ),
            confidence_delta=adv_dict.get("confidence_delta", 0.0),
            findings_count=len(adv_dict.get("findings", [])),
        )


# ---------------------------------------------------------------------------
# EpistemicState — the full K with fusion, retrieval policy, and evolution
# ---------------------------------------------------------------------------


@dataclass
class EpistemicState:
    """K = {k_1, …, k_n} — the system's current epistemic state.

    Supports:
      confidence()         multimodal evidence fusion → C_knowledge ∈ [0,1]
      knowledge_loss()     L_knowledge = 1 - confidence()
      should_retrieve()    retrieval policy: E[ΔL_sim] > cost?
      update()             K_t → K_{t+1} after a simulation round
      add_item()           inject new knowledge (from retrieval or observation)
    """

    items: list[KnowledgeItem] = field(default_factory=list)

    # ── Session 2 — Multimodal Confidence Fusion ────────────────────────────

    def confidence(self) -> float:
        """C_knowledge: fused epistemic confidence across all knowledge items.

        Uses harmonic mean of composite_confidence values — one uncertain source
        pulls the whole estimate down. Applies a contradiction penalty when items
        from the same modality strongly disagree.
        """
        if not self.items:
            return 1.0  # no knowledge packs → no epistemic penalty

        per_item = [it.composite_confidence for it in self.items]
        fused = _harmonic_mean(per_item)
        penalty = _contradiction_penalty(self.items)
        return round(max(0.0, fused - penalty), 4)

    def knowledge_loss(self) -> float:
        """L_knowledge = 1 - C_knowledge."""
        return round(max(0.0, 1.0 - self.confidence()), 4)

    # ── Session 3 — Retrieval Policy ────────────────────────────────────────

    def expected_gain(
        self,
        current_loss_components: dict[str, float],
        retrieval_modality: str = "document",
    ) -> float:
        """E[ΔL_sim]: expected reduction in simulation loss from retrieval.

        Gain is highest when:
          - current epistemic confidence is low (room to improve), AND
          - the retrieved modality is relevant to the highest-loss components.

        Diminishing returns: gain shrinks as confidence approaches 1.0.
        """
        current_conf = self.confidence()
        headroom = 1.0 - current_conf  # maximum possible improvement

        relevance = _MODALITY_RELEVANCE.get(retrieval_modality, {})
        # Weighted relevance: how much does this modality help the current worst dimensions?
        loss_weight = sum(
            relevance.get(dim, 0.0) * current_loss_components.get(dim, 0.0)
            for dim in ("state", "action", "affordance", "constraint", "goal")
        ) / max(len(relevance), 1)

        return round(headroom * loss_weight * 0.7, 4)

    def should_retrieve(
        self,
        current_loss_components: dict[str, float],
        retrieval_modality: str = "document",
        retrieval_source: str = "vector_search",
    ) -> bool:
        """Retrieval policy: retrieve only if E[ΔL_sim] > retrieval cost.

        This is an information-value decision, not a blanket "always fetch."
        Expensive retrievals (simulation_run, llm_inference) require large
        expected gains to justify their cost.
        """
        gain = self.expected_gain(current_loss_components, retrieval_modality)
        cost = RETRIEVAL_COSTS.get(retrieval_source, 0.20)
        return gain > cost

    # ── Session 4 — Knowledge Evolution  ────────────────────────────────────

    def update(self, outcome: SimulationOutcome) -> "EpistemicState":
        """K_t → K_{t+1}: update every item based on one simulation round.

        Rules:
          Converged + confidence gained   → reinforce simulation_success, reduce uncertainty
          Constraint violations found     → weaken consistency, raise uncertainty
          High knowledge loss this round  → flag items as potentially stale
          Neutral (mixed findings)        → small passive decay only
        """
        updated = [self._evolve_item(it, outcome) for it in self.items]
        return EpistemicState(items=updated)

    def _evolve_item(self, item: KnowledgeItem, outcome: SimulationOutcome) -> KnowledgeItem:
        sim_success = item.simulation_success
        consistency = item.consistency
        validation = item.validation
        uncertainty = item.uncertainty

        if outcome.converged and outcome.confidence_delta > 0:
            # Simulation agreed with this knowledge — reinforce
            sim_success = min(1.0, sim_success + 0.03)
            uncertainty = max(0.0, uncertainty - 0.02)
            validation = min(1.0, validation + 0.01)

        elif outcome.constraint_violations > 0:
            # Constraint violations: at least one item was wrong or incomplete
            sim_success = max(0.0, sim_success - 0.05)
            consistency = max(0.0, consistency - 0.03)
            uncertainty = min(1.0, uncertainty + 0.05)

        elif outcome.knowledge_loss > 0.50:
            # High knowledge loss → something in this pack may be stale
            uncertainty = min(1.0, uncertainty + 0.02)

        else:
            # Passive decay — knowledge gets slightly less certain over time
            uncertainty = min(1.0, uncertainty + 0.005)

        history = item.verification_history.copy()
        history.append(
            {
                "event": "simulation_round",
                "converged": outcome.converged,
                "confidence_delta": outcome.confidence_delta,
                "constraint_violations": outcome.constraint_violations,
            }
        )

        return KnowledgeItem(
            id=item.id,
            content=item.content,
            modality=item.modality,
            provenance=item.provenance,
            freshness=item.freshness,
            completeness=item.completeness,
            consistency=round(consistency, 4),
            validation=round(validation, 4),
            simulation_success=round(sim_success, 4),
            uncertainty=round(uncertainty, 4),
            verification_history=history[-20:],  # keep last 20 events
        )

    # ── Session 5 — Learning Loop helpers ───────────────────────────────────

    def add_item(self, item: KnowledgeItem) -> "EpistemicState":
        """Inject a newly retrieved knowledge item into K."""
        return EpistemicState(items=[*self.items, item])

    def retire_weak(self, uncertainty_threshold: float = 0.80) -> "EpistemicState":
        """Remove items whose uncertainty has grown too high to be useful."""
        surviving = [it for it in self.items if it.uncertainty < uncertainty_threshold]
        return EpistemicState(items=surviving)

    def summary(self) -> dict[str, Any]:
        """Compact snapshot for logging and round results."""
        if not self.items:
            return {"items": 0, "confidence": 1.0, "knowledge_loss": 0.0}
        per_item_conf = {it.id: _item_confidence(it) for it in self.items}
        weakest = min(per_item_conf, key=per_item_conf.get)  # type: ignore[arg-type]
        return {
            "items": len(self.items),
            "confidence": self.confidence(),
            "knowledge_loss": self.knowledge_loss(),
            "weakest_item": weakest,
            "weakest_confidence": per_item_conf[weakest],
            "modalities": list({it.modality for it in self.items}),
        }

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [it.to_dict() for it in self.items],
            "confidence": self.confidence(),
            "knowledge_loss": self.knowledge_loss(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EpistemicState":
        return cls(items=[KnowledgeItem.from_dict(it) for it in d.get("items", [])])

    @classmethod
    def from_world_state(cls, world_state: dict[str, Any]) -> "EpistemicState":
        """Construct from world_state — supports both EpistemicState dicts and flat knowledge_packs."""
        # Prefer rich EpistemicState if present
        es = world_state.get("epistemic_state")
        if isinstance(es, dict) and "items" in es:
            return cls.from_dict(es)

        # Fall back: convert flat knowledge_packs list to KnowledgeItems
        packs = world_state.get("knowledge_packs") or []
        items = []
        for i, pack in enumerate(packs):
            if not isinstance(pack, dict):
                continue
            # Handle nested confidence dict
            conf = pack.get("confidence", {})
            if isinstance(conf, dict):
                consistency = float(conf.get("consistency", 0.90))
                validation = float(conf.get("validation", 0.90))
                simulation_success = float(conf.get("simulation", 0.90))
                fresh = conf.get("freshness", pack.get("freshness", ""))
            else:
                consistency = float(
                    pack.get(
                        "consistency",
                        float(conf) if isinstance(conf, (int, float)) else 0.90,
                    )
                )
                validation = float(
                    pack.get(
                        "validation",
                        float(conf) if isinstance(conf, (int, float)) else 0.90,
                    )
                )
                simulation_success = float(
                    pack.get(
                        "simulation",
                        float(conf) if isinstance(conf, (int, float)) else 0.90,
                    )
                )
                fresh = pack.get("freshness", "")

            items.append(
                KnowledgeItem(
                    id=pack.get("id", f"pack-{i}"),
                    content=pack.get("content", pack.get("description", "")),
                    modality=pack.get("modality", "document"),
                    provenance=pack.get("provenance", ""),
                    freshness=str(fresh),
                    completeness=float(pack.get("completeness", 0.90)),
                    consistency=consistency,
                    validation=validation,
                    simulation_success=simulation_success,
                    uncertainty=float(pack.get("uncertainty", 0.0)),
                )
            )
        return cls(items=items)


# ---------------------------------------------------------------------------
# Session 5 — Learning Loop
#
# The full cycle is orchestrated by the G→A→R loop in world_model_simulation.py.
# Each round:
#   1. Check retrieval policy — fetch new knowledge if E[ΔL_sim] > cost
#   2. Run adversarial pass with current world_state + knowledge
#   3. Build SimulationOutcome from findings
#   4. Update epistemic state: K_t → K_{t+1}
#   5. Retire knowledge items whose uncertainty has grown too high
#   6. Inject updated epistemic state back into world_state for next round
#
# This function is called by run_adversarial_refinement_loop() after each round.
# ---------------------------------------------------------------------------


def evolve_epistemic_state(
    epistemic: EpistemicState,
    adv_dict: dict[str, Any],
    sim_loss_dict: dict[str, Any],
) -> EpistemicState:
    """One step of the learning loop: build outcome, evolve, retire weak items."""
    outcome = SimulationOutcome.from_round(adv_dict, sim_loss_dict)
    evolved = epistemic.update(outcome)
    return evolved.retire_weak(uncertainty_threshold=0.85)


# ===========================================================================
# Epistemic Predictive Architecture (EPA)
#
# E_t = (S_t, B_t, A_t, M_t)
#
# where B_t = (K_t, C_t, U_t) is the unified belief state.
#
# The transition function is:
#   f(E_t, G_t, a_t) = E_{t+1}
#
# G_t is a conditioning variable (active goal), not a loss term.
# The loss decomposes as:
#   L_E = L_S + L_B + L_A + L_M
# ===========================================================================


@dataclass
class UncertaintyEstimate:
    """Aleatoric + epistemic uncertainty decomposition.

    aleatoric  — irreducible (sensor noise, environment randomness).
                 Cannot be reduced by more knowledge.
    epistemic  — reducible (we don't know yet, but could find out).
                 Retrieval / simulation reduce this.
    """

    aleatoric: float = 0.0
    epistemic: float = 0.0

    @property
    def total(self) -> float:
        return round(min(1.0, self.aleatoric + self.epistemic), 4)

    def scalar(self) -> float:
        return self.total

    @classmethod
    def from_knowledge_item(cls, item: "KnowledgeItem") -> "UncertaintyEstimate":
        # Epistemic uncertainty comes from low validation/consistency.
        # Aleatoric comes from the modality (sensor/telemetry inherently noisy).
        epistemic = item.uncertainty
        aleatoric = 0.05 if item.modality in ("telemetry", "sensor", "image", "video") else 0.0
        return cls(aleatoric=round(aleatoric, 4), epistemic=round(epistemic, 4))

    @classmethod
    def pool(cls, estimates: "list[UncertaintyEstimate]") -> "UncertaintyEstimate":
        """Component-wise max — worst-case uncertainty."""
        if not estimates:
            return cls()
        return cls(
            aleatoric=max(e.aleatoric for e in estimates),
            epistemic=max(e.epistemic for e in estimates),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "aleatoric": self.aleatoric,
            "epistemic": self.epistemic,
            "total": self.total,
        }


@dataclass
class BeliefState:
    """B = (K, C, U) — the unified belief object.

    Collapses EpistemicState + ConfidenceVector + UncertaintyEstimate
    into one mathematical object.  The kernel reasons over B as a unit.

    In the transition:
        f(E_t, G_t, a_t) = E_{t+1}
        B_{t+1} is jointly predicted with S_{t+1}, A_{t+1}, M_{t+1}.

    L_B = belief prediction error = f(knowledge_loss, uncertainty_growth,
                                       confidence_decay, constraint_violation)
    """

    # ASSUMPTION: confidence is a scalar in [0, 1], not a distribution.
    # Epistemic uncertainty about confidence itself is not modelled.
    knowledge: list[KnowledgeItem] = field(default_factory=list)
    confidence: float = 1.0  # scalar from EpistemicState.confidence()
    uncertainty: UncertaintyEstimate = field(default_factory=UncertaintyEstimate)

    # ── Loss ──────────────────────────────────────────────────────────────────

    def loss(self) -> float:
        """L_B — belief prediction error.

        High when knowledge is weak, confidence is low, or uncertainty is high.
        Three equally-weighted components: 1 - confidence, epistemic uncertainty,
        consistency penalty from contradictions.
        """
        l_knowledge = 1.0 - max(0.0, min(1.0, self.confidence))
        l_uncertainty = self.uncertainty.epistemic  # reducible — dominates planning
        l_consistency = self._consistency_penalty()
        return round((l_knowledge + l_uncertainty + l_consistency) / 3.0, 4)

    def _consistency_penalty(self) -> float:
        if len(self.knowledge) < 2:
            return 0.0
        avg_consistency = sum(k.consistency for k in self.knowledge) / len(self.knowledge)
        return round(1.0 - avg_consistency, 4)

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def from_epistemic_state(cls, epistemic: EpistemicState) -> "BeliefState":
        """Lift EpistemicState into a BeliefState."""
        conf = epistemic.confidence()
        uncertainties = [UncertaintyEstimate.from_knowledge_item(k) for k in epistemic.items]
        pooled = UncertaintyEstimate.pool(uncertainties) if uncertainties else UncertaintyEstimate()
        return cls(
            knowledge=list(epistemic.items),
            confidence=conf,
            uncertainty=pooled,
        )

    @classmethod
    def from_world_state(cls, world_state: dict[str, Any]) -> "BeliefState":
        epistemic = EpistemicState.from_world_state(world_state)
        return cls.from_epistemic_state(epistemic)

    # ── Evolution ─────────────────────────────────────────────────────────────

    def update(self, outcome: SimulationOutcome) -> "BeliefState":
        """B_t → B_{t+1} after observing a simulation outcome."""
        evolved_epistemic = EpistemicState(items=list(self.knowledge)).update(outcome)
        return BeliefState.from_epistemic_state(evolved_epistemic)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_count": len(self.knowledge),
            "confidence": round(self.confidence, 4),
            "uncertainty": self.uncertainty.to_dict(),
            "loss": self.loss(),
        }

    def summary(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass
class GoalState:
    """G_t — the active intent conditioning the transition.

    The same E_t evolves differently depending on the active goal.
    A robot navigating to charge behaves differently from the same robot
    inspecting equipment, even when S_t is identical.

    G_t is an INPUT to the transition function, not a loss term.
    Goal progress is measured by progress(world_state) → [0, 1].

    In L_E = L_S + L_B + L_A + L_M, goal-directed error is absorbed into
    L_S (did the world move toward the goal?) and L_B (did beliefs update
    in a way that's useful for the goal?).
    """

    objective: str = ""  # natural language description
    target_predicates: list[str] = field(default_factory=list)
    priority: float = 1.0  # [0, 1] — higher = more urgent
    deadline_s: float | None = None  # monotonic seconds; None = no deadline

    def progress(self, world_state: dict[str, Any]) -> float:
        """Fraction of target predicates satisfied in world_state. [0, 1]"""
        if not self.target_predicates:
            return 0.0
        satisfied = 0
        for pred in self.target_predicates:
            if self._eval_predicate(pred, world_state):
                satisfied += 1
        return round(satisfied / len(self.target_predicates), 4)

    def is_complete(self, world_state: dict[str, Any]) -> bool:
        return self.progress(world_state) >= 1.0

    def _eval_predicate(self, predicate: str, ws: dict) -> bool:
        for op in ("==", "!=", ">=", "<=", ">", "<"):
            if op in predicate:
                lhs, rhs = predicate.split(op, 1)
                lv = self._resolve(lhs.strip(), ws)
                rv = self._coerce(rhs.strip())
                try:
                    if op == "==":
                        return lv == rv
                    if op == "!=":
                        return lv != rv
                    if op == ">":
                        return lv > rv  # type: ignore[operator]
                    if op == ">=":
                        return lv >= rv  # type: ignore[operator]
                    if op == "<":
                        return lv < rv  # type: ignore[operator]
                    if op == "<=":
                        return lv <= rv  # type: ignore[operator]
                except Exception:
                    return False
        return predicate.lower() in str(ws).lower()

    @staticmethod
    def _resolve(path: str, state: dict) -> Any:
        cur: Any = state
        for p in path.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    @staticmethod
    def _coerce(v: str) -> Any:
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v.strip("'\"")

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "target_predicates": self.target_predicates,
            "priority": self.priority,
            "deadline_s": self.deadline_s,
        }
