"""Canonical Capability Runtime name for the existing execution adapter.

The implementation remains ``ActionExecutor``. This compatibility module
provides the architecture-facing ownership name without creating a second
capability bus or execution path.
"""

from src.monkey_brain.kernel.pipeline.action_executor import (
    ActionExecutor,
    CapabilityRuntime,
)

__all__ = ["ActionExecutor", "CapabilityRuntime"]
