"""Storage Capabilities — file system and cloud storage integrations."""

from __future__ import annotations

import os
from cerebellum.capability import Capability


class LocalFileSystemCapability(Capability):
    """Local file system operations."""

    def __init__(self, base_path: str = "/"):
        super().__init__(name="local_filesystem")
        self._base_path = base_path

    async def execute(self, state, **kwargs):
        operation = state.get("operation", "read")
        # Resolve and confine to base_path — os.path.join with an absolute
        # subpath silently discards the base, enabling path traversal.
        raw = os.path.normpath(os.path.join(self._base_path, state.get("path", "")))
        if not raw.startswith(os.path.abspath(self._base_path)):
            return {"status": "error", "error": "path escapes base directory"}
        path = raw

        if operation == "read":
            with open(path, "r") as f:
                return {"content": f.read(), "path": path}
        elif operation == "write":
            with open(path, "w") as f:
                f.write(state.get("content", ""))
            return {"status": "written", "path": path}
        elif operation == "list":
            files = os.listdir(path)
            return {"files": files, "count": len(files)}
        elif operation == "exists":
            return {"exists": os.path.exists(path)}
        return {"status": "unknown_operation"}


class S3Capability(Capability):
    """Amazon S3 integration via boto3."""

    def __init__(self, bucket: str = "", region: str = "us-east-1"):
        super().__init__(name="s3")
        self._bucket = bucket
        self._region = region

    async def execute(self, state, **kwargs):
        import boto3

        s3 = boto3.client("s3", region_name=self._region)
        operation = state.get("operation", "list_objects")

        if operation == "list_objects":
            response = s3.list_objects_v2(Bucket=self._bucket, Prefix=state.get("prefix", ""))
            return {"objects": [obj["Key"] for obj in response.get("Contents", [])]}
        elif operation == "get_object":
            response = s3.get_object(Bucket=self._bucket, Key=state.get("key", ""))
            return {"content": response["Body"].read().decode("utf-8")}
        elif operation == "put_object":
            s3.put_object(
                Bucket=self._bucket,
                Key=state.get("key", ""),
                Body=state.get("body", ""),
            )
            return {"status": "uploaded"}
        return {"status": "unknown_operation"}


class AzureBlobCapability(Capability):
    """Azure Blob Storage integration."""

    def __init__(self, connection_string: str = "", container: str = ""):
        super().__init__(name="azure_blob")
        self._connection_string = connection_string
        self._container = container

    async def execute(self, state, **kwargs):
        return {
            "status": "configured",
            "storage": "azure_blob",
            "container": self._container,
        }


class GCSCapability(Capability):
    """Google Cloud Storage integration."""

    def __init__(self, bucket: str = ""):
        super().__init__(name="gcs")
        self._bucket = bucket

    async def execute(self, state, **kwargs):
        return {"status": "configured", "storage": "gcs", "bucket": self._bucket}


class MinioCapability(Capability):
    """MinIO S3-compatible storage integration."""

    def __init__(self, endpoint: str = "", access_key: str = "", secret_key: str = ""):
        super().__init__(name="minio")
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key

    async def execute(self, state, **kwargs):
        return {"status": "configured", "storage": "minio"}
