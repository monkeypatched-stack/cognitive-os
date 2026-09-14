"""Tests for pipeline contracts.

Validates construction, immutability, validation, and defaults
for all pipeline contract types.
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    PipelineAttachment,
    CompiledRequest,
    RuntimeContext,
    PipelineResponse,
    PipelineStatus,
    PipelineArtifact,
    PipelineTraceEntry,
    PipelineError,
)

# ═══════════════════════════════════════════════════════════════════════════
# PipelineRequest
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineRequest:
    def test_construction_minimal(self):
        req = PipelineRequest(question="get 2 l of milk")
        assert req.question == "get 2 l of milk"
        assert req.actor_id == "unknown"
        assert req.tenant_id == "default"
        assert req.session_id == ""
        assert req.request_id  # auto-generated
        assert req.attachments == ()
        assert req.metadata == {}
        assert req.timestamp > 0

    def test_construction_full(self):
        req = PipelineRequest(
            question="hello",
            actor_id="user-1",
            tenant_id="acme",
            session_id="sess-42",
            request_id="req-1",
            attachments=(PipelineAttachment(name="file.txt", data=b"hi"),),
            metadata={"source": "api"},
            timestamp=1234567890.0,
        )
        assert req.actor_id == "user-1"
        assert req.tenant_id == "acme"
        assert req.session_id == "sess-42"
        assert req.request_id == "req-1"
        assert len(req.attachments) == 1
        assert req.attachments[0].name == "file.txt"
        assert req.metadata["source"] == "api"
        assert req.timestamp == 1234567890.0

    def test_immutable(self):
        req = PipelineRequest(question="test")
        with pytest.raises(FrozenInstanceError):
            req.question = "changed"

    def test_empty_question_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            PipelineRequest(question="")
        with pytest.raises(ValueError, match="non-empty"):
            PipelineRequest(question="   ")

    def test_request_id_unique(self):
        r1 = PipelineRequest(question="a")
        r2 = PipelineRequest(question="b")
        assert r1.request_id != r2.request_id


# ═══════════════════════════════════════════════════════════════════════════
# PipelineAttachment
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineAttachment:
    def test_construction(self):
        att = PipelineAttachment(name="doc.pdf", content_type="application/pdf", data=b"%PDF")
        assert att.name == "doc.pdf"
        assert att.content_type == "application/pdf"
        assert att.data == b"%PDF"

    def test_immutable(self):
        att = PipelineAttachment(name="x")
        with pytest.raises(FrozenInstanceError):
            att.name = "y"


# ═══════════════════════════════════════════════════════════════════════════
# CompiledRequest
# ═══════════════════════════════════════════════════════════════════════════


class TestCompiledRequest:
    def test_construction(self):
        req = PipelineRequest(question="test")
        compiled = CompiledRequest(
            request=req,
            intent={"intent": "shopping", "confidence": 0.9},
            goal={"name": "buy_milk", "description": "Get milk"},
            intent_ir={"intent_ir_id": "ir-1", "signature": "abc"},
            goal_ir={"domain": "shopping", "entities": []},
            execution_context={"run_id": "ctx-1"},
        )
        assert compiled.request is req
        assert compiled.intent["intent"] == "shopping"
        assert compiled.execution_graph is None
        assert compiled.metadata == {}

    def test_immutable(self):
        req = PipelineRequest(question="test")
        compiled = CompiledRequest(
            request=req,
            intent={},
            goal={},
            intent_ir={},
            goal_ir={},
            execution_context={},
        )
        with pytest.raises(FrozenInstanceError):
            compiled.intent = {}


# ═══════════════════════════════════════════════════════════════════════════
# RuntimeContext
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeContext:
    def test_construction_empty(self):
        ctx = RuntimeContext()
        assert ctx.world is None
        assert ctx.actor is None
        assert ctx.capabilities is None
        assert ctx.knowledge is None
        assert ctx.context_stream is None
        assert ctx.execution is None
        assert ctx.learning is None
        assert ctx.metrics is None
        assert ctx.belief is None
        assert ctx.trust is None
        assert ctx.audit is None
        assert ctx.event_bus is None

    def test_construction_with_refs(self):
        sentinel_world = object()
        sentinel_actor = object()
        ctx = RuntimeContext(world=sentinel_world, actor=sentinel_actor)
        assert ctx.world is sentinel_world
        assert ctx.actor is sentinel_actor
        assert ctx.capabilities is None  # not set

    def test_immutable(self):
        ctx = RuntimeContext()
        with pytest.raises(FrozenInstanceError):
            ctx.world = object()

    def test_no_runtime_imports(self):
        """RuntimeContext must not import any concrete runtime class."""
        import src.monkey_brain.kernel.pipeline.contracts as mod

        source = open(mod.__file__).read()
        assert "from src.monkey_brain.kernel.compile" not in source
        assert "from src.monkey_brain.kernel.execute" not in source
        assert "from src.monkey_brain.kernel.graph_manager" not in source


# ═══════════════════════════════════════════════════════════════════════════
# PipelineResponse
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineResponse:
    def test_construction_minimal(self):
        resp = PipelineResponse(answer="42")
        assert resp.answer == "42"
        assert resp.status == PipelineStatus.SUCCESS
        assert resp.execution_id
        assert resp.artifacts == ()
        assert resp.trace == ()
        assert resp.metrics == {}
        assert resp.errors == ()
        assert resp.metadata == {}

    def test_construction_full(self):
        resp = PipelineResponse(
            answer="done",
            status=PipelineStatus.PARTIAL,
            execution_id="exec-1",
            artifacts=(PipelineArtifact(name="graph", artifact_type="graph/execution"),),
            trace=(PipelineTraceEntry(step="planning", status="ok", duration_ms=50.0),),
            metrics={"latency_ms": 100.0},
            errors=(PipelineError(code="E001", message="partial failure"),),
            metadata={"session_id": "s1"},
        )
        assert resp.answer == "done"
        assert resp.status == PipelineStatus.PARTIAL
        assert len(resp.artifacts) == 1
        assert len(resp.trace) == 1
        assert resp.metrics["latency_ms"] == 100.0
        assert len(resp.errors) == 1

    def test_immutable(self):
        resp = PipelineResponse(answer="test")
        with pytest.raises(FrozenInstanceError):
            resp.answer = "changed"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="must be one of"):
            PipelineResponse(answer="x", status="bogus")


# ═══════════════════════════════════════════════════════════════════════════
# PipelineStatus
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineStatus:
    def test_constants(self):
        assert PipelineStatus.SUCCESS == "success"
        assert PipelineStatus.PARTIAL == "partial"
        assert PipelineStatus.FAILED == "failed"
        assert PipelineStatus.PENDING == "pending"


# ═══════════════════════════════════════════════════════════════════════════
# PipelineArtifact
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineArtifact:
    def test_construction(self):
        art = PipelineArtifact(name="plan", artifact_type="application/json", data={"steps": []})
        assert art.name == "plan"
        assert art.data == {"steps": []}

    def test_immutable(self):
        art = PipelineArtifact(name="x", artifact_type="y")
        with pytest.raises(FrozenInstanceError):
            art.name = "z"


# ═══════════════════════════════════════════════════════════════════════════
# PipelineTraceEntry
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineTraceEntry:
    def test_construction(self):
        entry = PipelineTraceEntry(step="planning", status="ok", duration_ms=42.5)
        assert entry.step == "planning"
        assert entry.duration_ms == 42.5

    def test_defaults(self):
        entry = PipelineTraceEntry(step="x")
        assert entry.status == "ok"
        assert entry.duration_ms == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# PipelineError
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineError:
    def test_construction(self):
        err = PipelineError(code="E001", message="something broke", step="execution")
        assert err.code == "E001"
        assert err.step == "execution"

    def test_defaults(self):
        err = PipelineError(code="E002", message="fail")
        assert err.step == ""
        assert err.details == {}


# ═══════════════════════════════════════════════════════════════════════════
# Dependency direction verification
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencyDirection:
    def test_contracts_import_no_runtime(self):
        """contracts.py must not import any runtime implementation."""
        import inspect
        import src.monkey_brain.kernel.pipeline.contracts as mod

        source = inspect.getsource(mod)
        forbidden = [
            "from src.monkey_brain.kernel.compile.",
            "from src.monkey_brain.kernel.execute.",
            "from src.monkey_brain.kernel.graph_manager",
            "from src.monkey_brain.kernel.policy.",
            "from src.monkey_brain.kernel.simulation_runtime",
            "from src.monkey_brain.kernel.comparator_runtime",
            "from src.monkey_brain.kernel.cognitive_runtime",
        ]
        for imp in forbidden:
            assert imp not in source, f"contracts.py must not import: {imp}"

    def test_compiled_request_wraps_request(self):
        """CompiledRequest contains PipelineRequest, not a copy."""
        req = PipelineRequest(question="test")
        compiled = CompiledRequest(
            request=req,
            intent={},
            goal={},
            intent_ir={},
            goal_ir={},
            execution_context={},
        )
        assert compiled.request is req
