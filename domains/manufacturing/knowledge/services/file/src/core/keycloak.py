import os
import time
from dotenv import load_dotenv
import requests
import redis
from typing import Dict

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

load_dotenv()

# =========================================================================
# SECURITY: Fail-Closed Keycloak Configuration
# =========================================================================
# Keycloak secrets are REQUIRED when Keycloak authentication is enabled.
# The service fails to start if they are missing, rather than degrading to
# insecure fallbacks or allowing bypasses.
#
# These environment variables MUST be set via deployment mechanisms:
#   - Docker: docker run -e KEYCLOAK_ISSUER=... -e KC_CLIENT_ID=... ...
#   - K8s: Deployment.spec.containers[].env with valueFrom.secretKeyRef
#   - Local dev: .env file (never committed)


def _require_keycloak_config(var_name: str) -> str:
    """
    Load a required Keycloak configuration variable.

    Fails closed: raises RuntimeError if the variable is missing or empty.
    This forces deployment teams to explicitly provide the value.
    """
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise RuntimeError(
            f"Keycloak configuration error: {var_name} is not set.\n\n"
            f"This is a required security-critical configuration variable.\n"
            f"It must be provided via deployment environment variables:\n\n"
            f"  Docker:  docker run -e {var_name}='...' ...\n"
            f"  K8s:     Set in Deployment.spec.containers[].env\n"
            f"  Local:   Set {var_name} in .env before running services\n\n"
            f"Never commit .env files containing {var_name} to git."
        )
    return value


# Load Keycloak configuration with fail-closed guarantees
try:
    KEYCLOAK_ISSUER = _require_keycloak_config("KEYCLOAK_ISSUER")
    KEYCLOAK_AUDIENCE = _require_keycloak_config("KEYCLOAK_AUDIENCE")
    KC_CLIENT_ID = _require_keycloak_config("KC_CLIENT_ID")
    KC_CLIENT_SECRET = _require_keycloak_config("KC_CLIENT_SECRET")
except RuntimeError as e:
    # Fail at import time, before any requests can be made
    import sys

    print(f"FATAL: {e}", file=sys.stderr)
    raise

# Use separate URL for JWKS fetching (container-to-container)
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").lower() == "true"

# -------------------------------------------------------------------
# Security
# -------------------------------------------------------------------
security = HTTPBearer(auto_error=True)

# -------------------------------------------------------------------
# Redis (Replay Detection)
# -------------------------------------------------------------------
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379,
    decode_responses=True,
)

# -------------------------------------------------------------------
# JWKS Cache
# -------------------------------------------------------------------
# Use internal URL for fetching JWKS, but keep original issuer for validation
JWKS_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/asset_manager/protocol/openid-connect/certs"
JWKS_CACHE_TTL = 3600  # 1 hour

_jwks_cache = {"keys": None, "expires": 0}


def get_jwks():
    now = time.time()
    if _jwks_cache["keys"] is None or now > _jwks_cache["expires"]:
        resp = requests.get(JWKS_URL, timeout=5, verify=VERIFY_SSL)
        resp.raise_for_status()
        _jwks_cache["keys"] = resp.json()["keys"]
        _jwks_cache["expires"] = now + JWKS_CACHE_TTL
    return _jwks_cache["keys"]


# -------------------------------------------------------------------
# JWT Verification
# -------------------------------------------------------------------
def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict:
    token = credentials.credentials

    try:
        headers = jwt.get_unverified_header(token)
        kid = headers["kid"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid JWT header")

    for key in get_jwks():
        if key["kid"] == kid:
            try:
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    issuer=KEYCLOAK_ISSUER,
                    options={
                        "verify_signature": True,
                        "verify_aud": False,
                        "verify_exp": True,
                        "verify_iss": False,
                    },
                )

                # Enforce client identity explicitly
                if claims.get("azp") != KC_CLIENT_ID:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid authorized party (azp)",
                    )

                return claims

            except JWTError as e:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"JWT validation failed: {str(e)}",
                )

    raise HTTPException(status_code=401, detail="Unknown signing key")


# -------------------------------------------------------------------
# Token Introspection (Revocation)
# -------------------------------------------------------------------
def introspect_token(token: str) -> bool:
    resp = requests.post(
        f"{KEYCLOAK_INTERNAL_URL}/realms/asset_manager/protocol/openid-connect/token/introspect",
        data={"token": token},
        auth=(KC_CLIENT_ID, KC_CLIENT_SECRET),
        timeout=5,
        verify=VERIFY_SSL,
    )
    resp.raise_for_status()
    return resp.json().get("active", False)


def require_active_token(
    claims: Dict = Depends(verify_jwt),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict:
    token = credentials.credentials

    # Enforce revocation only for sensitive scopes
    if "high-risk" in claims.get("scope", ""):
        if not introspect_token(token):
            raise HTTPException(status_code=401, detail="Token revoked")

    return claims


# -------------------------------------------------------------------
# Token Replay Detection
# -------------------------------------------------------------------
def detect_replay(jti: str, ttl: int = 300):
    key = f"jti:{jti}"

    if redis_client.exists(key):
        raise HTTPException(status_code=401, detail="Token replay detected")

    redis_client.setex(key, ttl, "used")


def anti_replay_guard(
    claims: Dict = Depends(require_active_token),
) -> Dict:
    jti = claims.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Missing jti in token")

    detect_replay(jti)
    return claims
