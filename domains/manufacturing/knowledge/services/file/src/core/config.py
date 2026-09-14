import os
import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

load_dotenv()
load_dotenv("services/file/.env")

logger = configure_service_logging("file")

from ..routes.files import root_router as root_file_router
from ..routes.files import router as file_router
from ..routes.cad_conversion import router as cad_conversion_router
from ..routes.presigned import router as presigned_url_router

# ============================
# ENVIRONMENT VARIABLES
# ============================

# S3 Configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv(
    "SECRET_ACCESS_KEY"
)
AWS_REGION = os.getenv("AWS_REGION", os.getenv("REGION", "ap-south-1"))
S3_BUCKET = os.getenv("AWS_S3_BUCKET") or os.getenv("S3_BUCKET")
S3_BACKUP_BUCKET = os.getenv("AWS_S3_BACKUP_BUCKET") or os.getenv("S3_BACKUP_BUCKET")

# WebSocket/Forward Configuration
FORWARD_URL = os.getenv("WEBSOCKET_URL") or os.getenv("FORWARD_URL") or ""
FORWARD_TIMEOUT = int(os.getenv("WEBSOCKET_TIMEOUT", "10"))
FORWARD_ENABLED = bool(FORWARD_URL and FORWARD_URL.strip())

# Validate required config
if not S3_BUCKET:
    logger.warning("S3_BUCKET is not set. Set AWS_S3_BUCKET or S3_BUCKET env var.")

# ============================
# S3 CLIENT INITIALIZATION
# ============================

s3_client_kwargs = {"region_name": AWS_REGION}
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    s3_client_kwargs.update(
        {
            "aws_access_key_id": AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
        }
    )

s3_client = boto3.client("s3", **s3_client_kwargs)

# Log configuration
logger.info(f"Forward URL: {FORWARD_URL}")
logger.info(f"Forward Enabled: {FORWARD_ENABLED}")
logger.info(f"Forward Timeout: {FORWARD_TIMEOUT}s")
logger.info(f"S3 Bucket: {S3_BUCKET}")
logger.info(
    f"S3 Backup Bucket: {S3_BACKUP_BUCKET or (S3_BUCKET + '-backup' if S3_BUCKET else 'N/A')}"
)

# ============================
# FASTAPI APP INITIALIZATION
# ============================

app = FastAPI(
    title="Document Upload API with Backup",
    description="FastAPI application for Azure Container Apps",
    version="1.0.0",
)
install_request_logging(app, "file")
install_route_tracing(app, "file")

# ============================
# MIDDLEWARE
# ============================

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    # Explicit allowlist — a wildcard with allow_credentials=True lets any
    # site make authenticated cross-origin requests. Sourced from
    # CORS_ALLOW_ORIGINS (comma-separated), never "*".
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# Routers
# -------------------------------------------------
app.include_router(file_router)
app.include_router(root_file_router)
app.include_router(cad_conversion_router)
app.include_router(presigned_url_router)
