"""Cerebellum Provider Bootstrap — load all external capability providers on init.

Called once at startup. Registers every provider capability into the runtime's
capability registry so agents can discover them via _find_capability(name).

Providers loaded:
  nanda       — NANDA decentralized agent registry (discover + route)
  openclaw    — OpenClaw agent-to-agent execution
  n8n         — n8n workflow automation (SAP, email, webhooks, Slack)
  model       — ModelBackend (Claude/GPT/Gemini/Qwen) — registered as "llm"

All providers are registered regardless of whether their env vars are set.
Unavailable providers return {"status": "unavailable"} at execute time.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("cerebellum.providers")


def load_all_providers(runtime) -> list[str]:
    """Register all external capability providers into the runtime.

    Returns list of provider names successfully registered.
    """
    providers = []

    # NANDA — decentralized agent DNS (discover + route)
    try:
        from cerebellum.capabilities.agent.nanda import NANDACapability

        runtime.register(NANDACapability())
        providers.append("nanda")
        logger.info(
            "[providers] nanda registered (url=%s)",
            os.environ.get("NANDA_REGISTRY_URL", "not set"),
        )
    except Exception as exc:
        logger.warning("[providers] nanda skipped: %s", exc)

    # OpenClaw — agent-to-agent execution (CLI mode if no URL, HTTP mode if URL set)
    try:
        from cerebellum.capabilities.agent.agents import OpenClawCapability

        api_url = os.environ.get("OPENCLAW_API_URL", "")
        runtime.register(OpenClawCapability(api_url=api_url))
        providers.append("openclaw")
        mode = "http" if api_url else "cli"
        logger.info(
            "[providers] openclaw registered (mode=%s url=%s)",
            mode,
            api_url or "local-gateway",
        )
    except Exception as exc:
        logger.warning("[providers] openclaw skipped: %s", exc)

    # n8n — workflow automation (SAP connector, email, webhooks, Slack)
    try:
        from cerebellum.capabilities.workflow.workflows import N8nCapability

        n8n_base = os.environ.get("N8N_BASE_URL", "")
        n8n_webhook = os.environ.get("N8N_WEBHOOK_URL", f"{n8n_base}/webhook" if n8n_base else "")
        runtime.register(N8nCapability(webhook_url=n8n_webhook))
        providers.append("n8n")
        logger.info("[providers] n8n registered (url=%s)", n8n_webhook or "not set")
    except Exception as exc:
        logger.warning("[providers] n8n skipped: %s", exc)

    # ModelBackend — LLM provider (Claude/GPT/Gemini/Qwen)
    try:
        from cerebellum.capabilities.ai.providers import AnthropicCapability

        cap = AnthropicCapability()
        runtime.register(cap)
        # register() keys by capability.name ("anthropic") — this used to
        # append/log "llm_claude" instead, a label nothing in the registry
        # actually used (no caller looks up either name today; agents call
        # ModelBackend directly), but misleading anyone reading this list.
        providers.append(cap.name)
        logger.info(
            "[providers] %s registered (key=%s)",
            cap.name,
            "set" if cap._api_key else "not set",
        )
    except Exception as exc:
        logger.warning("[providers] anthropic skipped: %s", exc)

    # GitHub — repo management, issues, PRs via REST API v3
    try:
        from cerebellum.capabilities.source_control.source_control import (
            GitHubCapability,
        )

        github_token = os.environ.get("GITHUB_TOKEN", "")
        runtime.register(GitHubCapability(token=github_token))
        providers.append("github")
        mode = "authenticated" if github_token else "unauthenticated (read-only, rate-limited)"
        logger.info("[providers] github registered (%s)", mode)
    except Exception as exc:
        logger.warning("[providers] github skipped: %s", exc)

    # External agent discovery + remote execution
    try:
        from cerebellum.capabilities.agent.agents import (
            ExternalAgentCapability,
            AgentDiscoveryCapability,
        )

        runtime.register(ExternalAgentCapability())
        runtime.register(AgentDiscoveryCapability())
        providers.extend(["external_agent", "agent_discovery"])
    except Exception as exc:
        logger.warning("[providers] external agent capabilities skipped: %s", exc)

    # SittingFace codegen — SomaticCompiler + CodeGenAgent as a capability, so
    # MotorCortexAgent._find_capability("sittingface_codegen") can actually
    # find it instead of always falling through to its inline duplicate.
    try:
        from cerebellum.capabilities.etass.codegen import SittingFaceCodegenCapability

        runtime.register(SittingFaceCodegenCapability())
        providers.append("sittingface_codegen")
        logger.info("[providers] sittingface_codegen registered")
    except Exception as exc:
        logger.warning("[providers] sittingface_codegen skipped: %s", exc)

    # LLM governance / pytest / adversarial-falsification — same shape as
    # sittingface_codegen above: real, working ICapability implementations
    # whose only callers (CingulateAgent, UnitTestingAgent, AdversarialAgent)
    # always got None because nothing registered them. Each caller has a
    # fully-equivalent inline fallback, so this is a correctness/consistency
    # fix (the capability abstraction is actually reachable now), not a fix
    # for a feature that was broken end-to-end.
    try:
        from cerebellum.capabilities.etass.governance import LLMGovernanceCapability

        runtime.register(LLMGovernanceCapability())
        providers.append("llm_governance")
        logger.info("[providers] llm_governance registered")
    except Exception as exc:
        logger.warning("[providers] llm_governance skipped: %s", exc)

    try:
        from cerebellum.capabilities.etass.testing import PytestCapability

        runtime.register(PytestCapability())
        providers.append("pytest")
        logger.info("[providers] pytest registered")
    except Exception as exc:
        logger.warning("[providers] pytest skipped: %s", exc)

    try:
        from cerebellum.capabilities.etass.adversarial import (
            AdversarialFalsificationCapability,
        )

        runtime.register(AdversarialFalsificationCapability())
        providers.append("adversarial_falsification")
        logger.info("[providers] adversarial_falsification registered")
    except Exception as exc:
        logger.warning("[providers] adversarial_falsification skipped: %s", exc)

    logger.info("[providers] loaded %d providers: %s", len(providers), providers)
    return providers
