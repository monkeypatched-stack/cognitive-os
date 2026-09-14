"""Line resolution — turn what a person said into a line that actually exists.

The lines in the data are LINE-TAB-001 and LINE-CAP-001. A question about "line 3" is
therefore not a question with a hard answer — it is a question about a line that does not
exist, and the only correct response is to say so and show what does exist.

This is the agent whose absence started the whole investigation: asked for "pumps on line 3",
the system invented pump IDs, calibration dates and an overdue interval, and reported
success. Every alias here is derived from the data (the line id's own tokens, and the plant
names of the equipment on it) rather than hand-mapped, so the resolver cannot know about a
line the database has never heard of.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from broca.agents.ddd._base_ddd import BaseDDDAgent
from broca.registry import auto_register

from domains.manufacturing.agents import _data
from domains.manufacturing.agents._data import DataBackedAgentMixin

logger = logging.getLogger("manufacturing.agents.lines")

_STOPWORDS = frozenset({"line", "the", "on", "in", "at", "of", "for", "and", "a", "an"})


async def line_catalog() -> dict[str, dict[str, Any]]:
    """Every line that exists, with the aliases the data itself justifies.

    An alias only earns its place if it DISCRIMINATES. Plant names look like an obvious
    source of human vocabulary, but both lines sit in the same "Tablet Manufacturing Plant",
    so "tablet"/"plant"/"manufacturing" identify neither of them — and an alias that matches
    every line is not an alias, it is noise that manufactures a false confident match. Only
    the line id's own tokens survive: LINE-TAB-001 -> {tab, 001, 1}.
    """
    catalog: dict[str, dict[str, Any]] = {}

    for collection in ("instruments", "pharmaceutical_equipment", "workstations"):
        try:
            for line_id in await _data.distinct(collection, "line_id"):
                catalog.setdefault(
                    str(line_id), {"line_id": str(line_id), "aliases": set()}
                )
        except _data.CollectionAbsent:
            continue

    for line_id, entry in catalog.items():
        for token in re.split(r"[-_\s]+", line_id.lower()):
            if token and token not in _STOPWORDS:
                entry["aliases"].add(token)
                if token.isdigit():
                    entry["aliases"].add(str(int(token)))  # "001" also answers to "1"

    # Drop any alias shared by more than one line — including the numeric ones. Both lines
    # here are -001, so "line 1" is genuinely ambiguous and must be reported as such rather
    # than silently resolved to whichever came back from Mongo first.
    counts: dict[str, int] = {}
    for entry in catalog.values():
        for alias in entry["aliases"]:
            counts[alias] = counts.get(alias, 0) + 1
    for entry in catalog.values():
        entry["shared"] = {a for a in entry["aliases"] if counts[a] > 1}
        entry["aliases"] = {a for a in entry["aliases"] if counts[a] == 1}

    return catalog


def _referenced_numbers(question: str) -> set[str]:
    """Line numbers the question names: 'line 3' -> {'3'}."""
    return {m for m in re.findall(r"line\s*#?\s*(\d+)", question.lower())}


async def resolve_line(question: str) -> dict[str, Any]:
    """Resolve a free-text line reference against the lines that exist.

    Returns {"success": bool, ...}. Never guesses: no match and an ambiguous match are both
    reported as failures, with the real lines listed so the caller can see the options.
    """
    catalog = await line_catalog()
    known = sorted(catalog)
    sources = [_data.cite("instruments", len(known))]

    if not known:
        return {
            "success": False,
            "error": "no production lines exist in the data",
            "known_lines": [],
            "sources": [],
        }

    text = question.lower()
    words = [w for w in re.split(r"\W+", text) if w]
    numbers = _referenced_numbers(question)

    matched: set[str] = set()
    for line_id, entry in catalog.items():
        if line_id.lower() in text:
            matched.add(line_id)
            continue
        for alias in entry["aliases"]:
            # "tablet" resolves LINE-TAB-001 and "capsule" resolves LINE-CAP-001 by prefix —
            # the id abbreviates the word. Guarded at 3 chars so short tokens can't run wild.
            if any(
                w == alias or (len(alias) >= 3 and w.startswith(alias)) for w in words
            ):
                matched.add(line_id)
                break

    # "line 3" must match a line whose own number is 3, or nothing at all. Never a line that
    # merely contains a 3 somewhere. This is the exact point at which the fabrication started.
    if numbers:
        by_number = {
            lid
            for lid, e in catalog.items()
            if numbers & {a for a in e["aliases"] if a.isdigit()}
        }
        shared_number = any(
            numbers & {a for a in e["shared"] if a.isdigit()} for e in catalog.values()
        )
        if by_number:
            matched = (matched & by_number) or by_number
        elif shared_number:
            # every line carries this number (all lines here are -001), so it discriminates
            # nothing — ambiguous, not resolved.
            matched = set(catalog)
        else:
            matched = set()

    if len(matched) == 1:
        line_id = matched.pop()
        return {
            "success": True,
            "line_id": line_id,
            "known_lines": known,
            "sources": sources,
        }

    if not matched:
        asked = f"line {sorted(numbers)[0]}" if numbers else "that line"
        return {
            "success": False,
            "error": f"no line matching {asked} exists — the lines in the data are: "
            f"{', '.join(known)}",
            "known_lines": known,
            "sources": sources,
        }

    return {
        "success": False,
        "error": f"the line reference is ambiguous — it matches {', '.join(sorted(matched))}",
        "known_lines": known,
        "candidates": sorted(matched),
        "sources": sources,
    }


@auto_register
class LineResolverAgent(DataBackedAgentMixin, BaseDDDAgent):
    """Resolves a spoken line reference to a line_id that exists in the data."""

    agent_type = "line_resolver"
    description = (
        "Resolves a natural-language production line reference (e.g. 'the tablet "
        "line', 'line 1') to a canonical line_id, or reports that no such line exists"
    )
    ddd_layer = "entity"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"question": str(context.get("question", ""))}

    async def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return await resolve_line(perception["question"])

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("success"):
            line_id = decision["line_id"]
            return {
                "action": "report",
                "operation": "line.resolve",
                "success": True,
                "line_id": line_id,
                "summary": f"Resolved to {line_id}.",
                "answer": f"Resolved to {line_id}.",
                "sources": decision.get("sources", []),
            }
        # "There is no line 3" is an ANSWER — the agent looked, and reported what it found.
        # It is not a malfunction, and reporting success=False here was actively harmful: the
        # composition layer treats a failed capability as one to discard and replace with an
        # LLM guess, which is precisely the path that invented P-301 in the first place. A
        # truthful negative must be returned, not retried. `resolved` carries the distinction
        # for any caller that needs it; success=False is reserved for genuine inability.
        message = decision.get("error", "line could not be resolved")
        return {
            "action": "report",
            "operation": "line.resolve",
            "success": True,
            "resolved": False,
            "known_lines": decision.get("known_lines", []),
            "summary": message,
            "answer": message,
            "sources": decision.get("sources", []),
        }
