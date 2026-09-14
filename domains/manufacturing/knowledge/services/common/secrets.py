"""
Unified secrets management with fail-closed guarantees.

This module provides a centralized, auditable way to load and validate
security-sensitive configuration. All sensitive values are:

1. Explicitly declared (no implicit defaults)
2. Validated at startup (fail before service runs)
3. Checked before use (defense in depth)
4. Never logged or displayed (no accidental leaks)
5. Sourced from explicit deployment mechanisms (env vars, secrets managers)

DO NOT load secrets from .env files (which are dev-only and easily committed).
In production, use:
  - Environment variables (Docker/K8s)
  - Secret managers (Vault, AWS Secrets Manager, etc.)
  - Secret injection (init containers, sidecar agents)

Design: Fail-closed means the service refuses to start if a required secret
is missing. This is intentional — it prevents running with incomplete
credentials, which could silently fail in unexpected ways or degrade to
insecure fallbacks.
"""

import os
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Secret Classification
# ============================================================================


class SecretClassification:
    """Metadata about a secret: where it comes from, what it guards, etc."""

    def __init__(
        self,
        name: str,
        purpose: str,
        required: bool = True,
        env_var: str = "",
        validation_fn=None,
    ):
        """
        Args:
            name: Human-readable name (e.g., "ACCESS_TOKEN_SECRET")
            purpose: What this secret guards (e.g., "JWT signing for access tokens")
            required: If True, service fails to start if missing
            env_var: Environment variable name (defaults to 'name' if not set)
            validation_fn: Optional callable(value: str) -> str that validates
                          the secret. Raises ValueError if invalid.
        """
        self.name = name
        self.purpose = purpose
        self.required = required
        self.env_var = env_var or name
        self.validation_fn = validation_fn


# ============================================================================
# Catalog of Security-Sensitive Secrets
# ============================================================================
# This is the "source of truth" for what secrets a service needs.
# Add new secrets here when a service needs them.

AUTHENTICATION_SECRETS = [
    SecretClassification(
        name="ACCESS_TOKEN_SECRET",
        purpose="HMAC secret for signing JWT access tokens (auth service)",
        required=True,
        env_var="ACCESS_TOKEN_SECRET",
        validation_fn=lambda v: _validate_secret_length(v, min_bytes=32),
    ),
    SecretClassification(
        name="REFRESH_TOKEN_SECRET",
        purpose="HMAC secret for signing JWT refresh tokens (auth service)",
        required=True,
        env_var="REFRESH_TOKEN_SECRET",
        validation_fn=lambda v: _validate_secret_length(v, min_bytes=32),
    ),
]

KEYCLOAK_SECRETS = [
    SecretClassification(
        name="KEYCLOAK_ISSUER",
        purpose="Keycloak identity provider issuer URL (file service auth)",
        required=False,  # Optional if not using Keycloak
        env_var="KEYCLOAK_ISSUER",
    ),
    SecretClassification(
        name="KEYCLOAK_AUDIENCE",
        purpose="Expected audience claim in Keycloak tokens",
        required=False,
        env_var="KEYCLOAK_AUDIENCE",
    ),
    SecretClassification(
        name="KC_CLIENT_ID",
        purpose="Keycloak client ID for service-to-service authentication",
        required=False,
        env_var="KC_CLIENT_ID",
    ),
    SecretClassification(
        name="KC_CLIENT_SECRET",
        purpose="Keycloak client secret (DO NOT commit or log)",
        required=False,
        env_var="KC_CLIENT_SECRET",
        validation_fn=lambda v: _validate_secret_not_empty(v),
    ),
]

OPTIONAL_SERVICE_SECRETS = [
    SecretClassification(
        name="OPENAI_API_KEY",
        purpose="OpenAI API key for LLM operations",
        required=False,
        env_var="OPENAI_API_KEY",
    ),
    SecretClassification(
        name="DEEPGRAM_API_KEY",
        purpose="Deepgram API key for speech-to-text",
        required=False,
        env_var="DEEPGRAM_API_KEY",
    ),
    SecretClassification(
        name="N8N_WEBHOOK_SECRET",
        purpose="n8n webhook authentication secret",
        required=False,
        env_var="N8N_WEBHOOK_SECRET",
    ),
    SecretClassification(
        name="DATABRICKS_TOKEN",
        purpose="Databricks API token",
        required=False,
        env_var="DATABRICKS_TOKEN",
    ),
    SecretClassification(
        name="MODULE_CONTROL_INTERNAL_SECRET",
        purpose="Module control service internal authentication",
        required=False,
        env_var="MODULE_CONTROL_INTERNAL_SECRET",
    ),
]

# Full catalog by service
SECRET_CATALOG = {
    "auth": AUTHENTICATION_SECRETS + KEYCLOAK_SECRETS,
    "file": KEYCLOAK_SECRETS,
    "agentos": AUTHENTICATION_SECRETS + OPTIONAL_SERVICE_SECRETS,
    "all_services": AUTHENTICATION_SECRETS
    + KEYCLOAK_SECRETS
    + OPTIONAL_SERVICE_SECRETS,
}


# ============================================================================
# Validation Functions
# ============================================================================


def _validate_secret_not_empty(value: str) -> str:
    """Fail closed: reject empty secrets."""
    if not value or not value.strip():
        raise ValueError("Secret must not be empty")
    return value.strip()


# Exact (case-insensitive) values that must never be used as HMAC/JWT secrets.
# Includes historical compose/CI/Helm placeholders so a forgotten override cannot boot.
KNOWN_INSECURE_HMAC_SECRETS = frozenset(
    {
        "replace_me",
        "changeme",
        "change-me",
        "change_me",
        "password",
        "secret",
        "dev-access-token-secret",
        "dev-refresh-token-secret",
        "dev-access-secret",
        "test-access-token-secret",
        "test-refresh-token-secret",
        "test-access-secret",
        "ci-test-access-token-secret",
        "ci-test-refresh-token-secret",
        "my-shared-secret",
        "access-secret-123",
        "refresh-secret-456",
        "change-me-internal-service-token",
    }
)


def reject_insecure_hmac_secret(value: str, *, name: str = "HMAC secret") -> str:
    """Fail closed: reject empty, short, or well-known placeholder HMAC secrets.

    Never logs `value`. Does not generate a replacement secret.
    """
    _validate_secret_not_empty(value)
    stripped = value.strip()
    if stripped.lower() in KNOWN_INSECURE_HMAC_SECRETS:
        raise ValueError(
            f"{name} is a known insecure placeholder/default and cannot be used. "
            "Supply a unique secret via the environment or a secrets manager "
            "(e.g. openssl rand -hex 32)."
        )
    if len(stripped) < 32:
        raise ValueError(
            f"{name} must be at least 32 characters "
            f"(received {len(stripped)} chars). "
            "Use a randomly generated secret (e.g., 'openssl rand -hex 32')."
        )
    return stripped


def validate_hmac_secrets_from_env() -> None:
    """Boot-time check: ACCESS_TOKEN_SECRET and REFRESH_TOKEN_SECRET must be set and non-placeholder."""
    missing: list[str] = []
    invalid: list[str] = []
    for env_name in ("ACCESS_TOKEN_SECRET", "REFRESH_TOKEN_SECRET"):
        raw = os.environ.get(env_name)
        if not raw or not str(raw).strip():
            missing.append(env_name)
            continue
        try:
            reject_insecure_hmac_secret(str(raw), name=env_name)
        except ValueError:
            invalid.append(env_name)
    if missing or invalid:
        parts: list[str] = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if invalid:
            parts.append("insecure placeholder/default: " + ", ".join(invalid))
        raise RuntimeError(
            "HMAC/JWT secrets failed validation (" + "; ".join(parts) + "). "
            "Set unique non-placeholder values before starting the process."
        )


def _validate_secret_length(value: str, min_bytes: int = 16) -> str:
    """Fail closed: reject weak secrets (too short) and known HMAC placeholders."""
    if min_bytes >= 32:
        return reject_insecure_hmac_secret(value)
    _validate_secret_not_empty(value)
    value = value.strip()
    if value.lower() in KNOWN_INSECURE_HMAC_SECRETS:
        raise ValueError(
            "Secret is a known insecure placeholder/default and cannot be used."
        )
    if len(value) < min_bytes:
        raise ValueError(
            f"Secret must be at least {min_bytes} characters "
            f"(received {len(value)} chars). "
            f"Use a randomly generated secret (e.g., 'openssl rand -hex 32')."
        )
    return value


# ============================================================================
# Runtime Secret Loading and Validation
# ============================================================================


class SecretLoadError(RuntimeError):
    """Raised when a required secret cannot be loaded or is invalid."""

    pass


def load_secret(
    secret: SecretClassification,
    raise_if_missing: bool = True,
) -> Optional[str]:
    """
    Load and validate a single secret from environment.

    Args:
        secret: SecretClassification object defining what to load
        raise_if_missing: If True and secret is required but missing,
                         raise SecretLoadError. If False, return None.

    Returns:
        The secret value (validated), or None if not required and not found.

    Raises:
        SecretLoadError: If secret is required but missing/invalid.
        ValueError: If secret validation fails (via validation_fn).
    """
    value = os.environ.get(secret.env_var)

    if not value:
        if secret.required and raise_if_missing:
            raise SecretLoadError(
                f"Required secret '{secret.name}' not found in environment. "
                f"Environment variable: {secret.env_var}\n"
                f"Purpose: {secret.purpose}\n"
                f"Fix: Set {secret.env_var} via deployment mechanism "
                f"(Docker, K8s, secrets manager)."
            )
        return None

    # Validate the secret
    if secret.validation_fn:
        try:
            value = secret.validation_fn(value)
        except ValueError as e:
            raise SecretLoadError(
                f"Secret '{secret.name}' failed validation: {e}\n"
                f"Environment variable: {secret.env_var}"
            )

    return value


def load_secrets(
    service_name: str,
    catalog: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """
    Load all secrets for a service at startup.

    Args:
        service_name: Key in SECRET_CATALOG (e.g., "auth", "file", "agentos")
        catalog: Optional custom catalog (defaults to SECRET_CATALOG)

    Returns:
        Dict mapping secret names to values (required secrets present,
        optional secrets may be None).

    Raises:
        SecretLoadError: If any required secret is missing or invalid.
    """
    if catalog is None:
        catalog = SECRET_CATALOG

    secrets_to_load = catalog.get(service_name, [])
    if not secrets_to_load:
        return {}

    loaded_secrets: Dict[str, Optional[str]] = {}
    errors = []

    for secret in secrets_to_load:
        try:
            value = load_secret(secret, raise_if_missing=secret.required)
            if value is not None:
                loaded_secrets[secret.name] = value
        except SecretLoadError as e:
            if secret.required:
                errors.append(str(e))

    if errors:
        raise SecretLoadError(
            f"Service '{service_name}' startup failed due to missing or invalid secrets:\n\n"
            + "\n\n".join(errors)
        )

    return loaded_secrets


def validate_secrets_at_startup(service_name: str) -> None:
    """
    Validate all required secrets for a service at startup.

    This should be called in __init__.py or main.py before the service
    does any work. If it returns without raising, all required secrets
    are present and valid.

    Args:
        service_name: Key in SECRET_CATALOG

    Raises:
        SecretLoadError: If any required secret is missing or invalid.
    """
    loaded = load_secrets(service_name)
    logger.info(f"✓ Service '{service_name}' secrets validated at startup")


# ============================================================================
# Audit Logging (for compliance)
# ============================================================================


def log_secret_access(secret_name: str, operation: str, status: str) -> None:
    """
    Audit log when secrets are accessed (for compliance/forensics).

    Never logs the actual secret value.

    Args:
        secret_name: Name of the secret (e.g., "ACCESS_TOKEN_SECRET")
        operation: What was done (e.g., "jwt_sign", "jwt_verify")
        status: Result (e.g., "success", "failure:expired")
    """
    logger.info(
        f"AUDIT: Secret '{secret_name}' accessed; "
        f"operation='{operation}'; status='{status}'"
    )


# ============================================================================
# Configuration Documentation
# ============================================================================


def print_secrets_reference(service_name: str = "all_services") -> str:
    """
    Generate reference documentation of all secrets a service needs.

    Useful for deployment teams to understand what environment variables
    must be set.

    Args:
        service_name: Service name or "all_services" for full reference

    Returns:
        Formatted documentation string
    """
    secrets_to_doc = SECRET_CATALOG.get(service_name, [])
    if not secrets_to_doc:
        return f"No secrets defined for service '{service_name}'"

    lines = [
        f"\n{'=' * 70}",
        f"DEPLOYMENT SECRETS REFERENCE: {service_name}",
        f"{'=' * 70}\n",
    ]

    required = [s for s in secrets_to_doc if s.required]
    optional = [s for s in secrets_to_doc if not s.required]

    if required:
        lines.append("REQUIRED (service fails to start if missing):")
        lines.append("-" * 70)
        for secret in required:
            lines.append(f"\n  {secret.env_var}")
            lines.append(f"    Purpose: {secret.purpose}")
            lines.append(
                f"    Category: {'Security-critical' if 'SECRET' in secret.env_var else 'Configuration'}"
            )
        lines.append("")

    if optional:
        lines.append(
            "\nOPTIONAL (service runs without these, may have reduced functionality):"
        )
        lines.append("-" * 70)
        for secret in optional:
            lines.append(f"\n  {secret.env_var}")
            lines.append(f"    Purpose: {secret.purpose}")
        lines.append("")

    lines.append(f"{'=' * 70}\n")
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick reference when running this module directly
    import sys

    service = sys.argv[1] if len(sys.argv) > 1 else "all_services"
    print(print_secrets_reference(service))
