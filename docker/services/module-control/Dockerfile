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
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.lock.txt && \
    uv pip install --system -r requirements.lock.txt

COPY src/ ./src/
COPY services/common/ ./services/common/
COPY services/module_control/ ./services/module_control/

EXPOSE 8032

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8032/health || exit 1

CMD ["python", "-m", "uvicorn", "services.module_control.main:app", "--host", "0.0.0.0", "--port", "8032"]
