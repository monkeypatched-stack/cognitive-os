"""SittingFace Online Registry Server — GitHub for MonkeyBrain charts."""

from __future__ import annotations

import hashlib
import tarfile
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="SittingFace Registry", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory store ───────────────────────────────────────────────────────────

REGISTRY: dict[str, dict[str, Any]] = {}
INDEX: dict[str, dict] = {}
# knowledge_pack provenance: name → version → list of episodic ref dicts
KNOWLEDGE_PACK_PROVENANCE: dict[str, dict[str, list]] = {}


# ── Models ────────────────────────────────────────────────────────────────────


class ChartMeta(BaseModel):
    name: str
    version: str
    description: str = ""
    tags: list[str] = []
    chart_type: str = "module"
    checksum: str = ""
    created_at: str = ""
    updated_at: str = ""


class PublishRequest(BaseModel):
    name: str
    version: str
    description: str = ""
    tags: list[str] = []
    chart_type: str = "module"
    values_yaml: str
    templates: dict[str, str] = {}
    sub_charts: dict[str, dict[str, str]] = {}
    chart_yaml: str | None = None  # for helm charts


class ChartResponse(BaseModel):
    name: str
    version: str
    description: str
    tags: list[str]
    chart_type: str
    checksum: str
    created_at: str
    updated_at: str
    versions: list[str] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "sittingface-registry",
        "charts": len(INDEX),
    }


@app.get("/api/v1/charts", response_model=list[ChartMeta])
async def list_charts(
    type: str | None = Query(None),
    tag: str | None = Query(None),
    search: str | None = Query(None),
) -> list[ChartMeta]:
    results = []
    for name, meta in INDEX.items():
        if type and meta.get("chart_type") != type:
            continue
        if tag and tag not in meta.get("tags", []):
            continue
        if search:
            sl = search.lower()
            if (
                sl not in meta.get("name", "").lower()
                and sl not in meta.get("description", "").lower()
                and not any(sl in t.lower() for t in meta.get("tags", []))
            ):
                continue
        results.append(ChartMeta(**meta))
    return results


@app.get("/api/v1/charts/{name}", response_model=ChartResponse)
async def get_chart(name: str) -> ChartResponse:
    if name not in INDEX:
        raise HTTPException(404, f"Chart not found: {name}")
    meta = INDEX[name]
    versions = list(REGISTRY.get(name, {}).keys())
    return ChartResponse(**meta, versions=versions)


@app.get("/api/v1/charts/{name}/{version}")
async def get_chart_version(name: str, version: str) -> dict:
    if name not in REGISTRY or version not in REGISTRY[name]:
        raise HTTPException(404, f"Chart {name} v{version} not found")
    return REGISTRY[name][version]


@app.post("/api/v1/charts/publish", response_model=ChartMeta)
async def publish_chart(req: PublishRequest) -> ChartMeta:
    now = datetime.now(timezone.utc).isoformat()
    checksum = hashlib.sha256(req.values_yaml.encode()).hexdigest()[:16]

    chart_data: dict[str, Any] = {
        "values_yaml": req.values_yaml,
        "templates": req.templates,
        "sub_charts": req.sub_charts,
    }
    if req.chart_yaml:
        chart_data["chart_yaml"] = req.chart_yaml

    if req.name not in REGISTRY:
        REGISTRY[req.name] = {}
    REGISTRY[req.name][req.version] = chart_data

    INDEX[req.name] = {
        "name": req.name,
        "version": req.version,
        "description": req.description,
        "tags": req.tags,
        "chart_type": req.chart_type,
        "checksum": checksum,
        "created_at": INDEX.get(req.name, {}).get("created_at", now),
        "updated_at": now,
    }

    if req.chart_type == "knowledge_pack":
        try:
            pack_data = yaml.safe_load(req.values_yaml) or {}
            episodic_refs = pack_data.get("_episodic_refs", [])
            KNOWLEDGE_PACK_PROVENANCE.setdefault(req.name, {})[req.version] = episodic_refs
        except Exception:
            pass

    return ChartMeta(**INDEX[req.name])


@app.get("/api/v1/knowledge-packs/{name}/{version}/provenance")
async def get_knowledge_pack_provenance(name: str, version: str) -> dict:
    """Return the episodic provenance chain for a released KnowledgePack version."""
    if name not in INDEX or INDEX[name].get("chart_type") != "knowledge_pack":
        raise HTTPException(404, f"KnowledgePack not found: {name}")
    refs = KNOWLEDGE_PACK_PROVENANCE.get(name, {}).get(version)
    if refs is None:
        raise HTTPException(404, f"No provenance recorded for {name} v{version}")
    return {
        "name": name,
        "version": version,
        "episodic_refs": refs,
        "ref_count": len(refs),
    }


@app.post("/api/v1/charts/publish/upload", response_model=ChartMeta)
async def publish_chart_upload(
    file: UploadFile = File(...),
    name: str = Query(...),
    version: str = Query(...),
    description: str = Query(""),
    tags: str = Query(""),
    chart_type: str = Query("module"),
) -> ChartMeta:
    content = await file.read()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    chart_data: dict[str, Any] = {}
    with tarfile.open(fileobj=BytesIO(content), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Reject path traversal attempts before any content is read.
            safe_name = member.name.lstrip("/").replace("..", "")
            if safe_name != member.name.lstrip("/"):
                continue
            f = tar.extractfile(member)
            if f:
                text = f.read().decode("utf-8")
                basename = safe_name.split("/")[-1]
                if basename == "values.yaml":
                    chart_data["values_yaml"] = text
                elif basename == "Chart.yaml":
                    chart_data["chart_yaml"] = text
                elif "/templates/" in safe_name:
                    chart_data.setdefault("templates", {})[basename] = text

    if "values_yaml" not in chart_data:
        raise HTTPException(400, "No values.yaml found in archive")

    req = PublishRequest(
        name=name,
        version=version,
        description=description,
        tags=tag_list,
        chart_type=chart_type,
        **chart_data,
    )
    return await publish_chart(req)


@app.delete("/api/v1/charts/{name}")
async def delete_chart(name: str) -> dict:
    if name not in INDEX:
        raise HTTPException(404, f"Chart not found: {name}")
    del INDEX[name]
    REGISTRY.pop(name, None)
    return {"status": "deleted", "name": name}


@app.get("/api/v1/charts/{name}/{version}/download")
async def download_chart(name: str, version: str) -> dict:
    if name not in REGISTRY or version not in REGISTRY[name]:
        raise HTTPException(404, f"Chart {name} v{version} not found")
    return {"meta": INDEX.get(name, {}), "data": REGISTRY[name][version]}


@app.get("/api/v1/stats")
async def stats() -> dict:
    total = len(INDEX)
    by_type: dict[str, int] = {}
    all_tags: set[str] = set()
    for meta in INDEX.values():
        t = meta.get("chart_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        all_tags.update(meta.get("tags", []))
    return {"total_charts": total, "by_type": by_type, "tags": sorted(all_tags)}


# ── Vault (password-protected chart storage) ──────────────────────────────────

VAULT_STORE: dict[str, dict] = {}  # chart_name → {version → encrypted_data}
VAULT_SESSIONS: dict[str, dict] = {}  # token → {password, created}


_VAULT_TOKEN_TTL = timedelta(hours=1)


class VaultPublishRequest(BaseModel):
    name: str
    version: str
    description: str = ""
    tags: list[str] = []
    chart_type: str = "module"
    values_yaml: str
    templates: dict[str, str] = {}


def _get_vault_fernet(password: str):
    """Resolve a Fernet instance from a password, or raise 500."""
    from sittingface.vault import CodeVault

    vault = CodeVault(password=password)
    fernet = vault._get_fernet()
    if not fernet:
        raise HTTPException(500, "Vault encryption unavailable")
    return fernet


def _require_vault_session(token: str) -> str:
    """Validate a vault session token and return the associated password."""
    session = VAULT_SESSIONS.get(token)
    if session is None:
        raise HTTPException(401, "Invalid or expired token")
    created = datetime.fromisoformat(session["created"])
    if datetime.now(timezone.utc) - created > _VAULT_TOKEN_TTL:
        del VAULT_SESSIONS[token]
        raise HTTPException(401, "Invalid or expired token")
    return session["password"]


@app.post("/api/v1/vault/publish")
async def vault_publish(
    req: VaultPublishRequest,
    x_vault_password: str = Header(..., alias="X-Vault-Password"),
) -> dict:
    """Publish encrypted chart to vault. Pass password in X-Vault-Password header."""
    fernet = _get_vault_fernet(x_vault_password)

    values_encrypted = fernet.encrypt(req.values_yaml.encode()).decode()
    templates_encrypted = {k: fernet.encrypt(v.encode()).decode() for k, v in req.templates.items()}

    checksum = hashlib.sha256(req.values_yaml.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()

    if req.name not in VAULT_STORE:
        VAULT_STORE[req.name] = {}

    VAULT_STORE[req.name][req.version] = {
        "values_yaml_encrypted": values_encrypted,
        "templates_encrypted": templates_encrypted,
        "checksum": checksum,
    }

    INDEX[req.name] = {
        "name": req.name,
        "version": req.version,
        "description": req.description,
        "tags": req.tags,
        "chart_type": req.chart_type,
        "checksum": checksum,
        "created_at": INDEX.get(req.name, {}).get("created_at", now),
        "updated_at": now,
        "encrypted": True,
    }

    return {"status": "encrypted", "name": req.name, "version": req.version}


@app.get("/api/v1/vault/{name}/{version}")
async def vault_get(
    name: str,
    version: str,
    x_vault_password: str = Header(..., alias="X-Vault-Password"),
) -> dict:
    """Get decrypted chart from vault. Pass password in X-Vault-Password header."""
    if name not in VAULT_STORE or version not in VAULT_STORE[name]:
        raise HTTPException(404, f"Chart not found: {name} v{version}")

    fernet = _get_vault_fernet(x_vault_password)
    encrypted = VAULT_STORE[name][version]
    try:
        values_yaml = fernet.decrypt(encrypted["values_yaml_encrypted"].encode()).decode()
        templates = {
            k: fernet.decrypt(v.encode()).decode() for k, v in encrypted.get("templates_encrypted", {}).items()
        }
        return {
            "meta": INDEX.get(name, {}),
            "data": {"values_yaml": values_yaml, "templates": templates},
        }
    except Exception:
        raise HTTPException(403, "Invalid password")


@app.post("/api/v1/vault/auth")
async def vault_auth(
    x_vault_password: str = Header(..., alias="X-Vault-Password"),
) -> dict:
    """Exchange a vault password for a short-lived session token. Pass password in X-Vault-Password header."""
    import secrets

    token = secrets.token_urlsafe(32)
    VAULT_SESSIONS[token] = {
        "password": x_vault_password,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return {"token": token, "expires_in": int(_VAULT_TOKEN_TTL.total_seconds())}


@app.get("/api/v1/vault/{name}/{version}/download")
async def vault_download(name: str, version: str, token: str = Query(...)) -> dict:
    """Download decrypted chart using a session token obtained from /vault/auth."""
    password = _require_vault_session(token)

    if name not in VAULT_STORE or version not in VAULT_STORE[name]:
        raise HTTPException(404, f"Chart not found: {name} v{version}")

    fernet = _get_vault_fernet(password)
    encrypted = VAULT_STORE[name][version]
    try:
        values_yaml = fernet.decrypt(encrypted["values_yaml_encrypted"].encode()).decode()
        templates = {
            k: fernet.decrypt(v.encode()).decode() for k, v in encrypted.get("templates_encrypted", {}).items()
        }
        return {
            "meta": INDEX.get(name, {}),
            "data": {"values_yaml": values_yaml, "templates": templates},
        }
    except Exception:
        raise HTTPException(403, "Decryption failed — wrong password")


def run_server(host: str = "0.0.0.0", port: int = 8500):
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
