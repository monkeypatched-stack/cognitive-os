"""Enterprise Workflow Test — WorkOrder → Todo → Notification.

Demonstrates cross-agent collaboration:
1. WorkOrderAgent creates a work order
2. TodoAgent generates worker tasks
3. NotificationCapability sends alerts
4. Worker accepts → Todo updated → WorkOrder progress updated

This is the canonical enterprise workflow benchmark.
"""

import asyncio
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


# ── Domain Models ────────────────────────────────────────────────────────────


class WorkOrderStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"


class TodoStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class NotificationChannel(StrEnum):
    OPENCLAW = "openclaw"
    EMAIL = "email"
    SMS = "sms"


@dataclass
class WorkOrder:
    id: str = field(default_factory=lambda: f"WO-{uuid.uuid4().hex[:8]}")
    title: str = ""
    equipment: str = ""
    priority: str = "medium"
    status: WorkOrderStatus = WorkOrderStatus.CREATED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    todos: list[str] = field(default_factory=list)


@dataclass
class Todo:
    id: str = field(default_factory=lambda: f"TD-{uuid.uuid4().hex[:8]}")
    work_order_id: str = ""
    title: str = ""
    assigned_to: str = ""
    status: TodoStatus = TodoStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Notification:
    id: str = field(default_factory=lambda: f"NT-{uuid.uuid4().hex[:8]}")
    todo_id: str = ""
    channel: NotificationChannel = NotificationChannel.OPENCLAW
    message: str = ""
    sent: bool = False
    sent_at: datetime | None = None


# ── Agents ───────────────────────────────────────────────────────────────────


class WorkOrderAgent:
    """Creates work orders and manages their lifecycle."""

    def __init__(self, store: dict[str, WorkOrder]):
        self.store = store
        self._events: list[dict] = []

    def create_work_order(self, title: str, equipment: str, priority: str = "medium") -> WorkOrder:
        wo = WorkOrder(title=title, equipment=equipment, priority=priority)
        self.store[wo.id] = wo
        self._events.append({"type": "WorkOrderCreated", "work_order_id": wo.id, "title": title})
        return wo

    def get_events(self) -> list[dict]:
        events = list(self._events)
        self._events.clear()
        return events


class TodoAgent:
    """Generates worker todos from work orders."""

    def __init__(self, store: dict[str, Todo]):
        self.store = store
        self._events: list[dict] = []

    def generate_todos(self, work_order: WorkOrder) -> list[Todo]:
        todos = []
        for step in [
            "Inspect equipment",
            "Gather tools",
            "Perform maintenance",
            "Document results",
        ]:
            todo = Todo(work_order_id=work_order.id, title=step, assigned_to="worker-001")
            self.store[todo.id] = todo
            work_order.todos.append(todo.id)
            todos.append(todo)
            self._events.append(
                {
                    "type": "TodoCreated",
                    "todo_id": todo.id,
                    "work_order_id": work_order.id,
                }
            )
        return todos

    def accept_todo(self, todo_id: str) -> Todo:
        todo = self.store[todo_id]
        todo.status = TodoStatus.ACCEPTED
        self._events.append({"type": "TodoAccepted", "todo_id": todo_id})
        return todo

    def complete_todo(self, todo_id: str) -> Todo:
        todo = self.store[todo_id]
        todo.status = TodoStatus.COMPLETED
        self._events.append({"type": "TodoCompleted", "todo_id": todo_id})
        return todo

    def get_events(self) -> list[dict]:
        events = list(self._events)
        self._events.clear()
        return events


class NotificationCapability:
    """Sends notifications via configured channels."""

    def __init__(self, channel: NotificationChannel = NotificationChannel.OPENCLAW):
        self.channel = channel
        self.sent: list[Notification] = []

    def notify(self, todo: Todo, work_order: WorkOrder) -> Notification:
        n = Notification(
            todo_id=todo.id,
            channel=self.channel,
            message=f"Task '{todo.title}' for WO {work_order.id} ({work_order.title})",
            sent=True,
            sent_at=datetime.now(timezone.utc),
        )
        self.sent.append(n)
        return n


# ── Workflow Engine ──────────────────────────────────────────────────────────


class EnterpriseWorkflow:
    """Orchestrates cross-agent enterprise workflows."""

    def __init__(self):
        self.work_orders: dict[str, WorkOrder] = {}
        self.todos: dict[str, Todo] = {}
        self.wo_agent = WorkOrderAgent(self.work_orders)
        self.todo_agent = TodoAgent(self.todos)
        self.notifier = NotificationCapability()
        self._trace: list[str] = []

    def run_work_order_workflow(self, title: str, equipment: str, priority: str = "medium") -> dict[str, Any]:
        """Execute the full WorkOrder → Todo → Notification workflow."""
        trace = []

        # Step 1: Create Work Order
        wo = self.wo_agent.create_work_order(title, equipment, priority)
        trace.append(f"WorkOrderCreated: {wo.id}")

        # Step 2: Generate Todos
        todos = self.todo_agent.generate_todos(wo)
        for t in todos:
            trace.append(f"TodoCreated: {t.id} ({t.title})")

        # Step 3: Send Notifications
        for t in todos:
            n = self.notifier.notify(t, wo)
            trace.append(f"NotificationSent: {n.id} via {n.channel}")

        # Step 4: Worker accepts all todos
        for t in todos:
            self.todo_agent.accept_todo(t.id)
            trace.append(f"TodoAccepted: {t.id}")

        # Step 5: Worker completes all todos
        for t in todos:
            self.todo_agent.complete_todo(t.id)
            trace.append(f"TodoCompleted: {t.id}")

        # Step 6: Update WorkOrder status
        wo.status = WorkOrderStatus.COMPLETED
        trace.append(f"WorkOrderCompleted: {wo.id}")

        self._trace = trace
        return {
            "work_order": wo,
            "todos": todos,
            "notifications": self.notifier.sent,
            "trace": trace,
            "completed": wo.status == WorkOrderStatus.COMPLETED,
        }


# ── Tests ────────────────────────────────────────────────────────────────────


def test_work_order_creation():
    wf = EnterpriseWorkflow()
    result = wf.run_work_order_workflow("Fix blender motor", "Blender Station", "high")
    assert result["work_order"].title == "Fix blender motor"
    assert result["work_order"].equipment == "Blender Station"
    assert result["work_order"].priority == "high"
    assert result["work_order"].status == WorkOrderStatus.COMPLETED


def test_todo_generation():
    wf = EnterpriseWorkflow()
    result = wf.run_work_order_workflow("PM scheduled", "Packaging Line")
    assert len(result["todos"]) == 4
    assert result["todos"][0].title == "Inspect equipment"
    assert result["todos"][-1].title == "Document results"


def test_todo_referenced_by_work_order():
    wf = EnterpriseWorkflow()
    result = wf.run_work_order_workflow("Replace sensor", "Filling Station")
    wo = result["work_order"]
    for t in result["todos"]:
        assert t.work_order_id == wo.id


def test_notification_sent():
    wf = EnterpriseWorkflow()
    result = wf.run_work_order_workflow("Calibrate", "Weigh Station")
    assert len(result["notifications"]) == 4
    assert all(n.sent for n in result["notifications"])
    assert all(n.channel == NotificationChannel.OPENCLAW for n in result["notifications"])


def test_todo_status_progression():
    wf = EnterpriseWorkflow()
    wo = wf.wo_agent.create_work_order("test", "equip")
    todos = wf.todo_agent.generate_todos(wo)
    t = todos[0]
    assert t.status == TodoStatus.PENDING
    wf.todo_agent.accept_todo(t.id)
    assert t.status == TodoStatus.ACCEPTED
    wf.todo_agent.complete_todo(t.id)
    assert t.status == TodoStatus.COMPLETED


def test_work_order_reflects_todos():
    wf = EnterpriseWorkflow()
    wo = wf.wo_agent.create_work_order("test", "equip")
    todos = wf.todo_agent.generate_todos(wo)
    assert len(wo.todos) == 4
    assert all(td_id in wf.todos for td_id in wo.todos)


def test_full_workflow_trace():
    wf = EnterpriseWorkflow()
    result = wf.run_work_order_workflow("Replace filter", "HVAC Unit")
    trace = result["trace"]
    assert trace[0].startswith("WorkOrderCreated:")
    assert trace[1].startswith("TodoCreated:")
    assert any("NotificationSent:" in e for e in trace)
    assert any("TodoAccepted:" in e for e in trace)
    assert any("TodoCompleted:" in e for e in trace)
    assert trace[-1].startswith("WorkOrderCompleted:")


def test_multiple_work_orders():
    wf = EnterpriseWorkflow()
    r1 = wf.run_work_order_workflow("WO-1", "Station A")
    r2 = wf.run_work_order_workflow("WO-2", "Station B")
    assert r1["work_order"].id != r2["work_order"].id
    assert len(wf.todos) == 8  # 4 todos per work order
    assert len(wf.notifier.sent) == 8


def test_notification_message_format():
    wf = EnterpriseWorkflow()
    result = wf.run_work_order_workflow("Lubricate bearings", "Conveyor")
    n = result["notifications"][0]
    assert "Lubricate bearings" in n.message
    assert "WO-" in n.message
    assert n.sent_at is not None


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
    print(f"ENTERPRISE WORKFLOW BENCHMARK")
    print(f"{'=' * 60}")
    print(f"  Total: {ok}/{ok + len(f)}")
    if f:
        for e in f:
            print(f"  FAIL: {e}")
    else:
        print("  ALL CHECKS PASS")
    print(f"{'=' * 60}")
