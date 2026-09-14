"""simulator.py — AgentOS Unified Simulator

Unifies three previously separate simulation layers into a single entry point:

  Layer 1 — Sensor / event impact   (reactive, real-time, GMP-compliant)
  Layer 2 — World model snapshot     (environment state capture + DAG layout)
  Layer 3 — Workflow execution       (DAG execution, bandit agents, correction loop)
  Layer 4 — G→A→R adversarial loop  (Generate → Attack → Repair → repeat)

Public API
----------
simulate(prompt, *, context)                      async  Full unified simulation pipeline.
run_adversarial_refinement_loop(prompt, state)    async  G→A→R loop (simulation-only).
approve_and_execute(prompt, sim_result)           async  Post-approval execution vs predicted state.
execute_with_correction(prompt, predicted)        async  Retry loop with delta-based correction.

Internal helpers
----------------
_classify_prompt(prompt, context)
_run_sensor_layer(prompt, context, db)
_run_world_model_layer(prompt, context, db, domains, scope)
_run_workflow_layer(prompt, context, store)
_repair_from_findings(prompt, world_state, findings)
_capture_world_state(db)
_compare_states(before, after)
_build_execution_dag(steps)
_execute_dag(nodes, world_state, prompt, store)
_execute_single_step(capability, inputs, world_state, prompt, db)
_build_impact_assessment(step_results)
_build_correction_prompt(prompt, delta, results)
_sensor_document(db, context)
_asset_context(db, sensor)
_reading_classification(sensor, previous_value, new_value)
_sensor_effects(sensor, classification, asset_context, desired_result)
_web_search(prompt)
_document_search(db, prompt)
_retrieve_from_db(db, inputs, prompt)
_format_result(res)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# _execute_dag's bandit layer imports src.monkey_brain.execution / src.monkey_brain.learning,
# neither of which exists. The fallback is therefore permanent, not exceptional — warn once
# per process rather than staying silent (the old behaviour) or spamming every DAG node.
_bandit_warned = False


def _warn_bandit_unavailable(exc: Exception) -> None:
    global _bandit_warned
    if _bandit_warned:
        return
    _bandit_warned = True
    logger.warning(
        "[world_model_sim] bandit agent-selection layer unavailable (%s) — running every "
        "DAG node with NO agent selection, no reward recording, and no example storage. "
        "dry_run has nothing to guard in this mode.",
        exc,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOMAIN_COLLECTIONS: dict[str, list[str]] = {
    "sops": ["sops"],
    "process_definitions": ["process_definitions", "process_definition_canvas_layouts"],
    "material_flows": ["material_flows", "material_flow_nodes"],
    "equipment": ["pharmaceutical_machines", "pharmaceutical_equipment"],
    "inventory": [
        "inventory_items",
        "inventory_lots",
        "inventory_reservations",
        "stock_movements",
    ],
    "batch": [
        "batch_records",
        "batch_production_execution_records",
        "executed_bmr_records",
        "executed_bpr_records",
    ],
    "quality": [
        "cpp_cqa_registry",
        "ipc_result_records",
        "deviation_records",
        "capa_records",
    ],
    "maintenance": ["maintenance", "cleaning", "calibrations", "downtime"],
    "procurement": ["purchase_orders", "procurement_requisitions"],
    "orders": ["orders", "customer_orders", "work_orders"],
    "shipping": ["shipments", "shipment_lines", "shipping_events"],
    "documents": ["document_metadata", "documents", "uploaded_files"],
    "approvals": [
        "gxp_change_controls",
        "gxp_proposed_changes",
        "approvals",
        "notifications",
    ],
}

# ---------------------------------------------------------------------------
# Entity collections — tracked with name + status for loss calculation
# ---------------------------------------------------------------------------

ENTITY_TRACKED_COLLECTIONS: dict[str, dict[str, str]] = {
    "industrial_plants": {"name_field": "name", "status_field": "status"},
    "industrial_lines": {"name_field": "name", "status_field": "status"},
    "industrial_stages": {"name_field": "name", "status_field": "status"},
    "industrial_workstations": {"name_field": "name", "status_field": "status"},
    "pharmaceutical_machines": {"name_field": "name", "status_field": "status"},
    "pharmaceutical_equipment": {"name_field": "name", "status_field": "status"},
    "production_batches": {"name_field": "batch_number", "status_field": "status"},
    "batch_production_execution_records": {
        "name_field": "batch_execution_record_id",
        "status_field": "status",
    },
    "batch_step_executions": {"name_field": "step_name", "status_field": "status"},
    "work_orders": {"name_field": "title", "status_field": "status"},
    "gxp_change_controls": {"name_field": "title", "status_field": "status"},
}

SIM_RUNS_COLLECTION = "simulation_runs"
SIM_AUDITS_COLLECTION = "simulation_audits"
SIM_TRANSITIONS_COLLECTION = "simulation_transitions"

# Keywords that trigger the sensor layer
_SENSOR_KEYWORDS = {
    "sensor",
    "reading",
    "threshold",
    "alarm",
    "alert",
    "temperature",
    "pressure",
    "humidity",
    "weight",
    "variation",
    "drift",
    "excursion",
    "calibration",
    "measurement",
    "value",
    "spike",
    "deviation",
    "trend",
}

# Keywords that suggest a world-model / what-if query
_WHATIF_KEYWORDS = {
    "what if",
    "what would",
    "simulate",
    "impact",
    "scenario",
    "effect of",
    "consequence",
    "predict",
    "forecast",
    "change",
    "modify",
    "update",
    "replace",
    "swap",
    "upgrade",
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(value: Any) -> float | None:
    try:
        return None if (value is None or value == "") else float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _projection() -> dict[str, int]:
    return {"_id": 0, "embedding": 0}


# ---------------------------------------------------------------------------
# Prompt classification
# ---------------------------------------------------------------------------


def _classify_prompt(prompt: str, context: dict[str, Any]) -> dict[str, bool]:
    """Return which simulation layers should run for this prompt + context."""
    lower = prompt.lower()

    sensor_score = sum(1 for kw in _SENSOR_KEYWORDS if kw in lower)
    has_sensor_context = bool(
        context.get("sensor_id")
        or context.get("sensor_name")
        or context.get("previous_value") is not None
        or context.get("new_value") is not None
    )
    run_sensor = has_sensor_context or sensor_score >= 2

    whatif_score = sum(1 for kw in _WHATIF_KEYWORDS if kw in lower)
    run_world_model = whatif_score >= 1 or bool(context.get("scenario"))

    # Workflow layer always runs — it is the backbone execution engine.
    run_workflow = True

    return {
        "sensor": run_sensor,
        "world_model": run_world_model,
        "workflow": run_workflow,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def simulate(
    prompt: str,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Unified simulation entry point.

    Accepts a free-text prompt and an optional context dict.  Automatically
    activates whichever simulation layers are relevant, then merges their
    results into a single structured response.

    context keys (all optional)
    ----------------------------
    sensor_id, sensor_name          — trigger sensor layer
    previous_value, new_value       — required by sensor layer
    desired_result                  — goal statement for sensor layer
    scenario                        — dict describing a what-if scenario
    domains                         — list[str] of domain names for world model
    scope                           — dict of scope filters (plant_id, line_id …)
    store                           — AgentOS shared state store (Mem0)
    db                              — AsyncIOMotorDatabase (injected if available)
    """
    ctx = context or {}
    simulation_id = f"sim-{uuid4().hex[:12]}"
    started_at = _now()

    layers = _classify_prompt(prompt, ctx)
    logger.info("simulate(%s) layers=%s", simulation_id, layers)

    db = ctx.get("db")
    store = ctx.get("store")

    results: dict[str, Any] = {
        "simulation_id": simulation_id,
        "prompt": prompt,
        "layers_activated": layers,
        # Routing metadata injected by /simulate via UnifiedExecutor.create_goal()
        "intent": ctx.get("_intent", ""),
        "goal": ctx.get("_goal", ""),
        "sensor_result": None,
        "world_model_result": None,
        "workflow_result": None,
        "state_before": None,
        "state_after": None,
        "state_delta": None,
        "impact_assessment": None,
        "grounding_score": 0.0,
        "needs_correction": False,
        "correction_prompt": None,
        "feasibility_verdict": "unknown",
        "adversarial": None,
        "created_at": started_at,
    }

    # --- Layer 1: Sensor / event impact ---
    if layers["sensor"] and db is not None:
        try:
            sensor_result = await _run_sensor_layer(prompt, ctx, db)
            results["sensor_result"] = sensor_result
        except Exception as exc:
            logger.warning("Sensor layer failed: %s", exc)
            results["sensor_result"] = {"error": str(exc)}

    # --- Layer 2: World model snapshot ---
    if layers["world_model"] and db is not None:
        try:
            domains = ctx.get("domains") or list(DOMAIN_COLLECTIONS.keys())
            scope = ctx.get("scope") or {}
            scenario = ctx.get("scenario") or {
                "prompt": prompt,
                "scenario_type": "analysis",
            }
            world_model_result = await _run_world_model_layer(
                prompt, ctx, db, domains=domains, scope=scope, scenario=scenario
            )
            results["world_model_result"] = world_model_result
        except Exception as exc:
            logger.warning("World model layer failed: %s", exc)
            results["world_model_result"] = {"error": str(exc)}

    # --- Layer 3: Workflow execution ---
    if layers["workflow"]:
        try:
            state_before = await _capture_world_state(db)
            workflow_result = await _run_workflow_layer(prompt, ctx, store)
            state_after = await _capture_world_state(db)
            state_delta = _compare_states(state_before, state_after)
            impact = _build_impact_assessment(workflow_result.get("execution_results", []))

            results["state_before"] = state_before
            results["state_after"] = state_after
            results["state_delta"] = state_delta
            results["workflow_result"] = workflow_result
            results["impact_assessment"] = impact

            needs_correction = state_delta.get("has_changes", False) and state_delta.get("severity", "low") in (
                "medium",
                "high",
            )
            results["needs_correction"] = needs_correction
            if needs_correction:
                results["correction_prompt"] = _build_correction_prompt(
                    prompt, state_delta, workflow_result.get("execution_results", [])
                )
        except Exception as exc:
            logger.warning("Workflow layer failed: %s", exc)
            results["workflow_result"] = {"error": str(exc)}

    # --- Layer 4: G→A→R adversarial refinement loop ---
    # Simulation-only — never runs in the actual query path.
    # Uses the best available world state (state_after > state_before) as design context.
    adversarial_world_state = results.get("state_after") or results.get("state_before") or {}
    try:
        adv_loop = await run_adversarial_refinement_loop(
            prompt, adversarial_world_state, max_rounds=3, timeout_per_round=90.0
        )
        results["adversarial"] = adv_loop
        last_adv = (adv_loop.get("rounds") or [{}])[-1].get("attack") or {}
        logger.info(
            "[simulate] G→A→R: rounds=%d converged=%s findings=%d",
            adv_loop.get("rounds_run", 0),
            adv_loop.get("converged", False),
            len(last_adv.get("findings", [])),
        )
    except Exception as exc:
        logger.warning("[simulate] G→A→R loop failed: %s", exc)
        results["adversarial"] = {"error": str(exc)}

    # --- Merge grounding score across layers ---
    # Apply adversarial confidence delta to grounding score.
    results["grounding_score"] = _compute_grounding_score(results)
    adv_delta = (results.get("adversarial") or {}).get("total_confidence_delta", 0.0)
    results["grounding_score"] = max(0.0, min(1.0, results["grounding_score"] + adv_delta))
    results["feasibility_verdict"] = _compute_feasibility(results)
    results["finished_at"] = _now()

    # --- Loss calculation: compare predicted (state_before) vs actual (state_after) ---
    if results["state_before"] is not None and results["state_after"] is not None:
        loss_result = _calculate_loss(results["state_before"], results["state_after"], prompt)
        results["loss"] = loss_result

        # --- Bellman transition: store (s, a, r, s') ---
        if db is not None:
            action = ctx.get("action") or prompt[:200]
            await _store_transition(
                db,
                simulation_id=simulation_id,
                prompt=prompt,
                action=action,
                state_before=results["state_before"],
                state_after=results["state_after"],
                loss_result=loss_result,
                grounding_score=results["grounding_score"],
                feasibility_verdict=results["feasibility_verdict"],
            )
    else:
        results["loss"] = {
            "loss": 1.0,
            "total_facts": 0,
            "matching_facts": 0,
            "count_accuracy": 0.0,
            "name_accuracy": 0.0,
            "status_accuracy": 0.0,
            "entity_details": [],
        }

    # --- Dispatch result to shared state if store is available ---
    if store is not None:
        try:
            from src.monkey_brain.events.reducer import Action

            await store.dispatch(
                Action(
                    action_type="simulation.completed",
                    payload={
                        "simulation_id": simulation_id,
                        "grounding_score": results["grounding_score"],
                        "feasibility_verdict": results["feasibility_verdict"],
                        "layers_activated": layers,
                        "needs_correction": results["needs_correction"],
                    },
                    source_thread_id=f"simulator-{simulation_id}",
                )
            )
        except Exception as exc:
            logger.warning("Store dispatch failed: %s", exc)

    # --- Persist to MongoDB if db is available ---
    if db is not None:
        try:
            await db[SIM_RUNS_COLLECTION].insert_one({**_json_safe(results), "_inserted_at": _now()})
        except Exception as exc:
            logger.warning("Simulation persistence failed: %s", exc)

    return results


async def run_adversarial_refinement_loop(
    prompt: str,
    world_state: dict[str, Any],
    *,
    max_rounds: int = 3,
    timeout_per_round: float = 90.0,
) -> dict[str, Any]:
    """G→A→R loop: Generate → Attack → Repair → repeat until convergence or max_rounds.

    Simulation-only. The dry_run guard in _execute_dag() and the simulation-only
    Layer 4 in simulate() ensure this never runs in the actual query path.

      Generate  — world state passed in (captured before the loop starts).
      Attack    — all adversarial agents run concurrently with their problem-typed solvers.
      Repair    — LLM proposes minimal semantic patches based on solver-verified findings.
      Repeat    — patched design becomes the prompt for the next attack round.
      Converged — when an attack round produces zero findings; design held up.
    """
    from src.cortex.adversarial_agents import run_adversarial_pass
    from cortex.adversarial_agents import SimulationLoss as _SL
    from cortex.epistemic import EpistemicState, evolve_epistemic_state

    rounds: list[dict[str, Any]] = []
    current_prompt = prompt
    total_confidence_delta = 0.0

    # Session 5 — Learning Loop: initialise epistemic state from world_state
    epistemic = EpistemicState.from_world_state(world_state)

    for round_num in range(1, max_rounds + 1):
        logger.info("[G→A→R] round %d/%d — attacking", round_num, max_rounds)

        # Inject current epistemic state so L_knowledge reflects K_t for this round
        world_state["epistemic_state"] = epistemic.to_dict()

        # Attack — all problem-typed solvers run concurrently
        adv = await run_adversarial_pass(current_prompt, world_state, timeout=timeout_per_round)
        total_confidence_delta += adv.confidence_delta

        adv_dict: dict[str, Any] = {
            "findings": [
                {
                    "agent": f.agent,
                    "solver": f.solver,
                    "finding_type": f.finding_type,
                    "description": f.description,
                    "witness": f.witness,
                    "severity": f.severity,
                    "confidence": f.confidence,
                }
                for f in adv.findings
            ],
            "agents_run": adv.agents_run,
            "solvers_run": adv.solvers_run,
            "confidence_delta": adv.confidence_delta,
            "simulation_loss": adv.simulation_loss.to_dict(),
            "summary": adv.summary,
        }

        sim_loss_dict = adv.simulation_loss.to_dict()

        # Session 4 — K_t → K_{t+1}: evolve epistemic state from round outcome
        epistemic = evolve_epistemic_state(epistemic, adv_dict, sim_loss_dict)

        if not adv.findings:
            rounds.append(
                {
                    "round": round_num,
                    "attack": adv_dict,
                    "repair": None,
                    "terminated": "attacks_exhausted",
                    "epistemic": epistemic.summary(),
                }
            )
            logger.info("[G→A→R] round %d — no findings, converged", round_num)
            break

        # Repair — LLM generates hypothesis patch (Rule 7, Rule 11)
        repair = await _repair_from_findings(current_prompt, world_state, adv.findings)
        rounds.append(
            {
                "round": round_num,
                "attack": adv_dict,
                "repair": repair,
                "epistemic": epistemic.summary(),
            }
        )
        logger.info(
            "[G→A→R] round %d — %d findings, design patched",
            round_num,
            len(adv.findings),
        )
        current_prompt = repair.get("patched_prompt", current_prompt)

    else:
        if rounds:
            rounds[-1]["terminated"] = "max_rounds"

    converged = bool(rounds) and rounds[-1].get("terminated") == "attacks_exhausted"

    round_losses = [
        _SL(**r["attack"]["simulation_loss"])
        for r in rounds
        if isinstance(r.get("attack", {}).get("simulation_loss"), dict)
    ]
    peak_simulation_loss = _SL.pool(round_losses).to_dict()

    return {
        "rounds": rounds,
        "rounds_run": len(rounds),
        "max_rounds": max_rounds,
        "converged": converged,
        "final_prompt": current_prompt,
        "total_confidence_delta": round(total_confidence_delta, 4),
        "simulation_loss": peak_simulation_loss,
        "final_epistemic": epistemic.to_dict(),
    }


async def analyze_sensor_event_impact(
    prompt: str,
    context: dict[str, Any],
    db: Any,
) -> dict[str, Any]:
    """Public entry point for sensor/event impact analysis."""
    return await _run_sensor_layer(prompt, context, db)


async def approve_and_execute(
    prompt: str,
    simulation_result: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute after user approval and compare actual vs predicted state.

    Runs execute_with_correction against the predicted state captured during
    simulate(), then returns a signed audit package with the delta.
    """
    predicted_state = simulation_result.get("state_after") or {}
    result = await execute_with_correction(prompt, predicted_state, context=context)
    return {
        "prompt": prompt,
        "simulation_id": simulation_result.get("simulation_id"),
        "execution_result": result,
        "predicted_state": predicted_state,
        "actual_state": result.get("state_after", {}),
        "delta": result.get("delta_with_predicted", {}),
        "corrections_applied": result.get("correction_count", 0),
        "final_status": result.get("final_status", "unknown"),
        "approved_at": _now(),
    }


async def execute_with_correction(
    prompt: str,
    predicted_state: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Execute and correct based on delta against a predicted state.

    Each retry replaces the prompt with the auto-generated correction prompt
    so the compiler can pick a safer workflow on the next attempt.
    """
    correction_count = 0
    last_result: dict[str, Any] = {}
    current_prompt = prompt

    for attempt in range(max_retries):
        result = await simulate(current_prompt, context=context)
        if "error" in result:
            return {"error": result["error"], "attempts": attempt + 1}

        actual_state = result.get("state_after") or {}
        delta = _compare_states(predicted_state, actual_state)

        last_result = result
        last_result["attempt"] = attempt + 1
        last_result["delta_with_predicted"] = delta

        if not delta.get("has_changes") or delta.get("severity") == "low":
            last_result["correction_applied"] = False
            last_result["final_status"] = "accepted"
            return last_result

        correction_count += 1
        correction_prompt = _build_correction_prompt(
            current_prompt,
            delta,
            result.get("workflow_result", {}).get("execution_results", []),
        )
        last_result["correction_prompt"] = correction_prompt
        last_result["correction_applied"] = True
        current_prompt = correction_prompt

    last_result["final_status"] = "max_retries_exceeded"
    last_result["correction_count"] = correction_count
    return last_result


# ---------------------------------------------------------------------------
# Layer 1 — Sensor / event impact
# ---------------------------------------------------------------------------


async def _run_sensor_layer(
    prompt: str,
    context: dict[str, Any],
    db: Any,
) -> dict[str, Any]:
    """Classify a sensor event and determine GMP-required actions."""
    sensor = await _sensor_document(db, context)
    if not sensor:
        # Try to infer sensor from prompt when no explicit sensor context given.
        sensor = (
            await db["sensors"].find_one(
                {
                    "$or": [
                        {"name": {"$regex": re.escape(prompt[:60]), "$options": "i"}},
                        {
                            "sensor_type": {
                                "$regex": r"weight|temperature|pressure|humidity",
                                "$options": "i",
                            }
                        },
                    ]
                },
                _projection(),
            )
            or {}
        )

    if not sensor:
        return {
            "status": "no_sensor_found",
            "message": "No sensor matched the prompt or context.",
        }

    previous_value = _num(context.get("previous_value"))
    new_value = _num(context.get("new_value"))

    if previous_value is None or new_value is None:
        return {
            "status": "sensor_found",
            "sensor": sensor,
            "message": "Sensor located but previous_value / new_value not provided — skipping classification.",
        }

    desired_result = _text(context.get("desired_result")) or (
        "Keep the process compliant and prevent unsafe downstream release."
    )
    asset_ctx = await _asset_context(db, sensor)
    classification = _reading_classification(sensor, previous_value, new_value)
    effects = _sensor_effects(sensor, classification, asset_ctx, desired_result)

    return {
        "status": "analyzed",
        "sensor": {
            "sensor_id": sensor.get("sensor_id"),
            "name": sensor.get("name"),
            "sensor_type": sensor.get("sensor_type"),
            "unit": sensor.get("measurement_unit"),
        },
        "reading_change": classification,
        "effects": effects,
        "asset_context": {
            "stage_id": asset_ctx.get("stage_id"),
            "linked_sops_count": len(asset_ctx.get("linked_sops", [])),
            "related_work_orders_count": len(asset_ctx.get("related_work_orders", [])),
        },
    }


async def _sensor_document(db: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    if db is None:
        return None
    sensor_id = _text(context.get("sensor_id"))
    sensor_name = _text(context.get("sensor_name"))
    clauses: list[dict[str, Any]] = []
    if sensor_id:
        clauses += [
            {"sensor_id": {"$regex": f"^{re.escape(sensor_id)}$", "$options": "i"}},
            {"id": {"$regex": f"^{re.escape(sensor_id)}$", "$options": "i"}},
        ]
    if sensor_name:
        clauses.append({"name": {"$regex": re.escape(sensor_name), "$options": "i"}})
    if not clauses:
        return None
    return await db["sensors"].find_one({"$or": clauses}, _projection())


async def _asset_context(db: Any, sensor: dict[str, Any]) -> dict[str, Any]:
    if db is None:
        return {
            "machine": None,
            "equipment": None,
            "workstation": None,
            "stage_id": None,
            "linked_sops": [],
            "related_work_orders": [],
        }

    machine_id = _text(sensor.get("machine_id"))
    equipment_id = _text(sensor.get("equipment_id"))
    workstation_id = _text(sensor.get("workstation_id"))

    machine = None
    if machine_id:
        machine = await db["pharmaceutical_machines"].find_one(
            {"$or": [{"machine_id": machine_id}, {"id": machine_id}]}, _projection()
        )
    equipment = None
    if equipment_id:
        equipment = await db["pharmaceutical_equipment"].find_one(
            {"$or": [{"equipment_id": equipment_id}, {"id": equipment_id}]},
            _projection(),
        )

    if not workstation_id:
        workstation_id = _text((machine or {}).get("workstation_id") or (equipment or {}).get("workstation_id"))
    workstation = None
    if workstation_id:
        workstation = await db["workstations"].find_one(
            {"$or": [{"id": workstation_id}, {"workstation_id": workstation_id}]},
            _projection(),
        )

    stage_id = _text(
        (machine or {}).get("stage_id")
        or (equipment or {}).get("stage_id")
        or (workstation or {}).get("stage_id")
        or sensor.get("stage_id")
    )

    linked_sops: list[dict] = []
    sop_clauses: list[dict] = []
    for key, val in [
        ("stage_id", stage_id),
        ("workstation_id", workstation_id),
        ("machine_id", machine_id),
        ("equipment_id", equipment_id),
    ]:
        if val:
            sop_clauses.extend([{key: val}, {f"process_definition.{key}": val}])
    if sop_clauses:
        linked_sops = await db["sops"].find({"$or": sop_clauses}, _projection()).limit(25).to_list(25)

    related_work_orders: list[dict] = []
    wo_clauses: list[dict] = []
    if machine_id:
        wo_clauses += [{"machine_id": machine_id}, {"equipment_id": machine_id}]
    if equipment_id:
        wo_clauses.append({"equipment_id": equipment_id})
    if wo_clauses:
        related_work_orders = await db["work_orders"].find({"$or": wo_clauses}, _projection()).limit(10).to_list(10)

    return {
        "machine": machine,
        "equipment": equipment,
        "workstation": workstation,
        "stage_id": stage_id or None,
        "linked_sops": linked_sops,
        "related_work_orders": related_work_orders,
    }


def _reading_classification(sensor: dict[str, Any], previous_value: float, new_value: float) -> dict[str, Any]:
    low = _num(sensor.get("alert_threshold_low"))
    high = _num(sensor.get("alert_threshold_high"))
    range_min = _num(sensor.get("measurement_range_min"))
    range_max = _num(sensor.get("measurement_range_max"))

    delta = new_value - previous_value
    delta_pct = (delta / previous_value * 100.0) if previous_value else None
    threshold_crossed = bool((high is not None and new_value >= high) or (low is not None and new_value <= low))
    range_exceeded = bool(
        (range_max is not None and new_value > range_max) or (range_min is not None and new_value < range_min)
    )
    approaching_high = bool(high is not None and new_value < high and new_value >= high * 0.85)
    approaching_low = bool(low is not None and new_value > low and new_value <= low * 1.15)
    significant_delta = bool(delta_pct is not None and abs(delta_pct) >= 5.0)

    if range_exceeded:
        severity, event_state = "critical", "measurement_range_exceeded"
    elif threshold_crossed:
        severity, event_state = "high", "alert_threshold_crossed"
    elif approaching_high or approaching_low or significant_delta:
        severity, event_state = "medium", "warning_trend"
    else:
        severity, event_state = "low", "within_limits"

    return {
        "previous_value": previous_value,
        "new_value": new_value,
        "delta": round(delta, 6),
        "delta_pct": round(delta_pct, 3) if delta_pct is not None else None,
        "alert_threshold_low": low,
        "alert_threshold_high": high,
        "measurement_range_min": range_min,
        "measurement_range_max": range_max,
        "threshold_crossed": threshold_crossed,
        "range_exceeded": range_exceeded,
        "approaching_high_threshold": approaching_high,
        "approaching_low_threshold": approaching_low,
        "significant_delta": significant_delta,
        "event_state": event_state,
        "severity": severity,
    }


def _sensor_effects(
    sensor: dict[str, Any],
    classification: dict[str, Any],
    asset_ctx: dict[str, Any],
    desired_result: str,
) -> dict[str, Any]:
    sensor_name = _text(sensor.get("name") or sensor.get("sensor_id") or "sensor")
    unit = _text(sensor.get("measurement_unit")) or "units"
    threshold_crossed = classification["threshold_crossed"]
    warning = classification["event_state"] == "warning_trend"
    new_val = classification["new_value"]

    if threshold_crossed:
        summary = f"{sensor_name} crossed alert threshold at {new_val} {unit}."
        operational = [
            "Place affected asset/process on controlled hold.",
            "Execute threshold-response checklist.",
            "Notify Facilities, Engineering, and QA.",
            "Investigate sensor validity and product impact before release.",
        ]
        changes_needed = [
            {
                "area": "Quality workflow",
                "change": "Open deviation/CAPA record.",
                "approval_required": True,
                "target_records": ["deviation_records", "capa_records"],
            },
            {
                "area": "Release gate",
                "change": "Hold downstream release until QA disposition.",
                "approval_required": True,
            },
        ]
    elif warning:
        summary = f"{sensor_name} is trending toward threshold at {new_val} {unit}."
        operational = [
            "Continue under heightened monitoring.",
            "Verify sensor calibration status.",
            "Prepare escalation if trend persists.",
        ]
        changes_needed = [
            {
                "area": "Preventive action",
                "change": "Schedule follow-up monitoring.",
                "approval_required": False,
                "target_records": ["calibration_records", "maintenance_logs"],
            },
        ]
    else:
        summary = f"{sensor_name} changed to {new_val} {unit}, within configured limits."
        operational = ["Record the reading and continue normal monitoring."]
        changes_needed = [
            {
                "area": "Event record",
                "change": "Record before/after reading and delta.",
                "approval_required": False,
            },
        ]

    return {
        "summary": summary,
        "desired_result": desired_result,
        "impacted": {
            "sensor": {
                "sensor_id": sensor.get("sensor_id"),
                "name": sensor_name,
                "unit": unit,
                "status": sensor.get("status"),
            },
            "asset": {
                "machine_id": sensor.get("machine_id"),
                "equipment_id": sensor.get("equipment_id"),
                "stage_id": asset_ctx.get("stage_id"),
            },
            "linked_sops": [
                {
                    "id": s.get("id") or s.get("sop_id"),
                    "title": s.get("title") or s.get("name"),
                }
                for s in asset_ctx.get("linked_sops", [])
            ],
        },
        "result_effect": {
            "event_state": classification["event_state"],
            "severity": classification["severity"],
            "operational_effects": operational,
        },
        "changes_needed": changes_needed,
    }


# ---------------------------------------------------------------------------
# Layer 2 — World model snapshot
# ---------------------------------------------------------------------------


async def _run_world_model_layer(
    prompt: str,
    context: dict[str, Any],
    db: Any,
    *,
    domains: list[str],
    scope: dict[str, Any],
    scenario: dict[str, Any],
    sample_limit: int = 25,
) -> dict[str, Any]:
    """Build a world state snapshot and compile a simulation DAG layout."""
    if db is None:
        return {"status": "skipped", "reason": "No database connection."}

    # Build scoped query
    scope_keys = (
        "plant_id",
        "line_id",
        "stage_id",
        "workstation_id",
        "machine_id",
        "equipment_id",
        "batch_id",
        "order_id",
    )
    values = {k: scope[k] for k in scope_keys if scope.get(k)}
    query = {"$or": [{k: v} for k, v in values.items()]} if values else {}
    proj = _projection()

    # Collect domain collections
    collections: list[str] = []
    for domain in domains:
        collections.extend(DOMAIN_COLLECTIONS.get(domain, [domain]))
    collections = list(dict.fromkeys(collections))

    sampled: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for col in collections:
        try:
            limit = sample_limit if query else min(sample_limit, 5)
            docs = await db[col].find(query or {}, proj).limit(limit).to_list(limit)
            sampled[col] = [_compact(d) for d in docs]
            counts[col] = await db[col].count_documents(query or {})
        except Exception as e:
            logger.debug("Collection sampling failed for %s: %s", col, e)
            sampled[col] = []
            counts[col] = 0

    snapshot_id = f"snapshot-{uuid4().hex[:10]}"
    snapshot = {
        "snapshot_id": snapshot_id,
        "scenario": scenario,
        "scope": scope,
        "domains": domains,
        "record_counts": counts,
        "records_summary": {col: len(recs) for col, recs in sampled.items()},
        "created_at": _now(),
    }

    # LLM impact assessment
    impact = await _llm_impact_assessment(prompt, scenario, snapshot)

    return {
        "status": "completed",
        "snapshot_id": snapshot_id,
        "record_counts": counts,
        "impact_assessment": impact,
        "scenario": scenario,
    }


async def _llm_impact_assessment(
    prompt: str,
    scenario: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Call LLM for world model impact assessment. Tries Ollama → OpenRouter → fallback."""
    system_msg = (
        "You are a world model simulation impact assessor for GMP pharmaceutical manufacturing. "
        "Given a prompt, scenario, and world state snapshot, assess the impact step by step. "
        "Identify affected entities, state changes, risks, and required approvals. "
        "Return only valid JSON."
    )
    user_msg = json.dumps(
        {
            "prompt": prompt,
            "scenario": scenario,
            "world_state_summary": {
                "domains": snapshot.get("domains", []),
                "record_counts": snapshot.get("record_counts", {}),
            },
            "required_response_schema": {
                "step_analysis": [
                    {
                        "step_number": 0,
                        "entities_affected": [],
                        "impact_on_state": "",
                        "risks_or_constraints": [],
                    }
                ],
                "cumulative_results": {
                    "total_entities_affected": 0,
                    "collections_impacted": [],
                    "approvals_required": [],
                    "compliance_impacts": [],
                },
                "assessment_summary": "",
                "feasibility_verdict": "feasible|risky|not_recommended",
                "recommended_precautions": [],
            },
        },
        default=str,
    )

    result = await _call_ollama(system_msg, user_msg)
    if result:
        result["determination_source"] = "ollama"
        return result

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if api_key:
        result = await _call_openrouter(system_msg, user_msg, api_key)
        if result:
            result["determination_source"] = "openrouter"
            return result

    return {
        "assessment_summary": f"Fallback assessment for: {prompt[:120]}",
        "feasibility_verdict": "feasible",
        "step_analysis": [],
        "cumulative_results": {
            "total_entities_affected": 0,
            "collections_impacted": [],
            "approvals_required": [],
            "compliance_impacts": [],
        },
        "recommended_precautions": [],
        "determination_source": "fallback",
    }


async def _call_ollama(system_msg: str, user_msg: str) -> dict | None:
    try:
        import httpx

        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "gemma3:latest")
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{base}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
        if r.status_code != 200:
            return None
        content = r.json().get("message", {}).get("content", "")
        return _parse_llm_json(content)
    except Exception as exc:
        logger.warning("Ollama call failed: %s", exc)
        return None


async def _call_openrouter(system_msg: str, user_msg: str, api_key: str) -> dict | None:
    try:
        import httpx

        base = os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
        if r.status_code != 200:
            return None
        content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        return _parse_llm_json(content)
    except Exception as exc:
        logger.warning("OpenRouter call failed: %s", exc)
        return None


async def _repair_from_findings(
    prompt: str,
    world_state: dict[str, Any],
    findings: list[Any],
) -> dict[str, Any]:
    """LLM patches the design based on adversarial findings (Rule 7 + Rule 11).

    Treats solver-verified findings as hypotheses; LLM proposes minimal semantic
    repairs. The patched design becomes the prompt for the next attack round.
    Runs only during simulation — called exclusively from run_adversarial_refinement_loop().
    """
    summaries = [
        f"{getattr(f, 'finding_type', f.get('finding_type', '?'))} "
        f"[{getattr(f, 'severity', f.get('severity', '?'))}]: "
        f"{getattr(f, 'description', f.get('description', '?'))}"
        for f in findings[:10]
    ]
    system_msg = (
        "You are a design repair agent. Symbolic solvers found flaws in the design. "
        "Propose the minimal set of semantic repairs that eliminates each finding. "
        "Return valid JSON with keys: "
        "'patched_prompt' (full repaired design description), "
        "'repairs' (list of {finding_type, repair, confidence})."
    )
    user_msg = json.dumps(
        {
            "original_design": prompt[:2000],
            "adversarial_findings": summaries,
            "instruction": (
                "Patch the design to address each finding. "
                "Add missing entities, resolve contradictions, clarify ambiguous transitions. "
                "Return the complete patched design as 'patched_prompt'."
            ),
        }
    )

    result = await _call_ollama(system_msg, user_msg)
    if not result:
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if api_key:
            result = await _call_openrouter(system_msg, user_msg, api_key)

    if result and "patched_prompt" in result:
        return result

    # Fallback: append findings as explicit repair constraints
    constraint_block = "\n".join(f"  - REPAIR NEEDED: {s}" for s in summaries)
    return {
        "patched_prompt": (f"{prompt}\n\nDesign corrections from adversarial analysis:\n{constraint_block}"),
        "repairs": [
            {
                "finding_type": getattr(f, "finding_type", "?"),
                "repair": f"Address: {getattr(f, 'description', '?')[:120]}",
                "confidence": 0.5,
            }
            for f in findings[:10]
        ],
        "source": "fallback",
    }


def _parse_llm_json(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.debug("Exception caught: %s", e)
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            logger.debug("Exception caught: %s", e)
    s, e = content.find("{"), content.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(content[s : e + 1])
        except json.JSONDecodeError:
            logger.debug("Exception caught: %s", e)
    return None


# ---------------------------------------------------------------------------
# Layer 3 — Workflow execution
# ---------------------------------------------------------------------------


async def _run_workflow_layer(
    prompt: str,
    context: dict[str, Any],
    store: Any,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Classify goal → compile workflow → build DAG → execute with bandit agents.

    dry_run=True (the default for simulation) prevents bandit writes and event
    dispatches from contaminating the actual run's learned state.
    """
    try:
        from src.monkey_brain.goals.goal_classifier import classify_goal
        from compiler import compile as _compile  # type: ignore[import]
    except ImportError as exc:
        logger.warning("Workflow layer imports unavailable: %s", exc)
        return {"status": "skipped", "reason": str(exc), "execution_results": []}

    goal = await classify_goal(prompt)
    if not goal:
        try:
            from src.monkey_brain.goals.goal import Goal, GoalType

            goal = Goal(
                name="simulation",
                goal_type=GoalType.ANALYZE,
                required_inputs=["question"],
                required_outputs=["impact_assessment"],
                pipeline_id="simulation",
                expert="simulation",
                metadata={"question": prompt},
            )
        except ImportError:
            return {
                "status": "skipped",
                "reason": "Goal class unavailable.",
                "execution_results": [],
            }

    compilation_result = await _compile(goal)
    if "error" in compilation_result:
        return {
            "status": "error",
            "error": compilation_result["error"],
            "execution_results": [],
        }

    steps = compilation_result.get("steps", [])
    if not steps:
        return {"status": "no_steps", "execution_results": []}

    dag = _build_execution_dag(steps)

    db = context.get("db")
    execution_results = await _execute_dag(list(dag.nodes), {"status": "ready"}, prompt, store, db, dry_run=dry_run)

    return {
        "status": "completed",
        "goal": getattr(goal, "name", str(goal)),
        "workflow_id": compilation_result.get("workflow_id"),
        "steps_count": len(steps),
        "execution_results": execution_results,
    }


# ---------------------------------------------------------------------------
# DAG construction + execution
# ---------------------------------------------------------------------------


def _build_execution_dag(steps: list[dict[str, Any]]) -> Any:
    from src.monkey_brain.kernel.execute.runtime.dag import ExecutionDAG
    from src.monkey_brain.kernel.execute.runtime.dag import DAGNode
    from src.monkey_brain.kernel.execute.runtime.dag import DAGEdge

    nodes, edges = [], []
    for step in steps:
        nodes.append(
            DAGNode(
                node_id=step.get("id", "unknown"),
                operator_type=step.get("capability", "unknown"),
                metadata={
                    "inputs": step.get("inputs", []),
                    "outputs": step.get("outputs", []),
                },
            )
        )
        for dep in step.get("dependencies", []):
            edges.append(DAGEdge(source_node_id=dep, target_node_id=step.get("id", "unknown")))

    return ExecutionDAG(dag_id=f"dag-{uuid4().hex[:8]}", nodes=nodes, edges=edges)


async def _execute_dag(
    scheduled_nodes: list,
    world_state: dict[str, Any],
    prompt: str,
    store: Any,
    db: Any,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Execute DAG nodes.

    dry_run=True skips all Redis bandit writes and event-store dispatches so
    simulation runs never contaminate the actual run's learned state.
    """
    try:
        from src.monkey_brain.execution.agents.agent_bootstrap import AgentBootstrap
        from src.monkey_brain.learning.capability_bandit import compute_reward
        from src.monkey_brain.learning import store_example, get_similar_examples
        from src.monkey_brain.learning.agent_bandit import (
            select_agent,
            record_agent_reward,
        )
    except ImportError as exc:
        # NOTE: as of this writing NEITHER src.monkey_brain.execution NOR
        # src.monkey_brain.learning exists, so this branch is the one that ALWAYS runs.
        # Everything below it — bandit agent selection, reward recording, example
        # storage, and the whole reason `dry_run` exists — is unreachable. That was
        # completely silent (a bare `except ImportError: pass`-style fallback), so the
        # system looked like it was doing bandit-driven agent selection while it was
        # really executing every node with no selection at all. Say it out loud, once.
        _warn_bandit_unavailable(exc)
        results = []
        for node in scheduled_nodes:
            exec_result = await _execute_single_step(
                node.operator_type,
                node.metadata.get("inputs", []) if node.metadata else [],
                world_state,
                prompt,
                db,
            )
            # Same keys as the bandit path below, so downstream readers see one shape
            # rather than KeyError-ing on selection_method / available_agents.
            results.append(
                {
                    "node_id": node.node_id,
                    "operator_type": node.operator_type,
                    "available_agents": [],
                    "agent_selected": None,
                    "inputs": node.metadata.get("inputs", []) if node.metadata else [],
                    "result": exec_result,
                    "selection_method": "unavailable",
                }
            )
        return results

    agent_bootstrap = AgentBootstrap()
    all_agents = agent_bootstrap.list_agents()
    cap_to_agents: dict[str, list[str]] = {}
    for agent in all_agents:
        for cap in agent.capabilities:
            cap_to_agents.setdefault(cap, []).append(agent.agent_id)

    redis = _connect_redis()
    # Read-only lookup — safe in both dry_run and normal mode.
    await get_similar_examples(redis, prompt, limit=2)

    results: list[dict[str, Any]] = []
    for node in scheduled_nodes:
        node_id = node.node_id
        operator_type = node.operator_type
        inputs = node.metadata.get("inputs", []) if node.metadata else []

        available = list(
            set(aid for cap, aids in cap_to_agents.items() if cap.lower() == operator_type.lower() for aid in aids)
        )

        step: dict[str, Any] = {
            "node_id": node_id,
            "operator_type": operator_type,
            "available_agents": available,
            "agent_selected": None,
            "inputs": inputs,
            "result": None,
            "selection_method": "none",
        }

        if available:
            selected_id = await select_agent(redis, operator_type, available) if len(available) > 1 else available[0]
            step["agent_selected"] = selected_id
            step["selection_method"] = "bandit" if len(available) > 1 else "single"

            exec_result = await _execute_single_step(operator_type, inputs, world_state, prompt, db)
            step["result"] = exec_result

            if not dry_run:
                # Write bandit feedback only for actual runs — never for simulation.
                reward = compute_reward(exec_result)
                await record_agent_reward(redis, selected_id, operator_type, reward)
                await store_example(
                    redis,
                    prompt,
                    operator_type,
                    _format_result(exec_result),
                    reward,
                    [{"capability": operator_type, "inputs": inputs}],
                )
        else:
            step["result"] = await _execute_single_step(operator_type, inputs, world_state, prompt, db)
            step["selection_method"] = "fallback"

        # Dispatch step result to shared state — skipped in dry_run to avoid
        # contaminating the actual run's event log with simulation events.
        if store is not None and not dry_run:
            try:
                from src.monkey_brain.events.reducer import Action

                await store.dispatch(
                    Action(
                        action_type="step.completed",
                        payload={
                            "step_id": node_id,
                            "operator_type": operator_type,
                            "result": str(step["result"])[:200],
                            "status": "completed",
                            "agent_selected": step["agent_selected"],
                            "selection_method": step["selection_method"],
                        },
                        source_thread_id=f"simulator-dag-{node_id}",
                    )
                )
            except Exception as exc:
                logger.warning("store.dispatch failed for %s: %s", node_id, exc)

        results.append(step)

    return results


async def _execute_single_step(
    capability: str,
    inputs: list[str],
    world_state: dict[str, Any],
    prompt: str,
    db: Any,
) -> dict[str, Any]:
    cap = capability.lower()
    if cap == "retrieve":
        if db is not None:
            result = await _retrieve_from_db(db, inputs, prompt)
            if result.get("results_found", 0) > 0:
                return result
        return await _web_search(prompt)
    elif cap == "web_search":
        return await _web_search(prompt)
    elif cap == "document_search":
        return await _document_search(db, prompt) if db else {"status": "skipped"}
    elif cap == "transform":
        return {
            "status": "transformed",
            "effect": f"Applied transformation to {inputs}",
        }
    elif cap == "aggregate":
        return {"status": "aggregated", "effect": f"Aggregated data from {inputs}"}
    elif cap == "validate":
        return {"status": "validated", "effect": f"Validated inputs {inputs}"}
    else:
        return {
            "status": "simulated",
            "effect": f"Simulated {capability} with inputs {inputs}",
        }


# ---------------------------------------------------------------------------
# World state capture + comparison (entity-aware)
# ---------------------------------------------------------------------------


async def _capture_world_state(db: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "collections": {},
        "entities": {},
        "timestamp": time.time(),
    }
    if db is None:
        return state
    try:
        for col in (await db.list_collection_names())[:30]:
            try:
                count = await db[col].count_documents({})
                state["collections"][col] = {"count": count}
            except Exception as e:
                logger.debug("Collection count failed for %s: %s", col, e)

        for col, fields in ENTITY_TRACKED_COLLECTIONS.items():
            try:
                name_f = fields["name_field"]
                status_f = fields["status_field"]
                entities: list[dict] = []
                async for doc in db[col].find({}, {name_f: 1, status_f: 1, "_id": 0}).limit(100):
                    entities.append(
                        {
                            "name": doc.get(name_f, ""),
                            "status": doc.get(status_f, ""),
                        }
                    )
                status_counts: dict[str, int] = {}
                for e in entities:
                    s = e["status"] or "unknown"
                    status_counts[s] = status_counts.get(s, 0) + 1
                state["entities"][col] = {
                    "count": len(entities),
                    "names": sorted(e["name"] for e in entities if e["name"]),
                    "statuses": status_counts,
                }
            except Exception as e:
                logger.debug("Entity query failed for %s: %s", col, e)
    except Exception as e:
        logger.debug("State query failed: %s", e)
    return state


def _compare_states(before: dict, after: dict) -> dict[str, Any]:
    before_cols = before.get("collections", {})
    after_cols = after.get("collections", {})
    changes: list[dict] = []
    for col in set(list(before_cols) + list(after_cols)):
        delta = after_cols.get(col, {}).get("count", 0) - before_cols.get(col, {}).get("count", 0)
        if delta:
            changes.append(
                {
                    "collection": col,
                    "before": before_cols.get(col, {}).get("count", 0),
                    "after": after_cols.get(col, {}).get("count", 0),
                    "delta": delta,
                }
            )
    total = sum(abs(c["delta"]) for c in changes)
    severity = "none" if total == 0 else "low" if total <= 2 else "medium" if total <= 10 else "high"

    before_ent = before.get("entities", {})
    after_ent = after.get("entities", {})
    entity_changes: list[dict] = []
    for col in set(list(before_ent) + list(after_ent)):
        b = before_ent.get(col, {})
        a = after_ent.get(col, {})
        b_names = set(b.get("names", []))
        a_names = set(a.get("names", []))
        b_statuses = b.get("statuses", {})
        a_statuses = a.get("statuses", {})
        added = sorted(a_names - b_names)
        removed = sorted(b_names - a_names)
        status_diff = {}
        all_statuses = set(list(b_statuses) + list(a_statuses))
        for s in all_statuses:
            bd = b_statuses.get(s, 0)
            ad = a_statuses.get(s, 0)
            if bd != ad:
                status_diff[s] = {"before": bd, "after": ad, "delta": ad - bd}
        if added or removed or status_diff:
            entity_changes.append(
                {
                    "collection": col,
                    "added_names": added,
                    "removed_names": removed,
                    "status_changes": status_diff,
                }
            )

    return {
        "changes": changes,
        "total_changes": total,
        "severity": severity,
        "has_changes": bool(changes),
        "entity_changes": entity_changes,
        "has_entity_changes": bool(entity_changes),
    }


# ---------------------------------------------------------------------------
# Loss calculation
# ---------------------------------------------------------------------------


def _calculate_loss(
    predicted: dict[str, Any],
    actual: dict[str, Any],
    prompt: str,
    epistemic_confidence: float = 1.0,
) -> dict[str, Any]:
    """Calculate loss between predicted and actual world states.

    Loss = 1 - (matching facts / total facts).
    Facts include: collection counts, entity names, entity statuses.

    Now includes knowledge loss:
        L_knowledge = g(1-P, 1-F, 1-C, 1-V) * world_error
        L_total = w_world * L_world + w_knowledge * L_knowledge
    """
    pred_cols = predicted.get("collections", {})
    act_cols = actual.get("collections", {})
    all_cols = set(list(pred_cols) + list(act_cols))

    count_matches = 0
    count_total = 0
    for col in all_cols:
        pc = pred_cols.get(col, {}).get("count", 0)
        ac = act_cols.get(col, {}).get("count", 0)
        count_total += 1
        if pc == ac:
            count_matches += 1

    pred_ent = predicted.get("entities", {})
    act_ent = actual.get("entities", {})
    name_matches = 0
    name_total = 0
    status_matches = 0
    status_total = 0
    entity_details: list[dict] = []

    for col in set(list(pred_ent) + list(act_ent)):
        p = pred_ent.get(col, {})
        a = act_ent.get(col, {})
        p_names = set(p.get("names", []))
        a_names = set(a.get("names", []))
        name_total += max(len(p_names | a_names), 1)
        name_matches += len(p_names & a_names)

        p_stat = p.get("statuses", {})
        a_stat = a.get("statuses", {})
        for s in set(list(p_stat) + list(a_stat)):
            status_total += 1
            if p_stat.get(s, 0) == a_stat.get(s, 0):
                status_matches += 1

        if p_names != a_names or p_stat != a_stat:
            entity_details.append(
                {
                    "collection": col,
                    "predicted_names": sorted(p_names),
                    "actual_names": sorted(a_names),
                    "name_overlap": sorted(p_names & a_names),
                    "predicted_statuses": p_stat,
                    "actual_statuses": a_stat,
                }
            )

    total_facts = count_total + name_total + status_total
    total_matches = count_matches + name_matches + status_matches
    world_loss = round(1.0 - (total_matches / total_facts), 4) if total_facts else 1.0

    # Knowledge loss: high uncertainty amplifies world error
    # L_knowledge = world_loss * (2.0 - confidence), capped at 1.0
    knowledge_loss_val = min(1.0, world_loss * (2.0 - epistemic_confidence))

    # Combined loss: 60% world, 40% knowledge
    total_loss_val = round(0.6 * world_loss + 0.4 * knowledge_loss_val, 4)

    return {
        "loss": world_loss,
        "knowledge_loss": knowledge_loss_val,
        "total_loss": total_loss_val,
        "epistemic_confidence": epistemic_confidence,
        "total_facts": total_facts,
        "matching_facts": total_matches,
        "count_accuracy": round(count_matches / count_total, 4) if count_total else 1.0,
        "name_accuracy": round(name_matches / name_total, 4) if name_total else 1.0,
        "status_accuracy": (round(status_matches / status_total, 4) if status_total else 1.0),
        "entity_details": entity_details,
    }


# ---------------------------------------------------------------------------
# Bellman transition values
# ---------------------------------------------------------------------------


async def _store_transition(
    db: Any,
    *,
    simulation_id: str,
    prompt: str,
    action: str,
    pipeline_name: str | None = None,
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    loss_result: dict[str, Any],
    grounding_score: float,
    feasibility_verdict: str,
    epistemic_state: dict[str, Any] | None = None,
) -> None:
    """Store a Bellman-style transition (s, a, r, s') for the planner.

    Now includes epistemic state for the co-evolution model:
        (s_t, k_t, a_t) → (s_{t+1}, A_{t+1}, k_{t+1})
    """
    if db is None:
        return

    loss = loss_result.get("loss", 1.0)
    knowledge_loss_val = loss_result.get("knowledge_loss", 0.0)
    total_loss_val = loss_result.get("total_loss", loss)
    reward = round(1.0 - total_loss_val, 4)

    transition = {
        "simulation_id": simulation_id,
        "prompt": prompt,
        "action": action,
        "pipeline_name": pipeline_name or action[:200],
        "state_before": _json_safe(state_before),
        "state_after": _json_safe(state_after),
        "loss": loss,
        "knowledge_loss": knowledge_loss_val,
        "total_loss": total_loss_val,
        "reward": reward,
        "count_accuracy": loss_result.get("count_accuracy", 0.0),
        "name_accuracy": loss_result.get("name_accuracy", 0.0),
        "status_accuracy": loss_result.get("status_accuracy", 0.0),
        "matching_facts": loss_result.get("matching_facts", 0),
        "total_facts": loss_result.get("total_facts", 0),
        "grounding_score": grounding_score,
        "feasibility_verdict": feasibility_verdict,
        "created_at": _now(),
    }

    # Epistemic extension
    if epistemic_state:
        transition["epistemic_state"] = epistemic_state
        transition["knowledge_confidence"] = epistemic_state.get("knowledge_confidence", {})
        transition["knowledge_sources"] = epistemic_state.get("knowledge_sources", [])
        transition["avg_prediction_error"] = epistemic_state.get("avg_prediction_error", 0.0)

    try:
        await db[SIM_TRANSITIONS_COLLECTION].insert_one(transition)
    except Exception as exc:
        logger.warning("Failed to store transition: %s", exc)


async def get_transition_values(
    db: Any,
    action: str | None = None,
    pipeline_name: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Retrieve Bellman transition values for the planner.

    Supports query by action (exact prompt) or pipeline_name (pipeline identifier).
    """
    if db is None:
        return {"transitions": [], "action_values": {}}

    if pipeline_name:
        # Match transitions where pipeline_name matches
        query = {"pipeline_name": pipeline_name}
    elif action:
        query = {"action": action}
    else:
        query = {}

    transitions: list[dict] = []
    async for doc in db[SIM_TRANSITIONS_COLLECTION].find(query).sort("created_at", -1).limit(limit):
        doc.pop("_id", None)
        transitions.append(doc)

    # Aggregate by pipeline_name (preferred) or action
    action_values: dict[str, dict] = {}
    for t in transitions:
        key = t.get("pipeline_name") or t.get("action", "")
        if key not in action_values:
            action_values[key] = {"rewards": [], "losses": [], "count": 0}
        action_values[key]["rewards"].append(t.get("reward", 0))
        action_values[key]["losses"].append(t.get("loss", 1.0))
        action_values[key]["count"] += 1

    for a, v in action_values.items():
        v["expected_reward"] = round(sum(v["rewards"]) / len(v["rewards"]), 4) if v["rewards"] else 0
        v["expected_loss"] = round(sum(v["losses"]) / len(v["losses"]), 4) if v["losses"] else 1.0
        v["min_loss"] = round(min(v["losses"]), 4) if v["losses"] else 1.0
        v["max_loss"] = round(max(v["losses"]), 4) if v["losses"] else 1.0
        v["rewards"] = v["rewards"][-10:]
        v["losses"] = v["losses"][-10:]

    return {"transitions": transitions, "action_values": action_values}


def _build_impact_assessment(step_results: list[dict[str, Any]]) -> dict[str, Any]:
    total, collections_hit, records = 0, [], []
    for step in step_results:
        r = step.get("result") or {}
        for detail in r.get("details", []):
            col = detail.get("collection")
            found = detail.get("found", 0)
            if col:
                collections_hit.append(col)
                total += found
                records.append({"collection": col, "records": found})
    return {
        "step_analysis": [
            {
                "step_number": i,
                "node_id": s.get("node_id"),
                "capability": s.get("operator_type"),
                "effect": (s.get("result") or {}).get("effect") or (s.get("result") or {}).get("status"),
                "selection_method": s.get("selection_method"),
            }
            for i, s in enumerate(step_results)
        ],
        "cumulative_results": {
            "total_entities_affected": total,
            "collections_impacted": list(set(collections_hit)),
            "records_affected": records,
        },
        "assessment_summary": (
            f"Executed {len(step_results)} steps, "
            f"affected {total} records across {len(set(collections_hit))} collections."
        ),
        "feasibility_verdict": "feasible" if total > 0 else "no_data_found",
    }


def _compute_grounding_score(results: dict[str, Any]) -> float:
    """Aggregate grounding score 0.0–1.0 across all active simulation layers.

    Simulation loss is applied as a direct deduction from the base score.
    The five-component loss decomposes into independent dimensions so each
    can be weighted relative to its contribution to grounding confidence.

    Weight 0.60 on total simulation loss balances against the adversarial
    confidence_delta that is applied separately in simulate().
    """
    scores: list[float] = []

    sensor = results.get("sensor_result") or {}
    if sensor.get("status") == "analyzed":
        scores.append(1.0 if sensor.get("reading_change", {}).get("event_state") else 0.5)

    wm = results.get("world_model_result") or {}
    if wm.get("status") == "completed":
        counts = wm.get("record_counts", {})
        populated = sum(1 for v in counts.values() if v > 0)
        total_domains = max(len(counts), 1)
        scores.append(populated / total_domains)

    wf = results.get("workflow_result") or {}
    exec_results = wf.get("execution_results", [])
    if exec_results:
        found = sum(
            1
            for s in exec_results
            if (s.get("result") or {}).get("results_found", 0) > 0
            or (s.get("result") or {}).get("status") not in ("error", "simulated", None)
        )
        scores.append(found / max(len(exec_results), 1))

    base = sum(scores) / len(scores) if scores else 0.0

    # Apply simulation loss — decomposed across 5 dimensions, weighted to balance
    # against the adversarial confidence_delta applied separately in simulate()
    adv = results.get("adversarial") or {}
    sim_loss_dict = adv.get("simulation_loss") or {}
    sim_total = sim_loss_dict.get("total", 0.0)
    grounding = max(0.0, base - sim_total * 0.6)

    return round(grounding, 3)


def _compute_feasibility(results: dict[str, Any]) -> str:
    score = results.get("grounding_score", 0.0)
    sensor = results.get("sensor_result") or {}
    severity = sensor.get("reading_change", {}).get("severity", "low")

    if severity == "critical":
        return "not_recommended"
    if severity == "high":
        return "risky"
    if score >= 0.6:
        return "feasible"
    if score >= 0.3:
        return "risky"
    return "insufficient_data"


def _build_correction_prompt(
    prompt: str,
    delta: dict[str, Any],
    execution_results: list[dict],
) -> str:
    summary = ", ".join(f"{c['collection']}: {c['delta']:+d} records" for c in delta.get("changes", [])[:5])
    return (
        f"The previous simulation for '{prompt}' caused unexpected state changes: "
        f"{summary}. Retrigger with a safer workflow that minimises state changes."
    )


# ---------------------------------------------------------------------------
# Data retrieval helpers
# ---------------------------------------------------------------------------


async def _web_search(prompt: str) -> dict[str, Any]:
    try:
        from src.monkey_brain.kernel.plan.intents.helpers import (
            _ollama_web_search,
            _tavily_search,
            _fallback_web_search,
        )

        for fn in [_ollama_web_search, _tavily_search, _fallback_web_search]:
            try:
                results = await fn(prompt, limit=5)
                if results:
                    return {
                        "status": "searched",
                        "source": "web",
                        "results_found": len(results),
                        "details": [
                            {
                                "title": r.get("title"),
                                "text": r.get("snippet", "")[:500],
                                "url": r.get("url"),
                            }
                            for r in results[:5]
                        ],
                    }
            except Exception as e:
                logger.debug("Web search failed: %s", e)
                continue
    except ImportError:
        logger.debug("Web search module not available")
    return {"status": "searched", "source": "web", "results_found": 0, "details": []}


async def _document_search(db: Any, prompt: str) -> dict[str, Any]:
    if db is None:
        return {"status": "skipped", "results_found": 0, "details": []}
    try:
        terms = re.findall(r"\b\w{3,}\b", prompt.lower())
        results: list[dict] = []
        for col in ["sops", "documents", "uploaded_files", "document_metadata"]:
            try:
                if not await db[col].count_documents({}):
                    continue
                conditions = [
                    {field: {"$regex": t, "$options": "i"}}
                    for t in terms[:5]
                    for field in ("title", "name", "content", "description")
                ]
                docs = await db[col].find({"$or": conditions}).limit(3).to_list(3)
                if docs:
                    results.append(
                        {
                            "collection": col,
                            "found": len(docs),
                            "documents": [
                                {k: v for k, v in d.items() if k != "_id" and not isinstance(v, bytes)} for d in docs
                            ],
                        }
                    )
            except Exception as e:
                logger.debug("Document search failed for %s: %s", col, e)
                continue
        return {
            "status": "searched",
            "source": "documents",
            "results_found": len(results),
            "details": results,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def _retrieve_from_db(db: Any, inputs: list[str], prompt: str) -> dict[str, Any]:
    if db is None:
        return {"status": "skipped", "results_found": 0, "details": []}
    try:
        all_text = " ".join(inputs) + " " + prompt
        id_patterns = re.findall(r"\b[A-Z]{2,}[-_]\w+\b", all_text)
        words = re.findall(r"\b[a-zA-Z]{3,}\b", prompt)
        terms = list(set(inputs + id_patterns + words)) or [prompt[:50]]

        results: list[dict] = []
        for col in (await db.list_collection_names())[:30]:
            try:
                conditions = [
                    {field: {"$regex": t, "$options": "i"}}
                    for t in terms
                    for field in (
                        "name",
                        "id",
                        "batch_id",
                        "lot_number",
                        "label",
                        "description",
                        "plant_id",
                    )
                ]
                docs = await db[col].find({"$or": conditions}).limit(3).to_list(3)
                if docs:
                    results.append(
                        {
                            "collection": col,
                            "found": len(docs),
                            "documents": [
                                {k: v for k, v in d.items() if k != "_id" and not isinstance(v, bytes)} for d in docs
                            ],
                        }
                    )
            except Exception as e:
                logger.debug("Graph traversal failed for %s: %s", col, e)
                continue

        # Relationship traversal fallback
        if not results:
            for term in terms:
                for col, child_col, id_field in [
                    ("industrial_plants", "industrial_lines", "plant_id"),
                    ("industrial_lines", "industrial_stages", "line_id"),
                ]:
                    try:
                        doc = await db[col].find_one({"name": {"$regex": re.escape(term), "$options": "i"}})
                        if doc:
                            doc_id = doc.get("id", "")
                            if doc_id:
                                related = await db[child_col].find({id_field: doc_id}).limit(10).to_list(10)
                                if related:
                                    results.append(
                                        {
                                            "collection": child_col,
                                            "found": len(related),
                                            "documents": [{k: v for k, v in r.items() if k != "_id"} for r in related],
                                            "via": f"{col}: {doc.get('name')}",
                                        }
                                    )
                    except Exception as e:
                        logger.debug("Relationship query failed: %s", e)
                        continue
                if results:
                    break

        return {
            "status": "retrieved",
            "results_found": len(results),
            "details": results,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _compact(record: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "id",
        "sop_id",
        "batch_id",
        "order_id",
        "machine_id",
        "equipment_id",
        "name",
        "title",
        "status",
        "stage_id",
        "line_id",
        "plant_id",
        "workstation_id",
        "version",
        "updated_at",
        "created_at",
    )
    return {k: record[k] for k in keep if k in record}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        from bson import ObjectId

        if isinstance(value, ObjectId):
            return str(value)
    except ImportError:
        pass
    return value


def _connect_redis() -> Any:
    try:
        import redis as _redis

        url = os.getenv("REDIS_URL")
        if url:
            r = _redis.from_url(url)
        else:
            r = _redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                db=int(os.getenv("REDIS_DB", 0)),
                socket_connect_timeout=2,
            )
            r.ping()
        return r
    except Exception as e:
        logger.debug("Redis connection failed: %s", e)
        return None


def _format_result(res: dict) -> str:
    parts = [
        doc.get("name") or doc.get("title", "")
        for d in res.get("details", [])[:2]
        for doc in d.get("documents", [])[:1]
        if doc.get("name") or doc.get("title")
    ]
    return "; ".join(parts) if parts else res.get("status", "no result")
