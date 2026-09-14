"""Job scheduler."""

from __future__ import annotations


import typer

from repl._helpers import _load_jobs, _save_jobs, _brain_post, _brain_url, _log_file


def scheduler_list(
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List all scheduled workloads.  [like crontab -l]"""
    import json as _json

    jobs = _load_jobs()
    if json_output:
        typer.echo(_json.dumps(jobs, indent=2))
        return
    if not jobs:
        typer.echo("  No scheduled jobs.")
        return
    typer.echo(f"  {'ID':<6s} {'SCHEDULE':<18s} {'WORKLOAD':<20s} {'GOAL'}")
    typer.echo(f"  {'-' * 6} {'-' * 18} {'-' * 20} {'-' * 40}")
    for j in jobs:
        typer.echo(
            f"  {str(j.get('id', '')):<6s} {j.get('schedule', ''):<18s} {j.get('workload', ''):<20s} {j.get('goal', '')[:60]}"
        )


def scheduler_add(
    schedule: str = typer.Argument(..., help="Cron expression, e.g. '0 */6 * * *'"),
    workload: str = typer.Option(..., "--workload", "-w", prompt=True, help="Workload name"),
    goal: str = typer.Option("", "--goal", "-g", help="Goal description sent to brain"),
    domain: str = typer.Option("software_engineering", "--domain", "-d", help="Domain"),
    brain_url: str = typer.Option("", "--url", help="MonkeyBrain URL"),
    register_cron: bool = typer.Option(False, "--register-cron", help="Also register with OS crontab"),
):
    """Add a scheduled workload.  [like crontab -e]

    Example:
        monkeypatched cron add '0 6 * * *' --workload etass --goal 'daily stability check'
    """
    import subprocess, shutil

    jobs = _load_jobs()
    job_id = str(len(jobs) + 1)
    job = {
        "id": job_id,
        "schedule": schedule,
        "workload": workload,
        "goal": goal,
        "domain": domain,
        "brain_url": brain_url or _brain_url(),
    }
    jobs.append(job)
    _save_jobs(jobs)
    typer.echo(f"  Added job #{job_id}: [{schedule}] {workload}")

    if register_cron:
        if not shutil.which("crontab"):
            typer.echo("  crontab not found — skipping OS registration", err=True)
            return
        cli_path = shutil.which("monkeypatched") or sys.executable + " -m monkeypatched"
        cron_cmd = (
            f"{schedule} {cli_path} brain prompt '{goal or workload}' "
            f"--url {brain_url or _brain_url()} >> {_log_file('scheduler')} 2>&1"
        )
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new_crontab = existing.rstrip("\n") + f"\n{cron_cmd}\n"
        proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True)
        if proc.returncode == 0:
            typer.echo(f"  Registered with OS crontab")
        else:
            typer.echo(f"  OS crontab registration failed", err=True)


def scheduler_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job.  [like crontab -r for a specific line]"""
    jobs = _load_jobs()
    before = len(jobs)
    jobs = [j for j in jobs if str(j.get("id")) != str(job_id)]
    if len(jobs) == before:
        typer.echo(f"  Job #{job_id} not found.", err=True)
        raise typer.Exit(1)
    _save_jobs(jobs)
    typer.echo(f"  Removed job #{job_id}")


def scheduler_run(
    job_id: str = typer.Argument(..., help="Job ID to run immediately"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Run a scheduled job immediately (ignore schedule).  [like run-parts]"""
    jobs = _load_jobs()
    job = next((j for j in jobs if str(j.get("id")) == str(job_id)), None)
    if not job:
        typer.echo(f"  Job #{job_id} not found.", err=True)
        raise typer.Exit(1)
    typer.echo(f"  Running job #{job_id}: {job['workload']} — '{job.get('goal', '')}'")
    _brain_post(
        "/api/v1/agentos/prompt",
        job.get("brain_url", _brain_url()),
        {"question": job.get("goal") or job["workload"], "run_type": "full"},
        json_output,
    )


# ===========================================================================
# Top-level OS-mapped commands
# ===========================================================================
