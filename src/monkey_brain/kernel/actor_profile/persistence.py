"""Actor Profile Persistence — Wires Profile/Account to ActorStateStore.

Provides:
- Save/load Profile to MongoDB via ActorStateStore
- Save/load Account to MongoDB via ActorStateStore
- Save/load LoginInfo to MongoDB via ActorStateStore
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("agentos.actor_profile.persistence")


class ActorProfilePersistence:
    """Persistence layer for Actor Profile/Account/LoginInfo.

    Uses ActorStateStore's MongoDB connection for storage.
    """

    def __init__(self, db_connection_pool: Any = None):
        self._db = db_connection_pool
        self._collection_name = "actor_profiles"
        self._schema_initialized = False

    def _init_schema(self) -> None:
        """Initialize MongoDB collection."""
        if self._schema_initialized:
            return

        try:
            db = self._db.get_db()
            collection = db[self._collection_name]
            collection.create_index([("actor_id", 1)], unique=True)
            collection.create_index([("username", 1)])
            collection.create_index([("email", 1)])
            self._schema_initialized = True
            logger.info("ActorProfilePersistence schema initialized")
        except Exception as e:
            logger.error("Schema init failed: %s", e)

    def save_profile(self, actor_id: str, profile: Any) -> None:
        """Save actor profile to MongoDB."""
        if self._db is None:
            logger.warning("No database connection, skipping save")
            return

        try:
            self._init_schema()
            db = self._db.get_db()
            collection = db[self._collection_name]

            data = profile.to_dict()
            data["actor_id"] = actor_id
            data["updated_at"] = time.time()

            collection.update_one(
                {"actor_id": actor_id},
                {"$set": {"profile": data}},
                upsert=True,
            )
            logger.debug("Saved profile for actor %s", actor_id)
        except Exception as e:
            logger.error("Failed to save profile: %s", e)

    def load_profile(self, actor_id: str) -> dict | None:
        """Load actor profile from MongoDB."""
        if self._db is None:
            return None

        try:
            self._init_schema()
            db = self._db.get_db()
            collection = db[self._collection_name]

            doc = collection.find_one({"actor_id": actor_id})
            if doc and "profile" in doc:
                return doc["profile"]
            return None
        except Exception as e:
            logger.error("Failed to load profile: %s", e)
            return None

    def save_account(self, actor_id: str, account: Any) -> None:
        """Save actor account to MongoDB."""
        if self._db is None:
            logger.warning("No database connection, skipping save")
            return

        try:
            self._init_schema()
            db = self._db.get_db()
            collection = db[self._collection_name]

            data = account.to_dict()
            data["actor_id"] = actor_id
            data["updated_at"] = time.time()

            collection.update_one(
                {"actor_id": actor_id},
                {"$set": {"account": data}},
                upsert=True,
            )
            logger.debug("Saved account for actor %s", actor_id)
        except Exception as e:
            logger.error("Failed to save account: %s", e)

    def load_account(self, actor_id: str) -> dict | None:
        """Load actor account from MongoDB."""
        if self._db is None:
            return None

        try:
            self._init_schema()
            db = self._db.get_db()
            collection = db[self._collection_name]

            doc = collection.find_one({"actor_id": actor_id})
            if doc and "account" in doc:
                return doc["account"]
            return None
        except Exception as e:
            logger.error("Failed to load account: %s", e)
            return None

    def save_login_info(self, actor_id: str, login_info: Any) -> None:
        """Save actor login info to MongoDB."""
        if self._db is None:
            logger.warning("No database connection, skipping save")
            return

        try:
            self._init_schema()
            db = self._db.get_db()
            collection = db[self._collection_name]

            data = login_info.to_dict()
            data["actor_id"] = actor_id
            data["updated_at"] = time.time()

            collection.update_one(
                {"actor_id": actor_id},
                {"$set": {"login_info": data}},
                upsert=True,
            )
            logger.debug("Saved login info for actor %s", actor_id)
        except Exception as e:
            logger.error("Failed to save login info: %s", e)

    def load_login_info(self, actor_id: str) -> dict | None:
        """Load actor login info from MongoDB."""
        if self._db is None:
            return None

        try:
            self._init_schema()
            db = self._db.get_db()
            collection = db[self._collection_name]

            doc = collection.find_one({"actor_id": actor_id})
            if doc and "login_info" in doc:
                return doc["login_info"]
            return None
        except Exception as e:
            logger.error("Failed to load login info: %s", e)
            return None

    def save_actor(self, actor: Any) -> None:
        """Save all actor data (profile, account, login_info)."""
        actor_id = actor.entity_id

        if hasattr(actor, "profile") and actor.profile:
            self.save_profile(actor_id, actor.profile)

        if hasattr(actor, "account") and actor.account:
            self.save_account(actor_id, actor.account)

        if hasattr(actor, "login_info") and actor.login_info:
            self.save_login_info(actor_id, actor.login_info)

        logger.info("Saved actor %s", actor_id)

    def load_actor_data(self, actor_id: str) -> dict[str, Any]:
        """Load all actor data."""
        return {
            "profile": self.load_profile(actor_id),
            "account": self.load_account(actor_id),
            "login_info": self.load_login_info(actor_id),
        }


_actor_profile_persistence: "ActorProfilePersistence | None" = None


def get_actor_profile_persistence() -> ActorProfilePersistence:
    """Lazy, process-wide singleton — same shape as persistence/db_pool.py
    ::get_db_pool() (itself lazily constructed the same way) and
    kernel/domains/razorpay_upi_provider.py::get_default_provider(). Reuses
    the SAME real Mongo connection pool kernel/society/integration.py's
    confirmed-live belief-persistence path already establishes for
    ActorStateStore, rather than opening a second, separate connection —
    this was the actual, real gap keeping this whole module dead:
    api/routes/actor_profile.py's GET/PUT /profile routes were genuine
    stubs with no persistence at all (their own docstring says so
    explicitly), and this class already existed to fill exactly that,
    just never had anywhere real to get a database handle from until
    wired to the same get_db_pool() singleton.

    get_db_pool() raises if Mongo is genuinely unreachable (DBPool.
    connect() re-raises) — degrades to a pool-less ActorProfilePersistence
    instead (its own save_profile/load_profile already handle
    self._db is None by skipping, same non-fatal shape
    integration.py::get_actor_state_store already uses for the identical
    real failure mode)."""
    global _actor_profile_persistence
    if _actor_profile_persistence is None:
        try:
            from src.monkey_brain.persistence.db_pool import get_db_pool

            _actor_profile_persistence = ActorProfilePersistence(get_db_pool())
        except Exception as exc:
            logger.warning(
                "get_actor_profile_persistence: Mongo unavailable, profile persistence disabled: %s",
                exc,
            )
            _actor_profile_persistence = ActorProfilePersistence(None)
    return _actor_profile_persistence
