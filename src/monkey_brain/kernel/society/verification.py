"""World invariant verification — read-only checks over a PlanetaryRuntime's
real state, for the /verify REST API and any caller that wants to assert
world consistency (benchmarks, tests) without re-deriving these checks
ad hoc.

SUPERSEDED (ADR-010, Gate 3 — World Validation): verify_world_invariants()
originally implemented four checks directly (society-has-space,
actor-has-presence, no-orphaned-geography, valid-memberships — ADR-008).
It now delegates to kernel/validation/world_validator.py::validate_world(),
which covers those four plus six more categories (inventory consistency,
World Graph integrity, orphaned World Graph nodes, forbidden geography
cycles, cross-namespace duplicate identifiers, Commerce referential
integrity). Kept as a thin, name-stable wrapper so no existing caller
(tests, the /verify REST route) needs to change — the return shape is a
superset of the original (same four keys, plus "categories").

Purely observational: never raises, never mutates anything, always
returns a structured report.
"""

from __future__ import annotations

from typing import Any


def verify_world_invariants(planetary_runtime: Any) -> dict:
    from src.monkey_brain.kernel.validation.world_validator import validate_world

    return validate_world(planetary_runtime)
