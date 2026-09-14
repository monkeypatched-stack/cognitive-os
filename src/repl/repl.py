"""Interactive monkeypatched shell — the target of `monkeypatched start`,
run inside a freshly opened terminal window (see utils.py's start_cmd).

Each line typed is parsed and dispatched through the same Typer `app` used
by the one-shot CLI, so every command (including the login gate) behaves
identically here as it does from a normal shell invocation.
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import shutil
from pathlib import Path
from typing import Optional

import typer
from click.exceptions import NoArgsIsHelpError

from repl._helpers import _log_file
from repl.theme import (
    console,
    print_error,
    print_info,
    print_startup_art,
    print_success,
    prompt_text,
)

_AUTH_PORT = 8010  # login needs this one up
_AGENTOS_PORT = 8031
_NATS_PORT = 4222

_SERVICES_TIMEOUT = 300.0  # seconds to wait for auth/agentos to come up
_NATS_TIMEOUT = 20.0  # seconds to wait for nats-server to come up
_KILL_GRACE = 5.0  # seconds to wait for a killed process to release its port


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes currently listening on `port`, using
    whichever of lsof/fuser is available. Best-effort: returns [] if
    neither tool exists or nothing is found."""
    if shutil.which("lsof"):
        cmd = ["lsof", "-t", f"-i:{port}", "-sTCP:LISTEN"]
    elif shutil.which("fuser"):
        cmd = ["fuser", f"{port}/tcp"]
    else:
        return []

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return []

    pids = []
    for tok in result.stdout.split():
        try:
            pids.append(int(tok))
        except ValueError:
            continue
    return pids


def _kill_port(port: int, grace: float = _KILL_GRACE) -> None:
    """Kill whatever is currently listening on `port`, if anything, and
    wait for it to actually exit (not just for the port to stop
    accepting connections) before returning."""
    pids = _pids_on_port(port)
    if not pids:
        return

    print_info(f"Port {port} is in use — stopping the existing process...")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            print_error(f"No permission to stop process {pid} on port {port}.")

    def _still_alive() -> bool:
        return bool(_pids_on_port(port)) or _port_open(port)

    deadline = time.time() + grace
    while time.time() < deadline and _still_alive():
        time.sleep(0.2)

    if _still_alive():
        for pid in _pids_on_port(port):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.time() + grace
        while time.time() < deadline and _still_alive():
            time.sleep(0.2)

    if _still_alive():
        print_error(f"Process on port {port} did not fully exit — the new service may fail to bind this port.")
    else:
        print_success(f"Stopped the process on port {port}.")


def _start_nats() -> Optional[subprocess.Popen]:
    """Kill anything already on the NATS port, then start a fresh
    nats-server. Returns None if nats-server isn't installed or failed
    to start."""
    _kill_port(_NATS_PORT)

    if shutil.which("nats-server") is None:
        print_error("nats-server is not installed — NATS will not start.")
        return None

    repo_root = Path(__file__).resolve().parents[2]
    base_dir = Path("/private/tmp/monkeybrain")
    log_dir = base_dir / "logs"
    data_dir = base_dir / "data" / "nats"
    log_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "nats.log"

    proc = subprocess.Popen(
        ["nats-server", "-js", "-sd", str(data_dir), "-l", str(log_path)],
        cwd=str(repo_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + _NATS_TIMEOUT
    while time.time() < deadline:
        if _port_open(_NATS_PORT):
            print_success("NATS is up.")
            return proc
        if proc.poll() is not None:
            print_error(f"NATS exited early with code {proc.returncode}; check {log_path}")
            return None
        time.sleep(0.5)

    print_error(f"NATS did not come up within {_NATS_TIMEOUT:.0f}s — check {log_path}")
    return proc


def _start_main_service() -> tuple[subprocess.Popen, Path]:
    """Launch main.py (auth/file/agentos) in the background.

    Returns:
        (process, log_path)
    """
    repo_root = Path(__file__).resolve().parents[2]
    log_path = _log_file("main")

    env = os.environ.copy()

    # Ensure both the project root and the manufacturing knowledge
    # directory are importable.
    python_paths = [
        str(repo_root),
        str(repo_root / "domains" / "manufacturing" / "knowledge"),
    ]

    existing = env.get("PYTHONPATH")
    if existing:
        python_paths.append(existing)

    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    log_fh = open(log_path, "a")

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(repo_root),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    return proc, log_path


def _tail(path, n: int = 15) -> str:
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:]).rstrip()
    except OSError:
        return ""


def _wait_for_main_service(service_proc: subprocess.Popen, timeout: float, log_path) -> tuple[bool, bool]:
    """Poll until auth+agentos are up, the process dies, or we time out.
    Returns (auth_ready, agentos_ready). Prints periodic progress and a
    log tail so a stall is visible instead of a silent spinner, and can
    be interrupted with Ctrl+C without killing the REPL."""
    deadline = time.time() + timeout
    auth_ready = agentos_ready = False
    next_update = time.time() + 10.0

    try:
        with console.status("[muted]Waiting for services to come up...[/muted]") as status:
            while time.time() < deadline:
                auth_ready = _port_open(_AUTH_PORT)
                agentos_ready = _port_open(_AGENTOS_PORT)
                if auth_ready and agentos_ready:
                    print_success("Services are up.")
                    break
                if service_proc.poll() is not None:
                    print_error(f"main.py exited early with code {service_proc.returncode}")
                    break
                if time.time() >= next_update:
                    elapsed = int(time.time() - (deadline - timeout))
                    status.update(
                        f"[muted]Still waiting for services "
                        f"(auth={'up' if auth_ready else 'down'}, "
                        f"agentos={'up' if agentos_ready else 'down'}), "
                        f"{elapsed}s elapsed. Check {log_path} for errors.[/muted]"
                    )
                    next_update = time.time() + 10.0
                time.sleep(1)
    except KeyboardInterrupt:
        print()
        print_info("Stopped waiting for services (they may still start in the background).")

    if not (auth_ready and agentos_ready):
        tail = _tail(log_path)
        if tail:
            print_info(f"Last lines of {log_path}:\n{tail}")

    return auth_ready, agentos_ready


def _ensure_services_running(
    timeout: float = _SERVICES_TIMEOUT,
) -> tuple[Optional[subprocess.Popen], Optional[subprocess.Popen]]:
    """Kill any process already bound to the auth/agentos ports, then
    start a fresh main.py."""

    print_info("Restarting MonkeyBrain services (auth, file, agentos)...")

    _kill_port(_AUTH_PORT)
    _kill_port(_AGENTOS_PORT)

    service_proc, log_path = _start_main_service()

    auth_ready, agentos_ready = _wait_for_main_service(
        service_proc,
        timeout,
        log_path,
    )

    if not (auth_ready and agentos_ready):
        missing = []
        if not auth_ready:
            missing.append(f"auth:{_AUTH_PORT}")
        if not agentos_ready:
            missing.append(f"agentos:{_AGENTOS_PORT}")

        print_error(f"Services did not come up within {timeout:.0f}s (missing: {', '.join(missing)})")

    nats_proc = _start_nats()
    return service_proc, nats_proc


def _terminate(proc: Optional[subprocess.Popen], grace: float = 15.0) -> None:
    """Terminate a process we own, if it's still running."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()


def _stop_services(service_proc: Optional[subprocess.Popen], nats_proc: Optional[subprocess.Popen]) -> None:
    _terminate(service_proc)
    _terminate(nats_proc)


def _dispatch(app, args: list[str]) -> None:
    """Run one command through the Typer app, reporting errors without
    tearing down the caller's loop. A Ctrl+C here cancels only the current
    command — it must not escape and kill the whole REPL."""
    try:
        app(args, standalone_mode=False)
    except SystemExit:
        pass
    except NoArgsIsHelpError as exc:
        typer.echo(str(exc))
    except KeyboardInterrupt:
        print()
        print_info("Command interrupted.")
    except Exception as exc:
        print_error(str(exc))


import argparse
import os


def build_install_args(
    auto_install: bool = False,
    skip_ollama: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        auto_install=auto_install,
        skip=["ollama"] if skip_ollama else [],
        skip_mongo=False,
        skip_redis=False,
        skip_neo4j=False,
        skip_influxdb=False,
        skip_elasticsearch=False,
        skip_nats=False,
        skip_ollama=skip_ollama,
        skip_api=False,
        mongo_url=None,
        mongo_port=27017,
        db_name="demo",
        redis_url=None,
        redis_port=6379,
        neo4j_uri=None,
        neo4j_port=7687,
        neo4j_user="neo4j",
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        influxdb_url=None,
        influxdb_port=8181,
        influxdb_org="indus",
        influxdb_bucket="events",
        influxdb_token=os.getenv("INFLUXDB_TOKEN", ""),
        elasticsearch_url=None,
        elasticsearch_port=9200,
        nats_url=None,
        nats_port=4222,
        ollama_url=None,
        ollama_port=11434,
        ollama_model="gemma3:latest",
        port=8031,
    )


def install_cmd(
    auto_install: bool = typer.Option(False, "--auto-install"),
    skip_ollama: bool = typer.Option(False, "--skip-ollama"),
):
    from install_agentos import cmd_install

    return cmd_install(
        build_install_args(
            auto_install=auto_install,
            skip_ollama=skip_ollama,
        )
    )


def run_repl() -> None:
    from repl import app

    os.environ["MONKEYPATCHED_IN_REPL"] = "1"

    rc = install_cmd()
    if rc != 0:
        print_error("MonkeyBrain installation check failed. Run 'monkeypatched install' to repair the installation.")
        return

    service_proc, nats_proc = _ensure_services_running()

    try:
        # If CLI args were passed, run the single command and exit.
        if len(sys.argv) > 1:
            _dispatch(app, sys.argv[1:])
            return

        print_startup_art()

        try:
            import readline  # noqa: F401
        except ImportError:
            pass

        while True:
            try:
                line = input(prompt_text())
            except (EOFError, KeyboardInterrupt):
                print()
                break

            line = line.strip()
            if not line:
                continue
            if line in ("exit", "quit"):
                print()
                print_info("Goodbye.")
                print()
                break
            if line == "clear":
                os.system("clear" if os.name != "nt" else "cls")
                continue

            try:
                args = shlex.split(line)
            except ValueError as exc:
                print_error(f"Could not parse command: {exc}")
                continue

            if args and args[0] == "help":
                args = args[1:] + ["--help"]

            _dispatch(app, args)
    finally:
        _stop_services(service_proc, nats_proc)
