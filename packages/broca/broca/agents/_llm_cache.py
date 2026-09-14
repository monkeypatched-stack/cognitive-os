"""Content-addressed response cache for LLM generate() calls.

Wraps any client exposing async generate(prompt, system) -> str. Keys on
sha256(model + system + prompt), so identical calls — the same planning prompt
issued for both the execute and simulate targets, the compiler's repeated
candidate prompts, or a goal re-asked later — return instantly instead of
re-invoking the model (or, under the dev bridge, re-asking the answerer).

Enable:   LLM_RESPONSE_CACHE=1
Dir:      LLM_CACHE_DIR   (default /tmp/mb-llm-cache) — file-backed, so the
          cache survives restarts and is shared across worker processes.
Size:     LLM_CACHE_MAX   (in-process LRU entries, default 1024)

Trade-off: caching is deterministic — it collapses the planner's multi-candidate
exploration (identical prompts) to a single response. That is pure win for
repeated/deterministic calls; for calls that must stay diverse, disable the
cache or vary the prompt. Reads never fail the request: any cache error falls
through to the wrapped client.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger("broca.llm_cache")


def response_cache_enabled() -> bool:
    return os.getenv("LLM_RESPONSE_CACHE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _cache_dir() -> Path:
    d = Path(os.getenv("LLM_CACHE_DIR", "/tmp/mb-llm-cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class CachedLLMClient:
    """Caching decorator around an LLM client (drop-in provider)."""

    def __init__(self, inner, model_hint: str = "llm"):
        self.inner = inner
        self.model = getattr(inner, "model", model_hint)
        self.dir = _cache_dir()
        self._mem: "OrderedDict[str, str]" = OrderedDict()
        self._max = int(os.getenv("LLM_CACHE_MAX", "1024"))
        self.hits = 0
        self.misses = 0

    def _key(self, prompt: str, system: str) -> str:
        raw = f"{self.model}\x00{system or ''}\x00{prompt}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _mem_get(self, key: str):
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        return None

    def _mem_put(self, key: str, value: str) -> None:
        self._mem[key] = value
        self._mem.move_to_end(key)
        while len(self._mem) > self._max:
            self._mem.popitem(last=False)

    async def generate(self, prompt: str, system: str = "") -> str:
        key = self._key(prompt, system)

        cached = self._mem_get(key)
        if cached is not None:
            self.hits += 1
            return cached

        path = self.dir / key
        try:
            if path.exists():
                cached = path.read_text()
                self._mem_put(key, cached)
                self.hits += 1
                return cached
        except OSError:
            pass  # fall through to a live call

        self.misses += 1
        value = await self.inner.generate(prompt, system)

        self._mem_put(key, value)
        try:
            tmp = self.dir / f"{key}.tmp"
            tmp.write_text(value)
            tmp.rename(path)  # atomic — a reader never sees a partial file
        except OSError:
            pass  # cache is best-effort; the response is still returned
        return value


def maybe_cache(client, model_hint: str = "llm"):
    """Wrap `client` in a CachedLLMClient when caching is enabled and there is
    a client to wrap; otherwise return it unchanged."""
    if client is not None and response_cache_enabled():
        logger.info("[llm_cache] response cache ENABLED for %s", model_hint)
        return CachedLLMClient(client, model_hint=model_hint)
    return client
