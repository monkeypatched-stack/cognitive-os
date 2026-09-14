"""Service management commands."""

from __future__ import annotations

import os

import typer


def service_install(
    auto_install: bool = typer.Option(False, "--auto-install", help="Auto-install missing database services"),
    skip_mongo: bool = typer.Option(False, "--skip-mongo"),
    skip_redis: bool = typer.Option(False, "--skip-redis"),
    skip_neo4j: bool = typer.Option(False, "--skip-neo4j"),
    skip_influxdb: bool = typer.Option(False, "--skip-influxdb"),
    skip_elasticsearch: bool = typer.Option(False, "--skip-elasticsearch"),
    skip_nats: bool = typer.Option(False, "--skip-nats"),
    skip_api: bool = typer.Option(False, "--skip-api"),
):
    """Full installation of MonkeyBrain and its dependencies."""
    import argparse, os
    from install_agentos import cmd_install

    skip = []
    if skip_mongo:
        skip.append("mongodb")
    if skip_redis:
        skip.append("redis")
    if skip_neo4j:
        skip.append("neo4j")
    if skip_influxdb:
        skip.append("influxdb")
    if skip_elasticsearch:
        skip.append("elasticsearch")
    if skip_nats:
        skip.append("nats")
    if skip_api:
        skip.append("api")
    args = argparse.Namespace(
        auto_install=auto_install,
        skip=skip,
        skip_mongo=skip_mongo,
        skip_redis=skip_redis,
        skip_neo4j=skip_neo4j,
        skip_influxdb=skip_influxdb,
        skip_elasticsearch=skip_elasticsearch,
        skip_nats=skip_nats,
        skip_api=skip_api,
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
        influxdb_token="",
        elasticsearch_url=None,
        elasticsearch_port=9200,
        nats_url=None,
        nats_port=4222,
        port=8031,
    )
    raise typer.Exit(cmd_install(args))


def service_start(
    skip: list[str] = typer.Option([], "--skip", help="Services to skip"),
    auto_install: bool = typer.Option(False, "--auto-install", help="Install missing services before starting"),
):
    """Start all MonkeyBrain services."""
    import argparse
    from install_agentos import cmd_start

    raise typer.Exit(cmd_start(argparse.Namespace(skip=skip, port=8031, auto_install=auto_install)))


def service_stop():
    """Stop all MonkeyBrain services."""
    import argparse
    from install_agentos import cmd_stop

    raise typer.Exit(cmd_stop(argparse.Namespace(skip=[])))


def service_status():
    """Show status of all MonkeyBrain services."""
    import argparse
    from install_agentos import cmd_status

    raise typer.Exit(cmd_status(argparse.Namespace()))


def service_configure(
    mongo_url: str = typer.Option("", "--mongo-url"),
    redis_url: str = typer.Option("", "--redis-url"),
    neo4j_uri: str = typer.Option("", "--neo4j-uri"),
    influxdb_url: str = typer.Option("", "--influxdb-url"),
    elasticsearch_url: str = typer.Option("", "--elasticsearch-url"),
    nats_url: str = typer.Option("", "--nats-url"),
    db_name: str = typer.Option("demo", "--db-name"),
    port: int = typer.Option(8031, "--port"),
):
    """Reconfigure database connections."""
    import argparse
    from install_agentos import cmd_configure

    args = argparse.Namespace(
        mongo_url=mongo_url or None,
        mongo_port=27017,
        redis_url=redis_url or None,
        redis_port=6379,
        neo4j_uri=neo4j_uri or None,
        neo4j_port=7687,
        neo4j_user="neo4j",
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        influxdb_url=influxdb_url or None,
        influxdb_port=8181,
        influxdb_org="indus",
        influxdb_bucket="events",
        influxdb_token="",
        elasticsearch_url=elasticsearch_url or None,
        elasticsearch_port=9200,
        nats_url=nats_url or None,
        nats_port=4222,
        db_name=db_name,
        port=port,
    )
    raise typer.Exit(cmd_configure(args))


def service_seed():
    """Seed domain data into databases."""
    import argparse
    from install_agentos import cmd_seed

    raise typer.Exit(cmd_seed(argparse.Namespace()))


def service_logs(
    service: str = typer.Argument("", help="Service name to tail logs for"),
):
    """Tail service logs."""
    import argparse
    from install_agentos import cmd_logs

    raise typer.Exit(cmd_logs(argparse.Namespace(service=service)))


def service_uninstall(
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
    purge: bool = typer.Option(False, "--purge", help="Also uninstall database services (MongoDB, Redis, NATS)"),
):
    """Remove MonkeyBrain installation."""
    import argparse
    from install_agentos import cmd_uninstall

    args = argparse.Namespace(yes=yes, purge=purge)
    raise typer.Exit(cmd_uninstall(args))
