FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# No requirements.lock.txt install here on purpose -- this service's only
# dependency is fastapi+uvicorn, both already in the root lockfile, but
# pulling the WHOLE root dependency set in for a single-endpoint signing
# service would be a lot of unnecessary weight for something that must
# have as little as possible to be down for. Pinned directly instead.
RUN uv pip install --system "fastapi" "uvicorn[standard]"

# Real source lives under domains/manufacturing/knowledge/services/
# trusted_time/ (same real-source location every other services/* actually
# builds from -- see docker/services/agentos/Dockerfile's own comment on
# this; the repo-root services/<name>/ directories are empty build
# artifacts, not source).
COPY domains/manufacturing/knowledge/services/trusted_time/ ./services/trusted_time/

EXPOSE 8040

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8040/health || exit 1

CMD ["python", "-m", "uvicorn", "services.trusted_time.main:app", "--host", "0.0.0.0", "--port", "8040"]
