"""The generator must be told what data exists — and only the data it may see (R-2).

Asked to build LineResolverAgent, the LLM was given the list of n8n node types it could use
and nothing about the data. So it invented the data, and we activated the result:

    const pumpData = $input.first().json.body;
    const lines = pumpData.lines; const pumpNumbers = pumpData.pumpNumbers;
    if (pumpNumbers[i] === '3') { ... }

`lines`, `pumpNumbers` and '3' are all fictions. The catalog exists so a generator builds
against the real schema. It is an ALLOW-LIST, because the same database also holds the API
keys, the OTP hashes and the users — and this text goes into an LLM prompt.
"""

from __future__ import annotations

import asyncio

from src.monkey_brain.kernel import data_catalog


def test_the_catalog_is_an_allowlist_not_the_whole_database():
    """Every collection described must be one we chose to describe."""
    for collection in data_catalog.DOMAIN_COLLECTIONS:
        assert isinstance(collection, str)
    # the credential store is not domain data
    for secret in (
        "agentos_api_keys",
        "agentos_api_key_email_otps",
        "users",
        "part11_login_events",
        "roles",
        "permissions",
        "sessions",
    ):
        assert secret not in data_catalog.DOMAIN_COLLECTIONS, (
            f"{secret} would be described, field by field, inside an LLM prompt"
        )


def test_the_domain_collections_are_the_ones_agents_reason_about():
    for expected in (
        "instruments",
        "pharmaceutical_equipment",
        "calibration_records",
        "plant_locations",
        "workstations",
    ):
        assert expected in data_catalog.DOMAIN_COLLECTIONS


def test_no_data_means_forbid_guessing_not_encourage_it(monkeypatch):
    """When the database can't be inspected, the prompt must not leave a vacuum — a vacuum is
    what produced pumpNumbers."""
    monkeypatch.setattr(data_catalog, "schema_summary", _async_return(""))
    fragment = asyncio.run(data_catalog.prompt_fragment())
    assert "do not know what data exists" in fragment.lower()
    assert "do not invent" in fragment.lower()
    assert "no data access" in fragment.lower()


def test_the_fragment_carries_the_schema_and_the_rules(monkeypatch):
    summary = "Database `demo` (MongoDB).\n- instruments (41 documents)\n    line_id values: LINE-TAB-001, LINE-CAP-001"
    monkeypatch.setattr(data_catalog, "schema_summary", _async_return(summary))
    fragment = asyncio.run(data_catalog.prompt_fragment())

    assert "LINE-TAB-001" in fragment, "the generator cannot avoid inventing 'line 3' unseen"
    assert "Never invent a collection, a field, or an id" in fragment
    # and it must demand provenance, since ungrounded output is withheld downstream
    assert "sources" in fragment


def test_introspection_failure_is_not_fatal(monkeypatch):
    """A database that cannot be reached must degrade to 'I know nothing', never raise into
    the resolver."""

    def boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(data_catalog, "_introspect", boom)
    data_catalog.invalidate()
    assert asyncio.run(data_catalog.schema_summary()) == ""


def _async_return(value):
    async def _fn():
        return value

    return _fn
