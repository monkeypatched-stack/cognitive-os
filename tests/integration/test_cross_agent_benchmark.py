"""Cross-Agent Integration Benchmark — validates enterprise workflows end-to-end.

Tests the complete lifecycle:
- WorkOrder → Todo → Notification
- Knowledge Pack publishing after workflow completion
- Engineering knowledge retrieval for future tasks
- Capability-based agent interaction (no direct coupling)
"""

import sys
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domains.software_engineering.knowledge.pack import (
    SoftwareEngineeringKnowledgePublisher as EngineeringKnowledgePublisher,
)

# ── Domain Models ────────────────────────────────────────────────────────────


class WorkOrderStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"


# ── Agent Stubs (minimal, focused on workflow behavior) ──────────────────────


class WorkOrderAgent:
    def __init__(self):
        self.orders = {}
        self.events = []

    def create(self, title, equipment, priority="medium"):
        wo = {
            "id": f"WO-{uuid.uuid4().hex[:8]}",
            "title": title,
            "equipment": equipment,
            "priority": priority,
            "status": WorkOrderStatus.CREATED,
            "todos": [],
        }
        self.orders[wo["id"]] = wo
        self.events.append({"type": "WorkOrderCreated", "id": wo["id"]})
        return wo

    def complete(self, wo_id):
        self.orders[wo_id]["status"] = WorkOrderStatus.COMPLETED
        self.events.append({"type": "WorkOrderCompleted", "id": wo_id})


class TodoAgent:
    def __init__(self):
        self.todos = {}
        self.events = []

    def generate(self, wo):
        todos = []
        for step in ["Inspect", "Gather tools", "Perform", "Document"]:
            t = {
                "id": f"TD-{uuid.uuid4().hex[:8]}",
                "wo_id": wo["id"],
                "title": step,
                "assigned": "worker-001",
                "status": TodoStatus.PENDING,
            }
            self.todos[t["id"]] = t
            wo["todos"].append(t["id"])
            todos.append(t)
            self.events.append({"type": "TodoCreated", "id": t["id"]})
        return todos

    def accept(self, tid):
        self.todos[tid]["status"] = TodoStatus.ACCEPTED

    def complete(self, tid):
        self.todos[tid]["status"] = TodoStatus.COMPLETED


class NotificationCapability:
    def __init__(self):
        self.sent = []

    def notify(self, todo, wo):
        n = {
            "id": f"NT-{uuid.uuid4().hex[:8]}",
            "todo_id": todo["id"],
            "message": f"Task '{todo['title']}' for WO {wo['id']}",
            "sent": True,
        }
        self.sent.append(n)
        return n


# ── Benchmark Tests ──────────────────────────────────────────────────────────


def test_wo_persists():
    ag = WorkOrderAgent()
    wo = ag.create("Fix motor", "Blender")
    assert wo["id"] in ag.orders
    assert ag.orders[wo["id"]]["title"] == "Fix motor"


def test_todo_references_wo():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    wo = wo_ag.create("PM", "Conveyor")
    todos = td_ag.generate(wo)
    assert len(todos) == 4
    assert all(t["wo_id"] == wo["id"] for t in todos)


def test_wo_reflects_todos():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    wo = wo_ag.create("PM", "Conveyor")
    td_ag.generate(wo)
    assert len(wo["todos"]) == 4


def test_notification_delivered():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    nc = NotificationCapability()
    wo = wo_ag.create("PM", "Conveyor")
    todos = td_ag.generate(wo)
    for t in todos:
        n = nc.notify(t, wo)
        assert n["sent"] is True
    assert len(nc.sent) == 4


def test_todo_assignment():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    wo = wo_ag.create("Fix", "Station")
    todos = td_ag.generate(wo)
    assert all(t["assigned"] == "worker-001" for t in todos)


def test_todo_status_pending():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    wo = wo_ag.create("Fix", "Station")
    todos = td_ag.generate(wo)
    assert all(t["status"] == TodoStatus.PENDING for t in todos)


def test_todo_accept_complete():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    wo = wo_ag.create("Fix", "Station")
    todos = td_ag.generate(wo)
    td_ag.accept(todos[0]["id"])
    assert todos[0]["status"] == TodoStatus.ACCEPTED
    td_ag.complete(todos[0]["id"])
    assert todos[0]["status"] == TodoStatus.COMPLETED


def test_wo_completion():
    wo_ag = WorkOrderAgent()
    wo = wo_ag.create("Fix", "Station")
    assert wo["status"] == WorkOrderStatus.CREATED
    wo_ag.complete(wo["id"])
    assert wo["status"] == WorkOrderStatus.COMPLETED


def test_full_lifecycle():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    nc = NotificationCapability()
    wo = wo_ag.create("Replace filter", "HVAC")
    todos = td_ag.generate(wo)
    for t in todos:
        nc.notify(t, wo)
        td_ag.accept(t["id"])
        td_ag.complete(t["id"])
    wo_ag.complete(wo["id"])
    assert wo["status"] == WorkOrderStatus.COMPLETED
    assert all(t["status"] == TodoStatus.COMPLETED for t in todos)
    assert len(nc.sent) == 4


def test_knowledge_publishing():
    pub = EngineeringKnowledgePublisher()
    pub.publish(
        spec_id="bench-001",
        goal="WorkOrder workflow",
        domain="manufacturing",
        generated_files={"wo.py": "code", "td.py": "code", "nc.py": "code"},
        governance_findings=[{"severity": "low"}],
        benchmark_results={"throughput": 42},
        workflow_topology=["CreateWO", "GenTodos", "Notify", "Accept", "Complete"],
        confidence=0.9,
    )
    assert pub.summary()["total_published"] == 1
    items = pub.get_knowledge_items_for_retrieval("WorkOrder")
    assert len(items) >= 4


def test_no_direct_coupling():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    nc = NotificationCapability()
    wo = wo_ag.create("test", "equip")
    todos = td_ag.generate(wo)
    for t in todos:
        nc.notify(t, wo)
    assert len(nc.sent) == len(todos)
    assert wo_ag.orders[wo["id"]]["status"] == WorkOrderStatus.CREATED


def test_multi_workflow():
    wo_ag = WorkOrderAgent()
    td_ag = TodoAgent()
    nc = NotificationCapability()
    results = []
    for i in range(5):
        wo = wo_ag.create(f"WO-{i}", f"Station-{i}")
        todos = td_ag.generate(wo)
        for t in todos:
            nc.notify(t, wo)
            td_ag.accept(t["id"])
            td_ag.complete(t["id"])
        wo_ag.complete(wo["id"])
        results.append(wo)
    assert len(results) == 5
    assert len(nc.sent) == 20
    assert all(wo["status"] == WorkOrderStatus.COMPLETED for wo in results)


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)):
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"\n{'=' * 60}")
    print(f"CROSS-AGENT INTEGRATION BENCHMARK")
    print(f"{'=' * 60}")
    print(f"  Total: {ok}/{ok + len(f)}")
    if f:
        for e in f:
            print(f"  FAIL: {e}")
    else:
        print("  ALL CHECKS PASS")
    print(f"{'=' * 60}")
