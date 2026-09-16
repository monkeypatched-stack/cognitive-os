FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
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
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.lock.txt && \
    uv pip install --system \
        --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
        -r requirements.lock.txt

COPY src/ ./src/
# services/common/ and every service EXCEPT file/ live under
# domains/manufacturing/knowledge/services/ (see tests/conftest.py's own
# sys.path setup for the same reason, and docker/services/agentos/
# Dockerfile's own comment on this exact split) -- the repo-root
# services/{name}/ path only has REAL, tracked content for common's
# sibling "file" (a deliberate lightweight stub per that service's own
# Dockerfile comment). Confirmed live: every other repo-root services/
# subdirectory has zero git-tracked files (just local __pycache__/build
# leftovers), so `COPY services/<name>/` failed outright with
# "not found" the moment an earlier, unrelated build failure (torch's
# CPU-index pin) stopped masking it.
COPY domains/manufacturing/knowledge/services/common/ ./services/common/
COPY domains/manufacturing/knowledge/services/process_definition/ ./services/process_definition/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "services.process_definitions.main:app", "--host", "0.0.0.0", "--port", "8000"]
