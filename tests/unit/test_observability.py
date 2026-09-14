"""Observability is built in — every significant runtime operation emits telemetry.

Installs a capture sink and asserts the whole lifecycle is observable: compile, world
batch, knowledge share/import/revoke, cognitive cycles, checkpoints. In production these
route to Lemon; the sink here proves the instrumentation exists and fires.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile import (
    ActorRuntime,
    Context,
    GraphCompiler,
    SemanticGraphSnapshot,
    WorldModelRuntime,
)


class _Cognitive:
    name = "cognitive"


@pytest.fixture()
def sink():
    rec = _obs.Recorder()
    _obs.set_sink(rec)
    yield rec
    _obs.clear_sink()


def test_compile_emits_telemetry(sink):
    g = SemanticGraphSnapshot(
        nodes=[{"id": "A", "agent": "A"}, {"id": "B", "agent": "B"}],
        edges=[{"from": "A", "to": "B"}],
        strengths={},
        revision=1,
    )
    op = GraphCompiler().compile(g)
    GraphCompiler().recompile(op, g, dirty=[("A", "B")])
    assert "compile.full" in sink.names()
    assert "compile.incremental" in sink.names()
    assert sink.count("compile") == 2  # one full + one incremental event


def test_world_and_knowledge_events(sink):
    wm = WorldModelRuntime()
    wm.batch_update("acme", [{"src": "Mon", "dst": "Standup", "domain": "calendar"}])
    wm.share_domain("acme", "calendar")
    assert "world.batch" in sink.names()
    assert "knowledge.share" in sink.names()


def test_actor_lifecycle_events(sink, tmp_path):
    wm = WorldModelRuntime()
    wm.batch_update(
        "acme",
        [
            {"src": "Hive", "dst": "Inspect", "domain": "bk"},
            {"src": "Inspect", "dst": "Treat", "domain": "bk"},
        ],
    )
    ctx = Context(tenant_id="acme")
    a = ActorRuntime(
        "alice",
        cognitive_runtime=_Cognitive(),
        context=ctx,
        local_belief=wm.new_local_belief(),
        world_view=wm.view(ctx),
    )
    a.act("Hive")
    a.cognitive_cycle("Hive", "Treat")
    a.checkpoint(tmp_path / "c.json")
    a.import_shared("enterprise")
    a.revoke("enterprise")

    names = sink.names()
    for expected in (
        "actor.act",
        "actor.cognitive_cycle",
        "cognitive.cycle",
        "runtime.checkpoint",
        "knowledge.import",
        "knowledge.revoke",
    ):
        assert expected in names, f"missing telemetry for {expected}"
    # the cognitive cycle reported its epistemic loss as a gauge
    assert any(kind == "gauge" and name == "actor.epistemic_loss" for kind, name, *_ in sink.events)


def test_no_sink_is_silent_noop():
    # with no sink and no Lemon, emitting must never raise
    _obs.clear_sink()
    _obs.counter("x")
    _obs.gauge("y", 1.0)
    _obs.event("z", a=1)
