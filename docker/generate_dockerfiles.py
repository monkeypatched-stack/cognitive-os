#!/usr/bin/env python3
"""Generate Dockerfiles for all MonkeyBrain services."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = ROOT / "docker" / "services"
DOCKER_DIR.mkdir(parents=True, exist_ok=True)

SERVICES = [
    ("auth", "services.auth.main:app", 8010),
    ("assets", "services.assets.main:app", 8011),
    ("customers", "services.customers.main:app", 8012),
    ("events", "services.events.main:app", 8013),
    ("facilities", "services.facilities.main:app", 8014),
    ("floor-layout", "services.floor_layout.main:app", 8015),
    ("inventory", "services.inventory.main:app", 8016),
    ("iot", "services.iot.main:app", 8017),
    ("orders", "services.orders.main:app", 8018),
    ("pm", "services.pm.main:app", 8019),
    ("procurement", "services.procurement.main:app", 8020),
    ("products", "services.products.main:app", 8021),
    ("shipping", "services.shipping.main:app", 8022),
    ("shifts", "services.shifts.main:app", 8023),
    ("suppliers", "services.suppliers.main:app", 8024),
    ("taxonomy", "services.taxonomy.main:app", 8025),
    ("process-definition", "services.process_definitions.main:app", 8000),
    ("workorders", "services.workorders.main:app", 8027),
    ("changeover", "services.changeover.main:app", 8028),
    ("documents", "services.documents.main:app", 8029),
    ("file", "services.file.src.core.config:app", 8030),
    ("module-control", "services.module_control.main:app", 8032),
    # agentos is deliberately NOT generated from this shared template.
    # docker/services/agentos/Dockerfile has diverged for real, documented
    # reasons this template can't express: an extra `--extra livekit` uv
    # dependency group, a PYTHONPATH=/app:/app/src override, monkeypatched_sdk
    # + packages/broca + packages/cerebellum editable installs, and its own
    # services/auth/ copy (with the dev .env stripped out). Confirmed live,
    # twice, that re-running this generator with "agentos" still in SERVICES
    # silently clobbers all of that with the generic template -- do not add
    # it back without also teaching the template about every one of those
    # differences.
]

DOCKERFILE_TEMPLATE = """\
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \\
    apt-get install -y --no-install-recommends curl && \\
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock .
# --index / --index-strategy: torch is pinned to PyTorch's CPU-only wheel
# index (pyproject.toml's [tool.uv.sources]) to avoid pulling ~5GB of unused
# CUDA/nvidia-* libraries into an image with no GPU. `uv export`'s
# requirements.txt output drops that per-package index annotation, so
# `uv pip install` needs the same index passed explicitly -- confirmed live
# this broke every generated service image with "no solution found... no
# version of torch==2.14.0+cpu" once that pin landed. unsafe-best-match is
# required alongside it: uv's default first-index strategy would otherwise
# lock every OTHER package onto that index too (breaks certifi, also
# mirrored there, but not at the pinned version).
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.lock.txt && \\
    uv pip install --system \\
        --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \\
        -r requirements.lock.txt

COPY src/ ./src/
# services/common/ and every service EXCEPT file/ live under
# domains/manufacturing/knowledge/services/ (see tests/conftest.py's own
# sys.path setup for the same reason, and docker/services/agentos/
# Dockerfile's own comment on this exact split) -- the repo-root
# services/{{name}}/ path only has REAL, tracked content for common's
# sibling "file" (a deliberate lightweight stub per that service's own
# Dockerfile comment). Confirmed live: every other repo-root services/
# subdirectory has zero git-tracked files (just local __pycache__/build
# leftovers), so `COPY services/<name>/` failed outright with
# "not found" the moment an earlier, unrelated build failure (torch's
# CPU-index pin) stopped masking it.
COPY {common_src} ./services/common/
COPY {service_src} ./services/{service_dir}/

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:{port}/health || exit 1

CMD ["python", "-m", "uvicorn", "{module}", "--host", "0.0.0.0", "--port", "{port}"]
"""

DOMAINS_SERVICES_ROOT = "domains/manufacturing/knowledge/services"

for name, module, port in SERVICES:
    service_dir = name.replace("-", "_")
    # "file" is the one service with real, tracked content at the repo-root
    # services/file/ path (see template comment above) -- every other
    # service's real source lives under domains/manufacturing/knowledge/.
    service_src = f"services/{service_dir}/" if service_dir == "file" else f"{DOMAINS_SERVICES_ROOT}/{service_dir}/"
    dockerfile = DOCKERFILE_TEMPLATE.format(
        common_src=f"{DOMAINS_SERVICES_ROOT}/common/",
        service_src=service_src,
        service_dir=service_dir,
        module=module,
        port=port,
    )
    svc_dir = DOCKER_DIR / name
    svc_dir.mkdir(exist_ok=True)
    (svc_dir / "Dockerfile").write_text(dockerfile)
    print(f"  Created {name}/Dockerfile (port {port})")

print(f"\nGenerated {len(SERVICES)} Dockerfiles in {DOCKER_DIR}")
