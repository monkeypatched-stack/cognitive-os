"""Tests for CapabilityScheduler."""

import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from unittest.mock import MagicMock
from src.monkey_brain.kernel.execute.capabilities.scheduler import CapabilityScheduler
from src.monkey_brain.kernel.execute.capabilities.bus import CapabilityBus


def test_scheduler_construction():
    bus = MagicMock(spec=CapabilityBus)
    s = CapabilityScheduler(bus)
    assert s is not None


def test_scheduler_plan():
    bus = MagicMock(spec=CapabilityBus)
    s = CapabilityScheduler(bus)
    result = s.schedule("test_capability", param="value")
    assert result is not None
    assert result["capability"] == "test_capability"


def test_scheduler_empty_plan():
    bus = MagicMock(spec=CapabilityBus)
    s = CapabilityScheduler(bus)
    result = s.schedule("empty")
    assert result["status"] == "scheduled"
