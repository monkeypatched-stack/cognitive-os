#!/usr/bin/env python3
"""
MonkeyBrain Native Installer

Installs, configures, and runs a complete MonkeyBrain instance locally.

    python install_agentos.py [command] [options]

Commands:
    install     Full installation (deps + services + config + seed)
    start       Start all services
    stop        Stop all services
    status      Show status of all services
    configure   Reconfigure database connections
    seed        Seed domain data
    logs        Tail service logs

Examples:
    python install_agentos.py install
    python install_agentos.py install --mongo-port 27018 --redis-port 6380
    python install_agentos.py configure --influxdb-url http://10.0.0.5:8086
    python install_agentos.py status
    python install_agentos.py start --skip neo4j elasticsearch
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Paths ──────────────────────────────────────────────────────────────────────

INSTALL_DIR = Path.home() / ".monkeybrain"
VENV_DIR = INSTALL_DIR / "venv"
BIN_DIR = INSTALL_DIR / "bin"
CONFIG_DIR = INSTALL_DIR / "config"
LOG_DIR = INSTALL_DIR / "logs"
DATA_DIR = INSTALL_DIR / "data"
PID_DIR = INSTALL_DIR / "pids"
SECRET_FILE = INSTALL_DIR / "secrets.json"

SOURCE_DIR = Path(__file__).resolve().parent


# ── Service Definitions ────────────────────────────────────────────────────────


@dataclass
class DBService:
    name: str
    default_port: int
    env_url_var: str
    default_url: str
    package_brew: str
    package_apt: str
    service_name_brew: str | None = None
    service_name_apt: str | None = None
    optional: bool = False


SERVICES: list[DBService] = [
    DBService(
        name="mongodb",
        default_port=27017,
        env_url_var="MONGODB_URL",
        default_url="mongodb://localhost:27017",
        package_brew="mongodb-community",
        package_apt="mongosh",
        service_name_brew="mongodb-community",
        service_name_apt="mongod",
    ),
    DBService(
        name="redis",
        default_port=6379,
        env_url_var="REDIS_URL",
        default_url="redis://localhost:6379",
        package_brew="redis",
        package_apt="redis-server",
        service_name_brew="redis",
        service_name_apt="redis-server",
    ),
    DBService(
        name="neo4j",
        default_port=7687,
        env_url_var="NEO4J_URI",
        default_url="bolt://localhost:7687",
        package_brew="neo4j",
        package_apt="neo4j",
        service_name_brew="neo4j",
        service_name_apt="neo4j",
        optional=True,
    ),
    DBService(
        name="influxdb",
        default_port=8181,
        env_url_var="INFLUXDB_URL",
        default_url="http://localhost:8181",
        package_brew="influxdb3",
        package_apt="influxdb3",
        service_name_brew="influxdb3",
        service_name_apt="influxdb3",
    ),
    DBService(
        name="elasticsearch",
        default_port=9200,
        env_url_var="AUDIT_ELASTICSEARCH_URL",
        default_url="http://localhost:9200",
        package_brew="elasticsearch",
        package_apt="elasticsearch",
        service_name_brew="elasticsearch-full",
        service_name_apt="elasticsearch",
        optional=True,
    ),
    DBService(
        name="nats",
        default_port=4222,
        env_url_var="NATS_URL",
        default_url="nats://localhost:4222",
        package_brew="nats-server",
        package_apt="nats-server",
        service_name_brew="nats-server",
        service_name_apt="nats-server",
    ),
]


# ── Console ────────────────────────────────────────────────────────────────────


class C:
    R = "\033[0m"
    B = "\033[1m"
    G = "\033[92m"
    Y = "\033[93m"
    R_ = "\033[91m"
    CY = "\033[96m"
    DIM = "\033[2m"

    @staticmethod
    def ok(t: str) -> None:
        print(f"  {C.G}\u2714{C.R} {t}")

    @staticmethod
    def warn(t: str) -> None:
        print(f"  {C.Y}\u26a0{C.R} {t}")

    @staticmethod
    def fail(t: str) -> None:
        print(f"  {C.R_}\u2718{C.R} {t}")

    @staticmethod
    def step(t: str) -> None:
        print(f"\n{C.B}{C.CY}{t}{C.R}")

    @staticmethod
    def info(t: str) -> None:
        print(f"  {C.DIM}{t}{C.R}")


# ── Utilities ──────────────────────────────────────────────────────────────────


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run(
    cmd: list[str], *, check: bool = True, capture: bool = True, env: dict | None = None
) -> subprocess.CompletedProcess:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, env=merged_env)


def run_fg(cmd: list[str], *, env: dict | None = None) -> None:
    merged_env = {**os.environ, **(env or {})}
    subprocess.run(cmd, env=merged_env)


def get_platform() -> str:
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin" and machine == "arm64":
        return "macos-arm64"
    elif system == "Darwin":
        return "macos-x86"
    elif system == "Linux":
        try:
            with open("/etc/os-release") as f:
                content = f.read()
            if "Ubuntu" in content or "Debian" in content:
                return "ubuntu"
            elif "RHEL" in content or "CentOS" in content or "Rocky" in content:
                return "rhel"
        except FileNotFoundError:
            logger.debug("get_platform: suppressed exception", exc_info=True)
        return "linux"
    return f"{system}-{machine}".lower()


def detect_package_manager() -> str:
    plat = get_platform()
    if plat.startswith("macos"):
        return "brew"
    elif plat in ("ubuntu", "rhel", "linux"):
        if shutil.which("apt-get"):
            return "apt"
        elif shutil.which("dnf"):
            return "dnf"
        elif shutil.which("yum"):
            return "yum"
    return "unknown"


def load_config() -> dict:
    config_path = CONFIG_DIR / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "config.json").write_text(json.dumps(config, indent=2))


def load_secrets() -> dict:
    if SECRET_FILE.exists():
        return json.loads(SECRET_FILE.read_text())
    return {}


# ── Service Detection ─────────────────────────────────────────────────────────


def detect_running_services() -> dict[str, bool]:
    result = {}
    for svc in SERVICES:
        result[svc.name] = port_in_use(svc.default_port)
    return result


def is_service_running(svc: DBService) -> bool:
    return port_in_use(svc.default_port)


# ── Package Manager ────────────────────────────────────────────────────────────


def ensure_brew() -> None:
    if not shutil.which("brew"):
        C.fail("Homebrew not found. Install from https://brew.sh")
        raise SystemExit(1)


def install_package(svc: DBService) -> None:
    pm = detect_package_manager()
    if pm == "brew":
        ensure_brew()
        pkg = svc.package_brew
        C.info(f"Installing {pkg} via Homebrew...")
        run(["brew", "install", pkg], check=False)
    elif pm == "apt":
        C.info(f"Installing {svc.package_apt} via apt...")
        run(["sudo", "apt-get", "update", "-qq"], check=False)
        run(["sudo", "apt-get", "install", "-y", "-qq", svc.package_apt], check=False)
    elif pm in ("dnf", "yum"):
        C.info(f"Installing {svc.package_apt} via {pm}...")
        run(["sudo", pm, "install", "-y", svc.package_apt], check=False)
    else:
        C.fail(f"Cannot auto-install {svc.name} — unsupported package manager")
        raise SystemExit(1)


def start_service(svc: DBService) -> None:
    pm = detect_package_manager()
    try:
        if pm == "brew" and svc.service_name_brew:
            run(["brew", "services", "start", svc.service_name_brew], check=False)
        elif svc.service_name_apt and pm in ("apt", "dnf", "yum"):
            run(["sudo", "systemctl", "start", svc.service_name_apt], check=False)
        elif svc.name == "nats":
            nats_data = DATA_DIR / "nats"
            nats_data.mkdir(parents=True, exist_ok=True)
            log = LOG_DIR / "nats.log"
            pid_file = PID_DIR / "nats.pid"
            cmd = ["nats-server", "-js", "-sd", str(nats_data), "-l", str(log)]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pid_file.write_text(str(proc.pid))
            C.info(f"Started NATS server (PID {proc.pid})")
            return
        elif svc.name == "influxdb":
            influx_data = DATA_DIR / "influxdb"
            influx_data.mkdir(parents=True, exist_ok=True)
            log = LOG_DIR / "influxdb.log"
            pid_file = PID_DIR / "influxdb.pid"
            cmd = [
                "influxd3",
                "serve",
                "--data-dir",
                str(influx_data),
                "--http-bind",
                f":{svc.default_port}",
            ]
            with open(log, "w") as log_f:
                proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
            pid_file.write_text(str(proc.pid))
            C.info(f"Started InfluxDB (PID {proc.pid})")
            return
        else:
            C.warn(f"Don't know how to start {svc.name} on {pm}")
            return
    except Exception as e:
        C.fail(f"Failed to start {svc.name}: {e}")


def stop_service(svc: DBService) -> None:
    pm = detect_package_manager()
    pid_file = PID_DIR / f"{svc.name}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            C.info(f"Stopped {svc.name} (PID {pid})")
        except (ProcessLookupError, ValueError):
            logger.debug("stop_service: suppressed exception", exc_info=True)
        pid_file.unlink(missing_ok=True)
        return

    try:
        if pm == "brew" and svc.service_name_brew:
            run(["brew", "services", "stop", svc.service_name_brew], check=False)
        elif svc.service_name_apt and pm in ("apt", "dnf", "yum"):
            run(["sudo", "systemctl", "stop", svc.service_name_apt], check=False)
    except Exception as e:
        logger.debug("Service stop failed: %s", e)


# ── Installation Steps ────────────────────────────────────────────────────────


def step_check_python() -> None:
    v = sys.version_info[:2]
    if v < (3, 11):
        C.fail(f"Python 3.11+ required, found {v[0]}.{v[1]}")
        raise SystemExit(1)
    C.ok(f"Python {v[0]}.{v[1]}.{sys.version_info[2]}")


def step_detect_platform() -> None:
    plat = get_platform()
    pm = detect_package_manager()
    C.ok(f"Platform: {plat}, Package manager: {pm}")


def step_create_dirs() -> None:
    for d in [INSTALL_DIR, VENV_DIR, BIN_DIR, CONFIG_DIR, LOG_DIR, DATA_DIR, PID_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    C.ok(f"Directories created at {INSTALL_DIR}")


def step_create_venv() -> None:
    if (VENV_DIR / "bin" / "python").exists():
        C.ok("Virtual environment already exists")
        return
    run([sys.executable, "-m", "venv", str(VENV_DIR)])
    C.ok(f"Created venv at {VENV_DIR}")


def step_install_deps() -> None:
    import shutil

    uv_bin = shutil.which("uv")
    lock_file = SOURCE_DIR / "uv.lock"
    if uv_bin and lock_file.exists():
        C.info("Using uv for fast installs")
        req_file = VENV_DIR / "requirements.lock.txt"
        run(
            [
                uv_bin,
                "export",
                "--frozen",
                "--no-dev",
                "--no-hashes",
                "--no-emit-project",
                "--project",
                str(SOURCE_DIR),
                "-o",
                str(req_file),
            ],
            check=False,
        )
        run(
            [
                uv_bin,
                "pip",
                "install",
                "--python",
                str(VENV_DIR / "bin" / "python"),
                "-r",
                str(req_file),
            ],
            check=False,
        )
    else:
        raise RuntimeError("uv and uv.lock are required for reproducible installation; install uv and retry")
    C.ok("Dependencies installed")


def step_install_services(args: argparse.Namespace) -> None:
    running = detect_running_services()
    to_skip = set(args.skip or [])

    for svc in SERVICES:
        if svc.name in to_skip:
            C.info(f"Skipping {svc.name} (user requested)")
            continue

        if running[svc.name]:
            C.ok(f"{svc.name} already running on port {svc.default_port}")
            continue

        C.warn(f"{svc.name} not detected on port {svc.default_port}")

        if not args.auto_install:
            if svc.optional:
                C.info(f"  Skipping optional service {svc.name}")
                continue
            C.fail(f"  Required service {svc.name} is not running")
            C.info("  Install it manually or re-run with --auto-install")
            continue

        try:
            install_package(svc)
            start_service(svc)
            time.sleep(2)
            if is_service_running(svc):
                C.ok(f"{svc.name} installed and started on port {svc.default_port}")
            else:
                C.warn(f"{svc.name} installed but may not be running yet on port {svc.default_port}")
        except SystemExit:
            raise
        except Exception as e:
            C.fail(f"Failed to install {svc.name}: {e}")


def step_create_config(args: argparse.Namespace) -> None:
    config = load_config()
    if not config:
        config = {"service": {"name": "monkeybrain", "version": "1.0.0"}}

    config["service"]["port"] = args.port

    db_config = config.setdefault("databases", {})

    mongo_url = args.mongo_url or f"mongodb://localhost:{args.mongo_port}"
    redis_url = args.redis_url or f"redis://localhost:{args.redis_port}"
    neo4j_uri = args.neo4j_uri or f"bolt://localhost:{args.neo4j_port}"
    influx_url = args.influxdb_url or f"http://localhost:{args.influxdb_port}"
    es_url = args.elasticsearch_url or f"http://localhost:{args.elasticsearch_port}"
    nats_url = args.nats_url or f"nats://localhost:{args.nats_port}"

    db_config["mongodb"] = {
        "url": mongo_url,
        "database": args.db_name,
        "port": args.mongo_port,
    }
    db_config["redis"] = {"url": redis_url, "port": args.redis_port}
    db_config["neo4j"] = {
        "uri": neo4j_uri,
        "user": args.neo4j_user,
        "password": args.neo4j_password,
        "port": args.neo4j_port,
    }
    db_config["influxdb"] = {
        "url": influx_url,
        "org": args.influxdb_org,
        "bucket": args.influxdb_bucket,
        "port": args.influxdb_port,
    }
    db_config["elasticsearch"] = {"url": es_url, "port": args.elasticsearch_port}
    db_config["nats"] = {"url": nats_url, "port": args.nats_port}

    config["kernel"] = config.get("kernel", {})
    config["kernel"]["exploration_rate"] = config["kernel"].get("exploration_rate", 0.1)
    config["kernel"]["learning_rate"] = config["kernel"].get("learning_rate", 0.1)
    config["kernel"]["discount_factor"] = config["kernel"].get("discount_factor", 0.95)

    save_config(config)
    C.ok(f"Configuration saved to {CONFIG_DIR / 'config.json'}")

    env_lines = [
        f"MONGODB_URL={mongo_url}",
        f"DB_NAME={args.db_name}",
        f"REDIS_URL={redis_url}",
        f"NEO4J_URI={neo4j_uri}",
        f"NEO4J_USER={args.neo4j_user}",
        f"NEO4J_PASSWORD={args.neo4j_password}",
        f"NATS_URL={nats_url}",
        "NATS_EVENTS_SUBJECT=indus.websocket.events",
        "NATS_EVENTS_QUEUE=indus-influx-consumers",
        f"INFLUXDB_URL={influx_url}",
        f"INFLUXDB_ORG={args.influxdb_org}",
        f"INFLUXDB_BUCKET={args.influxdb_bucket}",
        f"INFLUXDB_TOKEN={args.influxdb_token}",
        f"AUDIT_ELASTICSEARCH_URL={es_url}",
        "MQTT_ENABLED=false",
    ]
    env_path = INSTALL_DIR / ".env"
    env_path.write_text("\n".join(env_lines) + "\n")
    C.ok(f"Environment written to {env_path}")


def step_generate_secrets() -> None:
    secrets_data = {}
    if SECRET_FILE.exists():
        secrets_data = json.loads(SECRET_FILE.read_text())
        C.ok("Secrets already exist, keeping existing values")
        return

    secrets_data = {
        "jwt_secret": secrets.token_urlsafe(64),
        "api_key": secrets.token_urlsafe(32),
        "neo4j_password": secrets.token_urlsafe(16),
    }
    SECRET_FILE.write_text(json.dumps(secrets_data, indent=2))
    os.chmod(SECRET_FILE, 0o600)
    C.ok(f"Secrets generated at {SECRET_FILE}")


def step_init_db() -> None:
    config = load_config()
    db_cfg = config.get("databases", {})
    mongo_url = db_cfg.get("mongodb", {}).get("url", "mongodb://localhost:27017")
    db_name = db_cfg.get("mongodb", {}).get("database", "demo")

    try:
        from pymongo import MongoClient

        client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        db = client[db_name]
        collections = db.list_collection_names()
        C.ok(f"MongoDB connected: {len(collections)} collections in '{db_name}'")

        for idx in range(27):
            name = f"USER-{str(idx).zfill(3)}"
            if db.users.find_one({"_id": name}):
                C.ok(f"  Users seeded: found {name}")
                break
    except Exception as e:
        C.warn(f"MongoDB check failed: {e}")


def step_create_scripts() -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    start_script = BIN_DIR / "monkeybrain"
    start_script.write_text(
        textwrap.dedent(f"""\
        #!/bin/bash
        cd "{SOURCE_DIR}"
        exec "{VENV_DIR / "bin" / "python"}" main.py "$@"
    """)
    )
    start_script.chmod(0o755)

    status_script = BIN_DIR / "monkeybrain-status"
    status_script.write_text(
        textwrap.dedent(f"""\
        #!/bin/bash
        cd "{SOURCE_DIR}"
        exec "{VENV_DIR / "bin" / "python"}" install_agentos.py status "$@"
    """)
    )
    status_script.chmod(0o755)

    C.ok(f"CLI scripts created in {BIN_DIR}")


# ── Commands ───────────────────────────────────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> int:
    print(f"""
{C.B}{C.CY}
  ╔══════════════════════════════════════════════════════════╗
  ║                  MonkeyBrain Installer                   ║
  ║                  Cognitive Operating System              ║
  ╚══════════════════════════════════════════════════════════╝
{C.R}""")

    steps = [
        ("Checking Python", step_check_python),
        ("Detecting platform", step_detect_platform),
        ("Creating directories", step_create_dirs),
        ("Creating virtual environment", step_create_venv),
        ("Installing dependencies", step_install_deps),
        ("Checking & installing services", lambda: step_install_services(args)),
        ("Creating configuration", lambda: step_create_config(args)),
        ("Generating secrets", step_generate_secrets),
        ("Checking database", step_init_db),
        ("Creating CLI scripts", step_create_scripts),
    ]

    for name, fn in steps:
        C.step(name)
        try:
            fn()
        except SystemExit:
            raise
        except Exception as e:
            C.fail(f"Failed: {e}")
            return 1

    print(f"\n{C.B}{C.G}  MonkeyBrain installed successfully!{C.R}\n")
    print(f"  Start:     {BIN_DIR}/monkeybrain")
    print("  Status:    python install_agentos.py status")
    print(f"  Config:    {CONFIG_DIR / 'config.json'}")
    print(f"  Secrets:   {SECRET_FILE}")
    print(f"  Logs:      {LOG_DIR}")
    print()
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    C.step("Starting MonkeyBrain services")

    running = detect_running_services()
    to_skip = set(args.skip or [])

    for svc in SERVICES:
        if svc.name in to_skip:
            continue
        if running[svc.name]:
            C.ok(f"{svc.name} already running on port {svc.default_port}")
        else:
            C.info(f"Starting {svc.name}...")
            start_service(svc)
            time.sleep(1)
            if is_service_running(svc):
                C.ok(f"{svc.name} started on port {svc.default_port}")
            else:
                C.warn(f"{svc.name} may not have started")

    C.step("Starting API server")
    os.chdir(SOURCE_DIR)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SOURCE_DIR)

    config = load_config()
    port = args.port or config.get("service", {}).get("port", 8031)
    env["PORT"] = str(port)
    python = str(VENV_DIR / "bin" / "python") if VENV_DIR.exists() else sys.executable

    proc = subprocess.Popen(
        [python, "main.py"],
        cwd=str(SOURCE_DIR),
        env=env,
    )
    C.ok(f"MonkeyBrain started (PID {proc.pid})")

    try:
        proc.wait()
    except KeyboardInterrupt:
        C.step("Shutting down...")
        proc.terminate()
        proc.wait(timeout=10)

    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    C.step("Stopping MonkeyBrain services")

    for svc in SERVICES:
        if is_service_running(svc):
            stop_service(svc)
            time.sleep(0.5)
            if not is_service_running(svc):
                C.ok(f"{svc.name} stopped")
            else:
                C.warn(f"{svc.name} still running")
        else:
            C.info(f"{svc.name} not running")

    C.step("Stopping API processes")
    try:
        result = run(["pgrep", "-f", "uvicorn.*main:app"], check=False)
        if result.stdout.strip():
            for pid_str in result.stdout.strip().split("\n"):
                pid = int(pid_str.strip())
                try:
                    os.kill(pid, signal.SIGTERM)
                    C.ok(f"Killed API process {pid}")
                except ProcessLookupError:
                    logger.debug("cmd_stop: suppressed exception", exc_info=True)
        else:
            C.info("No API processes found")
    except Exception as e:
        logger.debug("Could not check for API processes: %s", e)

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    C.step("MonkeyBrain Service Status")

    running = detect_running_services()
    all_ok = True

    for svc in SERVICES:
        is_running = running[svc.name]
        status = f"{C.G}RUNNING{C.R}" if is_running else f"{C.R_}STOPPED{C.R}"
        optional = " (optional)" if svc.optional else ""
        C.info(f"  {svc.name:16} :{svc.default_port:<6} {status}{optional}")
        if not is_running and not svc.optional:
            all_ok = False

    C.step("API Services")
    api_running = False
    for port in range(8000, 8033):
        if port_in_use(port):
            api_running = True
            C.info(f"  Port {port}: {C.G}LISTENING{C.R}")

    if not api_running:
        C.info("  No API services detected")

    C.step("Paths")
    C.info(f"  Install:  {INSTALL_DIR}")
    C.info(f"  Venv:     {VENV_DIR}")
    C.info(f"  Config:   {CONFIG_DIR / 'config.json'}")
    C.info(f"  Secrets:  {SECRET_FILE}")
    C.info(f"  Logs:     {LOG_DIR}")
    C.info(f"  CLI:      {BIN_DIR}/monkeybrain")

    if all_ok:
        print(f"\n  {C.G}All required services running{C.R}")
    else:
        print(f"\n  {C.Y}Some required services are stopped{C.R}")
        print("  Run: python install_agentos.py start --auto-install")

    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    C.step("Reconfiguring MonkeyBrain")
    step_create_config(args)
    C.ok("Configuration updated. Restart services to apply.")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    C.step("Seeding domain data")
    os.chdir(SOURCE_DIR)
    venv_python = str(VENV_DIR / "bin" / "python") if VENV_DIR.exists() else sys.executable

    seed_dir = SOURCE_DIR / "scripts"
    seed_files = sorted(seed_dir.glob("seed_*.py"))

    if not seed_files:
        C.warn("No seed scripts found")
        return 0

    for sf in seed_files:
        C.info(f"Running {sf.name}...")
        result = run([venv_python, str(sf)], check=False)
        if result.returncode == 0:
            C.ok(f"{sf.name} completed")
        else:
            err = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown error"
            C.warn(f"{sf.name} failed: {err}")

    C.ok("Seeding complete")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    log_files = sorted(LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        C.warn("No log files found")
        return 0
    C.info(f"Tailing {log_files[0]}")
    with contextlib.suppress(KeyboardInterrupt):
        subprocess.run(["tail", "-f", str(log_files[0])])
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Call /plan API and display the execution graph."""
    import urllib.error
    import urllib.request

    url = f"http://localhost:{args.port}/api/v1/agentos/plan"
    payload = json.dumps(
        {
            "question": args.question,
            "target": args.target,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        C.fail(f"Failed to reach API at {url}: {e}")
        return 1
    except Exception as e:
        C.fail(f"Plan request failed: {e}")
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    # Display formatted output
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    execution_order = data.get("execution_order") or []

    C.ok(f"Plan: {data.get('run_id', '?')}")
    print(f"  Question: {data.get('question', '?')[:80]}")
    print(f"  Workload: {data.get('workload_id') or '(none)'}")
    print()

    if not nodes:
        C.warn("Planner returned no nodes — graph is empty.")
        return 0

    id_to_name = {n["id"]: n.get("name") or n.get("agent") or n["id"] for n in nodes}

    C.info("Nodes:")
    for n in nodes:
        nid = n["id"]
        name = n.get("name") or n.get("agent") or nid
        print(f"  {nid:>8}  {name}")
    print()

    C.info("Edges (dependency → must wait for):")
    if edges:
        for e in edges:
            src = id_to_name.get(e["from"], e["from"])
            dst = id_to_name.get(e["to"], e["to"])
            print(f"  {e['from']:>8} ({src})  →  {e['to']:>8} ({dst})")
    else:
        print("  (none — all nodes are independent)")
    print()

    C.info("Execution order:")
    for i, batch in enumerate(execution_order):
        names = [id_to_name.get(nid, nid) for nid in batch]
        print(f"  Step {i + 1}: {' | '.join(names)}")
    print()

    intent_ir = data.get("intent_ir")
    if intent_ir:
        C.info("IntentIR:")
        print(f"  intent_type: {intent_ir.get('intent_type', '?')}")
        print(f"  confidence:  {intent_ir.get('confidence', '?')}")
        print(f"  run_id:      {intent_ir.get('run_id', '?')}")

    return 0


def cmd_act(args: argparse.Namespace) -> int:
    """Plan and execute a question end-to-end via the API."""
    import urllib.error
    import urllib.request

    base_url = f"http://localhost:{args.port}"

    # 1. Plan
    C.info("Planning...")
    plan_url = f"{base_url}/api/v1/agentos/plan"
    plan_payload = json.dumps(
        {
            "question": args.question,
            "target": "execute",
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            plan_url,
            data=plan_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            plan_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        C.fail(f"Plan failed: {e}")
        return 1

    run_id = plan_data.get("run_id")
    if not run_id:
        C.fail("No run_id in plan response")
        return 1

    if not plan_data.get("nodes"):
        C.warn("Planner returned no nodes — nothing to execute")
        return 0

    C.ok(f"Plan ready: {run_id}")

    # 2. Execute
    C.info("Executing...")
    exec_url = f"{base_url}/api/v1/agentos/execute"
    exec_payload = json.dumps(plan_data).encode("utf-8")

    try:
        req = urllib.request.Request(
            exec_url,
            data=exec_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            exec_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        C.fail(f"Execute failed: {e}")
        return 1

    if args.json:
        print(json.dumps(exec_data, indent=2))
        return 0

    # Display formatted output
    C.ok(f"Execute: {exec_data.get('run_id', '?')}")
    print(f"  Question: {exec_data.get('question', '?')[:80]}")
    print(f"  Answer:   {exec_data.get('answer', '?')[:200]}")
    print(f"  Latency:  {exec_data.get('elapsed_ms', 0):.0f}ms")
    print(f"  LLM:      {exec_data.get('llm_answered', False)}")

    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover: plan, execute, or chat with an agent.

    --spec     Plan only (show the execution graph)
    --execute  Plan and execute end-to-end
    --act      Execute directly via capability classification (no agent name needed)
    --observe  Show Lemon observability traces after operation
    --log      Show server logs after operation
    """
    import urllib.error
    import urllib.request

    base_url = f"http://localhost:{args.port}"

    def _show_observability():
        """Fetch and display the cognitive dashboard."""

        def _gauge(gauges, prefix):
            for k, v in gauges.items():
                if k.startswith(prefix):
                    return v
            return 0.0

        try:
            req = urllib.request.Request(
                f"{base_url}/api/v1/agentos/dashboard/current",
                headers={"Content-Type": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            C.fail(f"Dashboard fetch failed: {e}")
            return

        planning = data.get("planning", {})
        execution = data.get("execution", {})
        simulation = data.get("simulation", {})
        learning = data.get("learning", {})
        mutations = data.get("mutations", [])
        bellman = data.get("bellman", {})
        consensus = data.get("consensus", {})
        graph = data.get("graph", {})
        health = data.get("health", {})
        metrics = data.get("metrics", {})
        timeline = data.get("timeline", [])

        print()
        print(f"{C.B}═══════════════════════════════════════════════════════{C.R}")
        print(f"{C.B}MonkeyBrain Cognitive Dashboard{C.R}")
        print(f"{C.B}═══════════════════════════════════════════════════════{C.R}")
        print()

        # ── Planning ──
        print(f"{C.B}Planning{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        print(f"  Intent:          {planning.get('intent', '—')}")
        print(f"  Confidence:      {planning.get('intent_confidence', 0.0):.2f}")
        print(f"  Graph ID:        {graph.get('graph_id', '—')}")
        print(f"  Nodes:           {graph.get('node_count', 0)}")
        print(f"  Edges:           {graph.get('edge_count', 0)}")
        print(f"  Execution Layers:{graph.get('depth', 0)}")
        print(f"  Agents:          {', '.join(planning.get('selected_agents', [])[:6])}")
        print()

        # ── Execution ──
        print(f"{C.B}Execution{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        print(f"  Completed: {execution.get('completed_nodes', 0)}")
        print(f"  Running:   {execution.get('running_nodes', 0)}")
        print(f"  Failed:    {execution.get('failed_nodes', 0)}")
        print(f"  Total:     {execution.get('total_nodes', 0)}")
        print()

        # ── Simulation ──
        print(f"{C.B}Simulation{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        solver_results = simulation.get("solver_results", [])
        print(f"  Solvers:    {len(solver_results)}")
        print(f"  Consensus:  Byzantine ({simulation.get('consensus_score', 0.0):.2f})")
        print(f"  Grounding:  {simulation.get('grounding_score', 0.0):.2f}")
        print(f"  Verdict:    {simulation.get('feasibility_verdict', 'unknown')}")
        for sr in solver_results[:5]:
            print(f"    {sr.get('solver_name', '?')}: conf={sr.get('confidence', 0.0):.2f}")
        print()

        # ── Learning ──
        print(f"{C.B}Learning{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        print(f"  Topological Loss: {learning.get('topological_loss', 0.0):.2f}")
        print(f"  Epistemic Loss:   {learning.get('epistemic_loss', 0.0):.2f}")
        print(f"  Perplexity:       {learning.get('perplexity', 0.0):.2f}")
        print(f"  Bellman Q:        {learning.get('bellman_q', 0.0):.2f}")
        print(f"  Policy Updates:   {learning.get('policy_updates', 0)}")
        print(f"  Converged:        {'Yes' if learning.get('converged', False) else 'No'}")
        print()

        # ── Bellman ──
        print(f"{C.B}Bellman{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        q_entries = bellman.get("q_table_entries", 0)
        print(f"  Q-Table Entries: {q_entries}")
        print(f"  Learning Rate:   {bellman.get('learning_rate', 0.0):.3f}")
        print(f"  Discount:        {bellman.get('discount_factor', 0.0):.3f}")
        q_values = bellman.get("q_values", {})
        if q_values:
            sorted_q = sorted(q_values.items(), key=lambda x: -x[1])[:5]
            for agent, val in sorted_q:
                print(f"    {agent}: {val:.3f}")
        print()

        # ── Consensus ──
        print(f"{C.B}Consensus{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        print(f"  Score:     {consensus.get('consensus_score', 0.0):.2f}")
        print(f"  Agreement: {consensus.get('byzantine_agreement', 0.0):.2f}")
        print()

        # ── Graph ──
        print(f"{C.B}Graph{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        order = graph.get("execution_order", [])
        print(f"  Nodes: {len(nodes)}")
        print(f"  Edges: {len(edges)}")
        print("  Execution Order:")
        id_to_name = {n.get("node_id", ""): n.get("agent", "") for n in nodes}
        for i, batch in enumerate(order):
            if isinstance(batch, list):
                names = [id_to_name.get(nid, nid) for nid in batch]
                print(f"    Step {i + 1}: {' | '.join(names)}")
            else:
                print(f"    Step {i + 1}: {batch}")
        print()

        # ── Mutations ──
        print(f"{C.B}Mutations{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        for m in mutations[-6:]:
            ts = m.get("timestamp", "")
            ts_short = ts[11:19] if len(ts) > 19 else ts
            print(f"  {ts_short}  {m.get('runtime', ''):12}  {m.get('operation', '')}")
        if not mutations:
            print("  (none)")
        print()

        # ── Health ──
        print(f"{C.B}Health{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        components = health.get("components", [])
        for comp in components:
            status = comp.get("status", "unknown")
            if status == "healthy":
                icon = f"{C.G}✓{C.R}"
            elif status == "degraded":
                icon = f"{C.Y}⚠{C.R}"
            else:
                icon = f"{C.DIM}?{C.R}"
            print(f"  {comp.get('name', '?'):20} {icon}")
        print()

        # ── Metrics ──
        print(f"{C.B}Metrics{C.R}")
        print(f"{C.DIM}────────────────────────────{C.R}")
        print(f"  Planner Latency:     {metrics.get('planner_latency_ms', 0):.0f}ms")
        print(f"  Execution Latency:   {metrics.get('execution_latency_ms', 0):.0f}ms")
        print(f"  Simulation Latency:  {metrics.get('simulation_latency_ms', 0):.0f}ms")
        print(f"  Learning Latency:    {metrics.get('learning_latency_ms', 0):.0f}ms")
        print(f"  Total Runtime:       {metrics.get('total_runtime_ms', 0):.0f}ms")
        print(f"  Event Count:         {metrics.get('event_count', 0)}")
        print(f"  Policy Updates:      {metrics.get('policy_updates', 0)}")
        counters = metrics.get("counters", {})
        gauges = metrics.get("gauges", {})
        histograms = metrics.get("histograms", {})
        print(f"  Active Counters:     {len(counters)}")
        print(f"  Active Gauges:       {len(gauges)}")
        print(f"  Active Histograms:   {len(histograms)}")
        print()

        # ── Timeline ──
        if timeline:
            print(f"{C.B}Timeline{C.R}")
            print(f"{C.DIM}────────────────────────────{C.R}")
            for entry in timeline[-8:]:
                ts = entry.get("timestamp", "")
                ts_short = ts[11:19] if len(ts) > 19 else ts
                print(f"  {ts_short}  {entry.get('phase', ''):12}  {entry.get('event', '')}")
            print()

    def _show_logs():
        """Fetch and display recent server logs."""
        try:
            req = urllib.request.Request(
                f"{base_url}/api/v1/agentos/observability",
                headers={"Content-Type": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            C.fail(f"Log fetch failed: {e}")
            return

        C.step("Recent Logs")
        logs = data.get("logs", [])
        if not logs:
            C.info("No logs available")
            return

        for entry in logs[-20:]:
            level = entry.get("severity", "info").upper()
            msg = entry.get("message", "")
            component = entry.get("component", "")
            ts = entry.get("timestamp", "")

            if level in ("ERROR", "FATAL"):
                color = C.R_
            elif level in ("WARN",):
                color = C.Y
            else:
                color = C.DIM

            prefix = f"[{component}]" if component else ""
            ts_short = ts[11:19] if len(ts) > 19 else ts
            print(f"  {color}{level:8}{C.R} {ts_short} {prefix:20} {msg[:100]}")

    def _show_dashboard():
        """Launch live Dash dashboard server."""
        import subprocess
        import webbrowser

        dash_port = 8050
        dash_url = f"http://localhost:{dash_port}"

        print(f"  Starting Dashboard server on {dash_url} ...")
        print(f"  Polling API at {base_url}")

        try:
            webbrowser.open(dash_url)
        except Exception:
            logger.debug("_show_dashboard: suppressed exception", exc_info=True)

        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "src.monkey_brain.dashboard.app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(dash_port),
                    "--api",
                    base_url,
                ],
                cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
            )
            C.ok(f"Dashboard launched: {dash_url}")
        except Exception as e:
            C.fail(f"Dashboard launch failed: {e}")

    # --simulate: simulate via simulation runtime
    if getattr(args, "simulate", False):
        print(f"Planning: {args.question}")

        plan_url = f"{base_url}/api/v1/agentos/plan"
        plan_payload = json.dumps({"question": args.question, "target": "simulate"}).encode("utf-8")

        try:
            req = urllib.request.Request(
                plan_url,
                data=plan_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                plan_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            C.fail(f"Plan failed: {e}")
            return 1

        graph = plan_data.get("graph", {})
        print(f"  Plan: graph_id={graph.get('graph_id', '?')}, nodes={len(graph.get('nodes', []))}")

        print("Simulating...")
        sim_url = f"{base_url}/api/v1/agentos/simulate"

        try:
            req = urllib.request.Request(
                sim_url,
                data=json.dumps(plan_data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            C.fail(f"Simulation failed: {e}")
            return 1

        if args.json:
            print(json.dumps(data, indent=2))
            return 0

        graph = data.get("graph", {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        execution_order = graph.get("execution_order", [])

        metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
        grounding_score = data.get(
            "grounding_score",
            data.get("grounding_confidence", metadata.get("grounding_score", 0.0)),
        )
        low_grounding = bool(data.get("low_grounding", grounding_score < 0.5))
        needs_correction = bool(data.get("needs_correction", low_grounding))
        simulation_id = data.get("simulation_id") or metadata.get("simulation_id", "?")

        if low_grounding:
            C.warn(f"Simulation: {simulation_id}")
        else:
            C.ok(f"Simulation: {simulation_id}")
        print(f"  Question: {data.get('question', '?')[:80]}")
        print(f"  Graph ID: {graph.get('graph_id', '?')}")
        print(f"  Grounding score: {grounding_score:.2f}")
        print(f"  Status: {'LOW_GROUNDING' if low_grounding else 'OK'}")
        print(f"  Needs correction: {needs_correction}")
        print()

        if nodes:
            id_to_name = {n.get("id", ""): n.get("name") or n.get("label") or n.get("id", "") for n in nodes}
            C.info("Nodes:")
            for n in nodes:
                nid = n.get("id", "")
                name = id_to_name.get(nid, nid)
                print(f"  {nid:>8}  {name}")
            print()

        if edges:
            C.info("Edges:")
            for e in edges:
                src = id_to_name.get(e.get("from", ""), e.get("from", ""))
                dst = id_to_name.get(e.get("to", ""), e.get("to", ""))
                print(f"  {e.get('from', ''):>8} ({src})  ->  {e.get('to', ''):>8} ({dst})")
            print()

        if execution_order:
            C.info("Execution order:")
            for i, batch in enumerate(execution_order):
                if isinstance(batch, list):
                    names = [id_to_name.get(nid, nid) for nid in batch]
                    print(f"  Step {i + 1}: {' | '.join(names)}")
                else:
                    print(f"  Step {i + 1}: {batch}")
            print()

        if getattr(args, "observe", False):
            _show_observability()
        if getattr(args, "dashboard", False):
            _show_dashboard()
        if getattr(args, "log", False):
            _show_logs()

        return 0

    # --act: execute directly via capability classification
    if args.act:
        print(f"Classifying capability for: {args.question}")

        exec_url = f"{base_url}/api/v1/agentos/execute-direct"
        payload = json.dumps({"question": args.question}).encode("utf-8")

        try:
            req = urllib.request.Request(
                exec_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            C.fail(f"Execution failed: {e}")
            return 1

        if args.json:
            print(json.dumps(data, indent=2))
            return 0

        # Extract answer from CapabilityResult format
        answer = data.get("response", {}).get("answer", "") or data.get("answer", "?")
        capability = data.get("metadata", {}).get("capability", "?")
        print(f"\n  Capability: {capability}")
        print(f"  Answer: {answer[:300]}")
        print(f"  Success: {data.get('success', False)}")
        print(f"  Latency: {data.get('elapsed_ms', 0):.0f}ms")

        if getattr(args, "observe", False):
            _show_observability()
        if getattr(args, "dashboard", False):
            _show_dashboard()
        if getattr(args, "log", False):
            _show_logs()

        return 0

    # --spec or default: plan only
    if not args.execute:
        print(f"Planning: {args.question}")
        plan_url = f"{base_url}/api/v1/agentos/plan"
        plan_payload = json.dumps(
            {
                "question": args.question,
                "target": "execute",
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(
                plan_url,
                data=plan_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                plan_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            C.fail(f"Plan failed: {e}")
            return 1

        if args.json:
            print(json.dumps(plan_data, indent=2))
            return 0

        parsed = plan_data.get("parsed_answer")
        if not parsed and "answer" in plan_data and isinstance(plan_data["answer"], str):
            try:
                parsed = json.loads(plan_data["answer"])
            except json.JSONDecodeError:
                parsed = None
        parsed = parsed or plan_data

        graph = parsed.get("graph", parsed)
        steps = parsed.get("steps") or graph.get("nodes", [])
        edges = parsed.get("edges") or graph.get("edges", [])
        order = parsed.get("execution_order") or graph.get("execution_order", [])

        if not steps:
            print("No steps generated.")
            return 0

        id_to_name = {n["id"]: n.get("name") or n.get("agent") or n["id"] for n in steps}

        print(f"\nPlan: {plan_data.get('run_id', '?')}")
        print(f"  Steps: {len(steps)}")
        print(f"  Edges: {len(edges)}")
        for i, batch in enumerate(order):
            names = [id_to_name.get(nid, nid) for nid in batch]
            print(f"  Step {i + 1}: {' | '.join(names)}")

        if getattr(args, "observe", False):
            _show_observability()
        if getattr(args, "dashboard", False):
            _show_dashboard()
        if getattr(args, "log", False):
            _show_logs()

        return 0

    # --execute: plan and execute
    print(f"Planning: {args.question}")
    plan_url = f"{base_url}/api/v1/agentos/plan"
    plan_payload = json.dumps(
        {
            "question": args.question,
            "target": "execute",
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            plan_url,
            data=plan_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            plan_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        C.fail(f"Plan failed: {e}")
        return 1

    if args.json:
        print(json.dumps(plan_data, indent=2))
        return 0

    parsed = plan_data.get("parsed_answer")
    if not parsed and "answer" in plan_data and isinstance(plan_data["answer"], str):
        try:
            parsed = json.loads(plan_data["answer"])
        except json.JSONDecodeError:
            parsed = None
    parsed = parsed or plan_data

    graph = parsed.get("graph", parsed)
    steps = parsed.get("steps") or graph.get("nodes", [])
    edges = parsed.get("edges") or graph.get("edges", [])
    order = parsed.get("execution_order") or graph.get("execution_order", [])

    if not steps:
        print("No steps generated.")
        return 0

    id_to_name = {n["id"]: n.get("name") or n.get("agent") or n["id"] for n in steps}

    print(f"\nPlan: {plan_data.get('run_id', '?')}")
    print(f"  Steps: {len(steps)}")
    print(f"  Edges: {len(edges)}")
    for i, batch in enumerate(order):
        names = [id_to_name.get(nid, nid) for nid in batch]
        print(f"  Step {i + 1}: {' | '.join(names)}")

    # Execute
    print("\nExecuting...")
    exec_url = f"{base_url}/api/v1/agentos/execute"
    exec_payload = json.dumps(plan_data).encode("utf-8")

    try:
        req = urllib.request.Request(
            exec_url,
            data=exec_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            exec_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        C.fail(f"Execute failed: {e}")
        return 1

    if args.json:
        print(json.dumps(exec_data, indent=2))
        return 0

    print(f"\nExecute: {exec_data.get('run_id', '?')}")
    print(f"  Answer: {exec_data.get('answer', '?')[:300]}")
    print(f"  Latency: {exec_data.get('elapsed_ms', 0):.0f}ms")
    print(f"  LLM: {exec_data.get('llm_answered', False)}")

    if getattr(args, "observe", False):
        _show_observability()
    if getattr(args, "dashboard", False):
        _show_dashboard()
    if getattr(args, "log", False):
        _show_logs()

    return 0


# ── CLI Parser ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monkeybrain",
        description="MonkeyBrain — Cognitive Operating System installer & manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s install                              Full install with defaults
              %(prog)s install --mongo-port 27018           Custom MongoDB port
              %(prog)s install --auto-install               Auto-install missing services
              %(prog)s install --skip neo4j elasticsearch   Skip optional services
              %(prog)s start                                Start all services
              %(prog)s start --skip nats                    Start without NATS
              %(prog)s configure --influxdb-url http://host:8086
              %(prog)s status                               Show all service statuses
              %(prog)s seed                                 Run all seed scripts
              %(prog)s knowledge export                     Export knowledge bundle to file
              %(prog)s knowledge import <file>              Import knowledge bundle from file
        """),
    )

    sub = parser.add_subparsers(dest="command", help="Command to run")

    db_group = argparse.ArgumentParser(add_help=False)
    db = db_group.add_argument_group("Database configuration")
    db.add_argument("--mongo-port", type=int, default=27017, help="MongoDB port (default: 27017)")
    db.add_argument("--mongo-url", type=str, default=None, help="MongoDB full URL")
    db.add_argument(
        "--db-name",
        type=str,
        default="demo",
        help="MongoDB database name (default: demo)",
    )
    db.add_argument("--redis-port", type=int, default=6379, help="Redis port (default: 6379)")
    db.add_argument("--redis-url", type=str, default=None, help="Redis full URL")
    db.add_argument("--neo4j-port", type=int, default=7687, help="Neo4j Bolt port (default: 7687)")
    db.add_argument("--neo4j-uri", type=str, default=None, help="Neo4j full URI")
    db.add_argument(
        "--neo4j-user",
        type=str,
        default="neo4j",
        help="Neo4j username (default: neo4j)",
    )
    db.add_argument(
        "--neo4j-password",
        type=str,
        default=os.getenv("NEO4J_PASSWORD", ""),
        help="Neo4j password (env: NEO4J_PASSWORD)",
    )
    db.add_argument("--influxdb-port", type=int, default=8181, help="InfluxDB port (default: 8181)")
    db.add_argument("--influxdb-url", type=str, default=None, help="InfluxDB full URL")
    db.add_argument(
        "--influxdb-org",
        type=str,
        default="indus",
        help="InfluxDB org (default: indus)",
    )
    db.add_argument(
        "--influxdb-bucket",
        type=str,
        default="events",
        help="InfluxDB bucket (default: events)",
    )
    db.add_argument(
        "--influxdb-token",
        type=str,
        default=os.getenv("INFLUXDB_TOKEN", ""),
        help="InfluxDB token (env: INFLUXDB_TOKEN)",
    )
    db.add_argument(
        "--elasticsearch-port",
        type=int,
        default=9200,
        help="Elasticsearch port (default: 9200)",
    )
    db.add_argument("--elasticsearch-url", type=str, default=None, help="Elasticsearch full URL")
    db.add_argument("--nats-port", type=int, default=4222, help="NATS port (default: 4222)")
    db.add_argument("--nats-url", type=str, default=None, help="NATS full URL")
    db.add_argument("--port", type=int, default=8031, help="MonkeyBrain API port (default: 8031)")

    install_p = sub.add_parser("install", parents=[db_group], help="Full installation")
    install_p.add_argument(
        "--auto-install",
        action="store_true",
        help="Auto-install missing database services",
    )
    install_p.add_argument(
        "--skip",
        nargs="*",
        default=[],
        help="Services to skip (e.g. neo4j elasticsearch)",
    )

    start_p = sub.add_parser("start", parents=[db_group], help="Start all services")
    start_p.add_argument("--skip", nargs="*", default=[], help="Services to skip")

    sub.add_parser("stop", help="Stop all services")
    sub.add_parser("status", help="Show status of all services")

    sub.add_parser("configure", parents=[db_group], help="Reconfigure database connections")

    sub.add_parser("seed", help="Seed domain data")
    sub.add_parser("logs", help="Tail service logs")

    plan_p = sub.add_parser("plan", help="Generate an execution graph for a question")
    plan_p.add_argument("question", help="The question to plan")
    plan_p.add_argument(
        "--target",
        default="execute",
        choices=["execute", "simulate", "compare"],
        help="Runtime target (default: execute)",
    )
    plan_p.add_argument("--port", type=int, default=8031, help="API port (default: 8031)")
    plan_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted display",
    )

    act_p = sub.add_parser("act", help="Plan and execute a question end-to-end")
    act_p.add_argument("question", help="The question to plan and execute")
    act_p.add_argument("--port", type=int, default=8031, help="API port (default: 8031)")
    act_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted display",
    )

    discover_p = sub.add_parser("discover", help="Discover: plan, execute, or chat with an agent")
    discover_p.add_argument("question", help="The question to discover")
    discover_p.add_argument("--spec", action="store_true", help="Plan only (show the execution graph)")
    discover_p.add_argument("--execute", action="store_true", help="Plan and execute end-to-end")
    discover_p.add_argument(
        "--act",
        action="store_true",
        help="Execute directly via capability classification",
    )
    discover_p.add_argument("--simulate", action="store_true", help="Simulate via simulation runtime")
    discover_p.add_argument(
        "--observe",
        action="store_true",
        help="Show Lemon observability traces after operation",
    )
    discover_p.add_argument(
        "--dashboard",
        action="store_true",
        help="Generate interactive Plotly HTML dashboard",
    )
    discover_p.add_argument("--log", action="store_true", help="Show server logs after operation")
    discover_p.add_argument("--port", type=int, default=8031, help="API port (default: 8031)")
    discover_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # Knowledge subcommand group
    knowledge_p = sub.add_parser("knowledge", help="Knowledge export/import/version")
    knowledge_sub = knowledge_p.add_subparsers(dest="knowledge_command", help="Knowledge command")

    export_p = knowledge_sub.add_parser("export", help="Export knowledge bundle to file")
    export_p.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file path (default: knowledge_export_<timestamp>.json)",
    )
    export_p.add_argument("--port", type=int, default=8031, help="API port (default: 8031)")
    export_p.add_argument("--json", action="store_true", help="Output raw JSON")

    import_p = knowledge_sub.add_parser("import", help="Import knowledge bundle from file")
    import_p.add_argument("file", help="JSON file to import")
    import_p.add_argument("--origin", type=str, default="cli-import", help="Origin label for import")
    import_p.add_argument("--domain", type=str, default="default", help="Knowledge domain")
    import_p.add_argument(
        "--strategy",
        type=str,
        default="skip",
        choices=["skip", "higher", "local", "remote", "average"],
        help="Conflict resolution strategy (default: skip)",
    )
    import_p.add_argument("--port", type=int, default=8031, help="API port (default: 8031)")
    import_p.add_argument("--json", action="store_true", help="Output raw JSON")
    import_p.add_argument("--verbose", "-v", action="store_true", help="Show conflict details")

    version_p = knowledge_sub.add_parser("version", help="Check knowledge schema version")
    version_p.add_argument("--port", type=int, default=8031, help="API port (default: 8031)")
    version_p.add_argument("--json", action="store_true", help="Output raw JSON")

    return parser


# ── Knowledge CLI ────────────────────────────────────────────────────────────


def cmd_knowledge_export(args: argparse.Namespace) -> int:
    """Export knowledge from the runtime to a JSON file."""
    import urllib.error
    import urllib.request

    url = f"http://localhost:{args.port}/api/v1/agentos/knowledge/export"
    payload = json.dumps({}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        C.fail(f"Failed to reach API at {url}: {e}")
        return 1
    except Exception as e:
        C.fail(f"Export failed: {e}")
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    # Write to file
    output_path = args.output or f"knowledge_export_{int(data.get('exported_at', 0))}.json"
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    transitions = data.get("transitions", [])
    q_table = data.get("q_table", {})
    nodes = data.get("graph_nodes", [])
    edges = data.get("graph_edges", [])

    C.ok(f"Knowledge exported to {output_path}")
    print(f"  Runtime:    {data.get('runtime_id', '?')[:12]}")
    print(f"  Transitions: {len(transitions)}")
    print(f"  Q-table:    {len(q_table)} entries")
    print(f"  Graph:      {len(nodes)} nodes, {len(edges)} edges")
    print(f"  Signed:     {'yes' if data.get('signature') else 'no'}")
    print(f"  Version:    {data.get('version', '?')}")
    return 0


def cmd_knowledge_import(args: argparse.Namespace) -> int:
    """Import a knowledge bundle from a JSON file into the runtime."""
    import urllib.error
    import urllib.request

    # Read bundle from file
    try:
        with open(args.file) as f:
            bundle = json.load(f)
    except FileNotFoundError:
        C.fail(f"File not found: {args.file}")
        return 1
    except json.JSONDecodeError as e:
        C.fail(f"Invalid JSON in {args.file}: {e}")
        return 1

    url = f"http://localhost:{args.port}/api/v1/agentos/knowledge/import"
    payload = json.dumps(
        {
            "bundle": bundle,
            "origin": args.origin or "cli-import",
            "domain": args.domain or "default",
            "merge_strategy": args.strategy,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        C.fail(f"Failed to reach API at {url}: {e}")
        return 1
    except Exception as e:
        C.fail(f"Import failed: {e}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    C.ok(f"Knowledge import: {result.get('status', '?')}")
    print(f"  Accepted:   {result.get('accepted', 0)}")
    print(f"  Resolved:   {result.get('resolved', 0)}")
    print(f"  Rejected:   {result.get('rejected', 0)}")
    print(f"  Conflicts:  {len(result.get('conflicts', []))}")
    sig = result.get("signature_valid")
    if sig is not None:
        print(f"  Signature:  {'valid' if sig else 'INVALID'}")
    print(f"  Version:    {result.get('version', '?')}")

    conflicts = result.get("conflicts", [])
    if conflicts and args.verbose:
        print("\n  Conflicts:")
        for c in conflicts[:10]:
            print(f"    {c.get('edge', '?')}: local={c.get('local')} proposed={c.get('proposed')} → {c.get('action')}")
        if len(conflicts) > 10:
            print(f"    ... and {len(conflicts) - 10} more")

    return 0


def cmd_knowledge_version(args: argparse.Namespace) -> int:
    """Check the knowledge schema version."""
    import urllib.error
    import urllib.request

    url = f"http://localhost:{args.port}/api/v1/agentos/knowledge/version"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        C.fail(f"Failed to reach API at {url}: {e}")
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    C.ok(f"Knowledge schema version: {data.get('version', '?')}")
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    """Route knowledge subcommands."""
    sub_commands = {
        "export": cmd_knowledge_export,
        "import": cmd_knowledge_import,
        "version": cmd_knowledge_version,
    }
    sub = getattr(args, "knowledge_command", None)
    if not sub:
        C.fail("Usage: monkeybrain knowledge {export|import|version}")
        return 1
    handler = sub_commands.get(sub)
    if not handler:
        C.fail(f"Unknown knowledge command: {sub}")
        return 1
    return handler(args)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "install": cmd_install,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "configure": cmd_configure,
        "seed": cmd_seed,
        "logs": cmd_logs,
        "plan": cmd_plan,
        "act": cmd_act,
        "discover": cmd_discover,
        "knowledge": cmd_knowledge,
    }

    return commands[args.command](args)


def status_main() -> None:
    """Entry point for monkeybrain-status CLI command."""
    args = argparse.Namespace()
    cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
