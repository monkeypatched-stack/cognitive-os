"""IntentIR — the compiled execution plan produced by the planner.

Architecture:

    Natural language
      → intent classification         (planner: routing.resolve_intent)
      → goal resolution                (planner: routing.resolve_goal_from_intent)
      → IntentIR                       (planner: build_intent_ir, HERE)
      → validation                     (runtime: validate_intent_ir, HERE)
      → execution                      (runtime: GoalExecutor)

The runtime never re-classifies natural language. It receives an IntentIR,
verifies its integrity and schema version, validates its shape, and executes
it. Nothing downstream reconstructs intent from `question` text.

IntentIR is immutable after creation (frozen dataclass + read-only mapping
views for its dict-valued fields) and versioned via `schema_version`, so a
future planner/runtime split can evolve independently as long as both sides
agree on SUPPORTED_SCHEMA_VERSIONS.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

logger = logging.getLogger("agentos.intent_ir")

CURRENT_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})


def _frozen_mapping(d: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(d or {}))


def _is_production() -> bool:
    """True when any standard env marker declares a production deployment."""
    for var in ("AGENTOS_ENV", "MONKEYBRAIN_ENV", "ENVIRONMENT", "ENV"):
        if os.environ.get(var, "").strip().lower() in ("production", "prod"):
            return True
    return False


def _signing_key() -> bytes:
    """Server-side HMAC key. Never derived from or sent to the client.

    Fails closed in production: if AGENTOS_MASTER_KEY is unset there, IntentIR
    signing raises rather than falling back to a public constant that would
    make every signature forgeable. Outside production it falls back to a
    fixed dev key with a loud warning.
    """
    key = os.environ.get("AGENTOS_MASTER_KEY", "")
    if not key:
        if _is_production():
            raise RuntimeError(
                "AGENTOS_MASTER_KEY is not set in a production environment — refusing "
                "to sign IntentIR with the insecure dev-only fallback key. Set "
                "AGENTOS_MASTER_KEY before starting."
            )
        logger.warning(
            "AGENTOS_MASTER_KEY not set — IntentIR signatures use an insecure "
            "dev-only fallback key. Set AGENTOS_MASTER_KEY in any environment "
            "where intent tamper-protection matters."
        )
        key = "insecure-dev-only-intent-ir-key"
    return key.encode("utf-8")


@dataclass(frozen=True)
class IntentIR:
    """Immutable, versioned Intermediate Representation of a resolved intent.

    Construct via build_intent_ir(); do not instantiate directly outside
    this module (bypasses signing).
    """

    schema_version: str
    intent_ir_id: str
    run_id: str
    intent_type: str
    confidence: float
    goal: Mapping[str, Any]
    entities: tuple[str, ...]
    parameters: Mapping[str, Any]
    execution_metadata: Mapping[str, Any]
    created_at: float
    signature: str = ""

    def _signing_payload(self) -> str:
        """Canonical string over every field except `signature` itself."""
        return "|".join(
            [
                self.schema_version,
                self.intent_ir_id,
                self.run_id,
                self.intent_type,
                f"{self.confidence:.6f}",
                repr(sorted(self.goal.items())),
                repr(self.entities),
                repr(sorted(self.parameters.items())),
                repr(sorted(self.execution_metadata.items())),
                f"{self.created_at:.6f}",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intent_ir_id": self.intent_ir_id,
            "run_id": self.run_id,
            "intent_type": self.intent_type,
            "confidence": self.confidence,
            "goal": dict(self.goal),
            "entities": list(self.entities),
            "parameters": dict(self.parameters),
            "execution_metadata": dict(self.execution_metadata),
            "created_at": self.created_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "IntentIR":
        """Reconstruct from a dict — e.g. a client-supplied /execute payload.

        Does NOT verify the signature; call verify_intent_ir() separately.
        Malformed input (wrong types, missing keys) raises — callers must
        catch this and treat it as a validation failure, never silently
        fall back to reclassification.
        """
        return cls(
            schema_version=str(d["schema_version"]),
            intent_ir_id=str(d["intent_ir_id"]),
            run_id=str(d["run_id"]),
            intent_type=str(d["intent_type"]),
            confidence=float(d["confidence"]),
            goal=_frozen_mapping(d.get("goal")),
            entities=tuple(d.get("entities") or ()),
            parameters=_frozen_mapping(d.get("parameters")),
            execution_metadata=_frozen_mapping(d.get("execution_metadata")),
            created_at=float(d["created_at"]),
            signature=str(d.get("signature", "")),
        )


def build_intent_ir(
    *,
    intent: dict[str, Any],
    goal: Any,
    run_id: str,
    question: str,
    group: str = "",
) -> IntentIR:
    """Compile a classified intent + resolved goal into a signed IntentIR.

    Only the planner calls this. `intent`/`goal` must already come from
    routing.resolve_intent / resolve_goal_from_intent — this function does
    not classify anything itself.
    """
    goal_dict = goal.to_dict() if hasattr(goal, "to_dict") else dict(goal or {})
    ir = IntentIR(
        schema_version=CURRENT_SCHEMA_VERSION,
        intent_ir_id=f"ir-{uuid4().hex[:16]}",
        run_id=run_id,
        intent_type=str(intent.get("intent", "")),
        confidence=float(intent.get("confidence", 0.0)),
        goal=_frozen_mapping(goal_dict),
        entities=tuple(getattr(goal, "entities", None) or ()),
        parameters=_frozen_mapping(
            {
                "workload_id": intent.get("workload_id", ""),
                "group": group,
            }
        ),
        execution_metadata=_frozen_mapping(
            {
                "question": question,
                "goal_type": goal_dict.get("goal_type", ""),
            }
        ),
        created_at=time.time(),
    )
    signature = sign_intent_ir(ir)
    # dataclasses.replace would re-run __init__ fine since frozen just blocks
    # attribute assignment, not construction.
    from dataclasses import replace

    return replace(ir, signature=signature)


def sign_intent_ir(ir: IntentIR) -> str:
    """Sign the IntentIR using Ed25519 via the identity module."""
    blob = ir._signing_payload().encode("utf-8")
    try:
        from src.monkey_brain.kernel.identity import (
            get_identity,
            get_key_manager,
            sign_bytes,
        )

        identity = get_identity()
        km = get_key_manager()
        key = km.get_or_create(identity.runtime_id)
        return sign_bytes(blob, key)
    except Exception:
        # Fallback: HMAC with server key (backward compat)
        return hmac.new(_signing_key(), blob, hashlib.sha256).hexdigest()


def verify_intent_ir(ir: IntentIR) -> bool:
    """Verify IntentIR signature (Ed25519, HMAC fallback), memoized per object.

    The SAME signed IntentIR flows from the route down through each runtime and gets
    re-verified 2–3× per request (route → SimulationRuntime.run / execute_context; /compare
    hits three layers). The signature check is real crypto, not free. Verification is a pure
    function of the IR's FROZEN content, so caching the result on the object is exact — and
    scoped to the object's lifetime (the request), so a fresh request re-verifies against the
    current keys (no key-rotation staleness, no cross-request sharing). A tampered payload
    produces a different object with no memo, so it is always verified for real.
    """
    cached = getattr(ir, "_verify_result", None)
    if cached is not None:
        return cached
    result = _verify_signature(ir)
    # ir is frozen; object.__setattr__ writes the memo without disturbing the declared
    # fields (dataclass __eq__/__hash__ ignore it).
    object.__setattr__(ir, "_verify_result", result)
    return result


def _verify_signature(ir: IntentIR) -> bool:
    """The actual signature verification — Ed25519 first, HMAC fallback."""
    if not ir.signature:
        return False
    blob = ir._signing_payload().encode("utf-8")

    # Try Ed25519 verification
    try:
        from src.monkey_brain.kernel.identity import get_key_manager, verify_bytes

        km = get_key_manager()
        # We don't know which runtime signed it, so try the local identity
        from src.monkey_brain.kernel.identity import get_identity

        identity = get_identity()
        pub_pem = km.get_public_key_pem(identity.runtime_id)
        if verify_bytes(blob, ir.signature, pub_pem):
            return True
    except Exception:
        logger.debug("_verify_signature: suppressed exception", exc_info=True)

    # Fallback: HMAC verification
    expected = hmac.new(_signing_key(), blob, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, ir.signature)


class IntentIRRejected(Exception):
    """A client-supplied intent_ir that cannot be trusted to execute.

    `kind` is "malformed" (the payload could not be parsed into an IntentIR) or
    "invalid" (it parsed but failed schema/shape/signature validation). `detail` is a
    message string (malformed) or the list of validation errors (invalid). Route handlers
    catch this and render it in their own surface — JSONResponse, HTTPException, or an SSE
    _fatal_stream — so the decode+validate sequence stays in one place while each route
    keeps its own error shape.
    """

    def __init__(self, kind: str, detail: "str | list[str]") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail if isinstance(detail, str) else "; ".join(detail))


def decode_and_validate_ir(raw: Any) -> IntentIR:
    """Rebuild an IntentIR from a raw request payload and fully validate it.

    The single preamble shared by every route that runs a client-supplied plan
    (/execute, /execute/stream, /simulate, /compare). Rejects the same inputs the same
    way — previously /execute/stream caught a broader exception set than the others, so
    the routes could disagree on what counts as malformed. Raises IntentIRRejected on
    either failure; callers own only how they render it. A falsy payload is left to the
    caller to special-case (each route has its own "Missing intent_ir — run /plan first"
    hint), but is still rejected here as a backstop.
    """
    if not raw:
        raise IntentIRRejected("malformed", "Missing intent_ir")
    try:
        ir = IntentIR.from_dict(raw)
    except (KeyError, ValueError, TypeError) as e:
        raise IntentIRRejected("malformed", str(e))
    errors = validate_intent_ir(ir)
    if errors:
        raise IntentIRRejected("invalid", errors)
    return ir


def validate_intent_ir(ir: IntentIR) -> list[str]:
    """Structural validation — never re-runs classification.

    Returns a list of human-readable errors; empty list means valid.
    Execution must stop immediately if this is non-empty.
    """
    errors: list[str] = []

    if ir.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"unsupported schema_version {ir.schema_version!r} (supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
        # Field shapes may differ across versions — don't validate further.
        return errors

    if not ir.intent_type:
        errors.append("intent_type is required and must be non-empty")

    if not ir.run_id:
        errors.append("run_id is required and must be non-empty")

    if not (0.0 <= ir.confidence <= 1.0):
        errors.append(f"confidence out of range [0,1]: {ir.confidence}")

    goal_name = ir.goal.get("name") if isinstance(ir.goal, Mapping) else None
    if not goal_name:
        errors.append("goal.name is required and must be non-empty")

    if not isinstance(ir.entities, tuple):
        errors.append("entities must be a tuple")
    else:
        for e in ir.entities:
            if not isinstance(e, str):
                errors.append(f"entity {e!r} is not a string")

    if not isinstance(ir.parameters, Mapping):
        errors.append("parameters must be a mapping")

    if not isinstance(ir.execution_metadata, Mapping):
        errors.append("execution_metadata is required and must be a mapping")
    elif "question" not in ir.execution_metadata:
        errors.append("execution_metadata.question is required")

    if not verify_intent_ir(ir):
        errors.append("signature verification failed — intent payload may be tampered or corrupted")

    return errors
