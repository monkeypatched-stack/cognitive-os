"""Regulatory compliance agents — DDD policy layer specialisations.

Each agent validates a specific regulatory standard against context signals.
All agents are auto-registered into the Broca registry on import.
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent
from broca.agents.ddd.compliance.gdpr import GDPRAgent
from broca.agents.ddd.compliance.iso27001 import ISO27001Agent
from broca.agents.ddd.compliance.soc2 import SOC2Agent
from broca.agents.ddd.compliance.fda import FDAAgent
from broca.agents.ddd.compliance.gxp import GxPAgent
from broca.agents.ddd.compliance.iec61508 import IEC61508Agent
from broca.agents.ddd.compliance.iso10218 import ISO10218Agent

__all__ = [
    "BaseComplianceAgent",
    "GDPRAgent",
    "ISO27001Agent",
    "SOC2Agent",
    "FDAAgent",
    "GxPAgent",
    "IEC61508Agent",
    "ISO10218Agent",
]
