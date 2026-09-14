"""Broca ETASS agents — each handles one step type, discovers capabilities at runtime."""

from ._base import BaseETASSAgent
from .auth_policy import AuthPolicyAgent
from .cingulate import CingulateAgent
from .motor_cortex import MotorCortexAgent
from .source_control import SourceControlAgent
from .pull_request import PullRequestAgent
from .human_review import HumanReviewAgent
from .testing import UnitTestingAgent, StaticAnalysisAgent, SecurityAgent
from .introspection import IntrospectionAgent, OperationalEvidenceAgent
from .chart_evolution import ChartEvolutionAgent
from .client_charter import ClientCharterAgent
from .client_capability_agent import ClientCapabilityAgent
from .planner import PlannerAgent
from .executor import ExecutorAgent
from .prompt_compiler import PromptCompilerAgent
from .api_chart_agent import ApiChartAgent
from .service_gen_agent import ServiceGenAgent
from .serve_agent import ServeAgent
from .client_gen_agent import ClientGenAgent
from .compile_agent_agent import CompileAgentAgent
from .test_service_agent import TestServiceAgent
from .fixit_agent import FixItAgent
from .seed_agent import SeedAgent
from .adversarial_agent import AdversarialAgent
from .nanda import NANDAAgent, NANDAProxyAgent
from .requirements_agent import RequirementsAgent
from .architecture_decision_agent import ArchitectureDecisionAgent
from .code_review_agent import CodeReviewAgent
from .test_generation_agent import TestGenerationAgent
from .integration_test_agent import IntegrationTestAgent
from .artifact_packaging_agent import ArtifactPackagingAgent
from .deployment_agent import DeploymentAgent
from .release_agent import ReleaseAgent
from .specification_discovery_agent import SpecificationDiscoveryAgent
from .graph_generator_agent import GraphGeneratorAgent
from .auto_agent_generator import AutoAgentGenerator
from .policy_update_agent import PolicyUpdateAgent
from .ci import (
    JenkinsAgent,
    GitHubActionsAgent,
    GitLabCIAgent,
    AzurePipelinesAgent,
    CircleCIAgent,
    TravisCIAgent,
    TeamCityAgent,
    BambooAgent,
    BuildkiteAgent,
    DroneCIAgent,
)
from .ddd import (
    DomainAgent,
    BoundedContextAgent,
    AggregateAgent,
    EntityAgent,
    ValueObjectAgent,
    RepositoryAgent,
    PolicyAgent,
    FactoryAgent,
    EventAgent,
)
from .soma import (
    SomaGitAgent,
    SpecStatusAgent,
    SpecCheckoutAgent,
    SpecAddAgent,
    SpecCommitAgent,
    SpecPushAgent,
    SpecPullAgent,
    SpecSyncAgent,
    SpecBranchAgent,
    SpecDiffAgent,
    SpecStashAgent,
    SpecMergeAgent,
    SpecRepoInitAgent,
)

__all__ = [
    "BaseETASSAgent",
    "AuthPolicyAgent",
    "CingulateAgent",
    "MotorCortexAgent",
    "SourceControlAgent",
    "PullRequestAgent",
    "HumanReviewAgent",
    "UnitTestingAgent",
    "StaticAnalysisAgent",
    "SecurityAgent",
    "IntrospectionAgent",
    "OperationalEvidenceAgent",
    "ChartEvolutionAgent",
    "ClientCharterAgent",
    "ClientCapabilityAgent",
    "PlannerAgent",
    "ExecutorAgent",
    "PromptCompilerAgent",
    "ApiChartAgent",
    "ServiceGenAgent",
    "ServeAgent",
    "ClientGenAgent",
    "CompileAgentAgent",
    "TestServiceAgent",
    "FixItAgent",
    "SeedAgent",
    "AdversarialAgent",
    "NANDAAgent",
    "NANDAProxyAgent",
    "RequirementsAgent",
    "ArchitectureDecisionAgent",
    "CodeReviewAgent",
    "TestGenerationAgent",
    "IntegrationTestAgent",
    "ArtifactPackagingAgent",
    "DeploymentAgent",
    "ReleaseAgent",
    "SpecificationDiscoveryAgent",
    "GraphGeneratorAgent",
    "AutoAgentGenerator",
    "PolicyUpdateAgent",
    "ClientCapabilityRegisterAgent",
    "PlanSimulateAgent",
    "ComparatorAgent",
    "PolicyUpdateAgent",
    "JenkinsAgent",
    "GitHubActionsAgent",
    "GitLabCIAgent",
    "AzurePipelinesAgent",
    "CircleCIAgent",
    "TravisCIAgent",
    "TeamCityAgent",
    "BambooAgent",
    "BuildkiteAgent",
    "DroneCIAgent",
    "SomaGitAgent",
    "SpecStatusAgent",
    "SpecCheckoutAgent",
    "SpecAddAgent",
    "SpecCommitAgent",
    "SpecPushAgent",
    "SpecPullAgent",
    "SpecSyncAgent",
    "SpecBranchAgent",
    "SpecDiffAgent",
    "SpecStashAgent",
    "SpecMergeAgent",
    "SpecRepoInitAgent",
    "DomainAgent",
    "BoundedContextAgent",
    "AggregateAgent",
    "EntityAgent",
    "ValueObjectAgent",
    "RepositoryAgent",
    "PolicyAgent",
    "FactoryAgent",
    "EventAgent",
]
