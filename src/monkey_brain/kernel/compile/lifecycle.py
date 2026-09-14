"""Runtime lifecycle helpers — signed checkpoints and conflict-aware merge (ADR 0021).

Small primitives the birth-to-death behaviours need: tamper-evident checkpoints (a policy
change without the key fails verification) and a merge that RECORDS conflicts rather than
silently overwriting one runtime's knowledge with another's.

Signing uses Ed25519 asymmetric keys via the identity module — no shared secrets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor


def sign_checkpoint(data: dict, key: bytes) -> str:
    """Legacy HMAC signer — kept for backward compatibility with existing
    callers.  New code should use identity.sign_payload() instead."""
    import hmac

    payload = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_checkpoint(data: dict, signature: str, key: bytes) -> bool:
    """Legacy HMAC verifier — constant-time."""
    import hmac

    return hmac.compare_digest(sign_checkpoint(data, key), signature)


def merge_with_conflicts(
    target: SparseTransitionTensor,
    source: SparseTransitionTensor,
    *,
    feature: Feature = Feature.FREQUENCY,
    tol: float = 1e-6,
) -> list[dict[str, Any]]:
    """Merge `source` into `target`, RECORDING conflicts (same edge, materially different
    value) instead of silently overwriting. Non-conflicting edges are folded in. Returns
    the list of conflicts for the caller to resolve — no policy is overwritten silently.

    Phase 5: Default feature is FREQUENCY (world dynamics), not Q_VALUE (policy).
    Q-values live in PolicyStore, not in the tensor.
    """
    conflicts: list[dict[str, Any]] = []
    for src, dst in source:
        sv = source.feature(src, dst, feature)
        if target.has_edge(src, dst):
            tv = target.feature(src, dst, feature)
            if abs(tv - sv) > tol:
                conflicts.append({"edge": (src, dst), "target": tv, "source": sv})
            continue  # do not overwrite an existing value

        # Fold in the new edge — carrying the value of the feature being merged.
        # observe() only bumps counters; it leaves q at the neutral 0.5 prior. So a
        # merge on Q_VALUE dropped every source Q on the floor: an edge the source had
        # learned to 0.997 landed in the target at 0.5, and the merge discarded exactly
        # the thing it was merging. Preserve the destination's true domain too.
        target.observe(src, dst, domain=source.domain_of(src), dst_domain=source.domain_of(dst))
        _copy_feature(target, source, src, dst, feature)
    return conflicts


def _copy_feature(
    target: SparseTransitionTensor,
    source: SparseTransitionTensor,
    src: str,
    dst: str,
    feature: Feature,
) -> None:
    """Carry `feature`'s value from source's cell onto target's freshly-observed cell.

    Only the learned/derived features that a merge can meaningfully transfer; the
    frequency-derived ones (PROBABILITY, FREQUENCY, UNCERTAINTY) follow from the
    target's own observation counts and must not be forged.

    Phase 5: Q_VALUE case removed — Q-values live in PolicyStore, not in tensor.
    """
    i = target._state_index.get(src)
    j = target._state_index.get(dst)
    cell = target._cells.get((i, j)) if i is not None and j is not None else None
    if cell is None:
        return
    value = source.feature(src, dst, feature)
    if feature is Feature.REWARD:
        cell.reward_sum = value * max(cell.freq, 1)
    elif feature is Feature.LATENCY:
        cell.latency_sum = value * max(cell.freq, 1)
    elif feature is Feature.COST:
        cell.cost_sum = value * max(cell.freq, 1)
    elif feature is Feature.CONFIDENCE:
        cell.conf_sum = value * max(cell.freq, 1)
