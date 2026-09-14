"""Shared correlation_id minting for the communication/event model.

correlation_id identifies a logical end-to-end operation (a cognitive tick,
a negotiation, an interaction). Most call sites already have a better-fit
existing id to reuse (execution_id, transaction_id, interaction_id) — this
helper exists only for the fallback case: a bare operation with no upstream
id to inherit from (e.g. a standalone communication-eligibility check).
"""

from __future__ import annotations

from uuid import uuid4


def new_correlation_id() -> str:
    return uuid4().hex
