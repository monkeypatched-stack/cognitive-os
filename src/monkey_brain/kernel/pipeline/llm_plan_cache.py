"""Content-addressed cache for llm_planner.py's raw LLM responses.

Mirrors packages/broca/broca/agents/_llm_cache.py's exact shape (same
LLM_RESPONSE_CACHE/LLM_CACHE_DIR/LLM_CACHE_MAX env vars, same
sha256(model+system+prompt) key, same atomic tmp-then-rename write) —
duplicated rather than imported since monkey_brain and broca are separate
installable packages (same convention as kernel/execute/provider/
model_backend.py's own _dev_bridge duplicating packages/broca/broca/
agents/_llm_bridge.py).

Deliberately WRITE-ON-SUCCESS-ONLY, unlike CachedLLMClient (which caches
every response unconditionally): llm_planner.py's retry loop
(_MAX_PARSE_ATTEMPTS) exists specifically because a malformed-JSON
response is common and re-asking the SAME prompt commonly gets a
well-formed one on the next attempt. A naive cache-every-response wrapper
would return the SAME broken response on every retry, permanently
defeating that recovery path. Only a response that actually parsed
successfully is ever written here — a fresh call is guaranteed on any
attempt where nothing usable is cached yet, and a corrupted/stale cache
entry that fails to parse is treated exactly like a miss (falls through
to a real call) rather than poisoning the request.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger("agentos.pipeline.llm_plan_cache")

_mem: "OrderedDict[str, str]" = OrderedDict()


def plan_cache_enabled() -> bool:
    return os.getenv("LLM_RESPONSE_CACHE", "").strip().lower() in ("1", "true", "yes", "on")


def _cache_dir() -> Path:
    d = Path(os.getenv("LLM_CACHE_DIR", "/tmp/mb-llm-cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_entries() -> int:
    return int(os.getenv("LLM_CACHE_MAX", "1024"))


def cache_key(model: str, system: str, prompt: str) -> str:
    raw = f"{model}\x00{system or ''}\x00{prompt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_cached_response(model: str, system: str, prompt: str) -> str | None:
    """Returns the cached raw response for an identical (model, system,
    prompt), or None on a miss. Never raises — a cache-read error is
    exactly a miss, not a request failure."""
    if not plan_cache_enabled():
        return None
    key = cache_key(model, system, prompt)

    if key in _mem:
        _mem.move_to_end(key)
        return _mem[key]

    try:
        path = _cache_dir() / key
        if path.exists():
            value = path.read_text()
            _mem[key] = value
            _mem.move_to_end(key)
            while len(_mem) > _max_entries():
                _mem.popitem(last=False)
            return value
    except OSError:
        pass
    return None


def put_cached_response(model: str, system: str, prompt: str, value: str) -> None:
    """Write-on-success only — see module docstring. Never raises; a
    failed cache write just means the next identical call misses too."""
    if not plan_cache_enabled():
        return
    key = cache_key(model, system, prompt)
    _mem[key] = value
    _mem.move_to_end(key)
    while len(_mem) > _max_entries():
        _mem.popitem(last=False)
    try:
        cache_dir = _cache_dir()
        tmp = cache_dir / f"{key}.tmp"
        tmp.write_text(value)
        tmp.rename(cache_dir / key)  # atomic — a reader never sees a partial file
    except OSError as exc:
        logger.debug("[llm_plan_cache] cache write failed (non-fatal): %s", exc)
