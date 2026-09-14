from mimetypes import guess_type
import os
from pathlib import Path
import shutil
from typing import Any, Dict, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo
import boto3
from fastapi import FastAPI, File, Response, UploadFile, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError
from uuid import uuid4
from datetime import datetime
import asyncio
import httpx
import logging

import json
import base64

import websockets

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# env variables for s3
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv(
    "SECRET_ACCESS_KEY"
)
AWS_REGION = os.getenv("AWS_REGION", os.getenv("REGION", "ap-south-1"))
S3_BUCKET = os.getenv("AWS_S3_BUCKET") or os.getenv("S3_BUCKET")
S3_BACKUP_BUCKET = os.getenv("AWS_S3_BACKUP_BUCKET") or os.getenv("S3_BACKUP_BUCKET")
LOCAL_UPLOAD_DIR = Path(os.getenv("FILE_UPLOAD_DIR", "uploads/files"))

# env variabls for websocket
FORWARD_URL = os.getenv("WEBSOCKET_URL") or os.getenv("FORWARD_URL") or ""
FORWARD_TIMEOUT = int(os.getenv("WEBSOCKET_TIMEOUT", "120"))
FORWARD_ENABLED = bool(FORWARD_URL and FORWARD_URL.strip())
FORWARD_EXPECT_JSON = os.getenv("WEBSOCKET_EXPECT_JSON", "false").lower() == "true"

# Validate required config
if not S3_BUCKET:
    logger.warning("S3_BUCKET is not set. Set AWS_S3_BUCKET or S3_BUCKET env var.")

# Initialize FastAPI app
app = FastAPI(
    title="Document Upload API with Backup",
    description="FastAPI application for Azure Container Apps",
    version="1.0.0",
)

# Initialize S3 client (credentials optional if IAM role present)
s3_client_kwargs = {"region_name": AWS_REGION}
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    s3_client_kwargs.update(
        {
            "aws_access_key_id": AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
        }
    )

s3_client = boto3.client("s3", **s3_client_kwargs)

logger.info(f"Forward URL: {FORWARD_URL}")
logger.info(f"Forward Enabled: {FORWARD_ENABLED}")
logger.info(f"Forward Timeout: {FORWARD_TIMEOUT}s")
logger.info(f"Forward Expect JSON: {FORWARD_EXPECT_JSON}")
logger.info(f"S3 Bucket: {S3_BUCKET}")
logger.info(
    f"S3 Backup Bucket: {S3_BACKUP_BUCKET or (S3_BUCKET + '-backup' if S3_BUCKET else 'N/A')}"
)


def _local_upload_path(date: str, filename: str) -> Path:
    return LOCAL_UPLOAD_DIR / date / filename


def _local_backup_path(date: str, filename: str) -> Path:
    return LOCAL_UPLOAD_DIR / "backups" / date / filename


def _save_local_file(date: str, filename: str, file_content: bytes) -> Path:
    local_path = _local_upload_path(date, filename)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(file_content)
    return local_path


def _local_file_info(date: str, path: Path, backup_exists: bool = False) -> dict:
    stat = path.stat()
    return {
        "filename": path.name,
        "key": f"uploads/{date}/{path.name}",
        "size": stat.st_size,
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "url": str(path),
        "storage_backend": "local",
        "backup_exists": backup_exists,
    }


def _list_local_files(date: str, create_backup: bool = True) -> dict:
    folder = LOCAL_UPLOAD_DIR / date
    if not folder.exists():
        return {
            "date": date,
            "file_count": 0,
            "files": [],
            "message": "No files found for this date",
        }

    files = []
    for path in sorted(item for item in folder.iterdir() if item.is_file()):
        backup_path = _local_backup_path(date, path.name)
        backup_exists = backup_path.exists()
        if create_backup and not backup_exists:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
            backup_exists = True
        files.append(_local_file_info(date, path, backup_exists=backup_exists))

    return {
        "date": date,
        "file_count": len(files),
        "files": files,
        "storage_backend": "local",
    }


def _download_headers(filename: str) -> dict[str, str]:
    safe_name = Path(filename or "download").name.replace('"', "")
    encoded_name = quote(safe_name)
    return {
        "Content-Disposition": f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{encoded_name}",
        "X-Content-Type-Options": "nosniff",
    }


def _read_local_file(
    date: str, filename: str, *, as_attachment: bool = True
) -> Response:
    local_path = _local_upload_path(date, filename)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="File not found locally")

    content_type, _ = guess_type(filename)
    if not content_type:
        content_type = "application/octet-stream"

    headers = (
        _download_headers(filename)
        if as_attachment
        else {"X-Content-Type-Options": "nosniff"}
    )
    return Response(
        content=local_path.read_bytes(), media_type=content_type, headers=headers
    )


def _delete_local_file(date: str, filename: str, delete_backup: bool = True) -> dict:
    local_path = _local_upload_path(date, filename)
    backup_path = _local_backup_path(date, filename)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    local_path.unlink()
    backup_deleted = False
    if delete_backup and backup_path.exists():
        backup_path.unlink()
        backup_deleted = True

    return {
        "message": "File deleted successfully",
        "filename": filename,
        "date": date,
        "storage_backend": "local",
        "backup_deleted": backup_deleted,
    }


def _delete_local_folder(date: str, delete_backup: bool = True) -> dict:
    folder = LOCAL_UPLOAD_DIR / date
    backup_folder = LOCAL_UPLOAD_DIR / "backups" / date
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"No files found for date: {date}")

    deleted_count = sum(1 for item in folder.iterdir() if item.is_file())
    shutil.rmtree(folder)
    backup_deleted = False
    if delete_backup and backup_folder.exists():
        shutil.rmtree(backup_folder)
        backup_deleted = True

    return {
        "message": f"Deleted {deleted_count} files for date {date}",
        "date": date,
        "deleted_count": deleted_count,
        "storage_backend": "local",
        "backup_deleted": backup_deleted,
    }


async def delete_backup_folder(date: str, expected_count: int):
    """Background task to delete backup folder for a specific date."""
    try:
        backup_bucket = S3_BACKUP_BUCKET or (
            S3_BUCKET + "-backup" if S3_BUCKET else None
        )
        if not backup_bucket:
            logger.warning("No backup bucket configured; skipping backup deletion.")
            return

        backup_prefix = f"backups/{date}/"
        logger.info(f"Deleting backup folder: {backup_prefix} from {backup_bucket}")

        response = s3_client.list_objects_v2(Bucket=backup_bucket, Prefix=backup_prefix)
        if "Contents" in response:
            objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
            batch_size = 1000
            deleted_total = 0
            for i in range(0, len(objects_to_delete), batch_size):
                batch = objects_to_delete[i : i + batch_size]
                s3_client.delete_objects(
                    Bucket=backup_bucket, Delete={"Objects": batch}
                )
                deleted_total += len(batch)
            logger.info(f"✅ Deleted {deleted_total} backup files for {date}")
        else:
            logger.info(f"No backup files found for {date}")
    except Exception as e:
        logger.error(f"Failed to delete backup folder for {date}: {e}", exc_info=True)


async def upload_file_helper(file: UploadFile = File(...), notify: bool = True):
    """
    Upload a document to S3 and optionally forward it to another service via WebSocket.
    Folder structure: uploads/YYYY-MM-DD/<uuid>.<ext>
    """
    try:
        logger.info(f"Uploading file: {file.filename}, notify: {notify}")

        # Create unique filename and S3 key
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        date_folder = ist_now.strftime("%Y-%m-%d")
        file_extension = os.path.splitext(file.filename)[-1]
        unique_filename = f"{uuid4()}{file_extension}"
        s3_key = f"uploads/{str(date_folder)}/{unique_filename}"

        # Read file
        file_content = await file.read()
        logger.info(f"Read {len(file_content)} bytes from uploaded file")

        # Get MIME type
        mime_type = file.content_type

        storage_backend = "s3"
        storage_error = None
        local_path = None

        if S3_BUCKET:
            try:
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=s3_key,
                    Body=file_content,
                    ContentType=file.content_type or "application/octet-stream",
                )
                logger.info(f"Successfully uploaded to S3: {s3_key}")
                file_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
            except (BotoCoreError, ClientError, Exception) as exc:
                storage_backend = "local"
                storage_error = str(exc)
                local_path = _save_local_file(
                    date_folder, unique_filename, file_content
                )
                file_url = str(local_path)
                logger.error(
                    "S3 upload failed; saved file locally at %s: %s",
                    local_path,
                    exc,
                    exc_info=True,
                )
        else:
            storage_backend = "local"
            storage_error = "S3 bucket is not configured"
            local_path = _save_local_file(date_folder, unique_filename, file_content)
            file_url = str(local_path)
            logger.warning(
                "S3 bucket is not configured; saved file locally at %s", local_path
            )

        response_data = {
            "message": "File uploaded successfully",
            "filename": unique_filename,
            "date_folder": date_folder,
            "url": file_url,
            "mime_type": mime_type,
            "s3_key": s3_key if storage_backend == "s3" else None,
            "storage_backend": storage_backend,
            "local_path": str(local_path) if local_path else None,
            "storage_error": storage_error,
        }

        # 4️⃣ Forward file via WebSocket
        if notify and FORWARD_ENABLED:
            logger.info(f"Forwarding file to WebSocket bridge at {FORWARD_URL}")

            # Create temp directory if it doesn't exist
            os.makedirs("/tmp", exist_ok=True)
            temp_file_path = f"/tmp/{unique_filename}"

            try:
                # Save file temporarily for WebSocket sending
                with open(temp_file_path, "wb") as f:
                    f.write(file_content)
                logger.info(f"Saved temp file: {temp_file_path}")

                logger.info(mime_type)
                if mime_type == "application/pdf":

                    # Pass the original filename to preserve it
                    ws_result = await send_file_to_bridge(
                        file_path=temp_file_path,
                        original_filename=file.filename,
                        ws_uri=FORWARD_URL,
                        timeout=FORWARD_TIMEOUT,
                    )

                    # Clean up temp file
                    try:
                        os.remove(temp_file_path)
                        logger.info(f"Cleaned up temp file: {temp_file_path}")
                    except Exception as cleanup_err:
                        logger.warning(f"Failed to cleanup temp file: {cleanup_err}")

                    if ws_result.get("status") == "queued":
                        response_data["forward_status"] = "success"
                        response_data["forward_response"] = ws_result
                        logger.info(f"✅ File forwarded successfully via WebSocket")
                    else:
                        response_data["forward_status"] = "failed"
                        response_data["forward_response"] = ws_result
                        logger.error(f"❌ File forwarding failed: {ws_result}")

                elif mime_type in (
                    "image/jpeg",
                    "image/png",
                    "image/jpg",
                    "image/webp",
                ):
                    logger.info("Processing image with Qwen-VL via OpenRouter for OCR")

                    # Extract text from image using Qwen-VL via OpenRouter
                    ocr_result = await extract_text_with_openrouter(
                        file_content, mime_type
                    )

                    if ocr_result.get("success"):
                        response_data["ocr_text"] = ocr_result.get("text")
                        response_data["ocr_status"] = "success"
                        response_data["ocr_model"] = ocr_result.get("model")
                        logger.info(
                            f"✅ OCR completed: {len(ocr_result.get('text', ''))} characters extracted"
                        )
                        # send the extracted text to web socket
                        response = json.dumps(
                            {"content": ocr_result.get("text"), "mime_type": mime_type}
                        )
                        await send_text(response)
                    else:
                        response_data["ocr_status"] = "failed"
                        response_data["ocr_error"] = ocr_result.get("error")
                        logger.error(f"❌ OCR failed: {ocr_result.get('error')}")

                    # Clean up temp file
                    try:
                        os.remove(temp_file_path)
                        logger.info(f"Cleaned up temp file: {temp_file_path}")
                    except Exception as cleanup_err:
                        logger.warning(f"Failed to cleanup temp file: {cleanup_err}")

            except Exception as forward_err:
                logger.error(f"Error during file forwarding: {forward_err}")
                response_data["forward_status"] = "error"
                response_data["forward_error"] = str(forward_err)

        return response_data

    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


async def extract_text_with_openrouter(
    image_bytes: bytes, mime_type: str, model: str = "qwen/qwen-2-vl-72b-instruct"
) -> dict:
    """
    Extract text from image using Qwen-VL through OpenRouter API.

    Args:
        image_bytes: Binary image data
        mime_type: MIME type of the image (e.g., 'image/jpeg')
        model: OpenRouter model to use (default: qwen-2-vl-72b-instruct)

    Available Qwen-VL models on OpenRouter:
        - qwen/qwen-2-vl-72b-instruct (most capable)
        - qwen/qwen-2-vl-7b-instruct (faster, cheaper)

    Returns:
        dict with keys: success (bool), text (str), model (str), error (str)
    """
    try:
        # Get OpenRouter API key from environment
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

        if not openrouter_api_key:
            logger.error("OpenRouter API key not configured")
            return {"success": False, "error": "OPENROUTER_API_KEY not configured"}

        # Encode image to base64
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # Prepare request payload for OpenRouter
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_base64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this image. Return only the extracted text without any additional commentary or explanation.",
                        },
                    ],
                }
            ],
        }

        # Make async HTTP request to OpenRouter
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.getenv(
                        "APP_URL", "http://localhost:8000"
                    ),  # Optional
                    "X-Title": os.getenv("APP_NAME", "Document Processor"),  # Optional
                },
                json=payload,
            )

            if response.status_code == 200:
                result = response.json()
                extracted_text = (
                    result.get("choices", [{}])[0].get("message", {}).get("content", "")
                )

                return {
                    "success": True,
                    "text": extracted_text.strip(),
                    "model": result.get("model"),
                    "usage": result.get("usage"),
                    "provider": "openrouter",
                }
            else:
                error_detail = response.text
                logger.error(
                    f"OpenRouter API error: {response.status_code} - {error_detail}"
                )
                return {
                    "success": False,
                    "error": f"API request failed: {response.status_code} - {error_detail}",
                }

    except httpx.TimeoutException:
        logger.error("OpenRouter API request timed out")
        return {"success": False, "error": "Request timed out after 60 seconds"}
    except Exception as e:
        logger.error(f"Error in OCR processing with OpenRouter: {str(e)}")
        return {"success": False, "error": str(e)}


async def list_files_by_date_helper(date: str, create_backup: bool = True):
    """
    List all files uploaded on a given date with backup status.
    Example: GET /files/2025-11-11
    """
    if not S3_BUCKET:
        logger.warning("S3 bucket is not configured; listing local files for %s", date)
        return _list_local_files(date, create_backup=create_backup)

    try:
        logger.info(f"Listing files for date: {date}")
        prefix = f"uploads/{date}/"
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)

        if "Contents" not in response:
            logger.info(f"No files found for date: {date}")
            local_result = _list_local_files(date, create_backup=create_backup)
            if local_result.get("file_count", 0) > 0:
                local_result["s3_message"] = (
                    "No files found in S3; returned local files"
                )
                return local_result
            return {
                "date": date,
                "files": [],
                "message": "No files found for this date",
            }

        files = []
        backup_bucket = S3_BACKUP_BUCKET or f"{S3_BUCKET}-backup"

        for obj in response["Contents"]:
            key = obj["Key"]
            filename = key.split("/")[-1]
            backup_key = f"backups/{date}/{filename}"

            backup_exists = False
            try:
                s3_client.head_object(Bucket=backup_bucket, Key=backup_key)
                backup_exists = True
            except ClientError:
                backup_exists = False

            if not backup_exists and create_backup:
                try:
                    file_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                    file_bytes = file_obj["Body"].read()
                    if backup_bucket:
                        s3_client.put_object(
                            Bucket=backup_bucket, Key=backup_key, Body=file_bytes
                        )
                        backup_exists = True
                        logger.info(f"Created backup for: {key}")
                except Exception as e:
                    logger.error(f"Failed to upload {key} to backup: {e}")

            file_info = {
                "filename": filename,
                "key": key,
                "size": obj.get("Size"),
                "last_modified": (
                    obj.get("LastModified").isoformat()
                    if obj.get("LastModified")
                    else None
                ),
                "url": f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}",
                "backup_exists": backup_exists,
            }
            files.append(file_info)

        logger.info(f"Found {len(files)} files for date: {date}")
        return {"date": date, "file_count": len(files), "files": files}

    except (BotoCoreError, ClientError) as e:
        logger.error(
            "S3 list error; listing local files for %s: %s", date, e, exc_info=True
        )
        result = _list_local_files(date, create_backup=create_backup)
        result["s3_error"] = str(e)
        return result
    except Exception as e:
        logger.error(
            "Unexpected S3 list error; listing local files for %s: %s",
            date,
            e,
            exc_info=True,
        )
        result = _list_local_files(date, create_backup=create_backup)
        result["s3_error"] = str(e)
        return result


async def delete_file_helper(date: str, filename: str, delete_backup: bool = True):
    """Delete a file from S3 and optionally its backup."""
    if not S3_BUCKET:
        logger.warning(
            "S3 bucket is not configured; deleting local file %s/%s", date, filename
        )
        return _delete_local_file(date, filename, delete_backup=delete_backup)

    s3_key = f"uploads/{date}/{filename}"
    logger.info(f"Deleting file: {s3_key}, delete_backup: {delete_backup}")

    try:
        # Check existence
        try:
            s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code")
            if err_code in ("404", "NoSuchKey", "NotFound"):
                logger.error("File not found in S3; trying local delete: %s", s3_key)
                return _delete_local_file(date, filename, delete_backup=delete_backup)
            else:
                raise

        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.info(f"Deleted from S3: {s3_key}")

        response_data = {
            "message": "File deleted successfully",
            "filename": filename,
            "date": date,
            "s3_key": s3_key,
            "backup_deleted": False,
        }

        if delete_backup:
            backup_bucket = S3_BACKUP_BUCKET or f"{S3_BUCKET}-backup"
            backup_key = f"backups/{date}/{filename}"
            try:
                s3_client.delete_object(Bucket=backup_bucket, Key=backup_key)
                response_data["backup_deleted"] = True
                logger.info(f"Deleted backup: {backup_key}")
            except ClientError as e:
                err_code = e.response.get("Error", {}).get("Code")
                if err_code in ("404", "NoSuchKey", "NotFound"):
                    response_data["backup_deleted"] = False
                    logger.info(f"No backup found: {backup_key}")
                else:
                    raise

        return response_data

    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as e:
        logger.error(
            "S3 delete error; deleting local file %s/%s: %s",
            date,
            filename,
            e,
            exc_info=True,
        )
        response_data = _delete_local_file(date, filename, delete_backup=delete_backup)
        response_data["s3_error"] = str(e)
        return response_data
    except Exception as e:
        logger.error(
            "Unexpected S3 delete error; deleting local file %s/%s: %s",
            date,
            filename,
            e,
            exc_info=True,
        )
        response_data = _delete_local_file(date, filename, delete_backup=delete_backup)
        response_data["s3_error"] = str(e)
        return response_data


async def read_file_helper(date: str, filename: str, *, as_attachment: bool = True):
    """
    Read or download a file from S3 given its date and filename.
    URL format: /files/YYYY-MM-DD/<filename>
    """
    if not S3_BUCKET:
        logger.warning(
            "S3 bucket is not configured; reading local file %s/%s", date, filename
        )
        return _read_local_file(date, filename, as_attachment=as_attachment)

    s3_key = f"uploads/{date}/{filename}"
    logger.info(f"Reading file: {s3_key}")

    try:
        file_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        file_content = file_obj["Body"].read()

        content_type, _ = guess_type(filename)
        if not content_type:
            content_type = "application/octet-stream"

        logger.info(f"Successfully read file: {s3_key} ({len(file_content)} bytes)")
        headers = (
            _download_headers(filename)
            if as_attachment
            else {"X-Content-Type-Options": "nosniff"}
        )
        return Response(content=file_content, media_type=content_type, headers=headers)

    except ClientError as e:
        err_code = e.response.get("Error", {}).get("Code")
        if err_code in ("404", "NoSuchKey", "NotFound"):
            logger.error("File not found in S3; trying local fallback: %s", s3_key)
            return _read_local_file(date, filename, as_attachment=as_attachment)
        logger.error("S3 read ClientError; trying local fallback: %s", e, exc_info=True)
        return _read_local_file(date, filename, as_attachment=as_attachment)
    except (BotoCoreError, Exception) as e:
        logger.error("S3 read error; trying local fallback: %s", e, exc_info=True)
        return _read_local_file(date, filename, as_attachment=as_attachment)


async def delete_folder_helper(
    date: str, delete_backup: bool = True, background_tasks: BackgroundTasks = None
):
    """Delete all files for a specific date (entire folder)."""
    if not S3_BUCKET:
        logger.warning(
            "S3 bucket is not configured; deleting local folder for %s", date
        )
        return _delete_local_folder(date, delete_backup=delete_backup)

    try:
        logger.info(f"Deleting folder for date: {date}, delete_backup: {delete_backup}")
        prefix = f"uploads/{date}/"
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)

        if "Contents" not in response:
            logger.error(f"No files found for date: {date}")
            local_result = _delete_local_folder(date, delete_backup=delete_backup)
            local_result["s3_message"] = "No files found in S3; deleted local folder"
            return local_result

        objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
        deleted_count = len(objects_to_delete)

        if objects_to_delete:
            batch_size = 1000
            for i in range(0, len(objects_to_delete), batch_size):
                batch = objects_to_delete[i : i + batch_size]
                s3_client.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": batch})

        logger.info(f"Deleted {deleted_count} files for date: {date}")

        response_data = {
            "message": f"Deleted {deleted_count} files for date {date}",
            "date": date,
            "deleted_count": deleted_count,
            "backup_deleted": False,
        }

        if delete_backup and background_tasks:
            background_tasks.add_task(delete_backup_folder, date, deleted_count)
            response_data["backup_deletion"] = "scheduled"
            logger.info(f"Scheduled backup deletion for date: {date}")

        return response_data

    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as e:
        logger.error(
            "S3 folder delete error; deleting local folder for %s: %s",
            date,
            e,
            exc_info=True,
        )
        response_data = _delete_local_folder(date, delete_backup=delete_backup)
        response_data["s3_error"] = str(e)
        return response_data
    except Exception as e:
        logger.error(
            "Unexpected S3 folder delete error; deleting local folder for %s: %s",
            date,
            e,
            exc_info=True,
        )
        response_data = _delete_local_folder(date, delete_backup=delete_backup)
        response_data["s3_error"] = str(e)
        return response_data


async def send_file_to_bridge(
    file_path: str,
    original_filename: Optional[str] = None,
    ws_uri: Optional[str] = None,
    timeout: float = 30.0,
    send_as_binary: bool = False,
) -> Dict[str, Any]:
    """
    Send a file to the WebSocket-to-NATS bridge.

    Args:
        file_path: Path to the file to send
        original_filename: Original filename to preserve (optional)
        ws_uri: WebSocket URI (default: ws://websocket-server:6789)
                Use 'ws://localhost:6789' for local testing
        timeout: Connection and send timeout in seconds
        send_as_binary: If True, send as binary frame; if False, send as base64 JSON

    Returns:
        Dict with server response, e.g.:
        {
            "status": "queued",
            "len": 12345,
            "saved_file": "invoice.pdf"
        }

    Raises:
        FileNotFoundError: If file doesn't exist
        websockets.exceptions.WebSocketException: On connection/send errors
        asyncio.TimeoutError: If operation times out
        json.JSONDecodeError: If server response is invalid
    """
    # Default to Docker service name for container environments
    if ws_uri is None:
        ws_uri = os.getenv("WEBSOCKET_BRIDGE_URI", "ws://websocket-server:6789")

    # Validate file exists
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # FIX 4: Use original filename if provided, otherwise use basename
    file_name = original_filename if original_filename else os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    logger.info("Sending file to WebSocket bridge: %s (%d bytes)", file_name, file_size)
    logger.debug("WebSocket URI: %s", ws_uri)
    logger.debug("Send mode: %s", "binary" if send_as_binary else "base64-JSON")

    try:
        # Connect to WebSocket server with timeout
        async with websockets.connect(ws_uri, open_timeout=timeout) as websocket:
            logger.debug("✓ Connected to WebSocket server")

            # Read file contents
            with open(file_path, "rb") as f:
                file_data = f.read()

            logger.info(f"Read {len(file_data)} bytes from file")

            # Send file based on mode
            if send_as_binary:
                # Send as raw binary frame
                logger.debug("Sending binary frame...")
                await websocket.send(file_data)

            else:
                # Send as base64-encoded JSON
                logger.debug("Encoding file as base64...")
                encoded_data = base64.b64encode(file_data).decode("utf-8")

                payload = {"fileName": file_name, "data": encoded_data}

                logger.debug("Sending JSON payload with fileName: %s", file_name)
                await websocket.send(json.dumps(payload))

            logger.debug("Waiting for server acknowledgment...")
            response = {
                "status": "queued",
                "note": "File forwarded to bridge (no ACK expected)",
            }

            # Parse response
            if isinstance(response, str):
                try:
                    ack = json.loads(response)
                    logger.debug("Server response JSON: %s", ack)
                except json.JSONDecodeError as e:
                    # FIX 6: Treat non-JSON text responses as successful acknowledgment
                    # Many WebSocket servers return simple text confirmations like "OK" or server info
                    logger.info(
                        f"WebSocket server response (non-JSON): {response[:200]}"
                    )
                    ack = {
                        "status": "queued",  # Treat as success if we got a response
                        "server_response": str(response)[:200],
                        "note": "Server returned text instead of JSON - treating as acknowledgment",
                    }
            else:
                # FIX 5: Handle binary responses - also treat as success
                logger.info(
                    f"WebSocket server sent binary response ({len(response)} bytes)"
                )
                ack = {
                    "status": "queued",  # Treat as success
                    "binary_response_size": len(response),
                    "note": "Server returned binary response - treating as acknowledgment",
                }

            # Check response status
            if ack.get("status") == "queued":
                logger.info("✓ File queued successfully: %s", file_name)
            elif ack.get("status") == "nack":
                reason = ack.get("reason", "unknown")
                logger.warning("✗ File rejected by server: %s", reason)
            else:
                logger.warning(
                    "⚠ Unexpected server response status: %s", ack.get("status")
                )

            return ack

    except websockets.exceptions.InvalidURI as e:
        logger.error("Invalid WebSocket URI: %s", ws_uri)
        raise

    except websockets.exceptions.WebSocketException as e:
        logger.error("WebSocket error: %s", e)
        raise

    except Exception as e:
        logger.error("Unexpected error sending file: %s", e, exc_info=True)
        raise


async def send_text(text):
    payload = {"type": "text", "message": text}

    async with websockets.connect(FORWARD_URL) as ws:
        print("📡 Connected (text mode)")
        await ws.send(json.dumps(payload))
        print("💬 Message sent — closing connection")

        await asyncio.sleep(0.1)
        await ws.close()
