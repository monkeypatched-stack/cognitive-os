#!/usr/bin/env python3
"""Run all API microservices locally from the monorepo root."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python3"
if _VENV_PYTHON.exists() and sys.executable != str(_VENV_PYTHON):
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), __file__] + sys.argv[1:])

# domains/manufacturing/knowledge holds the generated `services` package
# (services.auth.main, services.file.src.core.config, ...) — put it on this
# process's own sys.path too, not just the subprocess env below, so
# _is_service_importable()'s find_spec() check (which runs here, in the
# parent) can actually see it.
_pkg_services = _ROOT / "domains" / "manufacturing" / "knowledge"
if _pkg_services.exists() and str(_pkg_services) not in sys.path:
    sys.path.insert(0, str(_pkg_services))


@dataclass(frozen=True)
class Service:
    name: str
    app: str
    port: int


SERVICES = [
    Service("auth", "services.auth.main:app", 8010),
    Service("file", "services.file.src.core.config:app", 8030),
    Service("agentos", "src.monkey_brain.api.main:app", 8031),
]

# Deliberately scoped down to these 3 services. The other domain
# microservices (assets, customers, events, ... — everything else under
# "services.*") DO have generated code too, at
# domains/manufacturing/knowledge/services/<name>/main.py — they were not
# omitted because they're missing, just out of scope for this run. Add them
# back to SERVICES (same "services.<name>.main:app" pattern) when needed.
# "agentos" is the one hand-written service (the actual MonkeyBrain API,
# src/monkey_brain/api/main.py).


_SERVICE_DEFAULTS: dict[str, str] = {
    "MONGODB_URL": "mongodb://localhost:27017",
    "DB_NAME": "demo",
    "REDIS_URL": "redis://localhost:6379",
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NATS_URL": "nats://localhost:4222",
    "NATS_EVENTS_SUBJECT": "indus.websocket.events",
    "NATS_EVENTS_QUEUE": "indus-influx-consumers",
    "INFLUXDB_URL": "http://localhost:8181",
    "INFLUXDB_ORG": "indus",
    "INFLUXDB_BUCKET": "events",
    "INFLUXDB_EVENTS_MEASUREMENT": "websocket_events",
    "NEO4J_PASSWORD": "password",
    "INFLUXDB_TOKEN": "",
}

_REQUIRED_ENV_VARS = (
    "ACCESS_TOKEN_SECRET",
    "REFRESH_TOKEN_SECRET",
)


def _validate_required_env() -> None:
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(
            f"Error: required environment variables not set: {', '.join(missing)}\n"
            "Set them in your shell or in a .env file before starting services."
        )
        raise SystemExit(1)
    try:
        from services.common.secrets import validate_hmac_secrets_from_env

        validate_hmac_secrets_from_env()
    except Exception as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)


def start_service(service: Service) -> subprocess.Popen:
    env = os.environ.copy()
    for key, default in _SERVICE_DEFAULTS.items():
        env.setdefault(key, default)

    # domains/manufacturing/knowledge holds the generated `services` package
    # that "auth"/"file" (and the rest of SERVICES, if added back) import
    # from. Merged in rather than setdefault()'d: when main.py is launched
    # via install_agentos.py's cmd_start(), PYTHONPATH is already set (to
    # just the repo root) in the inherited env, so setdefault() would be a
    # silent no-op and this entry would never be added.
    existing_pythonpath = env.get("PYTHONPATH", "")
    path_entries = [os.getcwd(), str(_pkg_services)]
    if existing_pythonpath:
        path_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(path_entries))

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        service.app,
        "--host",
        "0.0.0.0",
        "--port",
        str(service.port),
    ]
    print(f"Starting {service.name:13} http://localhost:{service.port}")
    return subprocess.Popen(cmd, env=env)


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _is_service_importable(service: Service) -> bool:
    """Check the module half of `service.app` (before the ':') actually
    exists, without importing it (find_spec has no side effects, unlike a
    real import). Most SERVICES entries point at domain microservices that
    haven't been code-generated yet — starting uvicorn against a
    nonexistent module crashes hard and takes down every other already-
    running service via stop_all(), which is worse than just not starting
    the one that was never generated.
    """
    import importlib.util

    module_path = service.app.split(":", 1)[0]
    try:
        return importlib.util.find_spec(module_path) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def stop_all(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()

    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    _validate_required_env()

    generated, not_generated = [], []
    for service in SERVICES:
        (generated if _is_service_importable(service) else not_generated).append(service)

    if not_generated:
        print("Skipping services whose code hasn't been generated yet:")
        for service in not_generated:
            print(f"  {service.name:13} ({service.app}) — not found")

    if not generated:
        print("\nNo services are importable — nothing to start.")
        return 1

    already_running = [service for service in generated if not port_is_available(service.port)]
    to_start = [service for service in generated if port_is_available(service.port)]

    if already_running:
        print("Skipping services that are already running:")
        for service in already_running:
            print(f"  {service.name:13} http://localhost:{service.port}")

    if not to_start:
        print("\nAll services are already running.")
        return 0

    processes = [start_service(service) for service in to_start]

    def handle_signal(signum, _frame):
        print(f"\nReceived signal {signum}; stopping services...")
        stop_all(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while True:
            for service, process in zip(to_start, processes):
                code = process.poll()
                if code is not None:
                    print(f"{service.name} exited with code {code}; stopping services...")
                    stop_all(processes)
                    return code
            time.sleep(1)
    finally:
        stop_all(processes)


if __name__ == "__main__":
    raise SystemExit(main())
