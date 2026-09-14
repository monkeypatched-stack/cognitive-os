"""Provider contracts — what a provider advertises about a capability it can
satisfy, and the policy for choosing between multiple providers that can
satisfy the same one.

Real telemetry (measured latency, actual success rate) is used when Lemon
has recorded any for a given provider+capability; otherwise falls back to
per-implementation-type heuristics (native/local is cheap+fast+confident,
llm_generative is the most variable, external_api the slowest). This is a
starting policy, not a learned model — the heuristics are named constants
specifically so they're easy to replace with real measurements later
without touching the scoring logic itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.runtime.capability_types import CapabilityType

logger = logging.getLogger("agentos.provider_contract")


@dataclass
class ProviderContract:
    """What a provider advertises for one capability it can satisfy."""

    provider: str
    capability: str
    capability_type: CapabilityType
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    cost: float = 0.5  # 0 = free/cheap .. 1 = expensive
    latency_ms: float = 1000.0
    confidence: float = 0.5  # 0 = unreliable .. 1 = highly reliable
    version: str = "1.0"
    success_rate: float = 0.8  # 0 = always fails .. 1 = always succeeds

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capability": self.capability,
            "capability_type": self.capability_type.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "cost": self.cost,
            "latency_ms": self.latency_ms,
            "confidence": self.confidence,
            "version": self.version,
            "success_rate": self.success_rate,
        }


# Per-implementation-type heuristics, used until real telemetry exists for a
# specific provider+capability pair. Ordered roughly by how the selection
# policy should prefer them by default: local/native cheapest and most
# confident, SDLC auto-generation the most expensive/slowest/least certain
# (it writes and deploys real code — see codegen_runtime.py).
_TYPE_DEFAULTS: dict[CapabilityType, dict[str, float]] = {
    CapabilityType.NATIVE: {
        "cost": 0.1,
        "latency_ms": 50,
        "confidence": 0.9,
        "success_rate": 0.9,
    },
    CapabilityType.WORKFLOW: {
        "cost": 0.2,
        "latency_ms": 300,
        "confidence": 0.7,
        "success_rate": 0.75,
    },
    CapabilityType.EXTERNAL_API: {
        "cost": 0.4,
        "latency_ms": 2000,
        "confidence": 0.6,
        "success_rate": 0.7,
    },
    CapabilityType.LLM_GENERATIVE: {
        "cost": 0.5,
        "latency_ms": 8000,
        "confidence": 0.6,
        "success_rate": 0.85,
    },
    CapabilityType.HUMAN: {
        "cost": 0.9,
        "latency_ms": 600000,
        "confidence": 0.95,
        "success_rate": 0.95,
    },
    CapabilityType.COMPOSITE: {
        "cost": 0.6,
        "latency_ms": 15000,
        "confidence": 0.55,
        "success_rate": 0.7,
    },
}
# SDLC auto-generation is deliberately worse than every real-provider default
# above on every axis — it's the last resort, never a competitive choice
# when any real provider is available (see codegen_runtime.py: ~24 real
# LLM-backed stages, several minutes, writes real files).
SDLC_DEFAULTS = {
    "cost": 0.95,
    "latency_ms": 240000,
    "confidence": 0.3,
    "success_rate": 0.4,
}


def default_contract(
    provider: str,
    capability: str,
    capability_type: CapabilityType,
    *,
    is_sdlc: bool = False,
) -> ProviderContract:
    """A contract using type-level heuristics — the starting point before
    any real telemetry exists for this specific provider+capability."""
    defaults = SDLC_DEFAULTS if is_sdlc else _TYPE_DEFAULTS.get(capability_type, _TYPE_DEFAULTS[CapabilityType.NATIVE])
    return ProviderContract(
        provider=provider,
        capability=capability,
        capability_type=capability_type,
        **defaults,
    )


def contract_from_telemetry(provider: str, capability: str, capability_type: CapabilityType) -> ProviderContract:
    """Prefer real measured latency/success-rate from Lemon over the type
    heuristic, when any has been recorded for this exact provider+capability.
    """
    contract = default_contract(provider, capability, capability_type)
    try:
        from src.introspection.lemon import get_lemon

        lemon = get_lemon()
        if lemon is None:
            return contract
        stats = lemon.get_provider_stats(provider, capability) if hasattr(lemon, "get_provider_stats") else None
        if stats:
            contract.latency_ms = stats.get("p50_latency_ms", contract.latency_ms)
            contract.success_rate = stats.get("success_rate", contract.success_rate)
    except Exception as e:
        logger.debug(
            "[provider_contract] telemetry lookup failed for %s/%s: %s",
            provider,
            capability,
            e,
        )
    return contract


def score(contract: ProviderContract, *, prefer_deterministic: bool = True) -> float:
    """Lower is better. Implements the selection policy in order of stated
    priority: local > deterministic-over-generative > cost > latency >
    success rate, each term weighted so an earlier-priority factor can never
    be outweighed by a combination of later ones.
    """
    local_penalty = 0.0 if contract.capability_type == CapabilityType.NATIVE else 1.0
    determinism_penalty = (
        1.0 if (prefer_deterministic and contract.capability_type == CapabilityType.LLM_GENERATIVE) else 0.0
    )
    cost_term = contract.cost
    latency_term = min(contract.latency_ms / 300_000, 1.0)  # normalize against SDLC's ~5min ceiling
    unreliability_term = 1.0 - contract.success_rate
    low_confidence_term = 1.0 - contract.confidence

    return (
        local_penalty * 1000
        + determinism_penalty * 100
        + cost_term * 10
        + latency_term * 5
        + unreliability_term * 3
        + low_confidence_term * 1
    )


def select_best(contracts: list[ProviderContract], *, prefer_deterministic: bool = True) -> ProviderContract | None:
    if not contracts:
        return None
    return min(contracts, key=lambda c: score(c, prefer_deterministic=prefer_deterministic))
