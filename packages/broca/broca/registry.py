"""Broca Agent Registry — dynamic agent discovery by step type.

Steps discover agents by type. Agents discover capabilities from Cerebellum.

Auto-registration:
  Any class that extends BaseETASSAgent (or BaseDDDAgent) is automatically
  tracked in _AUTOREGISTER_CLASSES at class-definition time via __init_subclass__.
  When the registry is first accessed via get_registry(), all tracked classes
  are instantiated and registered — no explicit register_etass_agents() call needed.

Provider loading:
  All Cerebellum capabilities (NANDA, OpenClaw, n8n, ModelBackend) are loaded
  at registry init time by scanning the capability providers declared in
  _CAPABILITY_PROVIDERS.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("broca.registry")

# Classes that declared themselves auto-registrable via @auto_register or __init_subclass__
_AUTOREGISTER_CLASSES: list[type] = []


def auto_register(cls):
    """Class decorator — mark an agent class for auto-registration.

    Applied to any BaseETASSAgent or BaseDDDAgent subclass that should be
    discoverable without an explicit register call.

    Usage:
        @auto_register
        class MyAgent(BaseETASSAgent):
            agent_type = "my_agent"
    """
    _AUTOREGISTER_CLASSES.append(cls)
    return cls


@runtime_checkable
class BrocaAgent(Protocol):
    """Protocol all Broca agents must implement."""

    @property
    def agent_type(self) -> str:
        """Canonical type string used for discovery (e.g. 'governance', 'codegen')."""
        ...

    @property
    def description(self) -> str:
        """One-line description — shown to LLMExplorer when generating steps."""
        ...

    async def handle(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute this agent's responsibility and return results + feedback signal."""
        ...

    def feedback(self) -> float:
        """Return reward signal (0.0–1.0) from last handle() call for Bellman update."""
        ...


class BrocaAgentRegistry:
    """Registry of agent_type → BrocaAgent.

    Steps call registry.discover(step_type) to get the right agent at runtime.
    Auto-populates from _AUTOREGISTER_CLASSES on first access.
    Falls back to NANDA for unknown step types.
    """

    def __init__(self) -> None:
        self._agents: dict[str, BrocaAgent] = {}
        self._runtime: Any | None = None
        self._bootstrapped: bool = False

    def _bootstrap(self) -> None:
        """Instantiate and register all @auto_register classes on first use."""
        if self._bootstrapped:
            return
        self._bootstrapped = True
        for cls in _AUTOREGISTER_CLASSES:
            try:
                agent = cls(runtime=self._runtime)
                self._agents[agent.agent_type] = agent
                logger.debug("[broca] auto-registered: %s", agent.agent_type)
            except Exception as exc:
                logger.warning("[broca] auto-register failed for %s: %s", cls.__name__, exc)
        if _AUTOREGISTER_CLASSES:
            logger.info("[broca] auto-registered %d agents", len(_AUTOREGISTER_CLASSES))

    def register(self, agent: BrocaAgent) -> None:
        self._agents[agent.agent_type] = agent
        logger.info("[broca] registered agent: %s — %s", agent.agent_type, agent.description)

    def discover(self, step_type: str) -> BrocaAgent | None:
        """Discover the agent responsible for a given step type.

        Triggers auto-registration on first call.
        Falls back to NANDA when no local agent is registered.
        """
        self._bootstrap()
        agent = self._agents.get(step_type)
        if agent is not None:
            return agent

        # Provider check — try exact match, then best fuzzy match
        _providers = None
        try:
            import os

            _path = os.path.join(os.path.dirname(__file__), "..", "..", "..")
            if _path not in sys.path:
                sys.path.insert(0, _path)
            from src.monkey_brain.kernel.provider_registry import init_providers

            _providers = init_providers()
            if not any(p.get("agents") for p in _providers.list_providers()):
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
                    try:
                        exe.submit(lambda: asyncio.run(_providers.discover_from_providers())).result(timeout=10)
                    except Exception:
                        pass
            # Exact match first
            agent_info = _providers.find_agent(step_type)
            if agent_info:
                from broca.agents.provider_proxy import ProviderProxyAgent

                provider_name = agent_info.get("provider", "unknown")
                return ProviderProxyAgent(agent_info, provider_name)
            # Fuzzy match — find closest agent name
            agent_info = self._fuzzy_find_provider_agent(_providers, step_type)
            if agent_info:
                from broca.agents.provider_proxy import ProviderProxyAgent

                provider_name = agent_info.get("provider", "unknown")
                return ProviderProxyAgent(agent_info, provider_name)
        except Exception:
            pass

        # NANDA fallback — discover remote agent
        try:
            nanda_agent = self._nanda_discover_sync(step_type)
            if nanda_agent is not None:
                return nanda_agent
        except Exception:
            pass

        return None

    def _fuzzy_find_provider_agent(self, providers, step_type: str) -> dict | None:
        """Find the closest matching agent from providers by name similarity."""
        all_agents = []
        for provider in providers._providers.values():
            all_agents.extend(provider.list_agents())
        if not all_agents:
            return None
        step_lower = step_type.lower().replace("_", "").replace("-", "")
        best_match = None
        best_score = 0
        for agent in all_agents:
            name = agent.get("name", "").lower().replace("_", "").replace("-", "")
            # Exact substring match
            if step_lower in name or name in step_lower:
                return agent
            # Word overlap score
            step_words = set(step_lower.split())
            name_words = set(name.split())
            overlap = len(step_words & name_words)
            if overlap > best_score:
                best_score = overlap
                best_match = agent
        return best_match if best_score > 0 else None

    def _nanda_discover_sync(self, step_type: str):
        """Query NANDA as a provider to find a remote agent for step_type.

        NANDA is discovery-only — MonkeyBrain never publishes to it.
        Returns a NANDAProxyAgent wrapping the remote card, or None.
        """
        import asyncio
        from cerebellum.capabilities.agent.nanda import NANDACapability

        cap = NANDACapability()
        if not cap._available:
            return None

        async def _query():
            result = await cap.execute(
                {
                    "operation": "discover",
                    "agent_type": step_type,
                    "capability": step_type,
                }
            )
            agents = result.get("agents", [])
            if not agents:
                return None
            from broca.agents.nanda import NANDAProxyAgent

            return NANDAProxyAgent(agents[0])

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return None  # async context — NANDA fallback skipped (would deadlock)
            return loop.run_until_complete(_query())
        except RuntimeError:
            return None

    def list_agents(self) -> dict[str, str]:
        """Return {agent_type: description} for LLMExplorer step generation."""
        self._bootstrap()
        return {k: v.description for k, v in self._agents.items()}

    def agent_types(self) -> list[str]:
        self._bootstrap()
        return list(self._agents.keys())


# Module-level singleton
_registry = BrocaAgentRegistry()


def get_registry() -> BrocaAgentRegistry:
    return _registry


_UNSET = object()
_etass_registered_for: Any = _UNSET


def register_etass_agents(runtime=None) -> list[str]:
    """Register all ETASS + DDD agents into the Broca registry.

    Idempotent per runtime: CognitiveRuntime.boot() (via init_sittingface)
    and CodeGenRuntime.boot() both call this with the same `wolverine`
    Runtime instance — without this guard every agent got reconstructed
    and re-registered a second time on every boot (double LLM-provider
    init, double log noise, and a DDD PolicyAgent silently clobbering the
    "policy" registry slot that init_broca() had already populated).
    Passing a different runtime (e.g. in tests) re-registers as expected.
    """
    global _etass_registered_for
    if runtime is _etass_registered_for:
        registry = get_registry()
        return [a.agent_type for a in list(registry._agents.values())]
    _etass_registered_for = runtime

    from broca.agents import (
        CingulateAgent,
        MotorCortexAgent,
        SourceControlAgent,
        PullRequestAgent,
        HumanReviewAgent,
        UnitTestingAgent,
        StaticAnalysisAgent,
        SecurityAgent,
        IntrospectionAgent,
        OperationalEvidenceAgent,
        ChartEvolutionAgent,
        ClientCharterAgent,
        PromptCompilerAgent,
        NANDAAgent,
        PlannerAgent,
        ExecutorAgent,
        ApiChartAgent,
        ServiceGenAgent,
        ServeAgent,
        ClientGenAgent,
        CompileAgentAgent,
        TestServiceAgent,
        FixItAgent,
        SeedAgent,
        AdversarialAgent,
        RequirementsAgent,
        ArchitectureDecisionAgent,
        CodeReviewAgent,
        TestGenerationAgent,
        IntegrationTestAgent,
        ArtifactPackagingAgent,
        DeploymentAgent,
        ReleaseAgent,
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
        SpecificationDiscoveryAgent,
        GraphGeneratorAgent,
        AutoAgentGenerator,
        PolicyUpdateAgent,
    )
    from broca.agents.ddd import (
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

    # Compliance agents — real classes with the exact agent_type strings
    # build_sdlc_graph()'s Implementation-stage compliance fan-out expects
    # (compliance_soc2/compliance_gdpr/compliance_iso27001/...), but nothing
    # imported this package during normal agentos boot — only the standalone
    # `monkeypatched make comply` CLI touched it via a separate dynamic
    # loader — so __init_subclass__'s auto-register never fired for them,
    # and every SDLC run's Implementation stage failed with "no Broca agent
    # registered for capability 'compliance_soc2'".

    etass_agents = [
        PromptCompilerAgent(runtime=runtime),  # canonical ETASS — compiles all prompts
        NANDAAgent(runtime=runtime),  # NANDA provider — discover + route remote agents
        CingulateAgent(runtime=runtime),
        MotorCortexAgent(runtime=runtime),
        SourceControlAgent(runtime=runtime),
        PullRequestAgent(runtime=runtime),
        HumanReviewAgent(runtime=runtime),
        UnitTestingAgent(runtime=runtime),
        StaticAnalysisAgent(runtime=runtime),
        SecurityAgent(runtime=runtime),
        IntrospectionAgent(runtime=runtime),
        OperationalEvidenceAgent(runtime=runtime),
        ChartEvolutionAgent(runtime=runtime),
        ClientCharterAgent(runtime=runtime),
        PlannerAgent(runtime=runtime),
        ExecutorAgent(runtime=runtime),
        ApiChartAgent(runtime=runtime),
        ServiceGenAgent(runtime=runtime),
        ServeAgent(runtime=runtime),
        ClientGenAgent(runtime=runtime),
        CompileAgentAgent(runtime=runtime),
        TestServiceAgent(runtime=runtime),
        FixItAgent(runtime=runtime),
        SeedAgent(runtime=runtime),
        AdversarialAgent(runtime=runtime),
        RequirementsAgent(runtime=runtime),
        ArchitectureDecisionAgent(runtime=runtime),
        CodeReviewAgent(runtime=runtime),
        TestGenerationAgent(runtime=runtime),
        IntegrationTestAgent(runtime=runtime),
        ArtifactPackagingAgent(runtime=runtime),
        DeploymentAgent(runtime=runtime),
        ReleaseAgent(runtime=runtime),
        JenkinsAgent(runtime=runtime),
        GitHubActionsAgent(runtime=runtime),
        GitLabCIAgent(runtime=runtime),
        AzurePipelinesAgent(runtime=runtime),
        CircleCIAgent(runtime=runtime),
        TravisCIAgent(runtime=runtime),
        TeamCityAgent(runtime=runtime),
        BambooAgent(runtime=runtime),
        BuildkiteAgent(runtime=runtime),
        DroneCIAgent(runtime=runtime),
        SpecificationDiscoveryAgent(runtime=runtime),
        GraphGeneratorAgent(runtime=runtime),
        AutoAgentGenerator(runtime=runtime),
        PolicyUpdateAgent(runtime=runtime),
    ]

    ddd_agents = [
        DomainAgent(runtime=runtime),
        BoundedContextAgent(runtime=runtime),
        AggregateAgent(runtime=runtime),
        EntityAgent(runtime=runtime),
        ValueObjectAgent(runtime=runtime),
        RepositoryAgent(runtime=runtime),
        PolicyAgent(runtime=runtime),
        FactoryAgent(runtime=runtime),
        EventAgent(runtime=runtime),
    ]

    # ── Domain-specific agents (auto-imported) ────────────────────────────────
    from broca.agents.domains import ALL_DOMAIN_AGENTS

    domain_agents = [cls(runtime=runtime) for cls in ALL_DOMAIN_AGENTS]

    registry = get_registry()
    registry._runtime = runtime  # Store so agents can lazy-discover capabilities without app coupling
    for agent in etass_agents + ddd_agents + domain_agents:
        registry.register(agent)

    logger.info(
        "[broca] registered %d ETASS + %d DDD + %d Domain agents",
        len(etass_agents),
        len(ddd_agents),
        len(domain_agents),
    )
    return [a.agent_type for a in etass_agents + ddd_agents + domain_agents]
