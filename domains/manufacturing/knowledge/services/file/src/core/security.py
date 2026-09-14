from fastapi import Depends, Header, HTTPException, status, Request

from .keycloak import anti_replay_guard
from .keycloak import verify_jwt


async def require_mtls(request: Request):
    print("x-ssl-client-verify =", request.headers.get("x-ssl-client-verify"))
    print("x-ssl-client-dn =", request.headers.get("x-ssl-client-dn"))

    if request.headers.get("x-ssl-client-verify") != "SUCCESS":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="mTLS required",
        )


async def require_auth_context(
    request: Request,
    claims: dict = Depends(anti_replay_guard),  # JWT + introspection + replay
):
    """
    Unified security layer.

    - Via Kong  → JWT only
    - Direct    → mTLS + JWT
    """

    via_kong = request.headers.get("x-kong-request-id") is not None

    if not via_kong:
        await require_mtls(request)

    return claims
