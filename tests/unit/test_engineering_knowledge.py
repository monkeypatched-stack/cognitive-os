"""Tests for Engineering Knowledge Pack Publishing."""

import sys
import os
import json

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domains.software_engineering.knowledge.pack import (
    SoftwareEngineeringKnowledgePublisher as EngineeringKnowledgePublisher,
    SoftwareEngineeringKnowledgePack as EngineeringKnowledgePack,
)


def test_publish():
    pub = EngineeringKnowledgePublisher()
    kp = pub.publish(
        spec_id="spec-001",
        goal="Build Work Order Service",
        domain="manufacturing",
        generated_files={"main.py": "code", "models.py": "code"},
        governance_findings=[{"severity": "low", "finding": "good structure"}],
        confidence=0.85,
    )
    assert kp.spec_id == "spec-001"
    assert kp.goal == "Build Work Order Service"
    assert len(kp.generated_files) == 2
    assert kp.confidence == 0.85


def test_to_knowledge_items():
    kp = EngineeringKnowledgePack(
        spec_id="spec-002",
        goal="Build Todo Service",
        domain="manufacturing",
        generated_files={"main.py": "code"},
        governance_findings=[{"severity": "critical"}],
        workflow_topology=["Create", "Todo", "Notify"],
    )
    items = kp.to_knowledge_items()
    assert len(items) >= 3  # spec, code, governance, workflow


def test_search():
    pub = EngineeringKnowledgePublisher()
    pub.publish(spec_id="s1", goal="Build Work Order Service", domain="manufacturing")
    pub.publish(spec_id="s2", goal="Build Todo Service", domain="manufacturing")
    pub.publish(spec_id="s3", goal="Inspect Robot Arm", domain="robotics")
    results = pub.search("work order")
    assert len(results) == 1
    assert results[0].spec_id == "s1"


def test_retrieval_items():
    pub = EngineeringKnowledgePublisher()
    pub.publish(
        spec_id="s1",
        goal="Build Work Order Service",
        domain="manufacturing",
        generated_files={"a.py": "code"},
        workflow_topology=["Create", "Notify"],
    )
    items = pub.get_knowledge_items_for_retrieval("work order")
    assert len(items) >= 2


def test_summary():
    pub = EngineeringKnowledgePublisher()
    pub.publish(spec_id="s1", goal="Goal A", domain="d1", confidence=0.9)
    pub.publish(spec_id="s2", goal="Goal B", domain="d2", confidence=0.7)
    s = pub.summary()
    assert s["total_published"] == 2
    assert s["avg_confidence"] == 0.8


def test_serialization():
    kp = EngineeringKnowledgePack(spec_id="s1", goal="test", confidence=0.8)
    d = kp.to_dict()
    assert d["spec_id"] == "s1"
    assert d["confidence"] == 0.8
    assert "published_at" in d


def test_to_dict_json_safe():
    kp = EngineeringKnowledgePack(spec_id="s1", goal="test", confidence=0.8)
    json_str = json.dumps(kp.to_dict())
    assert "s1" in json_str


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)):
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"\nEngineeringKnowledge: {ok}/{ok + len(f)} passed")
    for e in f:
        print(f"  FAIL: {e}")
