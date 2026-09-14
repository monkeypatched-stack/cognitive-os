"""codegen — routes microservice/service generation questions to the codegen engine."""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger("agentos.codegen")

import os

_CODEGEN_URL = os.getenv("CODEGEN_URL", "http://localhost:8031/api/v1/codegen/microservice")

_KEYWORDS = [
    r"generate\s+(a\s+)?.*\b(micro)?service\b",
    r"create\s+(a\s+)?.*\b(micro)?service\b",
    r"build\s+(a\s+)?.*\b(micro)?service\b",
    r"scaffold\s+(a\s+)?.*\b(api|service)\b",
    r"\bfastapi\b.*\b(service|microservice|api)\b",
    r"\b(micro)?service\b.*\bgenerate\b",
    r"\bcodegen\b",
    r"generate.*\b(crud|rest|api)\b",
    r"\bnew\s+(micro)?service\b",
    r"write.*\b(micro)?service\b",
]

_COMPILED = [re.compile(p, re.I) for p in _KEYWORDS]


def is_codegen_question(question: str) -> bool:
    return any(p.search(question) for p in _COMPILED)


def _extract_spec_from_question(question: str) -> dict:
    """Best-effort extraction of a MicroserviceSpec from natural language."""
    q = question.lower()

    # Try to guess the domain
    domain = "general"
    for d in (
        "inventory",
        "manufacturing",
        "order",
        "product",
        "customer",
        "auth",
        "shipping",
        "payment",
        "asset",
        "facility",
    ):
        if d in q:
            domain = d
            break

    # Try to guess a service name from "for managing X" or "for X"
    name_match = re.search(
        r"for\s+(?:managing|handling|tracking|storing)?\s*([\w\s]+?)(?:\s+microservice|\s+service|\s+api|$)",
        q,
        re.I,
    )
    if name_match:
        raw = name_match.group(1).strip()
        name = re.sub(r"\s+", "-", raw.lower())[:30].strip("-") or "generated-service"
    else:
        name = f"{domain}-service"

    return {
        "name": name,
        "domain": domain,
        "description": f"Generated from: {question[:120]}",
        "db": {"type": "mongodb"},
        "fields": [
            {"name": "name", "type": "str"},
            {"name": "description", "type": "str", "default": ""},
            {
                "name": "status",
                "type": "str",
                "enum": ["active", "inactive"],
                "default": "active",
            },
        ],
        "timestamps": True,
        "auth": {"enabled": True, "type": "bearer"},
    }


async def codegen_question_answer(client, question: str, force: bool = False):
    """Generate a microservice from a natural-language request.

    Extracts intent from the question, builds a MicroserviceSpec, calls the
    codegen engine, and returns a summary of what was generated.
    """
    spec = _extract_spec_from_question(question)

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            # Validate first
            val = await http.post(f"{_CODEGEN_URL}/validate", json=spec)
            if val.status_code != 200 or not val.json().get("valid"):
                return (
                    f"Could not build a valid spec from your request. "
                    f"Try: monkeypatched codegen <service-name> --domain <domain>",
                    [],
                    [],
                    False,
                )

            # Generate
            resp = await http.post(_CODEGEN_URL, json=spec)
            if resp.status_code != 200:
                return (
                    f"Codegen failed ({resp.status_code}): {resp.text[:200]}",
                    [],
                    [],
                    False,
                )

            body = resp.json()
            files = body.get("files", [])
            file_list = "\n".join(f"  • {f}" for f in files[:10])
            if len(files) > 10:
                file_list += f"\n  … and {len(files) - 10} more"

            answer = (
                f"Generated **{spec['name']}** ({spec['db']['type']}, {len(files)} files):\n"
                f"{file_list}\n\n"
                f"Use `monkeypatched codegen {spec['name']} --domain {spec['domain']}` "
                f"to generate with a custom spec."
            )
            return (answer, [], [], True)

    except Exception as e:
        logger.error("[codegen] generation failed: %s", e)
        return (f"Codegen error: {e}", [], [], False)
