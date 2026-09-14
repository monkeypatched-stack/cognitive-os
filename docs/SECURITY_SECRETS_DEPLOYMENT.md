# Security: Fail-Closed Secrets and Deployment Configuration

## Overview

This document describes how security-sensitive configuration (secrets, credentials, keys) is managed in this system. The design enforces **fail-closed** behavior: services refuse to start if required secrets are missing, rather than degrading to insecure fallbacks or allowing empty credentials.

**Key principle:** A missing secret is a deployment problem, not a runtime problem to be recovered from. It forces explicit, auditable provisioning rather than silent failures.

---

## Security Model: Fail-Closed

### What "Fail-Closed" Means

- **Required secrets MUST be explicitly provided** via deployment mechanisms
- **Service refuses to start** if a required secret is missing or invalid
- **No defaults, no fallbacks** that could mask deployment issues
- **Validation happens at startup**, not at first use
- **Clear error messages** guide deployment teams to fix the problem

### Why This Matters

**Alternative (bad):** Service loads with empty or default credentials
```python
# ❌ WRONG
SECRET_KEY = os.getenv("SECRET_KEY", "default-key")  # Falls back to weak default
TOKEN_SECRET = os.getenv("TOKEN_SECRET", "")  # Falls back to empty
```

Problems:
- Silent failure: service runs but is insecure
- Debugging nightmare: errors appear at first JWT sign/verify, not at startup
- Easy to forget: developers forget to set the variable and it works "locally"
- Production risk: default/empty secrets could reach production

**Correct (fail-closed):**
```python
# ✅ CORRECT
def _require_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required but not set")
    return value


SECRET_KEY = _require_secret("SECRET_KEY")
```

Benefits:
- Explicit: deployment teams know exactly what needs to be set
- Fast failure: errors at startup, not after minutes of operation
- Auditable: every service start logs what was validated
- Production-ready: same code path in dev and production

---

## Secrets Architecture

### Classification

Secrets are classified by sensitivity and purpose:

#### 1. **Security-Critical Secrets** (REQUIRED, fail-closed)

These guard authentication and authorization. Service **refuses to start** if missing.

| Secret | Purpose | Set via | Min Length |
|--------|---------|---------|------------|
| `ACCESS_TOKEN_SECRET` | HMAC key for JWT access tokens | Environment | 32 chars |
| `REFRESH_TOKEN_SECRET` | HMAC key for JWT refresh tokens | Environment | 32 chars |
| `KC_CLIENT_SECRET` | Keycloak client credential | Environment | N/A |
| `MODULE_CONTROL_INTERNAL_SECRET` | Internal service authentication | Environment | 32 chars |

#### 2. **Keycloak Configuration** (REQUIRED if using Keycloak)

Required only if the service uses Keycloak for authentication (e.g., file service).

| Variable | Purpose | Set via |
|----------|---------|---------|
| `KEYCLOAK_ISSUER` | Keycloak issuer URL | Environment |
| `KEYCLOAK_AUDIENCE` | Expected audience claim | Environment |
| `KC_CLIENT_ID` | Keycloak client ID | Environment |
| `KC_CLIENT_SECRET` | Keycloak client secret | Environment |

#### 3. **Optional Service Secrets**

These enable optional features. Service runs without them but may have reduced functionality.

| Secret | Feature |
|--------|---------|
| `OPENAI_API_KEY` | LLM operations |
| `DEEPGRAM_API_KEY` | Speech-to-text |
| `N8N_WEBHOOK_SECRET` | n8n workflow webhooks |
| `DATABRICKS_TOKEN` | Databricks integration |

---

## How Secrets Are Loaded

### Local Development

For local development **only**, secrets can come from `.env` files:

```bash
# .env or services/auth/.env
ACCESS_TOKEN_SECRET=your-secret-key-here
REFRESH_TOKEN_SECRET=your-refresh-key-here
```

**Important:** Never commit `.env` files with real secrets to git. These files are in `.gitignore`.

### Docker Deployment

Set secrets via Docker environment variables:

```bash
docker run \
  -e ACCESS_TOKEN_SECRET="$(openssl rand -hex 32)" \
  -e REFRESH_TOKEN_SECRET="$(openssl rand -hex 32)" \
  -e KEYCLOAK_ISSUER="https://keycloak.example.com" \
  myservice:latest
```

Or via docker-compose:

```yaml
services:
  auth:
    image: myservice:latest
    environment:
      ACCESS_TOKEN_SECRET: "${ACCESS_TOKEN_SECRET}"
      REFRESH_TOKEN_SECRET: "${REFRESH_TOKEN_SECRET}"
```

Populate from `.env.production` or a secrets file:

```bash
docker-compose -f docker-compose.yml \
  --env-file .env.production \
  up
```

### Kubernetes Deployment

Use Kubernetes Secrets and SecretKeyRef:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: auth-secrets
type: Opaque
stringData:
  ACCESS_TOKEN_SECRET: "$(openssl rand -hex 32)"
  REFRESH_TOKEN_SECRET: "$(openssl rand -hex 32)"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: auth-service
spec:
  containers:
  - name: auth
    image: myservice:latest
    env:
    - name: ACCESS_TOKEN_SECRET
      valueFrom:
        secretKeyRef:
          name: auth-secrets
          key: ACCESS_TOKEN_SECRET
    - name: REFRESH_TOKEN_SECRET
      valueFrom:
        secretKeyRef:
          name: auth-secrets
          key: REFRESH_TOKEN_SECRET
```

### Secret Management Systems (Vault, AWS Secrets Manager, etc.)

For enterprise deployments, integrate with a secrets manager:

```python
# Example: Load from AWS Secrets Manager
import boto3

secrets_client = boto3.client("secretsmanager", region_name="us-east-1")
response = secrets_client.get_secret_value(SecretId="prod/auth-secrets")
secret_dict = json.loads(response["SecretString"])

ACCESS_TOKEN_SECRET = secret_dict["ACCESS_TOKEN_SECRET"]
REFRESH_TOKEN_SECRET = secret_dict["REFRESH_TOKEN_SECRET"]
```

Or use init containers to mount secrets:

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  initContainers:
  - name: load-secrets
    image: vault-init:latest
    volumeMounts:
    - name: secrets-volume
      mountPath: /tmp/secrets
    env:
    - name: VAULT_ADDR
      value: "https://vault.example.com"
  containers:
  - name: app
    env:
    - name: ACCESS_TOKEN_SECRET
      valueFrom:
        fieldRef:
          fieldPath: /tmp/secrets/access_token_secret
```

---

## Generating Secure Secrets

### For TOKEN_SECRET (HMAC Keys)

Minimum 32 bytes (256 bits) of entropy:

```bash
# Generate a random 32-byte hex string (64 hex chars)
openssl rand -hex 32

# Example output:
# a7f3d8c5e2b9a1f4c6e8d0b2f9a3c5e7d9b1f3a5c7e9d1b3f5a7c9e0b2d4f6
```

### For KEYCLOAK_CLIENT_SECRET

Keycloak generates these in the admin console. You can also generate manually:

```bash
# Generate a secure random string
openssl rand -base64 32

# Example output:
# k9Fm8xZ3p/Y2qL5mR7bW8vN+X2c9j3D0k6L+M7n8P9q1
```

### For API Keys (OpenAI, etc.)

These are provided by the service:
- OpenAI: Generate at https://platform.openai.com/api-keys
- Deepgram: Generate at https://console.deepgram.com/
- Use those values directly

---

## Deployment Checklist

### Before Deploying to Production

- [ ] All required secrets are generated and stored securely
- [ ] Secrets are NOT in `.env` files committed to git
- [ ] Secrets are NOT in docker images (verified by build-time assertion)
- [ ] Deployment manifest includes all required environment variables
- [ ] Secrets manager (Vault, etc.) is configured if using one
- [ ] Backup/recovery procedure for secrets is documented
- [ ] Audit logging is enabled for secret access
- [ ] Each environment (dev, staging, prod) has its own secrets
- [ ] Secret rotation policy is established

### Pre-Deployment Testing

```bash
# Build the image
docker build -t myservice:latest .

# Verify no secrets in image
python scripts/verify_image_secrets.py myservice:latest

# Generate test secrets
TEST_ACCESS_SECRET=$(openssl rand -hex 32)
TEST_REFRESH_SECRET=$(openssl rand -hex 32)

# Test with required secrets
docker run \
  -e ACCESS_TOKEN_SECRET="$TEST_ACCESS_SECRET" \
  -e REFRESH_TOKEN_SECRET="$TEST_REFRESH_SECRET" \
  myservice:latest

# Test startup (should pass health checks)
# Test without required secrets (should fail)
docker run myservice:latest 2>&1 | grep -i "required"  # Should error
```

---

## Troubleshooting

### "RuntimeError: ACCESS_TOKEN_SECRET is not configured"

**Cause:** The service tried to start without the required environment variable.

**Fix:**
1. Verify the environment variable is set:
   ```bash
   echo $ACCESS_TOKEN_SECRET
   ```
2. If empty, generate and set it:
   ```bash
   export ACCESS_TOKEN_SECRET=$(openssl rand -hex 32)
   docker run -e ACCESS_TOKEN_SECRET=$ACCESS_TOKEN_SECRET myservice:latest
   ```

### "JWTError: Secret is required but was not provided"

**Cause:** Token signing/verification was attempted without a valid secret.

**Fix:**
1. Check that ACCESS_TOKEN_SECRET is set at service startup
2. Look for any code that might be calling token functions before config is loaded
3. Ensure no other code is overriding the secret with an empty value

### "ValueError: SECRET_NAME must be set to a non-empty value"

**Cause:** The environment variable is set but empty (or whitespace-only).

**Fix:**
1. Verify the value: `echo "$VARIABLE" | od -c` (should not be empty)
2. Check for typos in the environment variable name
3. If set via docker-compose, check `.env` file is not overriding with empty value

### Service Runs Without Errors But Tokens Fail

**Cause:** Configuration was loaded from .env file in development, but deployment used empty environment variables.

**Fix:**
1. Ensure deployment environment variables are explicitly set
2. Do not rely on .env files in production
3. Use secrets manager or environment variable injection
4. Add logging to verify what secret is being used (log name only, not value)

---

## Code Examples

### Auth Service Startup

```python
# services/auth/main.py
from fastapi import FastAPI
from services.common.config import settings
from services.common.secrets import validate_secrets_at_startup

app = FastAPI()


@app.on_event("startup")
async def startup():
    # Fail-closed: validates required secrets, raises if missing
    validate_secrets_at_startup("auth")
    print("✓ Auth service started with required secrets validated")
```

### Token Creation (Fail-Closed)

```python
# services/auth/helpers/tokens.py
def _require_secret(name: str, value: str) -> str:
    """Fail closed — never sign with an empty key."""
    if not value or not value.strip():
        raise RuntimeError(
            f"{name} is not configured. Set it in your .env file or environment before starting the service."
        )
    return value


def create_access_token(user_id: str) -> str:
    # Validate secret before using it
    secret = _require_secret("ACCESS_TOKEN_SECRET", settings.ACCESS_TOKEN_SECRET)
    return jwt.encode({"sub": user_id}, secret, algorithm="HS256")
```

### Keycloak Configuration (Fail-Closed)

```python
# services/file/src/core/keycloak.py
def _require_keycloak_config(var_name: str) -> str:
    """Fail-closed: raises if required config is missing."""
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise RuntimeError(f"{var_name} is required but not set")
    return value


# Validates at import time (before any requests)
KEYCLOAK_ISSUER = _require_keycloak_config("KEYCLOAK_ISSUER")
KC_CLIENT_SECRET = _require_keycloak_config("KC_CLIENT_SECRET")
```

---

## Compliance and Auditing

### What Gets Logged

- ✅ Service startup: "Auth service started with required secrets validated"
- ✅ Secret name: "Attempting to sign token with ACCESS_TOKEN_SECRET"
- ✅ Operation: "JWT token created successfully"
- ✅ Errors: "Required secret not set at startup"

### What Does NOT Get Logged

- ❌ Secret values (never log them)
- ❌ Secret checksums or hashes (could leak via logs)
- ❌ Environment variable content (could contain secrets)
- ❌ Full error messages that might reveal secret location

### Compliance Requirements

- **OWASP:** Secrets must not be stored in code, committed to git, or embedded in images
- **SOC 2:** Secrets must be managed with explicit access controls and audit trails
- **PCI DSS:** Credentials must be protected and never displayed in logs
- **HIPAA:** Secrets used for encryption must be properly managed and rotated

---

## Related Files

- `domains/manufacturing/knowledge/services/common/secrets.py` — Unified secrets framework
- `domains/manufacturing/knowledge/services/common/config.py` — Configuration with fail-closed validation
- `domains/manufacturing/knowledge/services/file/src/core/keycloak.py` — Keycloak setup with fail-closed loading
- `domains/manufacturing/knowledge/services/auth/helpers/tokens.py` — Token generation with runtime validation
- `.dockerignore` — Prevents secrets from entering images
- `.github/workflows/ci.yml` — Build-time assertion to verify no secrets in images

---

## Questions or Issues?

- Review `docs/DOCKER_SECURITY_SECRETS_GATE.md` for image-level security
- Check `.dockerignore` comments for what files are excluded
- Run `python services/common/secrets.py auth` to see all required auth service secrets
- Contact security team if adding new secrets or changing secret loading

---

## Version History

- **2026-08-30:** Initial fail-closed security framework implemented
  - Unified secrets.py module with fail-closed validation
  - Updated config.py with explicit secret requirements and documentation
  - Updated keycloak.py to fail-closed loading (import-time validation)
  - Comprehensive deployment documentation
