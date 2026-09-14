"""Cognitive Trust Network — typed relationships between runtimes (ADR 0021).

MonkeyBrain connects cognitive runtimes.  A trust relationship is a signed,
revocable, auditable agreement that grants explicit permissions for classes of
knowledge exchange — it does NOT merge worlds.

Every trust edge carries:
  - typed relationship (Personal↔Personal, Enterprise↔Government, …)
  - permission set
  - trust score (0–1)
  - knowledge scope (what kinds of knowledge may flow)
  - policy scope (what policies apply)
  - expiration (optional TTL)
  - revocation status
  - reputation (cumulative score from interactions)
  - delegation chain (who authorized this trust)
  - auditable provenance trail

    Person → Runtime → Trust Graph → Knowledge Exchange
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.monkey_brain.kernel.compile import _obs

logger = logging.getLogger("agentos.compile.trust")


class Relationship(str, Enum):
    FRIEND = "friend"
    FAMILY = "family"
    COLLEAGUE = "colleague"
    MENTOR = "mentor"
    STUDENT = "student"
    ORGANIZATION = "organization"
    COMMUNITY = "community"
    RESEARCH_PARTNER = "research_partner"
    # Typed inter-runtime relationships
    PERSONAL_TO_PERSONAL = "personal_to_personal"
    PERSONAL_TO_ENTERPRISE = "personal_to_enterprise"
    ENTERPRISE_TO_GOVERNMENT = "enterprise_to_government"
    GOVERNMENT_TO_GOVERNMENT = "government_to_government"
    INSTITUTION_TO_INSTITUTION = "institution_to_institution"


class Perm:
    """Permission classes (a friendship is not unrestricted access)."""

    SEND_MESSAGE = "msg.send"
    RECEIVE_MESSAGE = "msg.receive"
    PUBLISH_OBSERVATIONS = "know.publish_obs"
    SUBSCRIBE_OBSERVATIONS = "know.subscribe_obs"
    REQUEST_KNOWLEDGE = "know.request"
    SHARE_EXECUTION_GRAPHS = "know.share_graphs"
    SHARE_WORKFLOWS = "know.share_workflows"
    SHARE_ONTOLOGY = "know.share_ontology"
    PUBLISH_BELIEFS = "learn.publish_beliefs"
    RECEIVE_BELIEFS = "learn.receive_beliefs"
    PUBLISH_EXPERIENCES = "learn.publish_exp"
    RECEIVE_EXPERIENCES = "learn.receive_exp"
    INVITE_WORKFLOW = "collab.invite"
    EXECUTE_JOINTLY = "collab.execute"
    DELEGATE_TASKS = "collab.delegate"
    SYNCHRONIZE_CONTEXT = "collab.sync_context"


_MSG = {Perm.SEND_MESSAGE, Perm.RECEIVE_MESSAGE}

RELATIONSHIP_POLICIES: dict[Relationship, set[str]] = {
    Relationship.FRIEND: _MSG | {Perm.SHARE_WORKFLOWS, Perm.SUBSCRIBE_OBSERVATIONS},
    Relationship.FAMILY: _MSG | {Perm.SHARE_WORKFLOWS, Perm.PUBLISH_BELIEFS, Perm.RECEIVE_BELIEFS},
    Relationship.COLLEAGUE: _MSG
    | {
        Perm.SHARE_WORKFLOWS,
        Perm.SHARE_EXECUTION_GRAPHS,
        Perm.EXECUTE_JOINTLY,
        Perm.DELEGATE_TASKS,
    },
    Relationship.MENTOR: _MSG
    | {
        Perm.SHARE_EXECUTION_GRAPHS,
        Perm.SHARE_WORKFLOWS,
        Perm.PUBLISH_BELIEFS,
        Perm.PUBLISH_EXPERIENCES,
        Perm.SHARE_ONTOLOGY,
    },
    Relationship.STUDENT: _MSG
    | {
        Perm.RECEIVE_BELIEFS,
        Perm.RECEIVE_EXPERIENCES,
        Perm.SUBSCRIBE_OBSERVATIONS,
        Perm.REQUEST_KNOWLEDGE,
    },
    Relationship.ORGANIZATION: _MSG
    | {
        Perm.SHARE_WORKFLOWS,
        Perm.SHARE_EXECUTION_GRAPHS,
        Perm.SYNCHRONIZE_CONTEXT,
        Perm.DELEGATE_TASKS,
    },
    Relationship.COMMUNITY: _MSG | {Perm.PUBLISH_OBSERVATIONS, Perm.SUBSCRIBE_OBSERVATIONS, Perm.SHARE_ONTOLOGY},
    Relationship.RESEARCH_PARTNER: _MSG
    | {
        Perm.SHARE_EXECUTION_GRAPHS,
        Perm.PUBLISH_BELIEFS,
        Perm.RECEIVE_BELIEFS,
        Perm.SHARE_ONTOLOGY,
        Perm.EXECUTE_JOINTLY,
    },
    Relationship.PERSONAL_TO_PERSONAL: _MSG | {Perm.SHARE_WORKFLOWS, Perm.PUBLISH_BELIEFS},
    Relationship.PERSONAL_TO_ENTERPRISE: _MSG | {Perm.SHARE_WORKFLOWS, Perm.SHARE_EXECUTION_GRAPHS},
    Relationship.ENTERPRISE_TO_GOVERNMENT: _MSG | {Perm.SHARE_EXECUTION_GRAPHS, Perm.SYNCHRONIZE_CONTEXT},
    Relationship.GOVERNMENT_TO_GOVERNMENT: _MSG
    | {Perm.SHARE_EXECUTION_GRAPHS, Perm.SYNCHRONIZE_CONTEXT, Perm.DELEGATE_TASKS},
    Relationship.INSTITUTION_TO_INSTITUTION: _MSG
    | {Perm.SHARE_EXECUTION_GRAPHS, Perm.SHARE_ONTOLOGY, Perm.PUBLISH_EXPERIENCES},
}

_DEFAULT_TRUST: dict[Relationship, float] = {
    Relationship.FAMILY: 0.9,
    Relationship.MENTOR: 0.85,
    Relationship.RESEARCH_PARTNER: 0.75,
    Relationship.COLLEAGUE: 0.7,
    Relationship.ORGANIZATION: 0.65,
    Relationship.FRIEND: 0.6,
    Relationship.STUDENT: 0.6,
    Relationship.COMMUNITY: 0.4,
    Relationship.PERSONAL_TO_PERSONAL: 0.6,
    Relationship.PERSONAL_TO_ENTERPRISE: 0.5,
    Relationship.ENTERPRISE_TO_GOVERNMENT: 0.4,
    Relationship.GOVERNMENT_TO_GOVERNMENT: 0.7,
    Relationship.INSTITUTION_TO_INSTITUTION: 0.6,
}


@dataclass
class TrustEdge:
    """A typed, revocable, auditable trust relationship between two runtimes."""

    src: str
    dst: str
    relationship: Relationship
    permissions: set[str] = field(default_factory=set)
    trust_score: float = 0.5
    knowledge_scope: set[str] = field(default_factory=set)  # allowed knowledge kinds (empty = all)
    policy_scope: set[str] = field(default_factory=set)  # policies that apply (empty = all)
    expiration: float = 0.0  # 0 = no expiry
    revoked: bool = False
    revoked_at: float = 0.0
    revoked_by: str = ""
    reputation: float = 0.5  # cumulative interaction score
    delegation_chain: list[str] = field(default_factory=list)  # who authorized this
    created_at: float = field(default_factory=time.time)
    provenance: list[tuple] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        if self.revoked:
            return False
        if self.expiration > 0 and time.time() > self.expiration:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "relationship": self.relationship.value,
            "permissions": sorted(self.permissions),
            "trust_score": self.trust_score,
            "knowledge_scope": sorted(self.knowledge_scope),
            "policy_scope": sorted(self.policy_scope),
            "expiration": self.expiration,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "reputation": self.reputation,
            "delegation_chain": self.delegation_chain,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrustEdge":
        return cls(
            src=d["src"],
            dst=d["dst"],
            relationship=Relationship(d["relationship"]),
            permissions=set(d.get("permissions", [])),
            trust_score=d.get("trust_score", 0.5),
            knowledge_scope=set(d.get("knowledge_scope", [])),
            policy_scope=set(d.get("policy_scope", [])),
            expiration=d.get("expiration", 0.0),
            revoked=d.get("revoked", False),
            revoked_at=d.get("revoked_at", 0.0),
            revoked_by=d.get("revoked_by", ""),
            reputation=d.get("reputation", 0.5),
            delegation_chain=list(d.get("delegation_chain", [])),
            created_at=d.get("created_at", time.time()),
        )


class TrustNetwork:
    """A directed graph of typed, revocable trust edges between runtimes."""

    def __init__(self, store_path: str | None = None) -> None:
        self._edges: dict[tuple[str, str], TrustEdge] = {}
        self._revoked: dict[tuple[str, str], TrustEdge] = {}
        # Optional durable store: relationships survive restart instead of being re-provisioned.
        self._store = store_path
        if store_path:
            self.load(store_path)

    # ── persistence (durable trust relationships) ────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {"edges": [e.to_dict() for e in self._edges.values()]}

    def load_dict(self, d: dict[str, Any]) -> None:
        for ed in d.get("edges", []):
            e = TrustEdge.from_dict(ed)
            self._edges[(e.src, e.dst)] = e

    def load(self, path: str) -> None:
        import json
        import os

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self.load_dict(json.load(fh))
            except Exception as exc:
                logger.warning("[trust] failed to load %s: %s", path, exc)

    def _save(self) -> None:
        if not self._store:
            return
        import json
        from src.monkey_brain.kernel.identity import _atomic_write

        _atomic_write(self._store, json.dumps(self.to_dict()).encode("utf-8"), mode=0o600)

    def connect(
        self,
        src: str,
        dst: str,
        relationship: Relationship,
        *,
        trust: float | None = None,
        permissions: set[str] | None = None,
        mutual: bool = False,
        knowledge_scope: set[str] | None = None,
        policy_scope: set[str] | None = None,
        expiration: float = 0.0,
        delegation_chain: list[str] | None = None,
    ) -> TrustEdge:
        """Create a trust edge src→dst."""
        perms = set(permissions) if permissions is not None else set(RELATIONSHIP_POLICIES.get(relationship, set()))
        edge = TrustEdge(
            src=src,
            dst=dst,
            relationship=relationship,
            permissions=perms,
            trust_score=(trust if trust is not None else _DEFAULT_TRUST.get(relationship, 0.5)),
            knowledge_scope=knowledge_scope or set(),
            policy_scope=policy_scope or set(),
            expiration=expiration,
            delegation_chain=delegation_chain or [],
        )
        edge.provenance.append(("connect", relationship.value, round(time.time(), 3)))
        self._edges[(src, dst)] = edge
        _obs.event(
            "trust.connect",
            src=src,
            dst=dst,
            relationship=relationship.value,
            trust=edge.trust_score,
            permissions=len(perms),
        )
        if mutual:
            self.connect(
                dst,
                src,
                relationship,
                trust=trust,
                permissions=permissions,
                mutual=False,
                knowledge_scope=knowledge_scope,
                policy_scope=policy_scope,
            )
        self._save()
        return edge

    def edge(self, src: str, dst: str) -> TrustEdge | None:
        e = self._edges.get((src, dst))
        if e and not e.is_active:
            return None
        return e

    def permits(self, src: str, dst: str, permission: str) -> bool:
        e = self.edge(src, dst)
        return bool(e and permission in e.permissions)

    def trust(self, src: str, dst: str) -> float:
        e = self.edge(src, dst)
        return e.trust_score if e else 0.0

    def grant(self, src: str, dst: str, permission: str) -> bool:
        e = self.edge(src, dst)
        if not e:
            return False
        e.permissions.add(permission)
        e.provenance.append(("grant", permission, round(time.time(), 3)))
        self._save()
        return True

    def revoke(self, src: str, dst: str, permission: str | None = None, revoked_by: str = "") -> bool:
        """Revoke a permission or the entire edge."""
        e = self._edges.get((src, dst))
        if not e:
            return False
        if permission:
            e.permissions.discard(permission)
            e.provenance.append(("revoke_permission", permission, round(time.time(), 3)))
        else:
            e.revoked = True
            e.revoked_at = time.time()
            e.revoked_by = revoked_by
            e.provenance.append(("revoke_edge", "", round(time.time(), 3)))
            self._revoked[(src, dst)] = e
            del self._edges[(src, dst)]
            _obs.event("trust.revoke", src=src, dst=dst, by=revoked_by)
        self._save()
        return True

    def update_reputation(self, src: str, dst: str, delta: float) -> float:
        """Adjust reputation score for a trust edge.  Returns new score."""
        e = self.edge(src, dst)
        if not e:
            return 0.0
        e.reputation = max(0.0, min(1.0, e.reputation + delta))
        e.provenance.append(("reputation", round(delta, 4), round(time.time(), 3)))
        return e.reputation

    def record_outcome(self, src: str, dst: str, success: bool, latency_ms: float = 0.0) -> float:
        """Automatically update reputation based on an execution outcome.

        Success: +0.05 (capped at 1.0)
        Failure: -0.10 (capped at 0.0)
        Slow execution (>5s): additional -0.02
        Fast execution (<100ms): additional +0.02
        """
        e = self.edge(src, dst)
        if not e:
            return 0.0
        delta = 0.05 if success else -0.10
        if latency_ms > 5000:
            delta -= 0.02
        elif latency_ms < 100 and success:
            delta += 0.02
        return self.update_reputation(src, dst, delta)

    def disconnect(self, src: str, dst: str) -> None:
        if (src, dst) in self._edges:
            self._edges[(src, dst)].provenance.append(("disconnect", "", round(time.time(), 3)))
            del self._edges[(src, dst)]
            _obs.event("trust.disconnect", src=src, dst=dst)

    def audit(self, src: str, dst: str) -> list[tuple]:
        e = self._edges.get((src, dst)) or self._revoked.get((src, dst))
        return list(e.provenance) if e else []

    def relationships_of(self, runtime: str) -> list[tuple[str, Relationship]]:
        return [(dst, e.relationship) for (s, dst), e in self._edges.items() if s == runtime and e.is_active]

    # ── hierarchical / transitive delegation (the pyramid) ───────────────────────────

    def permits_via(self, src: str, dst: str, relationship: Relationship, *, max_depth: int = 8) -> bool:
        """Is `dst` reachable from `src` along a chain of edges of `relationship`?"""
        if src == dst:
            return True
        out: dict[str, list[tuple[str, TrustEdge]]] = {}
        for (s, d), e in self._edges.items():
            if e.relationship is relationship and e.is_active:
                out.setdefault(s, []).append((d, e))
        seen = {src}
        frontier = [(src, 0)]
        while frontier:
            node, depth = frontier.pop()
            if depth >= max_depth:
                continue
            for nxt, _e in out.get(node, ()):
                if nxt == dst:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append((nxt, depth + 1))
        return False

    def delegation_path(self, src: str, dst: str, relationship: Relationship, *, max_depth: int = 8) -> list[str]:
        """The reporting chain src→…→dst along `relationship` edges."""
        if src == dst:
            return [src]
        out: dict[str, list[str]] = {}
        for (s, d), e in self._edges.items():
            if e.relationship is relationship and e.is_active:
                out.setdefault(s, []).append(d)
        seen = {src}
        frontier = [[src]]
        while frontier:
            path = frontier.pop(0)
            if len(path) - 1 >= max_depth:
                continue
            for nxt in out.get(path[-1], ()):
                if nxt == dst:
                    return path + [nxt]
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(path + [nxt])
        return []
