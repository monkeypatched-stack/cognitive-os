"""Policy Control Plane (PCP) — authoritative policy distribution hub.

Spec: "The PCP is the source of truth for role-permission mappings, workload
identity bindings, and cross-domain trust relationships."

Responsibilities:
  1. Canonical role-permission store   — resolves via hierarchical RBAC
  2. OPA bundle distribution           — fetches and caches OPA policy bundles
  3. NATS-based policy push            — distributes invalidation events to mesh
  4. Cross-domain trust registry       — federation trust anchors

All operations fall back gracefully so the PCP never blocks the request path.
Activation requires MongoDB + optional NATS. OPA bundle push requires OPA_URL.

Typical usage (lifespan):
    from services.common.policy_control_plane import PolicyControlPlane
    pcp = PolicyControlPlane(db=db)
    await pcp.start()

Or via dependency:
    from services.common.policy_control_plane import get_pcp
    pcp = await get_pcp(db=Depends(get_database))
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class TrustDomain:
    """A trusted external mesh domain that may exchange tokens with this mesh."""

    def __init__(
        self,
        domain_id: str,
        name: str,
        spiffe_trust_domain: str,
        jwks_url: str = "",
        allowed_scopes: list[str] | None = None,
    ):
        self.domain_id = domain_id
        self.name = name
        self.spiffe_trust_domain = spiffe_trust_domain
        self.jwks_url = jwks_url
        self.allowed_scopes: list[str] = allowed_scopes or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "name": self.name,
            "spiffe_trust_domain": self.spiffe_trust_domain,
            "jwks_url": self.jwks_url,
            "allowed_scopes": self.allowed_scopes,
        }


# ---------------------------------------------------------------------------
# Policy Control Plane
# ---------------------------------------------------------------------------


class PolicyControlPlane:
    """Central policy authority for the mesh.

    Reads from MongoDB for persistence; publishes to NATS for mesh-wide
    invalidation; optionally refreshes OPA bundle on schedule.
    """

    _COLLECTION_ROLES = "roles"
    _COLLECTION_TRUST = "pcp_trust_domains"
    _COLLECTION_BUNDLES = "pcp_opa_bundles"
    _NATS_POLICY_SUBJECT = "indus.policy.invalidate"

    def __init__(self, db=None) -> None:
        self._db = db
        self._nats = None
        self._bundle_cache: dict[str, dict] = {}
        self._trust_cache: dict[str, TrustDomain] = {}
        self._bundle_refresh_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Connect to NATS and begin background bundle refresh if OPA_URL is set."""
        await self._connect_nats()
        if os.getenv("OPA_URL"):
            interval = int(os.getenv("PCP_BUNDLE_REFRESH_SECONDS", "300"))

            # refresh the bundle every N second
            self._bundle_refresh_task = asyncio.create_task(
                self._bundle_refresh_loop(interval)
            )
            logger.info("PCP: OPA bundle refresh started (interval=%ds)", interval)

        # Sync role-permission policies into the cingulate PolicyRegistry (governance authority)
        try:
            from src.cingulate.governance.policy_registry import get_policy_registry

            registry = get_policy_registry()
            synced = await registry.sync_from_pcp(db=self._db)
            logger.info(
                "PCP: synced %d role policies into cingulate PolicyRegistry", synced
            )
        except Exception as exc:
            logger.debug("PCP: cingulate registry sync skipped: %s", exc)
        logger.info("PCP: started")

    async def stop(self) -> None:
        if self._bundle_refresh_task:
            self._bundle_refresh_task.cancel()
        if self._nats:
            try:
                await self._nats.close()
            except Exception:
                pass
        logger.info("PCP: stopped")

    # -----------------------------------------------------------------------
    # Role-permission resolution
    # -----------------------------------------------------------------------

    async def resolve_permissions(self, role_ids: list[str]) -> list[str]:
        """Return the full permission set for role_ids, honouring hierarchy."""
        if self._db is None:
            return []
        try:
            from services.auth.helpers.rbac import resolve_permissions

            return await resolve_permissions(self._db, role_ids)
        except Exception as exc:
            logger.warning("PCP: permission resolution failed: %s", exc)
            return []

    async def list_roles(self) -> list[dict]:
        """Return all active roles from MongoDB."""
        if self._db is None:
            return []
        roles = []
        async for doc in self._db[self._COLLECTION_ROLES].find(
            {"is_active": {"$ne": False}},
            {"_id": 0, "role_id": 1, "name": 1, "permissions": 1, "parent_role_ids": 1},
        ):
            roles.append(doc)
        return roles

    async def publish_role_change(
        self, role_id: str, change_type: str = "updated"
    ) -> None:
        """Broadcast a role-change event so all mesh nodes can invalidate caches."""
        event = {
            "event_type": "policy.role.changed",
            "role_id": role_id,
            "change_type": change_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._nats_publish(self._NATS_POLICY_SUBJECT, event)

    # -----------------------------------------------------------------------
    # OPA bundle management
    # -----------------------------------------------------------------------

    async def fetch_opa_bundle(self, bundle_name: str = "default") -> dict | None:
        """Fetch the named bundle's activation status from OPA and cache locally.

        OPA has no `/v1/bundles/{name}` endpoint — that was never a real OPA
        REST API (confirmed against OPA's own REST API reference: the actual
        surface is /v1/data, /v1/policies, /v1/query, /v1/compile, /v1/status,
        /v1/config). Bundle activation state is reported by /v1/status under
        result.bundles.<name> — but only for bundles OPA polls via a `bundles:`
        config section (a remote HTTP(S)/OCI service). This deployment loads
        opa/policies/*.rego as a filesystem bundle (`--bundle` CLI flag, no
        remote service needed) — those are genuinely active and queryable but
        may not appear under result.bundles the same way a polled bundle does.
        Fall back to /v1/policies (always reports whatever Rego modules are
        loaded, regardless of how) so this doesn't report "no bundle" for a
        deployment that's actually serving real policies.
        """
        opa_url = os.getenv("OPA_URL", "").rstrip("/")
        if not opa_url:
            return None
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{opa_url}/v1/status")
                if r.status_code == 200:
                    bundles = r.json().get("result", {}).get("bundles", {})
                    bundle_status = bundles.get(bundle_name)
                    if bundle_status is None:
                        bundle_status = await self._fallback_policies_status(
                            client, opa_url
                        )
                        if bundle_status is None:
                            logger.warning(
                                "PCP: OPA has no bundle named %r configured and no policies "
                                "loaded at all (known bundles: %s)",
                                bundle_name,
                                list(bundles),
                            )
                            return None
                    bundle_data = {
                        "name": bundle_name,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "content": bundle_status,
                    }
                    self._bundle_cache[bundle_name] = bundle_data
                    if self._db is not None:
                        await self._db[self._COLLECTION_BUNDLES].update_one(
                            {"name": bundle_name},
                            {"$set": bundle_data},
                            upsert=True,
                        )
                    return bundle_data
                else:
                    logger.warning("PCP: OPA status fetch returned %d", r.status_code)
        except Exception as exc:
            logger.warning("PCP: OPA bundle fetch failed: %s", exc)
        return None

    async def _fallback_policies_status(self, client, opa_url: str) -> dict | None:
        """Filesystem-bundle fallback: /v1/policies always lists loaded Rego
        modules regardless of how they were loaded (--bundle flag, --data
        flag, or PUT /v1/policies). Returns a bundle-status-shaped dict so
        callers of fetch_opa_bundle don't need to know which path was used."""
        try:
            r = await client.get(f"{opa_url}/v1/policies")
            if r.status_code != 200:
                return None
            modules = r.json().get("result", [])
            if not modules:
                return None
            return {
                "source": "filesystem_bundle",
                "policy_count": len(modules),
                "policy_ids": [m.get("id", "") for m in modules],
            }
        except Exception as exc:
            logger.debug("PCP: OPA policies fallback check failed: %s", exc)
            return None

    async def push_policy(self, policy_path: str, policy_rego: str) -> bool:
        """Push a new Rego policy document to OPA via the Policies API."""
        opa_url = os.getenv("OPA_URL", "").rstrip("/")
        if not opa_url:
            return False
        try:
            import httpx

            url = f"{opa_url}/v1/policies/{policy_path.replace('/', '_')}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.put(
                    url,
                    content=policy_rego.encode(),
                    headers={"Content-Type": "text/plain"},
                )
                if r.status_code in (200, 201):
                    logger.info("PCP: policy pushed to OPA: %s", policy_path)
                    await self._nats_publish(
                        self._NATS_POLICY_SUBJECT,
                        {
                            "event_type": "policy.bundle.updated",
                            "policy_path": policy_path,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    return True
                logger.warning("PCP: OPA policy push returned %d", r.status_code)
        except Exception as exc:
            logger.warning("PCP: OPA policy push failed: %s", exc)
        return False

    # -----------------------------------------------------------------------
    # Cross-domain trust
    # -----------------------------------------------------------------------

    async def register_trust_domain(self, domain: TrustDomain) -> None:
        """Persist a new trusted federated domain."""
        if self._db is None:
            logger.warning("PCP: no database; trust domain registration skipped")
            return
        doc = {
            **domain.to_dict(),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._db[self._COLLECTION_TRUST].update_one(
            {"domain_id": domain.domain_id},
            {"$set": doc},
            upsert=True,
        )
        self._trust_cache[domain.domain_id] = domain
        await self._nats_publish(
            self._NATS_POLICY_SUBJECT,
            {
                "event_type": "policy.trust.registered",
                "domain_id": domain.domain_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info("PCP: trust domain registered: %s", domain.domain_id)

    async def get_trust_domain(self, domain_id: str) -> TrustDomain | None:
        """Look up a trusted domain (cache-first, MongoDB fallback)."""
        if domain_id in self._trust_cache:
            return self._trust_cache[domain_id]
        if self._db is None:
            return None
        doc = await self._db[self._COLLECTION_TRUST].find_one(
            {"domain_id": domain_id}, {"_id": 0}
        )
        if doc:
            td = TrustDomain(
                domain_id=doc["domain_id"],
                name=doc["name"],
                spiffe_trust_domain=doc["spiffe_trust_domain"],
                jwks_url=doc.get("jwks_url", ""),
                allowed_scopes=doc.get("allowed_scopes", []),
            )
            self._trust_cache[domain_id] = td
            return td
        return None

    async def list_trust_domains(self) -> list[dict]:
        """Return all registered federated trust domains."""
        if self._db is None:
            return []
        result = []
        async for doc in self._db[self._COLLECTION_TRUST].find({}, {"_id": 0}):
            result.append(doc)
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _connect_nats(self) -> None:
        nats_url = os.getenv("NATS_URL", "")
        if not nats_url:
            return
        try:
            import nats

            self._nats = await nats.connect(nats_url)
            # Redacted: a NATS URL conventionally embeds credentials (nats://user:pass@host).
            from services.common.db import _redact_url

            logger.info("PCP: connected to NATS at %s", _redact_url(nats_url))
        except Exception as exc:
            logger.warning(
                "PCP: NATS connection failed (policy events will be skipped): %s", exc
            )

    async def _nats_publish(self, subject: str, payload: dict) -> None:
        if not self._nats:
            return
        try:
            import json

            await self._nats.publish(subject, json.dumps(payload).encode())
        except Exception as exc:
            logger.debug("PCP: NATS publish failed (non-fatal): %s", exc)

    async def _bundle_refresh_loop(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.fetch_opa_bundle()


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

_pcp_singleton: PolicyControlPlane | None = None


async def get_pcp(db=None) -> PolicyControlPlane:
    """Return (or lazily create) the singleton PCP instance."""
    global _pcp_singleton
    if _pcp_singleton is None:
        _pcp_singleton = PolicyControlPlane(db=db)
        await _pcp_singleton.start()
    elif db is not None and _pcp_singleton._db is None:
        _pcp_singleton._db = db
    return _pcp_singleton
