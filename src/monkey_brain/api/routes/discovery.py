"""Discovery API — providers, capabilities, agents, models.

GET /providers    — available providers
GET /capabilities — available capabilities
GET /agents       — available agents
GET /models       — available models
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    ProviderResponse,
    CapabilityResponse,
    AgentResponse,
    ModelResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.discovery")
router = APIRouter()


@router.get("/providers", response_model=ProviderResponse, tags=["Discovery"])
async def get_providers(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-discovery")),
) -> ProviderResponse:
    registry = getattr(request.app.state, "_provider_registry", None)
    if registry is not None and hasattr(registry, "list_providers"):
        providers = registry.list_providers()
    else:
        providers = []
    return ProviderResponse(providers=providers, count=len(providers))


@router.post("/providers/{name}/health", tags=["Discovery"])
@idempotent("discovery.check_provider_health")
async def check_provider_health(
    name: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict:
    """Real "Test health" admin action (ProvidersPanel's own contract
    audit named this as the one action worth adding): actually reaches
    the provider — GET its real URL, or for a CLI-backed provider like
    OpenClaw with no URL, check the CLI is on PATH — and records the
    result. Never returns a fabricated status for a provider that isn't
    registered."""
    from fastapi import HTTPException

    registry = getattr(request.app.state, "_provider_registry", None)
    if registry is None or not hasattr(registry, "check_provider_health"):
        raise HTTPException(status_code=503, detail="ProviderRegistry not available")
    result = await registry.check_provider_health(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no such provider {name!r}")
    return result


@router.get("/capabilities", response_model=CapabilityResponse, tags=["Discovery"])
async def get_capabilities(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-discovery")),
) -> CapabilityResponse:
    capabilities = []
    cr = getattr(request.app.state, "cognitive_runtime", None)
    bus = None
    if cr is not None:
        bus = getattr(cr, "capability_bus", None) or getattr(cr, "_bus", None)
    if bus is not None and hasattr(bus, "list_capabilities"):
        for c in bus.list_capabilities():
            capabilities.append({"name": getattr(c, "name", str(c))})
    else:
        try:
            from broca.registry import get_registry

            registry = get_registry()
            for agent_type in registry.list_agents():
                capabilities.append({"name": agent_type})
        except Exception:
            logger.debug(
                "broca registry unavailable, no fallback capabilities listed",
                exc_info=True,
            )
    return CapabilityResponse(capabilities=capabilities, count=len(capabilities))


@router.get("/agents", response_model=AgentResponse, tags=["Discovery"])
async def get_agents(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-discovery")),
) -> AgentResponse:
    return AgentResponse()


@router.get("/models", response_model=ModelResponse, tags=["Discovery"])
async def get_models(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-discovery")),
) -> ModelResponse:
    models = []

    from src.monkey_brain.kernel.execute.provider.model_backend import (
        _DEFAULT_MODEL_MAP,
        _DEFAULT_PROVIDER,
    )

    for provider, default_model in _DEFAULT_MODEL_MAP.items():
        models.append(
            {
                "name": default_model,
                "provider": provider,
                "default": provider == _DEFAULT_PROVIDER,
            }
        )

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    name = m.get("name", "")
                    if not any(md["name"] == name for md in models):
                        models.append({"name": name, "provider": "ollama", "default": False})
    except Exception:
        logger.debug("Ollama model discovery at %s unavailable", ollama_url, exc_info=True)

    return ModelResponse(models=models, count=len(models))
