FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/src

# PYTHONPATH=/app:/app/src replicates what the local dev venv's editable
# install actually does (.venv/lib/*/site-packages/__editable__.
# monkeybrain_runtime-*.pth contains a single line: the repo's src/
# directory). Confirmed live this codebase relies on BOTH import styles
# resolving to the same files — e.g. runtime/runtime.py does
# `from monkey_brain.runtime.agent_resolver import ...` (bare, needs
# src/ itself on sys.path) while most of the rest of the app does
# `from src.monkey_brain....` (needs /app, the repo-root equivalent, on
# sys.path) — without both, imports fail partway through app startup
# with ModuleNotFoundError, which is exactly what happened here before
# this fix (confirmed via a real `docker build` + `docker run`).

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock .
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.lock.txt && \
    uv pip install --system -r requirements.lock.txt

COPY src/ ./src/
# monkeypatched_sdk is its own top-level package (repo root, sibling to
# src/) — src.cortex.prediction imports monkeypatched_sdk.models directly.
COPY monkeypatched_sdk/ ./monkeypatched_sdk/
# Live Deployment Validation finding: packages/broca and packages/
# cerebellum were never copied into this image at all — confirmed live,
# a real container crashed at startup with
# ModuleNotFoundError: No module named 'broca.registry' (bootstrap.py::
# init_broca, 18 real call sites across src/monkey_brain/, not dead
# code). The local dev venv only works because `pip install -e
# packages/broca` registers a custom editable-install finder that
# transparently redirects `broca.registry`/`broca.agents.*` submodule
# imports to packages/broca/broca/ even though bare `import broca` itself
# resolves to src/broca/'s own (older, incomplete — no registry.py)
# stub package of the same name. Reproducing that same editable install
# here (not a plain COPY) is what actually reproduces dev's real import
# behavior in the container. cerebellum (init_providers, 5 real call
# sites) has the same shape: a real `cerebellum` package that only
# exists under packages/cerebellum/, with no src/ stub of its own at
# all, so it was ModuleNotFoundError-ing too, just silently (init_
# providers already wraps its import in try/except — see bootstrap.py).
# packages/cortex is NOT installed here: bare `import cortex` already
# resolves entirely to src/cortex/ (confirmed live — reasoning_
# scheduler.py, epa.py, epistemic.py, everything kernel/cognitive_
# kernel.py actually imports, all live under src/cortex/, not
# packages/cortex/), so packages/cortex is an unrelated, unused
# duplicate — installing it would risk a real name collision with the
# src/cortex/ package this code actually depends on, not close a gap.
COPY packages/broca/ ./packages/broca/
COPY packages/cerebellum/ ./packages/cerebellum/
RUN uv pip install --system --no-deps -e ./packages/broca -e ./packages/cerebellum
# services/{common,auth,agentos} live under domains/manufacturing/knowledge/
# (see tests/conftest.py's sys.path setup — same reason), NOT at the repo
# root, which only has services/file/. The previous COPY services/common/
# and COPY services/agentos/ referenced paths that do not exist relative
# to this Dockerfile's build context (docker-compose.yml: context: .,
# the repo root) — Gate 8 confirmed via a real `docker build` that this
# broke image creation outright (COPY of a nonexistent source path fails
# the build), before ever reaching a container to run. src.monkey_brain
# imports from services.common.* and services.auth.* (grepped — nothing
# else under services.*), plus services.agentos itself for this
# Dockerfile's own CMD entrypoint below.
COPY domains/manufacturing/knowledge/services/common/ ./services/common/
COPY domains/manufacturing/knowledge/services/auth/ ./services/auth/
# Real, live-confirmed bug: services/auth/.env is a developer's local-dev
# config file (real personal email, dev tokens, and — critically —
# REDIS_URL=redis://localhost:6379) that the COPY above bakes into the
# image. services/common/config.py's Settings loads it (env_file =
# (".env", "services/auth/.env")), and its REDIS_URL silently overrides
# the correctly-configured REDIS_URL this container is actually given
# (confirmed live: /proc/1/environ shows the correct redis://redis:6379
# at exec time, but the live process's os.environ later reads back
# redis://localhost:6379 — traced to this exact file) — breaking Redis
# connectivity for every module that reads REDIS_URL via this Settings
# path (payment_store.py and its siblings), even though the container's
# real environment was never wrong. Not a source-repo change — this file
# is still there for actual local (non-Docker) dev — just excluded from
# the shipped image.
RUN rm -f services/auth/.env
COPY domains/manufacturing/knowledge/services/agentos/ ./services/agentos/

EXPOSE 8031

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8031/health || exit 1

CMD ["python", "-m", "uvicorn", "services.agentos.main:app", "--host", "0.0.0.0", "--port", "8031"]
