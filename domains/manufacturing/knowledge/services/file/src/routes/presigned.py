import logging
import os
import boto3
from fastapi import APIRouter, Query, HTTPException
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends
from ..core.security import require_auth_context

S3_BUCKET = os.getenv("AWS_S3_BUCKET") or os.getenv("S3_BUCKET")
logger = logging.getLogger(__name__)


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


router = APIRouter(
    prefix="/url",
    tags=["Files"],
    dependencies=[Depends(require_auth_context)],
)


@router.get("/presigned-url/preview")
async def generate_preview_presigned_url(
    key: str = Query(
        ..., description="S3 object key (e.g. uploads/2025-11-21/file.pdf)"
    ),
    expires_in: int = Query(
        300, ge=1, le=3600, description="Expiry time in seconds (1–3600)"
    ),
):
    """
    Generate a presigned URL for **previewing** an S3 object in the browser.

    Example:
    GET /presigned-url/preview?key=uploads/2025-11-21/file.pdf&expires_in=300
    """
    if not S3_BUCKET:
        raise HTTPException(status_code=500, detail="S3 bucket is not configured")

    logger.info(
        f"Generating PREVIEW presigned URL for key={key}, expires_in={expires_in}"
    )

    try:
        params = {
            "Bucket": S3_BUCKET,
            "Key": key,
            # Inline -> browser tries to render (PDF, image, etc.)
            "ResponseContentDisposition": "inline",
        }

        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

        return {
            "mode": "preview",
            "bucket": S3_BUCKET,
            "key": key,
            "url": url,
            "expires_in": expires_in,
        }

    except (BotoCoreError, ClientError) as e:
        logger.error(
            f"Error generating PREVIEW presigned URL for key={key}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate preview presigned URL: {str(e)}",
        )


@router.get("/presigned-url/download")
async def generate_download_presigned_url(
    key: str = Query(
        ..., description="S3 object key (e.g. uploads/2025-11-21/file.pdf)"
    ),
    expires_in: int = Query(
        300, ge=1, le=3600, description="Expiry time in seconds (1–3600)"
    ),
    filename: str | None = Query(
        None, description="Optional filename to suggest in the browser download dialog"
    ),
):
    """
    Generate a presigned URL for **downloading** an S3 object.

    Example:
    GET /presigned-url/download?key=uploads/2025-11-21/file.pdf&filename=invoice-123.pdf
    """
    if not S3_BUCKET:
        raise HTTPException(status_code=500, detail="S3 bucket is not configured")

    logger.info(
        f"Generating DOWNLOAD presigned URL for key={key}, expires_in={expires_in}, filename={filename}"
    )

    try:
        # Content-Disposition: attachment; filename="..."
        if filename:
            disposition = f'attachment; filename="{filename}"'
        else:
            disposition = "attachment"

        params = {
            "Bucket": S3_BUCKET,
            "Key": key,
            "ResponseContentDisposition": disposition,
        }

        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

        return {
            "mode": "download",
            "bucket": S3_BUCKET,
            "key": key,
            "url": url,
            "expires_in": expires_in,
            "filename": filename,
        }

    except (BotoCoreError, ClientError) as e:
        logger.error(
            f"Error generating DOWNLOAD presigned URL for key={key}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate download presigned URL: {str(e)}",
        )
