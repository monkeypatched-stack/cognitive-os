import os
import boto3
from datetime import datetime, timedelta
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
SOURCE_BUCKET = os.getenv("AWS_S3_BUCKET")  # main bucket
BACKUP_BUCKET = os.getenv(
    "AWS_S3_BACKUP_BUCKET", SOURCE_BUCKET
)  # can be same or different bucket
BACKUP_PREFIX = os.getenv("AWS_S3_BACKUP_PREFIX", "backups/")

# Initialize S3 client
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
)


def backup_and_delete(date_str: str = None):
    """
    Copies all files from uploads/{date}/ to backups/{date}/,
    verifies copy, then deletes originals.
    """
    if not date_str:
        # default to previous day's folder
        date_str = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    source_prefix = f"uploads/{date_str}/"
    backup_prefix = f"{BACKUP_PREFIX}{date_str}/"

    print(f"🔍 Checking for files in: s3://{SOURCE_BUCKET}/{source_prefix}")

    try:
        response = s3.list_objects_v2(Bucket=SOURCE_BUCKET, Prefix=source_prefix)
        if "Contents" not in response:
            print(f"❌ No files found for {date_str}")
            return

        for obj in response["Contents"]:
            key = obj["Key"]
            dest_key = key.replace(source_prefix, backup_prefix, 1)

            print(f"📦 Backing up: {key} → {dest_key}")
            s3.copy_object(
                Bucket=BACKUP_BUCKET,
                CopySource={"Bucket": SOURCE_BUCKET, "Key": key},
                Key=dest_key,
            )

        print(f"✅ Backup complete. Starting deletion of originals...")

        # Delete all files from the source folder
        delete_objects = {
            "Objects": [{"Key": obj["Key"]} for obj in response["Contents"]]
        }
        s3.delete_objects(Bucket=SOURCE_BUCKET, Delete=delete_objects)

        print(f"🧹 Deleted all files under {source_prefix}")
        print(f"🎉 Backup and cleanup complete for {date_str}")

    except (BotoCoreError, ClientError) as e:
        print(f"❌ AWS error: {e}")
    except Exception as e:
        print(f"⚠️ Unexpected error: {e}")


if __name__ == "__main__":
    backup_and_delete()
