#!/usr/bin/env python3
"""
Migration: industrial_workers → users collection
- Generates email from worker name (first.last@company.com)
- Generates a UNIQUE random password per user (never a shared default) and
  flags each account for forced reset on first login
- Maps role name → role_id from roles collection
- Sets is_active=True for all migrated users

The generated plaintext credentials are written to a local, owner-only file
(CREDENTIALS_OUTFILE) for the operator to distribute out-of-band. They are
never stored in the database — only bcrypt hashes are.
"""

import os
import re
import secrets
import stat
import sys
from datetime import datetime, timezone

import bcrypt
from pymongo import MongoClient

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "industrial_db_development"
EMAIL_DOMAIN = "company.com"
CREDENTIALS_OUTFILE = os.getenv("CREDENTIALS_OUTFILE", "migrated_user_credentials.txt")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def generate_password() -> str:
    """A fresh, high-entropy password for a single migrated user."""
    return secrets.token_urlsafe(18)


def name_to_email(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z\s]", "", name).strip().lower()
    parts = clean.split()
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[-1]}@{EMAIL_DOMAIN}"
    return f"{parts[0]}@{EMAIL_DOMAIN}"


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # Build role name → role_id mapping
    roles = {}
    for r in db["roles"].find({}, {"role": 1, "role_id": 1}):
        roles[r["role"]] = r["role_id"]
    print(f"Found {len(roles)} roles in roles collection")

    workers = list(db["industrial_workers"].find({}))
    print(f"Found {len(workers)} workers in industrial_workers collection")

    if not workers:
        print("No workers to migrate")
        sys.exit(0)

    now = datetime.now(timezone.utc)
    users_to_insert = []
    skipped = []
    credentials: list[tuple[str, str, str]] = []  # (email, user_id, plaintext)

    for w in workers:
        role_name = w.get("role")
        role_id = roles.get(role_name)

        if not role_id:
            skipped.append(f"{w['worker_id']} ({role_name})")
            continue

        plaintext = generate_password()
        email = name_to_email(w["name"])
        user = {
            "user_id": w["worker_id"],
            "employee_id": w["worker_id"],
            "name": w["name"],
            "department": w.get("department"),
            "team": None,
            "email": email,
            "phone": None,
            "is_active": True,
            "role": role_id,
            "password": hash_password(plaintext),
            # Force a password change on first login — the migration-issued
            # password is a one-time bootstrap credential, not a standing one.
            "must_reset_password": True,
            "created_at": now,
            "updated_at": now,
        }
        users_to_insert.append(user)
        credentials.append((email, w["worker_id"], plaintext))

    if skipped:
        print(f"Skipping {len(skipped)} workers with unmatched roles:")
        for s in skipped:
            print(f"  - {s}")

    if not users_to_insert:
        print("No valid users to insert")
        sys.exit(1)

    # Drop existing users collection if it exists, then insert
    if "users" in db.list_collection_names():
        existing_count = db["users"].count_documents({})
        print(f"users collection already has {existing_count} documents")
        confirm = input("Drop and recreate? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Aborted")
            sys.exit(0)
        db["users"].drop()
        print("Dropped existing users collection")

    result = db["users"].insert_many(users_to_insert)
    print(f"Inserted {len(result.inserted_ids)} users into users collection")

    # Write the one-time credentials to an owner-only file for out-of-band
    # distribution. Never printed to stdout in bulk, never stored in the DB.
    fd = os.open(
        CREDENTIALS_OUTFILE,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        stat.S_IRUSR | stat.S_IWUSR,
    )
    with os.fdopen(fd, "w") as fh:
        fh.write("# One-time migration passwords — users must reset on first login.\n")
        fh.write("# email\tuser_id\tpassword\n")
        for email, user_id, plaintext in credentials:
            fh.write(f"{email}\t{user_id}\t{plaintext}\n")
    print(f"Wrote {len(credentials)} one-time credentials to {CREDENTIALS_OUTFILE} (mode 0600)")
    print("Distribute these out-of-band, then delete the file. Each user must reset on first login.")

    # Verify
    count = db["users"].count_documents({})
    print(f"Verification: users collection now has {count} documents")

    client.close()


if __name__ == "__main__":
    main()
