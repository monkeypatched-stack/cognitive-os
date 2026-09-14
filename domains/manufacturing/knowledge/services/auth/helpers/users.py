from typing import Optional
import bcrypt
import re
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.auth.models.users import (
    UserEntryCreate,
    UserEntryResponse,
    UserEntryUpdate,
)

COLLECTION = "users"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    results = [_serialize(d) async for d in cursor]
    return results, total


async def get_by_id(db: AsyncIOMotorDatabase, user_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"user_id": user_id})
    return _serialize(doc) if doc else None


async def get_by_employee_id(
    db: AsyncIOMotorDatabase, employee_id: str
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"employee_id": employee_id})
    return _serialize(doc) if doc else None


async def get_by_department(db: AsyncIOMotorDatabase, department: str) -> list[dict]:
    cursor = db[COLLECTION].find({"department": department})
    return [_serialize(d) async for d in cursor]


async def get_by_team(db: AsyncIOMotorDatabase, team: str) -> list[dict]:
    cursor = db[COLLECTION].find({"team": team})
    return [_serialize(d) async for d in cursor]


async def get_by_role(db: AsyncIOMotorDatabase, role_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"role_id": role_id})
    return [_serialize(d) async for d in cursor]


async def get_by_email(db: AsyncIOMotorDatabase, email: str) -> list[dict]:
    normalized_email = email.strip().lower()
    cursor = db[COLLECTION].find({"email": normalized_email})
    return [_serialize(d) async for d in cursor]


async def get_by_username(db: AsyncIOMotorDatabase, username: str):
    cursor = db[COLLECTION].find({"name": username})
    return [_serialize(d) async for d in cursor]


# ── Create ────────────────────────────────────────────────────────────────────


async def create(db: AsyncIOMotorDatabase, data: UserEntryCreate) -> dict:
    doc = data.model_dump()
    password = data.password.get_secret_value()
    validate_password_policy(password)
    doc["password"] = _hash(password)
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase,
    user_id: str,
    data: UserEntryUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, user_id)
    password = fields.get("password")
    if password is None:
        fields.pop("password", None)
    else:
        password_value = (
            password.get_secret_value()
            if hasattr(password, "get_secret_value")
            else str(password)
        )
        validate_password_policy(password_value)
        fields["password"] = _hash(password_value)
    if not fields:
        return await get_by_id(db, user_id)
    result = await db[COLLECTION].find_one_and_update(
        {"user_id": user_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete(db: AsyncIOMotorDatabase, user_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"user_id": user_id})
    return result.deleted_count == 1


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def validate_password_policy(password: str) -> None:
    checks = [
        (len(password) >= 12, "at least 12 characters"),
        (re.search(r"[A-Z]", password), "an uppercase letter"),
        (re.search(r"[a-z]", password), "a lowercase letter"),
        (re.search(r"\d", password), "a number"),
        (re.search(r"[^A-Za-z0-9]", password), "a special character"),
    ]
    missing = [message for ok, message in checks if not ok]
    if missing:
        raise ValueError("Password must contain " + ", ".join(missing))
