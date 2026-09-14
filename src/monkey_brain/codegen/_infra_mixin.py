"""InfraMixin — templates for Dockerfile, docker-compose.yml, pyproject.toml, .env.example."""

from __future__ import annotations


class InfraMixin:
    """Generates deployment/packaging files: Dockerfile, compose, pyproject, env."""

    def _dockerfile(self) -> str:
        driver_dep = ""
        if self.s.db.type == "postgresql":
            driver_dep = " asyncpg"
        elif self.s.db.type == "sqlite":
            driver_dep = " aiosqlite"
        elif self.s.is_mongo:
            driver_dep = " motor beanie"

        return f"""\
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim AS runtime

RUN addgroup --system app && adduser --system --ingroup app app
WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl{driver_dep} uvicorn[standard] && rm /tmp/*.whl

USER app
EXPOSE {self.s.port}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD python -c "import httpx, sys; r=httpx.get(\\'http://localhost:{self.s.port}/health\\'); sys.exit(0 if r.status_code==200 else 1)"

CMD ["uvicorn", "{self.n}.main:app", "--host", "0.0.0.0", "--port", "{self.s.port}", "--workers", "4"]
"""

    # ------------------------------------------------------------------
    # docker-compose.yml
    # ------------------------------------------------------------------

    def _docker_compose(self) -> str:
        if self.s.db.type == "postgresql":
            db_service = f"""\
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: appuser
      POSTGRES_PASSWORD: apppass
      POSTGRES_DB: {self.n}_db
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U appuser"]
      interval: 10s
      timeout: 5s
      retries: 5
"""
            db_env = f"      {self.s.db.url_env}: postgresql+asyncpg://appuser:apppass@db:5432/{self.n}_db"
            volumes = "\nvolumes:\n  pgdata:"
            depends_on = "    depends_on:\n      db:\n        condition: service_healthy\n"
        elif self.s.db.type == "mongodb":
            db_service = f"""\
  db:
    image: mongo:7
    environment:
      MONGO_INITDB_ROOT_USERNAME: appuser
      MONGO_INITDB_ROOT_PASSWORD: apppass
    volumes:
      - mongodata:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand(\\'ping\\')"]
      interval: 10s
      timeout: 5s
      retries: 5
"""
            db_env = f"      {self.s.db.url_env}: mongodb://appuser:apppass@db:27017"
            volumes = "\nvolumes:\n  mongodata:"
            depends_on = "    depends_on:\n      db:\n        condition: service_healthy\n"
        else:
            db_service = ""
            db_env = f"      {self.s.db.url_env}: sqlite+aiosqlite:///./data/{self.n}.db"
            volumes = ""
            depends_on = ""

        auth_env = ""
        if self.s.auth.enabled and self.s.auth.type == "bearer":
            auth_env = f"      {self.s.auth.secret_env}: change-me-in-production\n"
        elif self.s.auth.enabled and self.s.auth.type == "api_key":
            auth_env = f"      {self.s.auth.api_key_env}: change-me-in-production\n"

        return f"""\
services:
  api:
    build: .
    ports:
      - "{self.s.port}:{self.s.port}"
    environment:
{db_env}
{auth_env}      CORS_ORIGINS: "*"
{depends_on}    restart: unless-stopped
{db_service}{volumes}
"""

    # ------------------------------------------------------------------
    # pyproject.toml
    # ------------------------------------------------------------------

    def _pyproject(self) -> str:
        db_deps = {
            "postgresql": '    "asyncpg>=0.29",\n    "alembic>=1.13",\n    "sqlalchemy[asyncio]>=2.0",',
            "sqlite": '    "aiosqlite>=0.20",\n    "alembic>=1.13",\n    "sqlalchemy[asyncio]>=2.0",',
            "mongodb": '    "motor>=3.3",\n    "beanie>=1.24",',
        }[self.s.db.type]

        auth_deps = ""
        if self.s.auth.enabled and self.s.auth.type == "bearer":
            auth_deps = '    "python-jose[cryptography]>=3.3",\n'

        return f"""\
[project]
name = "{self.n}-service"
version = {self.s.version!r}
description = {self.s.description!r}
requires-python = ">=3.12"

dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
{db_deps}
{auth_deps}]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[build-system]
requires = ["setuptools>=70"]
build-backend = "setuptools.backends.legacy:build"

[tool.setuptools.packages.find]
where = ["."]
include = ["{self.n}*"]
"""

    # ------------------------------------------------------------------
    # .env.example
    # ------------------------------------------------------------------

    def _env_example(self) -> str:
        db_url = {
            "postgresql": f"{self.s.db.url_env}=postgresql+asyncpg://user:pass@localhost:5432/{self.n}_db",
            "sqlite": f"{self.s.db.url_env}=sqlite+aiosqlite:///./dev.db",
            "mongodb": f"{self.s.db.url_env}=mongodb://user:pass@localhost:27017\nMONGO_DB={self.n}_db",
        }[self.s.db.type]

        auth_vars = ""
        if self.s.auth.enabled and self.s.auth.type == "bearer":
            auth_vars = f"\n# Auth\n{self.s.auth.secret_env}=your-jwt-secret-at-least-32-chars\n"
        elif self.s.auth.enabled and self.s.auth.type == "api_key":
            auth_vars = f"\n# Auth\n{self.s.auth.api_key_env}=your-api-key-here\n"

        return f"""\
# {self.cls} Service — environment variables
# Copy to .env and fill in real values

# Database
{db_url}
{auth_vars}
# Runtime
CORS_ORIGINS=http://localhost:3000,http://localhost:8080
SQL_ECHO=false
"""
