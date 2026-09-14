"""epa.py — Epistemic Predictive Architecture (EPA).

Formal definition of:

    E_t = (S_t, B_t, A_t, M_t)

    f(E_t, G_t, a_t) = E_{t+1}

    L_E = L_S + L_B + L_A + L_M + L_K + L_C + L_G

This is not "JEPA + confidence."

JEPA predicts the evolution of the external world (z_t → z_{t+1}).
EPA predicts the evolution of the cognitive state of an intelligent system.
That is a different objective.

A standard JEPA latent space contains:
    world geometry, object positions, velocities

The EPA latent space also contains:
    what the system knows
    how confident it is
    what actions are available to it
    whether the agent organization needs to change

The transition equation never changes across domains.
Only the encoding of S changes:
    robotics     → joint angles, obstacles, geometry
    enterprise   → entity states, workflow statuses
    manufacturing → sensor readings, machine states
    multi-agent  → agent positions, task assignments

This is what makes it general.

Relationship to existing code:
    EpistemicState (epistemic.py)   → now lifts into BeliefState B
    SimulationLoss (adversarial_agents.py) → now has to_epa_dict() (4-term view)
    CapabilityGraph (cerebellum)    → provides A_t
    AgentMesh (broca)               → provides M_t
    GoalState (epistemic.py)        → G_t conditioning variable
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from cortex.epistemic import BeliefState, GoalState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EpistemicPredictiveState — E_t = (S, B, A, M)
# ---------------------------------------------------------------------------


@dataclass
class EpistemicPredictiveState:
    """The full cognitive state of an intelligent system at time t.

    S  — world state (dict; encoding depends on domain)
    B  — belief state (K, C, U) — what the system knows and how uncertain it is
    A  — available action space (set of capability names)
    M  — agent mesh state (summary dict from AgentMesh)

    The EPA makes a single claim:
        A JEPA-like latent model can learn to predict E_{t+1} from (E_t, G_t, a_t).
    """

    S: dict[str, Any] = field(default_factory=dict)  # world state
    B: BeliefState = field(default_factory=BeliefState)
    A: set[str] = field(default_factory=set)  # available capabilities
    M: dict[str, Any] = field(default_factory=dict)  # mesh summary

    # ── Convenience ────────────────────────────────────────────────────────────

    @property
    def world_state(self) -> dict[str, Any]:
        return self.S

    @property
    def belief(self) -> BeliefState:
        return self.B

    @property
    def affordances(self) -> set[str]:
        return self.A

    @property
    def mesh(self) -> dict[str, Any]:
        return self.M

    # ── Construction ───────────────────────────────────────────────────────────

    @classmethod
    def from_world_state(
        cls,
        world_state: dict[str, Any],
        *,
        capability_graph=None,
        agent_mesh=None,
    ) -> "EpistemicPredictiveState":
        """Build E_t from a world_state dict plus optional graph/mesh objects."""
        belief = BeliefState.from_world_state(world_state)

        affordances: set[str] = set()
        if capability_graph is not None:
            try:
                affordances = set(capability_graph.available(world_state))
            except Exception as e:
                logger.debug("Capability graph query failed: %s", e)

        mesh_summary: dict[str, Any] = {}
        if agent_mesh is not None:
            try:
                mesh_summary = agent_mesh.summary()
            except Exception as e:
                logger.debug("Agent mesh summary failed: %s", e)

        return cls(S=world_state, B=belief, A=affordances, M=mesh_summary)

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "S": self.S,
            "B": self.B.to_dict(),
            "A": sorted(self.A),
            "M": self.M,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "world_keys": sorted(self.S.keys()),
            "belief": self.B.summary(),
            "affordances": sorted(self.A),
            "mesh": self.M,
        }


# ---------------------------------------------------------------------------
# EPA transition function
# ---------------------------------------------------------------------------


def epa_transition(
    E_t: EpistemicPredictiveState,
    G_t: GoalState,
    a_t: str,
    *,
    evidence: dict[str, Any] | None = None,
    capability_graph=None,
    agent_mesh=None,
    fusion_engine=None,
    world_model=None,
    solver_mesh=None,
    train: bool = True,
) -> EpistemicPredictiveState:
    """f(E_t, G_t, a_t) = E_{t+1}

    # ASSUMPTION: all four components (S, B, A, M) update atomically in one call.
    # If any update is skipped (e.g. capability_graph is None), loss is computed over a partial state.

    ARCHITECTURAL LAW — This is the ONLY authorised state mutation API.

    Correct:
        state = epa_transition(state, goal, action, evidence=evidence, ...)

    Forbidden anywhere outside this function:
        belief.update(outcome)          # direct belief mutation
        state.B.confidence += 0.1      # direct confidence mutation
        mesh.spawn(agent)              # direct mesh mutation
        state.apply_capability_effects(...)  # deprecated kernel-only path

    The single authoritative transition.  No belief update happens anywhere else.

    Updates (in order):
        S_{t+1} — world state after executing capability a_t (effects + knowledge_effects)
        A_{t+1} — affordances via CapabilityGraph.delta_affordance + descriptor enables/disables
        B_{t+1} — fuse evidence first (if fusion_engine), then update belief;
                   K update via evolve_epistemic_state on the knowledge items
        M_{t+1} — mesh summary from agent_mesh; goal progress injected

    Parameters
    ----------
    evidence       : raw observation dict from capability execution result
    capability_graph: cerebellum CapabilityGraph — provides affordance delta
    agent_mesh     : broca AgentMesh — provides M_t summary
    fusion_engine  : EvidenceFusionEngine — fuses evidence before belief update
    train          : when False, JEPA's forward-model inference still runs (so the
                     returned state reflects JEPA's current forecast) but its two
                     training call-backs (_jepa_update / _epistemic_update) are
                     skipped. Lets a caller take a pre-execution "predicted" pass
                     without corrupting JEPA's training signal — the real,
                     post-execution call (train=True, the default) is the only one
                     that teaches JEPA anything.
    """
    from cortex.epistemic import SimulationOutcome, EpistemicState as _EState

    new_S = dict(E_t.S)
    evidence = evidence or {}

    # ── S_{t+1}: apply descriptor effects + evidence to world state ──────────
    desc = None
    if capability_graph is not None:
        try:
            desc = capability_graph.get(a_t)
        except Exception as e:
            logger.debug("Capability graph lookup failed: %s", e)

    if desc is not None:
        for effect in getattr(desc, "effects", []):
            new_S.setdefault("events", []).append(effect)
        # Apply knowledge_effects: "adds:X" injects X into produced; "removes:Y" clears it
        for ke in getattr(desc, "knowledge_effects", []):
            if ke.startswith("adds:"):
                new_S.setdefault("knowledge_additions", []).append(ke[5:])
            elif ke.startswith("removes:"):
                new_S.setdefault("knowledge_removals", []).append(ke[8:])

    # ── S_deterministic: apply explicit numeric updates from capability evidence ─
    # These are authoritative — the capability execution result directly sets them.
    # JEPA must not override these keys.
    _deterministic_keys: set[str] = set()
    for k, v in evidence.items():
        if k in E_t.S and isinstance(v, (int, float)) and not isinstance(v, bool):
            new_S[k] = v
            _deterministic_keys.add(k)

    if evidence:
        new_S["last_evidence"] = evidence

    # ── Δ_JEPA: residual prediction for keys NOT set by the capability ─────────
    # S_{t+1} = S_deterministic + Δ_JEPA
    #
    # Deterministic path (capability descriptor / evidence) is authoritative.
    # Epistemic JEPA predicts the residual for everything it did NOT touch.
    # infer_epistemic() gives per-component signals over the full
    # z_E = [z_W | z_B | z_K | z_A | z_G | z_M] latent.
    _jepa_infer: dict | None = None
    if world_model is not None:
        try:
            if hasattr(world_model, "infer_epistemic"):
                _jepa_infer = world_model.infer_epistemic(E_t.S, a_t, B=E_t.B, A=E_t.A, G=G_t, M=E_t.M)
                # Normalise to the shape downstream code expects
                _jepa_infer.setdefault("trained", _jepa_infer.get("epistemic_trained", False))
                _jepa_infer.setdefault("avg_loss", _jepa_infer.get("epistemic_avg_loss", 1.0))
                _jepa_infer.setdefault("latent_signal", _jepa_infer.get("latent_signal_W", 0.0))
                _jepa_infer.setdefault("belief_avg_loss", abs(_jepa_infer.get("latent_signal_B", 0.0)))
            else:
                _jepa_infer = world_model.infer(E_t.S, a_t, belief=E_t.B)

            if _jepa_infer.get("trained") or _jepa_infer.get("epistemic_trained"):
                # z_W drives world state residual; other component signals (z_B, z_K, z_A, z_G, z_M)
                # are available in _jepa_infer for future per-component decoders.
                sig_W = _jepa_infer.get("latent_signal_W", _jepa_infer.get("latent_signal", 0.0))
                for k, v in E_t.S.items():
                    if k not in _deterministic_keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                        new_S[k] = round(float(new_S.get(k, v)) + sig_W * abs(float(v)) * 0.05, 4)

            new_S["_jepa_avg_loss"] = _jepa_infer.get("avg_loss", _jepa_infer.get("epistemic_avg_loss", 1.0))
            new_S["_jepa_belief_avg_loss"] = _jepa_infer.get("belief_avg_loss", 1.0)
            if train:
                clean_S = {k: v for k, v in new_S.items() if not k.startswith("_")}
                world_model._jepa_update(E_t.S, a_t, clean_S)  # maintains avg_loss history for epa_loss
        except Exception as e:
            logger.debug("JEPA update failed (best-effort): %s", e)

    # ── Solver mesh prediction (when JEPA unavailable) ────────────────────────
    # Use SolverMesh to predict world state changes via simulation or constraint checks
    if world_model is None and solver_mesh is not None and evidence:
        try:
            # Apply evidence-driven deltas to world state as solver prediction
            for k, v in evidence.items():
                if k in new_S and isinstance(v, (int, float)) and not isinstance(v, bool):
                    new_S[k] = round(float(new_S.get(k, 0)) + (v - float(E_t.S.get(k, 0))) * 0.5, 4)
        except Exception as e:
            logger.debug("Solver mesh prediction failed (best-effort): %s", e)

    # ── A_{t+1}: update affordances ──────────────────────────────────────────
    new_A = set(E_t.A)
    if capability_graph is not None:
        try:
            gained, lost = capability_graph.delta_affordance(a_t, E_t.A)
            new_A = (new_A | gained) - lost
        except Exception as e:
            logger.debug("Affordance delta failed: %s", e)
    # Descriptor-level enables/disables (no graph needed)
    if desc is not None:
        new_A |= set(getattr(desc, "enables", []))
        new_A -= set(getattr(desc, "disables", []))

    # ── B_{t+1}: evidence fusion → belief update → K evolution ───────────────
    fused_evidence = evidence
    if fusion_engine is not None and evidence:
        try:
            # EvidenceFusionEngine.fuse() expects (list[Evidence], KnowledgePack, EpistemicState).
            # IMPROVEMENT: wraps entire evidence dict as a single Evidence — loses multi-source weighting.
            from src.knowledge.interface import Evidence
            from src.knowledge.item import Modality

            ev_list = [
                Evidence(
                    source=evidence.get("action", "epa"),
                    modality=Modality.DOCUMENT,
                    content=str(evidence),
                    confidence_delta=float(evidence.get("confidence_delta", 0.0)),
                )
            ]
            result = fusion_engine.fuse(ev_list, None, None)
            if result is not None and hasattr(result, "to_dict"):
                fused_evidence = {**evidence, **result.to_dict()}
        except Exception as e:
            logger.debug("Evidence fusion failed (best-effort): %s", e)

    # Build SimulationOutcome from fused evidence or symbolic approximation
    if fused_evidence:
        converged = bool(fused_evidence.get("converged", a_t in E_t.A))
        k_loss = float(fused_evidence.get("knowledge_loss", 0.0 if converged else 0.4))
        conf_delta = float(
            fused_evidence.get(
                "confidence_delta",
                (getattr(desc, "confidence_delta", 0.0) if desc else (0.1 if converged else -0.1)),
            )
        )
        violations = int(fused_evidence.get("constraint_violations", 0))
        findings_count = int(fused_evidence.get("findings_count", 0))
    else:
        positive = a_t in E_t.A
        converged = positive
        k_loss = 0.0 if positive else 0.4
        conf_delta = (
            getattr(desc, "confidence_delta", 0.1 if positive else -0.1) if desc else (0.1 if positive else -0.1)
        )
        violations = 0
        findings_count = 0

    outcome = SimulationOutcome(
        converged=converged,
        knowledge_loss=k_loss,
        confidence_delta=conf_delta,
        constraint_violations=violations,
        findings_count=findings_count,
    )
    # Belief update (K_t → K_{t+1} is embedded inside BeliefState.update)
    new_B = E_t.B.update(outcome)

    # Additional K evolution: retire weak items and apply evidence-driven evolution
    try:
        from cortex.epistemic import evolve_epistemic_state

        k_epistemic = _EState(items=list(new_B.knowledge))
        adv_dict = {
            "findings": [],
            "terminated": "attacks_exhausted" if converged else "max_rounds",
            "confidence_delta": conf_delta,
        }
        sim_dict = {"knowledge": k_loss}
        k_evolved = evolve_epistemic_state(k_epistemic, adv_dict, sim_dict)
        from cortex.epistemic import BeliefState as _BS, UncertaintyEstimate

        uncertainties = [UncertaintyEstimate.from_knowledge_item(k) for k in k_evolved.items]
        pooled = UncertaintyEstimate.pool(uncertainties) if uncertainties else UncertaintyEstimate()
        new_B = _BS(
            knowledge=k_evolved.items,
            confidence=k_evolved.confidence(),
            uncertainty=pooled,
        )
    except Exception as e:
        logger.debug("Knowledge evolution failed (best-effort): %s", e)

    # ── M_{t+1}: mesh summary + goal progress ────────────────────────────────
    new_M = dict(E_t.M)
    goal_progress = G_t.progress(new_S)
    new_M["goal_progress"] = goal_progress
    new_M["last_action"] = a_t
    if agent_mesh is not None:
        try:
            mesh_summary = agent_mesh.summary()
            new_M.update(mesh_summary)
            new_M["goal_progress"] = goal_progress
            new_M["last_action"] = a_t
        except Exception as e:
            logger.debug("Mesh update failed: %s", e)

    # ── Epistemic JEPA online learning ───────────────────────────────────────
    # All of E_{t+1} = (new_S, new_B, new_A, G_t, new_M) now known.
    # _epistemic_update() teaches the unified z_E predictor the full E transition.
    if train and world_model is not None and _jepa_infer is not None:
        try:
            clean_S_next = {k: v for k, v in new_S.items() if not k.startswith("_")}
            if hasattr(world_model, "_epistemic_update"):
                world_model._epistemic_update(
                    E_t.S,
                    E_t.B,
                    E_t.A,
                    G_t,
                    E_t.M,  # E_t components
                    a_t,
                    clean_S_next,
                    new_B,
                    new_A,
                    G_t,
                    new_M,  # E_{t+1} components
                )
            else:
                world_model._jepa_update_belief(E_t.S, E_t.B, a_t, new_B)
        except Exception as e:
            logger.debug("Belief predictor update failed: %s", e)

    return EpistemicPredictiveState(S=new_S, B=new_B, A=new_A, M=new_M)


# ---------------------------------------------------------------------------
# EPA loss function
# ---------------------------------------------------------------------------


def epa_loss(
    predicted: EpistemicPredictiveState,
    actual: EpistemicPredictiveState,
    *,
    world_model=None,
    l_k: float = 0.0,
    constraint_violations: int = 0,
    goal_progress: float = 0.0,
) -> dict[str, float]:
    """L_E = L_S + L_B + L_A + L_M + L_K + L_C + L_G

    L_S — world state prediction error (JEPA latent or Jaccard fallback)
    L_B — belief prediction error (confidence + uncertainty + knowledge quality)
    L_A — affordance prediction error (Jaccard distance on action sets)
    L_M — mesh prediction error (agent count delta)
    L_K — knowledge retrieval gap (expected vs actual quality)
    L_C — constraint violation loss: 1.0 per violation, 0.0 when none
    L_G — goal progress loss: 1.0 - goal_progress (0 = goal achieved, 1 = no progress)
    """
    if world_model is not None and getattr(world_model, "_step", 0) > 0:
        l_s = round(world_model.avg_loss, 4)
    else:
        l_s = _jaccard_dict_distance(predicted.S, actual.S)

    if world_model is not None and getattr(world_model, "_belief_step", 0) > 0:
        l_b = round(world_model.belief_avg_loss, 4)
    else:
        conf_err = abs(predicted.B.confidence - actual.B.confidence)
        unc_err = abs(predicted.B.uncertainty.total - actual.B.uncertainty.total)
        k_quality = actual.B.loss()
        l_b = round((conf_err + unc_err + k_quality) / 3.0, 4)

    l_a = _jaccard_set_distance(predicted.A, actual.A)

    pred_agents = set(predicted.M.get("agent_types", []))
    actual_agents = set(actual.M.get("agent_types", []))
    l_m = _jaccard_set_distance(pred_agents, actual_agents)

    l_k = round(float(l_k), 4)
    l_c = round(min(1.0, float(constraint_violations) * 0.25), 4)
    l_g = round(max(0.0, 1.0 - float(goal_progress)), 4)
    l_e = round(l_s + l_b + l_a + l_m + l_k + l_c + l_g, 4)

    return {
        "L_S": l_s,
        "L_B": l_b,
        "L_A": l_a,
        "L_M": l_m,
        "L_K": l_k,
        "L_C": l_c,
        "L_G": l_g,
        "L_E": l_e,
    }


def _jaccard_dict_distance(a: dict, b: dict) -> float:
    """Approximate world-state prediction error via key/value overlap."""
    all_keys = set(a) | set(b)
    if not all_keys:
        return 0.0
    matches = sum(1 for k in all_keys if a.get(k) == b.get(k))
    return round(1.0 - matches / len(all_keys), 4)


def _jaccard_set_distance(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return round(1.0 - len(a & b) / len(union), 4)
