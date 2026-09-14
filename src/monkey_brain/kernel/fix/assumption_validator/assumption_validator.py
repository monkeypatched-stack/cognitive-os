"""Assumption Validator — validates execution assumptions."""

from __future__ import annotations
from typing import Any


class AssumptionValidator:
    """Validates execution assumptions against specifications."""

    def scan_specification(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Scan a specification for assumption violations."""
        violations = []
        for key, value in spec.items():
            if isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, (int, float)) and sub_val < 0:
                        violations.append(
                            {
                                "field": f"{key}.{sub_key}",
                                "reason": f"negative value: {sub_val}",
                            }
                        )
            elif isinstance(value, (int, float)) and value < 0:
                violations.append(
                    {
                        "field": key,
                        "reason": f"negative value: {value}",
                    }
                )
        return violations
