"""Kernel configuration — single source of truth for all tunable constants.

All magic numbers across the kernel must be imported from here.
"""

from __future__ import annotations

# ── Loss decomposition weights (must sum to 1.0) ─────────────────────────────
LOSS_WEIGHT_L_S: float = 0.25  # semantic / retrieval loss
LOSS_WEIGHT_L_B: float = 0.20  # belief / evidence loss
LOSS_WEIGHT_L_A: float = 0.15  # action / execution loss
LOSS_WEIGHT_L_M: float = 0.10  # memory / persistence loss
LOSS_WEIGHT_L_K: float = 0.10  # knowledge loss
LOSS_WEIGHT_L_C: float = 0.10  # constraint / compliance loss
LOSS_WEIGHT_L_G: float = 0.10  # goal / grounding loss

# ── Learning ──────────────────────────────────────────────────────────────────
LEARNING_RATE: float = 0.1
DISCOUNT_FACTOR: float = 0.95
EXPLORATION_RATE: float = 0.1

# ── Repair / loss thresholds ─────────────────────────────────────────────────
LOSS_THRESHOLD: float = 0.3  # composite EPA loss above which repair is warranted
REPAIR_LOSS_THRESHOLD: float = 0.3  # LossDrivenRepair: minimum loss to attempt repair

# ── Intent classification ─────────────────────────────────────────────────────
INTENT_MIN_CONFIDENCE: float = 0.58  # classified_intent_is() default threshold
PLANNING_CONFIDENCE_THRESHOLD: float = 0.3  # /plan route low-grounding gate

# ── Capability feedback ───────────────────────────────────────────────────────
CONFIDENCE_DELTA_SUCCESS: float = 0.1  # confidence boost on successful cap execution
CONFIDENCE_DELTA_FAILURE: float = -0.05  # confidence penalty on failed cap execution
KNOWLEDGE_LOSS_ON_FAILURE: float = 0.3  # L_K contribution when a capability fails

# ── Goal classification ───────────────────────────────────────────────────────
GOAL_CLASSIFICATION_MIN_CONFIDENCE: float = 0.5  # GoalClassifier: below this, treat as unclassified

# ── QA agent confidence tiers ─────────────────────────────────────────────────
QA_RETRIEVAL_CONFIDENCE_THRESHOLD: float = 0.7  # below this, trigger additional retrieval
QA_LOW_CONFIDENCE_THRESHOLD: float = 0.5  # below this, answer flagged "low confidence"
QA_HIGH_CONFIDENCE_THRESHOLD: float = 0.8  # at/above this, answer flagged "high confidence"

# ── PlannerPolicy (RL-based planning — distinct from the Bellman/execution
#    policy above; LEARNING_RATE/EXPLORATION_RATE intentionally NOT shared,
#    these two policies are independently tunable) ────────────────────────────
PLANNER_LEARNING_RATE: float = 0.1
PLANNER_EXPLORATION_RATE: float = 0.2
PLANNER_EXPLORATION_DECAY: float = 0.99
PLANNER_MIN_EXPLORATION_RATE: float = 0.05
