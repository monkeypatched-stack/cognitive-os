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
import json
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
from dataclasses import dataclass, field
from pathlib import Path

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
        package_brew="mongodb-community@7.0",
        package_apt="mongosh",
        service_name_brew="mongodb-community@7.0",
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
        service_name_brew="influxdb",
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
    DBService(
        name="ollama",
        default_port=11434,
        env_url_var="OLLAMA_BASE_URL",
        default_url="http://localhost:11434",
        package_brew="ollama",
        package_apt="ollama",
        service_name_brew="ollama",
        service_name_apt="ollama",
        optional=True,
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
            pass
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


def _start_mongodb_direct() -> None:
    """Fall back to direct mongod invocation when brew services launchd trust fails."""
    conf = Path("/opt/homebrew/etc/mongod.conf")
    log = Path("/opt/homebrew/var/log/mongodb/mongo.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    if conf.exists():
        result = run(
            ["mongod", "--config", str(conf), "--fork", "--logpath", str(log)],
            check=False,
        )
    else:
        data = DATA_DIR / "mongodb"
        data.mkdir(parents=True, exist_ok=True)
        result = run(
            ["mongod", "--dbpath", str(data), "--fork", "--logpath", str(log)],
            check=False,
        )
    if result.returncode != 0:
        C.fail(f"mongod direct start failed: {result.stderr.strip()}")


def start_service(svc: DBService) -> None:
    pm = detect_package_manager()
    try:
        if pm == "brew" and svc.service_name_brew:
            result = run(["brew", "services", "start", svc.service_name_brew], check=False)
            # launchd trust failure (exit 1 + "Bootstrap failed") → fall back to direct launch
            if result.returncode != 0 and svc.name == "mongodb":
                C.info("brew services failed for mongodb — starting mongod directly")
                _start_mongodb_direct()
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
            with open(log, "w") as log_fh:
                proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT)
            pid_file.write_text(str(proc.pid))
            C.info(f"Started InfluxDB (PID {proc.pid})")
            return
        else:
            C.warn(f"Don't know how to start {svc.name} on {pm}")
            return
    except Exception as e:
        C.fail(f"Failed to start {svc.name}: {e}")


def _wait_for_running(svc: DBService, timeout: float = 8.0, interval: float = 1.0) -> bool:
    """Poll until the service port opens, or timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_service_running(svc):
            return True
        time.sleep(interval)
    return is_service_running(svc)


def ensure_service_up(svc: DBService, *, max_attempts: int = 3) -> bool:
    """Get svc running, retrying a fresh install+start if it doesn't come up.

    A single install_package()+start_service() pass can fail transiently
    (brew services races, launchd bootstrap taking longer than expected,
    a first-time install needing its data dir initialized) — so each
    attempt reinstalls (idempotent — brew/apt skip if already current)
    before starting again, rather than giving up after one try.
    """
    if is_service_running(svc):
        return True

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            C.info(f"  Retry {attempt}/{max_attempts}: reinstalling and restarting {svc.name}...")
        try:
            install_package(svc)
        except SystemExit:
            raise
        except Exception as e:
            C.warn(f"  {svc.name} install attempt {attempt} failed: {e}")

        try:
            start_service(svc)
        except Exception as e:
            C.warn(f"  {svc.name} start attempt {attempt} failed: {e}")

        if _wait_for_running(svc):
            return True

    return False


def stop_service(svc: DBService) -> None:
    pm = detect_package_manager()
    pid_file = PID_DIR / f"{svc.name}.pid"

    # Always disarm launchd/systemd first so it doesn't restart the process after we kill it
    try:
        if pm == "brew" and svc.service_name_brew:
            run(["brew", "services", "stop", svc.service_name_brew], check=False)
        elif svc.service_name_apt and pm in ("apt", "dnf", "yum"):
            run(["sudo", "systemctl", "stop", svc.service_name_apt], check=False)
    except Exception:
        pass

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            C.info(f"Stopped {svc.name} (PID {pid})")
        except (ProcessLookupError, ValueError):
            pass
        pid_file.unlink(missing_ok=True)


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


def _is_brew_installed(svc: DBService) -> bool:
    """Return True if the service's brew package is already installed (even if not running)."""
    if not svc.service_name_brew or not shutil.which("brew"):
        return False
    result = run(["brew", "list", "--formula", svc.package_brew], check=False)
    return result.returncode == 0


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

        # If the package is already installed but just not running, start it
        # (with retries) without requiring --auto-install.
        if _is_brew_installed(svc):
            C.info(f"  {svc.name} is installed — starting...")
            try:
                if ensure_service_up(svc):
                    C.ok(f"{svc.name} started on port {svc.default_port}")
                else:
                    C.fail(f"{svc.name} still not running on port {svc.default_port} after retries")
            except SystemExit:
                raise
            except Exception as e:
                C.fail(f"Failed to start {svc.name}: {e}")
            continue

        # Not installed — require --auto-install to fetch and install it.
        if not args.auto_install:
            if svc.optional:
                C.info(f"  Skipping optional service {svc.name}")
                continue
            C.fail(f"  Required service {svc.name} is not installed")
            C.info(f"  Run: python install_agentos.py install --auto-install")
            continue

        try:
            if ensure_service_up(svc):
                C.ok(f"{svc.name} installed and started on port {svc.default_port}")
            else:
                C.fail(f"{svc.name} installed but still not running on port {svc.default_port} after retries")
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

    ollama_url = args.ollama_url or f"http://localhost:{args.ollama_port}"
    db_config["ollama"] = {
        "url": ollama_url,
        "port": args.ollama_port,
        "model": args.ollama_model,
    }

    config["model"] = f"ollama/{args.ollama_model}"
    config["provider"] = "ollama"
    config["spec_repo"] = str(SOURCE_DIR)

    config["llm"] = config.get("llm", {})
    config["llm"]["backend"] = "ollama"
    config["llm"]["ollama_base_url"] = ollama_url
    config["llm"]["ollama_model"] = args.ollama_model
    config["llm"]["spec_discovery_provider"] = "auto"

    config["kernel"] = config.get("kernel", {})
    config["kernel"]["exploration_rate"] = config["kernel"].get("exploration_rate", 0.1)
    config["kernel"]["learning_rate"] = config["kernel"].get("learning_rate", 0.1)
    config["kernel"]["discount_factor"] = config["kernel"].get("discount_factor", 0.95)

    config["service"] = config.get("service", {})
    config["service"]["name"] = "monkeybrain"
    config["service"]["port"] = args.port
    config["service"]["environment"] = "development"

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
        f"NATS_EVENTS_SUBJECT=indus.websocket.events",
        f"NATS_EVENTS_QUEUE=indus-influx-consumers",
        f"INFLUXDB_URL={influx_url}",
        f"INFLUXDB_ORG={args.influxdb_org}",
        f"INFLUXDB_BUCKET={args.influxdb_bucket}",
        f"INFLUXDB_TOKEN={args.influxdb_token}",
        f"AUDIT_ELASTICSEARCH_URL={es_url}",
        f"MQTT_ENABLED=false",
        f"OLLAMA_BASE_URL={ollama_url}",
        f"OLLAMA_MODEL={args.ollama_model}",
        f"OLLAMA_DEFAULT_MODEL={args.ollama_model}",
        f"SPEC_DISCOVERY_PROVIDER=auto",
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

    if get_platform().startswith("macos"):
        # `monkeypatch` — opens a new Terminal.app window running the
        # interactive REPL (monkeypatched/repl.py), the same command
        # `monkeypatched start` launches, but typeable directly without
        # first having an active login session (the Typer app's login
        # gate only applies inside the REPL, not to opening it).
        inner_cmd = f'cd "{SOURCE_DIR}" && PYTHONPATH="{SOURCE_DIR / "src"}" "{VENV_DIR / "bin" / "python"}" -m repl'
        inner_cmd_escaped = inner_cmd.replace("\\", "\\\\").replace('"', '\\"')
        applescript = f'tell application "Terminal" to do script "{inner_cmd_escaped}"'
        monkeypatch_script = BIN_DIR / "monkeypatch"
        monkeypatch_script.write_text(f"#!/bin/bash\nosascript -e '{applescript}'\n")
        monkeypatch_script.chmod(0o755)

    _ensure_bin_dir_on_path()

    C.ok(f"CLI scripts created in {BIN_DIR}")


def _ensure_bin_dir_on_path() -> None:
    """Append an export line to the user's shell rc so BIN_DIR's scripts
    (monkeybrain, monkeybrain-status, monkeypatch) are typeable directly —
    without this, step_create_scripts() writes working scripts that nothing
    on PATH can find."""
    if str(BIN_DIR) in os.environ.get("PATH", "").split(os.pathsep):
        return

    shell_rc = Path.home() / (".zshrc" if shutil.which("zsh") else ".bashrc")
    marker = str(BIN_DIR)
    export_line = f'export PATH="{marker}:$PATH"\n'

    existing = shell_rc.read_text() if shell_rc.exists() else ""
    if marker in existing:
        return

    with open(shell_rc, "a") as f:
        f.write(f"\n# Added by MonkeyBrain installer\n{export_line}")
    C.ok(f"Added {BIN_DIR} to PATH in {shell_rc.name} (restart your shell, or run: source {shell_rc})")


# ── Commands ───────────────────────────────────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> int:
    # Normalize --skip-<name> flags into the args.skip list
    flag_map = {
        "skip_mongo": "mongodb",
        "skip_redis": "redis",
        "skip_neo4j": "neo4j",
        "skip_influxdb": "influxdb",
        "skip_elasticsearch": "elasticsearch",
        "skip_nats": "nats",
        "skip_ollama": "ollama",
        "skip_api": "api",
    }
    skip_set = set(args.skip or [])
    for attr, svc_name in flag_map.items():
        if getattr(args, attr, False):
            skip_set.add(svc_name)
    args.skip = list(skip_set)

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
    print(f"  Shell:     monkeypatch          (opens the interactive shell)")
    print(f"  Start:     python {SOURCE_DIR / 'install_agentos.py'} start")
    print(f"  Status:    python {SOURCE_DIR / 'install_agentos.py'} status")
    print(f"  Config:    {CONFIG_DIR / 'config.json'}")
    print(f"  Secrets:   {SECRET_FILE}")
    print(f"  Logs:      {LOG_DIR}")
    print()
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    C.step("Starting MonkeyBrain services")

    running = detect_running_services()
    to_skip = set(args.skip or [])
    auto_install = getattr(args, "auto_install", False)

    for svc in SERVICES:
        if svc.name in to_skip:
            continue
        if running[svc.name]:
            C.ok(f"{svc.name} already running on port {svc.default_port}")
            continue

        if _is_brew_installed(svc):
            C.info(f"Starting {svc.name}...")
            if ensure_service_up(svc):
                C.ok(f"{svc.name} started on port {svc.default_port}")
            else:
                C.fail(f"{svc.name} still not running on port {svc.default_port} after retries")
            continue

        if not auto_install:
            if svc.optional:
                C.info(f"Skipping optional service {svc.name} (not installed)")
            else:
                C.warn(f"{svc.name} not installed — run with --auto-install to install it")
            continue

        C.info(f"Installing and starting {svc.name}...")
        try:
            if ensure_service_up(svc):
                C.ok(f"{svc.name} installed and started on port {svc.default_port}")
            else:
                C.fail(f"{svc.name} installed but still not running on port {svc.default_port} after retries")
        except SystemExit:
            raise
        except Exception as e:
            C.fail(f"Failed to install/start {svc.name}: {e}")

    C.step("Starting API server")
    os.chdir(SOURCE_DIR)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SOURCE_DIR)

    config = load_config()
    port = args.port or config.get("service", {}).get("port", 8031)
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


def _kill_by_port(port: int) -> bool:
    """SIGKILL every process holding the port. Returns True if anything was killed."""
    try:
        result = run(["lsof", "-ti", f":{port}"], check=False)
        pids = [p.strip() for p in result.stdout.strip().splitlines() if p.strip()]
        for pid_str in pids:
            try:
                os.kill(int(pid_str), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        return bool(pids)
    except Exception:
        return False


def _wait_for_stop(svc: DBService, timeout: float = 8.0, interval: float = 0.5) -> bool:
    """Poll until the service port closes. Force-kills if still open after timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        if not is_service_running(svc):
            return True
    # Graceful stop timed out — stop brew service (prevents launchd restart) then SIGKILL
    pm = detect_package_manager()
    if pm == "brew" and svc.service_name_brew:
        run(["brew", "services", "stop", svc.service_name_brew], check=False)
        time.sleep(1.0)  # let launchd fully unload before kill
    _kill_by_port(svc.default_port)
    time.sleep(2.0)
    return not is_service_running(svc)


def cmd_stop(args: argparse.Namespace) -> int:
    C.step("Stopping MonkeyBrain services")

    for svc in SERVICES:
        if is_service_running(svc):
            stop_service(svc)
            if _wait_for_stop(svc):
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
                    pass
        else:
            C.info("No API processes found")
    except Exception:
        C.info("Could not check for API processes")

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
        print(f"  Run: python install_agentos.py start --auto-install")

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


def cmd_uninstall(args: argparse.Namespace) -> int:
    C.step("Uninstalling MonkeyBrain")

    # 1. Stop all running services first
    C.step("Stopping services")
    stop_args = argparse.Namespace(skip=[])
    cmd_stop(stop_args)

    # 2. Remove the install directory (~/.monkeybrain)
    if INSTALL_DIR.exists():
        if not args.yes:
            answer = input(f"\n  Remove {INSTALL_DIR}? [y/N] ").strip().lower()
            if answer != "y":
                C.warn("Aborted — install directory kept")
                return 0
        import shutil

        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        C.ok(f"Removed {INSTALL_DIR}")
    else:
        C.info(f"{INSTALL_DIR} not found — already clean")

    # 3. Remove the monkeybrain entry from PATH if install.sh was used
    shell_rc = Path.home() / (".zshrc" if shutil.which("zsh") else ".bashrc")
    if shell_rc.exists():
        text = shell_rc.read_text()
        marker = str(BIN_DIR)
        if marker in text:
            lines = [l for l in text.splitlines() if marker not in l]
            shell_rc.write_text("\n".join(lines) + "\n")
            C.ok(f"Removed PATH entry from {shell_rc.name}")

    # 4. Optionally remove database services (brew uninstall)
    if args.purge:
        C.step("Purging database services (--purge)")
        pm = detect_package_manager()
        for svc in SERVICES:
            if svc.optional:
                continue
            try:
                if pm == "brew":
                    run(["brew", "services", "stop", svc.service_name_brew], check=False)
                    run(["brew", "uninstall", "--force", svc.package_brew], check=False)
                    C.ok(f"Uninstalled {svc.name}")
                else:
                    C.info(f"Skipping {svc.name} — manual removal required on {pm}")
            except Exception as e:
                C.warn(f"Could not remove {svc.name}: {e}")
    else:
        C.info("Database services (MongoDB, Redis, NATS) left in place. Use --purge to remove them.")

    print(f"\n{C.B}{C.G}  MonkeyBrain uninstalled.{C.R}\n")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    service_name = getattr(args, "service", "") or "main"
    log_file = LOG_DIR / f"{service_name}.log"
    if not log_file.exists():
        # Try main log as fallback when a specific service log doesn't exist
        fallback = LOG_DIR / "main.log"
        if service_name != "main" and fallback.exists():
            C.warn(f"No log for '{service_name}', tailing main.log")
            log_file = fallback
        else:
            C.warn(f"No logs found at {log_file}. Start the server first.")
            return 0

    try:
        subprocess.run(["tail", "-f", str(log_file)])
    except KeyboardInterrupt:
        pass
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
              %(prog)s uninstall                            Remove MonkeyBrain
              %(prog)s uninstall --purge                    Remove MonkeyBrain + databases
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
    db.add_argument("--neo4j-password", type=str, default="password", help="Neo4j password")
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
        default=os.environ.get("INFLUXDB_TOKEN", ""),
        help="InfluxDB token (or set INFLUXDB_TOKEN env var)",
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
    db.add_argument("--ollama-port", type=int, default=11434, help="Ollama port (default: 11434)")
    db.add_argument("--ollama-url", type=str, default=None, help="Ollama full URL")
    db.add_argument(
        "--ollama-model",
        type=str,
        default="gemma3:latest",
        help="Ollama model (default: gemma3:latest)",
    )
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
        metavar="SERVICE",
        help="Services to skip (e.g. --skip neo4j elasticsearch)",
    )
    install_p.add_argument("--skip-mongo", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-redis", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-neo4j", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-influxdb", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-elasticsearch", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-nats", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-ollama", action="store_true", help=argparse.SUPPRESS)
    install_p.add_argument("--skip-api", action="store_true", help=argparse.SUPPRESS)

    start_p = sub.add_parser("start", parents=[db_group], help="Start all services")
    start_p.add_argument("--skip", nargs="*", default=[], metavar="SERVICE", help="Services to skip")

    sub.add_parser("stop", help="Stop all services")
    sub.add_parser("status", help="Show status of all services")

    uninstall_p = sub.add_parser("uninstall", help="Remove MonkeyBrain installation")
    uninstall_p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    uninstall_p.add_argument(
        "--purge",
        action="store_true",
        help="Also uninstall database services (MongoDB, Redis, NATS)",
    )

    configure_p = sub.add_parser("configure", parents=[db_group], help="Reconfigure database connections")

    sub.add_parser("seed", help="Seed domain data")
    logs_p = sub.add_parser("logs", help="Tail service logs")
    logs_p.add_argument("service", nargs="?", default="", help="Service name to tail (default: main)")

    return parser


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
        "uninstall": cmd_uninstall,
    }

    return commands[args.command](args)


def status_main() -> None:
    """Entry point for monkeybrain-status CLI command."""
    args = argparse.Namespace(command="status")
    cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
