"""Broca DDD cognitive agent hierarchy.

Every DDD construct is an executable cognitive abstraction:
  Perceive → Reason → Act → Learn

Agents are registered via BrocaAgentRegistry — never imported directly
into MonkeyBrain core (loose coupling).
"""

from broca.agents.ddd._base_ddd import BaseDDDAgent
from broca.agents.ddd.domain import DomainAgent
from broca.agents.ddd.bounded_context import BoundedContextAgent
from broca.agents.ddd.aggregate import AggregateAgent
from broca.agents.ddd.entity import EntityAgent
from broca.agents.ddd.value_object import ValueObjectAgent
from broca.agents.ddd.repository import RepositoryAgent
from broca.agents.ddd.policy import PolicyAgent
from broca.agents.ddd.factory import FactoryAgent
from broca.agents.ddd.event import EventAgent
from broca.agents.ddd.compliance import (
    BaseComplianceAgent,
    GDPRAgent,
    ISO27001Agent,
    SOC2Agent,
    FDAAgent,
    GxPAgent,
    IEC61508Agent,
    ISO10218Agent,
)

__all__ = [
    "BaseDDDAgent",
    "DomainAgent",
    "BoundedContextAgent",
    "AggregateAgent",
    "EntityAgent",
    "ValueObjectAgent",
    "RepositoryAgent",
    "PolicyAgent",
    "FactoryAgent",
    "EventAgent",
    # Compliance
    "BaseComplianceAgent",
    "GDPRAgent",
    "ISO27001Agent",
    "SOC2Agent",
    "FDAAgent",
    "GxPAgent",
    "IEC61508Agent",
    "ISO10218Agent",
]
