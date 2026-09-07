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

COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY src/ ./src/
# services/{common,auth} live under domains/manufacturing/knowledge/ (not repo-root
# services/) — same layout as docker/services/agentos/Dockerfile.
COPY domains/manufacturing/knowledge/services/common/ ./services/common/
COPY domains/manufacturing/knowledge/services/auth/ ./services/auth/
# services/auth/routers/integration_config.py imports services.module_control
# (confirmed live: ModuleNotFoundError: No module named 'services.module_control'
# on first real boot of this image — docker-compose.yml's own "auth" service
# uses this same Dockerfile with no compensating bind-mount, so this was a
# pre-existing gap there too, just never previously exercised/noticed).
COPY domains/manufacturing/knowledge/services/module_control/ ./services/module_control/
RUN rm -f services/auth/.env

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8010/health || exit 1

CMD ["python", "-m", "uvicorn", "services.auth.main:app", "--host", "0.0.0.0", "--port", "8010"]
