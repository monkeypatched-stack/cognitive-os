"""Shared HTTP call to a dedicated actor Pod's own POST /prompt
(actor_runtime.py) -- the one real entry point for "send this actor a
fresh natural-language mission" when that actor's cognition genuinely
lives in its own robot-class Pod (drone-a, drone-b, ...), not in this
shared control-plane process.

Extracted out of api/routes/prompt.py's own _try_forward_to_actor_pod so
voice_command_runtime.py and api/routes/actors.py's own POST
/actors/{id}/prompt can call the exact same code path a human's cloud
/prompt call would forward through, rather than each hand-rolling their
own copy of the request/auth shape.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("agentos.edge.actor_prompt_forwarder")

# A real planning+execution cycle (LLM plan -> governance -> PX4 flight)
# can legitimately run well past a short request timeout -- confirmed live
# a real multi-step mission (Arm/Takeoff/Waypoint/Land) took ~196s
# end-to-end (climb, fly the distance, and PX4 arrival confirmation
# between each governed step all cost real wall-clock time). Raised again
# (300s -> 450s) once a plan reliably started including the Arm step it
# was previously missing (kernel/domains/robot.py's ArmCapability.
# description fix) -- confirmed live that a real Arm+Takeoff+2xWaypoint+
# Land mission now legitimately exceeds 300s, and this timeout must stay
# comfortably ABOVE cognitive_actor.py's own belief_formation timeout
# (420s) it wraps, same "inner < outer" principle used throughout this
# call chain -- see that call site's own comment for the full chain.
DEFAULT_TIMEOUT_SEC = 450.0


class ActorPromptForwardError(RuntimeError):
    """Raised when the actor Pod's own /prompt could not be reached or
    returned a non-2xx response -- always a real failure to surface, never
    swallowed into a fake success the way a missing INTERNAL_SERVICE_TOKEN
    silently degrading to "not forwarded" would be for the cloud /prompt
    route's own best-effort fallback path."""


async def forward_prompt_to_actor_pod(
    actor_id: str, question: str, *, timeout: float = DEFAULT_TIMEOUT_SEC
) -> dict[str, Any]:
    """POST {"question": question} to http://cognitiveos-actor-{actor_id}:
    8051/prompt with the X-Internal-Service-Token header actor_runtime.py's
    own require_internal_service_token() checks. Raises
    ActorPromptForwardError on any failure -- callers that want a
    best-effort "try this, fall back to something else" shape (like
    api/routes/prompt.py's own proactive/reactive forward) catch this
    themselves rather than getting a silent None back."""
    token = os.environ.get("INTERNAL_SERVICE_TOKEN", "")
    if not token:
        raise ActorPromptForwardError("INTERNAL_SERVICE_TOKEN not set — cannot forward to actor Pod")

    import httpx

    url = f"http://cognitiveos-actor-{actor_id}:8051/prompt"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "X-Internal-Service-Token": token,
                    "Content-Type": "application/json",
                },
                json={"question": question},
            )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise ActorPromptForwardError(f"forwarding to {url} failed: {exc}") from exc
