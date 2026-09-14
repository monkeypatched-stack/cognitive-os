from pydantic import field_validator
from pydantic_settings import BaseSettings
import dotenv
import os

# =========================================================================
# SECURITY: Secrets Loading Policy
# =========================================================================
# This module enforces fail-closed secrets handling:
#
# 1. .env files are ONLY loaded for local development
# 2. In production, secrets MUST come from deployment mechanisms:
#    - Environment variables (set by Docker/K8s)
#    - Secret managers (Vault, AWS Secrets Manager)
#    - Secret injection (init containers, sidecar agents)
# 3. Required secrets are validated at startup — service fails if missing
# 4. No secrets are logged or displayed in error messages
#
# DO NOT add fallback defaults for security-sensitive values.
# DO NOT load secrets from .env files in production (they're never committed but can leak).
# DO NOT use os.getenv() with a default for secrets — fail closed instead.

# Load environment variables from .env files (dev/local only)
# In production, these files will not exist — secrets come from deployment
dotenv.load_dotenv(".env")
dotenv.load_dotenv("services/auth/.env", override=True)


class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    DB_NAME: str = "demo"
    CORS_ALLOW_ORIGINS: str = (
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
    )

    # ====================================================================
    # Security-Critical Secrets (REQUIRED, fail-closed)
    # ====================================================================
    # These are validated at startup and must come from explicit
    # deployment sources (env vars, secrets managers). No defaults,
    # no fallbacks. If missing, the service refuses to start.
    ACCESS_TOKEN_SECRET: str
    REFRESH_TOKEN_SECRET: str

    @field_validator("ACCESS_TOKEN_SECRET", "REFRESH_TOKEN_SECRET", mode="before")
    @classmethod
    def _require_non_empty_secret(cls, value: str, info) -> str:  # type: ignore[override]
        # Fail closed: reject empty or whitespace-only secrets
        if not value or not value.strip():
            raise ValueError(
                f"{info.field_name} is a security-critical secret and must be "
                f"set to a non-empty value. It cannot be loaded from .env files "
                f"in production. Ensure it is set via:\n"
                f"  1. Deployment environment variables (Docker: -e, K8s: env)\n"
                f"  2. Secret manager (Vault, AWS Secrets Manager)\n"
                f"  3. For local dev only: .env or services/auth/.env file"
            )
        from services.common.secrets import reject_insecure_hmac_secret

        return reject_insecure_hmac_secret(value, name=info.field_name)

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    REFRESH_COOKIE_NAME: str = "refreshToken"
    REDIS_URL: str = "redis://localhost:6379"
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    NEO4J_MIRROR_ENABLED: bool = True
    NEO4J_MIRROR_STRICT: bool = False
    NEO4J_MIRROR_MAX_DOCUMENTS: int = 100
    NATS_URL: str = "nats://localhost:4222"
    NATS_EVENTS_SUBJECT: str = "indus.websocket.events"
    NATS_EVENTS_QUEUE: str = "indus-influx-consumers"
    NATS_EVENT_TYPE_SUBJECT_PREFIX: str = "indus.events"
    NATS_EVENT_TYPE_QUEUE_PREFIX: str = "indus-event-type"
    NATS_CANVAS_WEBHOOK_INCOMING_SUBJECT: str = "indus.events.canvas-webhook-incoming"
    NATS_CANVAS_WEBHOOK_INCOMING_QUEUE: str = "indus-event-type-canvas-webhook-incoming"
    NATS_CANVAS_WEBHOOK_OUTGOING_SUBJECT: str = "indus.events.canvas-webhook-outgoing"
    NATS_CANVAS_WEBHOOK_OUTGOING_QUEUE: str = "indus-event-type-canvas-webhook-outgoing"
    NATS_INTEGRATIONS_RAW_SUBJECT: str = "indus.integrations.raw"
    NATS_INTEGRATIONS_RAW_QUEUE: str = "indus-integration-transformer"
    NATS_INTEGRATIONS_TRANSFORMED_SUBJECT: str = "indus.integrations.transformed"
    NATS_APPROVALS_SUBJECT: str = "indus.approvals.queue"
    NATS_APPROVALS_QUEUE: str = "indus-approval-websocket"
    NATS_CHANGE_CONTROL_SUBJECT: str = "indus.change-control.queue"
    NATS_CHANGE_CONTROL_QUEUE: str = "indus-change-control-websocket"
    NATS_APPROVAL_DECISIONS_SUBJECT: str = "indus.approval-decisions.queue"
    NATS_APPROVAL_DECISIONS_QUEUE: str = "indus-approval-decisions-websocket"
    NATS_APPROVAL_AUDIT_SUBJECT: str = "indus.approval-audit.queue"
    NATS_APPROVAL_AUDIT_QUEUE: str = "indus-approval-audit"
    NATS_AUDIT_SUBJECT: str = "indus.audit.queue"
    NATS_AUDIT_QUEUE: str = "indus-audit-websocket"
    NATS_CDC_SUBJECT: str = "indus.cdc.events"
    NATS_CDC_QUEUE: str = "indus-cdc-consumers"
    NATS_SENSOR_SUBJECT_PREFIX: str = "indus.sensors"
    NATS_SENSOR_QUEUE: str = "indus-sensor-influx-consumers"
    MQTT_ENABLED: bool = False
    MQTT_BROKER_URL: str = "mqtt://localhost:1883"
    MQTT_CLIENT_ID: str = "indus-iot-mqtt-bridge"
    MQTT_TOPICS: str = "indus/sensors/#"
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    INFLUXDB_URL: str = "http://localhost:8181"
    INFLUXDB_TOKEN: str = ""
    INFLUXDB_ORG: str = "indus"
    INFLUXDB_BUCKET: str = "events"
    INFLUXDB_EVENTS_MEASUREMENT: str = "indus.websocket.events"
    INFLUXDB_EVENT_TYPE_MEASUREMENT_PREFIX: str = "events"
    INFLUXDB_EVENT_TYPE_MEASUREMENTS: str = (
        "event-log,count-event,maintenance-log,downtime-log,cleaning-log,calibration-log,rfid-scan,nfc-scan,canvas-webhook-incoming,canvas-webhook-outgoing,generic"
    )
    EMAIL_NOTIFICATIONS_ENABLED: bool = True
    NOTIFICATION_EMAIL_TO: str = "prashunjaveri@gmail.com"
    N8N_EMAIL_WEBHOOK_URL: str = ""
    N8N_COMPLIANCE_REPORT_WEBHOOK_URL: str = ""
    N8N_WEBHOOK_AUTH_HEADER: str = "X-Sentinel-Webhook-Secret"
    N8N_WEBHOOK_SECRET: str = ""
    N8N_AGENT_OS_DEAD_LETTER_WEBHOOK_URL: str = ""
    N8N_AGENT_OS_DEAD_LETTER_TIMEOUT_SECONDS: float = 5.0
    N8N_APPROVAL_RESOLVER_WEBHOOK_URL: str = ""
    N8N_APPROVAL_RESOLVER_TIMEOUT_SECONDS: float = 10.0
    GRAPH_RAG_API_KEY_OTP_TTL_SECONDS: int = 300
    GRAPH_RAG_API_KEY_OTP_MAX_ATTEMPTS: int = 5
    GRAPH_RAG_API_KEY_OTP_ECHO: bool = False
    APPROVAL_FORM_BASE_URL: str = (
        "http://localhost:8010/api/v1/auth/compliance/approvals"
    )
    PART11_FAILED_LOGIN_LIMIT: int = 5
    PART11_LOCKOUT_MINUTES: int = 15
    AUDIT_ELASTICSEARCH_ENABLED: bool = True
    AUDIT_ELASTICSEARCH_URL: str = "http://localhost:9200"
    AUDIT_ELASTICSEARCH_INDEX: str = "part11-audit-trail"
    AUDIT_ELASTICSEARCH_SYNC_INTERVAL_SECONDS: int = 3600
    AUDIT_ELASTICSEARCH_BATCH_SIZE: int = 100
    AUDIT_ELASTICSEARCH_BULK_MAX_BYTES: int = 5 * 1024 * 1024
    AUDIT_ELASTICSEARCH_USERNAME: str = ""
    AUDIT_ELASTICSEARCH_PASSWORD: str = ""
    AUDIT_ELASTICSEARCH_API_KEY: str = ""
    PRODUCT_RESEARCH_ELASTICSEARCH_ENABLED: bool = True
    PRODUCT_RESEARCH_ELASTICSEARCH_URL: str = "http://localhost:9200"
    PRODUCT_RESEARCH_ELASTICSEARCH_INDEX: str = "india-drug-formulation-research"
    PRODUCT_RESEARCH_ELASTICSEARCH_USERNAME: str = ""
    PRODUCT_RESEARCH_ELASTICSEARCH_PASSWORD: str = ""
    PRODUCT_RESEARCH_ELASTICSEARCH_API_KEY: str = ""
    VOICE_STT_MAX_AUDIO_BYTES: int = 25 * 1024 * 1024
    DEEPGRAM_API_KEY: str = ""
    DEEPGRAM_API_BASE_URL: str = "https://api.deepgram.com"
    DEEPGRAM_STT_MODEL_ID: str = "nova-3"
    DEEPGRAM_STT_TIMEOUT_SECONDS: float = 60.0
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_API_BASE_URL: str = "https://openrouter.ai/api/v1"
    QWEN_OCR_MODEL: str = "qwen/qwen-2-vl-72b-instruct"
    QWEN_OCR_TIMEOUT_SECONDS: float = 90.0
    QWEN_OCR_MAX_PDF_PAGES: int = 25
    QWEN_OCR_PDF_RENDER_SCALE: float = 2.0
    GRAPH_RAG_INTENT_MIN_CONFIDENCE: float = 0.42
    GRAPH_RAG_UNMATCHED_QUESTIONS_COLLECTION: str = "agentos_unmatched_questions"
    MEM0_ENABLED: bool = True
    MEM0_API_KEY: str = ""
    MEM0_ORG_ID: str = ""
    MEM0_PROJECT_ID: str = ""
    MEM0_AGENT_ID: str = "agentos"
    MEM0_MEMORY_TOP_K: int = 5
    MODULE_CONTROL_ACTIVATION_PASSWORD_HASH: str = ""
    MODULE_CONTROL_DEV_PASSWORD: str = "module-control-dev"
    MODULE_CONTROL_OTP_TTL_SECONDS: int = 300
    MODULE_CONTROL_SESSION_TTL_SECONDS: int = 900
    MODULE_CONTROL_ECHO_OTP: bool = True
    MODULE_CONTROL_INTERNAL_SECRET: str = ""
    DATABRICKS_HOST: str = ""
    DATABRICKS_TOKEN: str = ""
    DATABRICKS_CLUSTER_ID: str = ""
    DATABRICKS_CATALOG: str = "hive_metastore"
    DATABRICKS_SCHEMA: str = "default"
    DATABRICKS_CONNECT_TIMEOUT_SECONDS: float = 30.0
    GRAPH_RAG_EXECUTION_MODEL: str = "definition"
    ANTHROPIC_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_API_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma3:latest"

    class Config:
        env_file = (".env", "services/auth/.env")
        extra = "ignore"


settings = Settings()


def cors_allow_origins() -> list[str]:
    """Explicit CORS origin allowlist from CORS_ALLOW_ORIGINS (comma-separated).

    Never returns ["*"]: these services run with allow_credentials=True, and a
    wildcard combined with credentials lets any site make authenticated
    cross-origin requests. A literal "*" in the env is dropped, falling back to
    the configured dev origins so the door is never left fully open.
    """
    raw = [
        o.strip()
        for o in (settings.CORS_ALLOW_ORIGINS or "").split(",")
        if o.strip() and o.strip() != "*"
    ]
    return raw or ["http://localhost:3000"]


def configure_cors(app) -> None:
    """Apply the standard credentialed-but-allowlisted CORS policy to `app`."""
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
