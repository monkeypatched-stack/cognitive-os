import logging
import os
from fastapi import (
    APIRouter,
    Depends,
    File,
    UploadFile,
    HTTPException,
    BackgroundTasks,
    Query,
)

from ..core.security import require_auth_context
from botocore.exceptions import BotoCoreError, ClientError

from ..helpers.helpers import (
    delete_file_helper,
    list_files_by_date_helper,
    read_file_helper,
    upload_file_helper,
    delete_folder_helper,
)

S3_BUCKET = os.getenv("AWS_S3_BUCKET") or os.getenv("S3_BUCKET")
logger = logging.getLogger(__name__)

# --------------------------------------------
# ROUTER
# -------------------------------------------

router = APIRouter(
    prefix="/files",
    tags=["Files"],
    dependencies=[Depends(require_auth_context)],
)
root_router = APIRouter(tags=["Files"], dependencies=[Depends(require_auth_context)])


# --------------------------------------------
# ROUTES
# -------------------------------------------


@root_router.post("/upload")
@root_router.post("/upload/")
@router.post("/upload")
@router.post("/upload/")
async def upload_file(file: UploadFile = File(...), notify: bool = True):
    """
    Upload a document to S3 and optionally forward it to another service via HTTP POST.
    Folder structure: uploads/YYYY-MM-DD/<uuid>.<ext>
    """
    try:
        return await upload_file_helper(file, notify)
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as e:
        logger.error(f"S3 upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"S3 upload error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/{date}")
async def list_files_by_date(date: str, create_backup: bool = True):
    """
    List all files uploaded on a given date with backup status.
    Example: GET /files/2025-11-11
    """
    try:
        return await list_files_by_date_helper(date, create_backup)
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as e:
        logger.error(f"S3 list error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"S3 list error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/{date}/{filename}")
async def read_file(
    date: str,
    filename: str,
    download: bool = Query(
        True, description="Return as browser download attachment when true"
    ),
):
    """
    Read or download a file from S3 given its date and filename.
    URL format: /files/YYYY-MM-DD/<filename>
    """
    s3_key = f"uploads/{date}/{filename}"
    logger.info(f"Reading file: {s3_key}")

    try:
        return await read_file_helper(date, filename, as_attachment=download)

    except HTTPException:
        raise
    except ClientError as e:
        err_code = e.response.get("Error", {}).get("Code")
        if err_code in ("404", "NoSuchKey", "NotFound"):
            logger.error(f"File not found: {s3_key}")
            raise HTTPException(status_code=404, detail="File not found in S3")
        logger.error(f"S3 read ClientError: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"S3 read error: {str(e)}")
    except (BotoCoreError, Exception) as e:
        logger.error(f"S3 read error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"S3 read error: {str(e)}")


@router.delete("/{date}/{filename}")
async def delete_file(date: str, filename: str, delete_backup: bool = True):
    """Delete a file from S3 and optionally its backup."""
    s3_key = f"uploads/{date}/{filename}"
    logger.info(f"Deleting file: {s3_key}, delete_backup: {delete_backup}")

    try:
        return await delete_file_helper(date, filename, delete_backup)
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as e:
        logger.error(f"S3 delete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"S3 delete error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.delete("/{date}")
async def delete_folder(
    date: str, delete_backup: bool = True, background_tasks: BackgroundTasks = None
):
    """Delete all files for a specific date (entire folder)."""
    try:
        return await delete_folder_helper(date, delete_backup, background_tasks)

    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as e:
        logger.error(f"S3 delete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"S3 delete error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
