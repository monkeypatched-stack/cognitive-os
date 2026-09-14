from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from services.common.compliance import (
    COMPLIANCE_EXCLUDED_COLLECTIONS,
    append_audit_entry,
    append_record_version,
    attach_entity_compliance_metadata,
    attach_entity_compliance_update,
    document_record_id,
    enforce_retention,
)
from services.common.config import settings
from services.common.cdc import publish_cdc_event
from services.common.embeddings import enrich_document_embedding
from services.common.neo4j_mirror import (
    cleanup_neo4j_bloat,
    close_neo4j_mirror_driver,
    mirror_deleted_document,
    mirror_document,
    mirror_write_operation,
    safe_mirror,
)

client: AsyncIOMotorClient | None = None
database = None


class MirroredCollection:
    def __init__(self, collection):
        self._collection = collection
        self.name = collection.name
        self._raw_db = collection.database

    def __getattr__(self, name):
        return getattr(self._collection, name)

    async def find_one(self, filter=None, *args, **kwargs):
        # First, attempt to find the Mongo document and check for a mem0_state_id
        doc = await self._collection.find_one(filter, *args, **kwargs)
        if not doc:
            return None
        mem0_id = doc.get("mem0_state_id")
        if mem0_id:
            try:
                from src.monkey_brain.memory.persistence_integration import (
                    load_mem0_state,
                )

                state = await load_mem0_state(mem0_id)
                if state and isinstance(state, dict) and state.get("raw_state"):
                    # Return the mem0 raw_state as the authoritative record
                    return state.get("raw_state")
            except Exception:
                # If mem0 lookup fails, fall back to the Mongo document
                pass
        return doc

    def with_options(self, *args, **kwargs):
        return MirroredCollection(self._collection.with_options(*args, **kwargs))

    def _compliance_enabled(self) -> bool:
        return self.name not in COMPLIANCE_EXCLUDED_COLLECTIONS

    async def _emit_event(
        self, operation: str, document: dict | None, extra: dict | None = None
    ) -> None:
        """Emit an event into the event store and attempt best-effort propagation tracking.

        This will record a document in `event_store` containing transaction id, timestamp,
        entity id, and propagated_to list which is updated as downstream systems succeed.
        """
        try:
            from datetime import datetime
            from uuid import uuid4

            event = {
                "event": f"{self.name.upper()}_{operation.upper()}",
                "collection": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "transaction_id": str(uuid4()),
                "propagated_to": [],
                "entity": None,
                "document_id": None,
                "payload": extra or {},
            }

            if isinstance(document, dict):
                # Try to compute a stable entity id using neo4j mirror helper if available
                try:
                    from services.common.neo4j_mirror import _document_key

                    entity_id = _document_key(self.name, document)
                    event["entity"] = entity_id
                except Exception:
                    pass
                # document id if present
                if document.get("_id") is not None:
                    try:
                        event["document_id"] = str(document.get("_id"))
                    except Exception:
                        event["document_id"] = document.get("_id")

            # Mongo write already happened for the caller; mark mongo as propagated
            event["propagated_to"].append("mongo")

            # Attempt embedding propagation if embedding exists
            try:
                if isinstance(document, dict) and document.get("embedding") is not None:
                    event["propagated_to"].append("embedding")
            except Exception:
                pass

            # Attempt Neo4j propagation (best-effort)
            try:
                if isinstance(document, dict):
                    from services.common.neo4j_mirror import mirror_document

                    try:
                        await mirror_document(self.name, document, operation)
                        event["propagated_to"].append("neo4j")
                    except Exception:
                        # mirror_document may raise; swallow to avoid breaking the write
                        pass
            except Exception:
                pass

            # Attempt Mem0 snapshot (best-effort)
            try:
                if isinstance(document, dict):
                    try:
                        # Import lazily to avoid circular imports during startup
                        from src.monkey_brain.memory.persistence_integration import (
                            snapshot_world_state_to_mem0,
                        )

                        mem0_state = {
                            "type": "entity_snapshot",
                            "collection": self.name,
                            "document_id": event.get("document_id"),
                            "raw_state": document,
                        }
                        state_id = await snapshot_world_state_to_mem0(mem0_state)
                        if state_id:
                            event["propagated_to"].append("mem0")
                            event.setdefault("mem0_state_ids", []).append(state_id)
                            # Best-effort: write mem0_state_id back into the Mongo document so reads can prefer Mem0
                            try:
                                doc_filter = None
                                # Prefer explicit document id if available
                                if event.get("document_id"):
                                    doc_filter = {"_id": event.get("document_id")}
                                elif document.get("_id") is not None:
                                    doc_filter = {"_id": document.get("_id")}
                                if doc_filter:
                                    try:
                                        await self._raw_db[self.name].update_one(
                                            doc_filter,
                                            {"$set": {"mem0_state_id": state_id}},
                                        )
                                    except Exception:
                                        # best-effort: ignore failures
                                        pass
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass

            # Persist the event to the event_store collection (best-effort)
            try:
                await self._raw_db["event_store"].insert_one(event)
            except Exception:
                # Do not raise – event store failure shouldn't break core operation
                pass
        except Exception:
            # Keep tolerant: do not allow event emission to affect main DB writes
            return

    async def _record_audit(self, **kwargs) -> None:
        if not self._compliance_enabled():
            return
        await append_audit_entry(self._raw_db, collection=self.name, **kwargs)

    async def _record_version(self, document, action: str) -> None:
        if self._compliance_enabled() and isinstance(document, dict):
            await append_record_version(
                self._raw_db, collection=self.name, record=document, action=action
            )

    async def _enforce_retention(self, document) -> None:
        if self._compliance_enabled() and isinstance(document, dict):
            try:
                await enforce_retention(
                    self._raw_db, collection=self.name, record=document
                )
            except PermissionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED, detail=str(exc)
                ) from exc

    async def _cleanup_bloat(self) -> None:
        await safe_mirror(cleanup_neo4j_bloat())

    async def _persist_embedding(self, document) -> dict | None:
        if not isinstance(document, dict):
            return None
        document_id = document.get("_id")
        if document_id is None:
            return None
        enriched = enrich_document_embedding(self.name, document)
        await self._collection.update_one(
            {"_id": document_id},
            {"$set": {"embedding": enriched["embedding"]}},
        )
        return enriched

    async def insert_one(self, document, *args, **kwargs):
        document = attach_entity_compliance_metadata(
            self.name, document, action="insert_one"
        )
        document = enrich_document_embedding(self.name, document)
        result = await self._collection.insert_one(document, *args, **kwargs)
        await self._record_version(document, "insert_one")
        await self._record_audit(
            action="insert_one",
            record_id=document_record_id(document, result.inserted_id),
            after=document,
            result={"inserted_id": result.inserted_id},
        )
        await safe_mirror(mirror_document(self.name, document, "insert_one"))
        await publish_cdc_event(
            collection=self.name,
            operation="insert_one",
            record=document,
            after=document,
            result={"inserted_id": result.inserted_id},
        )
        await self._cleanup_bloat()
        # Event sourcing: record the mutation and propagate metadata
        try:
            await self._emit_event(
                "insert", document, extra={"result_id": str(result.inserted_id)}
            )
        except Exception:
            pass
        return result

    async def insert_many(self, documents, *args, **kwargs):
        documents = [
            attach_entity_compliance_metadata(self.name, document, action="insert_many")
            for document in documents
        ]
        documents = [
            enrich_document_embedding(self.name, document) for document in documents
        ]
        result = await self._collection.insert_many(documents, *args, **kwargs)
        for document, inserted_id in zip(documents, result.inserted_ids):
            await self._record_version(document, "insert_many")
            await self._record_audit(
                action="insert_many",
                record_id=document_record_id(document, inserted_id),
                after=document,
                result={"inserted_id": inserted_id},
            )
        for document in documents[: settings.NEO4J_MIRROR_MAX_DOCUMENTS]:
            await safe_mirror(mirror_document(self.name, document, "insert_many"))
            await publish_cdc_event(
                collection=self.name,
                operation="insert_many",
                record=document,
                after=document,
            )
        if len(documents) > settings.NEO4J_MIRROR_MAX_DOCUMENTS:
            await safe_mirror(
                mirror_write_operation(
                    self.name,
                    "insert_many_truncated",
                    result=result,
                    update={"document_count": len(documents)},
                )
            )
        await self._cleanup_bloat()
        try:
            # Emit a bulk insert event with the count
            await self._emit_event("insert_many", None, extra={"count": len(documents)})
        except Exception:
            pass
        return result

    async def find_one_and_update(self, filter, update, *args, **kwargs):
        before = (
            await self._collection.find_one(filter)
            if self._compliance_enabled()
            else None
        )
        update = attach_entity_compliance_update(
            self.name, update, action="find_one_and_update"
        )
        result = await self._collection.find_one_and_update(
            filter, update, *args, **kwargs
        )
        await safe_mirror(
            mirror_write_operation(
                self.name, "find_one_and_update", filter, update, result
            )
        )
        if result:
            document = await self._find_after_document(filter, result)
            if document:
                enriched = await self._persist_embedding(document)
                if enriched:
                    result = enriched
                    document = enriched
                await self._record_audit(
                    action="find_one_and_update",
                    record_id=document_record_id(document),
                    before=before,
                    after=document,
                    filter=filter,
                    update=update,
                )
                await self._record_version(document, "find_one_and_update")
                await safe_mirror(
                    mirror_document(self.name, document, "find_one_and_update")
                )
                await publish_cdc_event(
                    collection=self.name,
                    operation="find_one_and_update",
                    record=document,
                    before=before,
                    after=document,
                    filter=filter,
                    update=update,
                )
        await self._cleanup_bloat()
        try:
            await self._emit_event("update", document, extra={"filter": filter})
        except Exception:
            pass
        return result

    async def find_one_and_replace(self, filter, replacement, *args, **kwargs):
        before = (
            await self._collection.find_one(filter)
            if self._compliance_enabled()
            else None
        )
        replacement = attach_entity_compliance_metadata(
            self.name, replacement, action="find_one_and_replace"
        )
        replacement = enrich_document_embedding(self.name, replacement)
        result = await self._collection.find_one_and_replace(
            filter, replacement, *args, **kwargs
        )
        await safe_mirror(
            mirror_write_operation(
                self.name, "find_one_and_replace", filter, replacement, result
            )
        )
        if result:
            document = await self._find_after_document(filter, result)
            if document:
                await self._record_audit(
                    action="find_one_and_replace",
                    record_id=document_record_id(document),
                    before=before,
                    after=document,
                    filter=filter,
                )
                await self._record_version(document, "find_one_and_replace")
                await safe_mirror(
                    mirror_document(self.name, document, "find_one_and_replace")
                )
                await publish_cdc_event(
                    collection=self.name,
                    operation="find_one_and_replace",
                    record=document,
                    before=before,
                    after=document,
                    filter=filter,
                )
        await self._cleanup_bloat()
        try:
            await self._emit_event("replace", document, extra={"filter": filter})
        except Exception:
            pass
        return result

    async def find_one_and_delete(self, filter, *args, **kwargs):
        before = await self._collection.find_one(filter)
        await self._enforce_retention(before)
        result = await self._collection.find_one_and_delete(filter, *args, **kwargs)
        if result:
            await self._record_audit(
                action="find_one_and_delete",
                record_id=document_record_id(result),
                before=result,
                filter=filter,
            )
        await safe_mirror(
            mirror_write_operation(
                self.name, "find_one_and_delete", filter, result=result
            )
        )
        if result:
            await safe_mirror(
                mirror_deleted_document(self.name, result, "find_one_and_delete")
            )
            await publish_cdc_event(
                collection=self.name,
                operation="find_one_and_delete",
                record=result,
                before=result,
                filter=filter,
            )
        await self._cleanup_bloat()
        try:
            await self._emit_event("delete", result, extra={"filter": filter})
        except Exception:
            pass
        return result

    async def update_one(self, filter, update, *args, **kwargs):
        before = (
            await self._collection.find_one(filter)
            if self._compliance_enabled()
            else None
        )
        update = attach_entity_compliance_update(self.name, update, action="update_one")
        result = await self._collection.update_one(filter, update, *args, **kwargs)
        await safe_mirror(
            mirror_write_operation(self.name, "update_one", filter, update, result)
        )
        if result.matched_count or result.upserted_id:
            document = await self._find_after_write(filter, result)
            if document:
                enriched = await self._persist_embedding(document)
                if enriched:
                    document = enriched
                await self._record_audit(
                    action="update_one",
                    record_id=document_record_id(document, result.upserted_id),
                    before=before,
                    after=document,
                    filter=filter,
                    update=update,
                    result={
                        "matched_count": result.matched_count,
                        "modified_count": result.modified_count,
                    },
                )
                await self._record_version(document, "update_one")
                await safe_mirror(mirror_document(self.name, document, "update_one"))
                await publish_cdc_event(
                    collection=self.name,
                    operation="update_one",
                    record=document,
                    before=before,
                    after=document,
                    filter=filter,
                    update=update,
                    result={
                        "matched_count": result.matched_count,
                        "modified_count": result.modified_count,
                    },
                )
        await self._cleanup_bloat()
        try:
            # Emit update event with filter/update info
            await self._emit_event(
                "update", document, extra={"filter": filter, "update": update}
            )
        except Exception:
            pass
        return result

    async def update_many(self, filter, update, *args, **kwargs):
        update = attach_entity_compliance_update(
            self.name, update, action="update_many"
        )
        before_documents = []
        if self._compliance_enabled():
            cursor = self._collection.find(filter).limit(
                settings.NEO4J_MIRROR_MAX_DOCUMENTS
            )
            async for document in cursor:
                before_documents.append(document)
        result = await self._collection.update_many(filter, update, *args, **kwargs)
        await safe_mirror(
            mirror_write_operation(self.name, "update_many", filter, update, result)
        )
        if result.modified_count:
            cursor = self._collection.find(filter).limit(
                settings.NEO4J_MIRROR_MAX_DOCUMENTS
            )
            before_by_id = {
                str(document.get("_id")): document
                for document in before_documents
                if isinstance(document, dict)
            }
            async for document in cursor:
                enriched = await self._persist_embedding(document)
                if enriched:
                    document = enriched
                before = (
                    before_by_id.get(str(document.get("_id")))
                    if isinstance(document, dict)
                    else None
                )
                await self._record_audit(
                    action="update_many",
                    record_id=document_record_id(document),
                    before=before,
                    after=document,
                    filter=filter,
                    update=update,
                    result={
                        "matched_count": result.matched_count,
                        "modified_count": result.modified_count,
                    },
                )
                await self._record_version(document, "update_many")
                await safe_mirror(mirror_document(self.name, document, "update_many"))
                await publish_cdc_event(
                    collection=self.name,
                    operation="update_many",
                    record=document,
                    before=before,
                    after=document,
                    filter=filter,
                    update=update,
                    result={
                        "matched_count": result.matched_count,
                        "modified_count": result.modified_count,
                    },
                )
        await self._cleanup_bloat()
        return result

    async def replace_one(self, filter, replacement, *args, **kwargs):
        before = (
            await self._collection.find_one(filter)
            if self._compliance_enabled()
            else None
        )
        replacement = attach_entity_compliance_metadata(
            self.name, replacement, action="replace_one"
        )
        replacement = enrich_document_embedding(self.name, replacement)
        result = await self._collection.replace_one(
            filter, replacement, *args, **kwargs
        )
        await safe_mirror(
            mirror_write_operation(
                self.name, "replace_one", filter, replacement, result
            )
        )
        if result.matched_count or result.upserted_id:
            document = await self._find_after_write(filter, result)
            if document:
                await self._record_audit(
                    action="replace_one",
                    record_id=document_record_id(document, result.upserted_id),
                    before=before,
                    after=document,
                    filter=filter,
                    result={
                        "matched_count": result.matched_count,
                        "modified_count": result.modified_count,
                    },
                )
                await self._record_version(document, "replace_one")
                await safe_mirror(mirror_document(self.name, document, "replace_one"))
                await publish_cdc_event(
                    collection=self.name,
                    operation="replace_one",
                    record=document,
                    before=before,
                    after=document,
                    filter=filter,
                    result={
                        "matched_count": result.matched_count,
                        "modified_count": result.modified_count,
                    },
                )
        await self._cleanup_bloat()
        return result

    async def bulk_write(self, requests, *args, **kwargs):
        requests = list(requests)
        result = await self._collection.bulk_write(requests, *args, **kwargs)
        result_summary = {
            "request_count": len(requests),
            "inserted_count": getattr(result, "inserted_count", 0),
            "matched_count": getattr(result, "matched_count", 0),
            "modified_count": getattr(result, "modified_count", 0),
            "deleted_count": getattr(result, "deleted_count", 0),
            "upserted_count": getattr(result, "upserted_count", 0),
        }
        if any(
            result_summary[key]
            for key in (
                "inserted_count",
                "matched_count",
                "modified_count",
                "deleted_count",
                "upserted_count",
            )
        ):
            await self._record_audit(
                action="bulk_write",
                update={
                    "request_count": len(requests),
                    "operation_types": [type(request).__name__ for request in requests],
                },
                result=result_summary,
            )
        await safe_mirror(
            mirror_write_operation(
                self.name,
                "bulk_write",
                update={"request_count": len(requests)},
                result=result,
            )
        )
        await self._cleanup_bloat()
        return result

    async def delete_one(self, filter, *args, **kwargs):
        document = await self._collection.find_one(filter)
        await self._enforce_retention(document)
        result = await self._collection.delete_one(filter, *args, **kwargs)
        if document and result.deleted_count:
            await self._record_audit(
                action="delete_one",
                record_id=document_record_id(document),
                before=document,
                filter=filter,
                result={"deleted_count": result.deleted_count},
            )
        await safe_mirror(
            mirror_write_operation(self.name, "delete_one", filter, result=result)
        )
        if document and result.deleted_count:
            await safe_mirror(
                mirror_deleted_document(self.name, document, "delete_one")
            )
            await publish_cdc_event(
                collection=self.name,
                operation="delete_one",
                record=document,
                before=document,
                filter=filter,
                result={"deleted_count": result.deleted_count},
            )
        await self._cleanup_bloat()
        return result

    async def delete_many(self, filter, *args, **kwargs):
        documents = []
        cursor = self._collection.find(filter).limit(
            settings.NEO4J_MIRROR_MAX_DOCUMENTS
        )
        async for document in cursor:
            documents.append(document)
        for document in documents:
            await self._enforce_retention(document)
        result = await self._collection.delete_many(filter, *args, **kwargs)
        if result.deleted_count:
            for document in documents:
                await self._record_audit(
                    action="delete_many",
                    record_id=document_record_id(document),
                    before=document,
                    filter=filter,
                    result={"deleted_count": result.deleted_count},
                )
        await safe_mirror(
            mirror_write_operation(self.name, "delete_many", filter, result=result)
        )
        if result.deleted_count:
            for document in documents:
                await safe_mirror(
                    mirror_deleted_document(self.name, document, "delete_many")
                )
                await publish_cdc_event(
                    collection=self.name,
                    operation="delete_many",
                    record=document,
                    before=document,
                    filter=filter,
                    result={"deleted_count": result.deleted_count},
                )
        await self._cleanup_bloat()
        return result

    async def _find_after_write(self, filter, result):
        upserted_id = getattr(result, "upserted_id", None)
        if upserted_id is not None:
            return await self._collection.find_one({"_id": upserted_id})
        return await self._collection.find_one(filter)

    async def _find_after_document(self, filter, document):
        document_id = document.get("_id") if isinstance(document, dict) else None
        if document_id is not None:
            return await self._collection.find_one({"_id": document_id})
        return await self._collection.find_one(filter)


def _tenant_wrap(collection):
    """Scope a collection to the request's tenant when one is in context; pass-through
    otherwise (system tasks). Single chokepoint — scopes every .find() site at once."""
    try:
        from services.common.tenant_scope import wrap

        return wrap(collection)
    except Exception:
        return collection


class MirroredDatabase:
    def __init__(self, db):
        self._db = db

    @property
    def raw_database(self):
        return self._db

    def __getitem__(self, name):
        return _tenant_wrap(MirroredCollection(self._db[name]))

    def __getattr__(self, name):
        value = getattr(self._db, name)
        if isinstance(value, AsyncIOMotorCollection):
            return _tenant_wrap(MirroredCollection(value))
        return value

    def get_collection(self, name, *args, **kwargs):
        return _tenant_wrap(
            MirroredCollection(self._db.get_collection(name, *args, **kwargs))
        )


def _redact_url(url: str) -> str:
    """Strip credentials before logging — a Mongo URL embeds them (mongodb://user:pass@host)."""
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit

        p = urlsplit(url)
        if not (p.username or p.password):
            return url
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        return urlunsplit((p.scheme, f"***@{host}", p.path, p.query, p.fragment))
    except Exception:
        return "<unparseable-url-redacted>"


def _mongo_options() -> dict:
    """Explicit timeouts: the driver default (serverSelectionTimeoutMS=30s) stalls EVERY
    operation for 30s when Mongo is unreachable, saturating the worker pool."""
    import os as _os

    return {
        "serverSelectionTimeoutMS": int(
            _os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")
        ),
        "connectTimeoutMS": int(_os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
        "socketTimeoutMS": int(_os.getenv("MONGO_SOCKET_TIMEOUT_MS", "30000")),
        "maxPoolSize": int(_os.getenv("MONGO_MAX_POOL_SIZE", "100")),
    }


async def connect_db():
    global client, database
    client = AsyncIOMotorClient(settings.MONGODB_URL, **_mongo_options())
    database = MirroredDatabase(client[settings.DB_NAME])
    # Was: print(f"... {settings.MONGODB_URL}") — that printed the DB CREDENTIALS to stdout.
    print(f"Connected to MongoDB at {_redact_url(settings.MONGODB_URL)}")


async def close_db():
    global client, database
    if client:
        client.close()
        client = None
        database = None
        print("MongoDB connection closed")
    await close_neo4j_mirror_driver()


def get_database():
    global client, database
    if database is None:
        client = AsyncIOMotorClient(settings.MONGODB_URL, **_mongo_options())
        database = MirroredDatabase(client[settings.DB_NAME])
    return database
