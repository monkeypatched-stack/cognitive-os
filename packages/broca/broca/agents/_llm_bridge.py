"""Dev-mode LLM bridge — route LLM generate() calls to an external answerer.

When LLM_DEV_BRIDGE is enabled, provider selection returns a LocalBridgeClient
instead of Ollama/Claude. Each generate() call publishes the prompt to a queue
directory and blocks until a sibling response file appears, letting an external
process (or a human/agent) answer prompts dynamically — no API keys, no local
model, no hardcoded responses. Intended for development and offline testing.

Enable:      LLM_DEV_BRIDGE=1
Queue dir:   LLM_BRIDGE_DIR         (default /tmp/mb-llm-bridge)
Timeout:     LLM_BRIDGE_TIMEOUT     (seconds, default 600)

Protocol (one request/response pair per LLM call):
    {id}.request.json   written by the client:
        {"id", "tag", "system", "prompt"}
    {id}.response.txt   written by the answerer with the completion text.
The client deletes both files once it reads the response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger("broca.llm_bridge")


def dev_bridge_enabled() -> bool:
    return os.getenv("LLM_DEV_BRIDGE", "").strip().lower() in ("1", "true", "yes", "on")


def bridge_dir() -> Path:
    d = Path(os.getenv("LLM_BRIDGE_DIR", "/tmp/mb-llm-bridge"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class LocalBridgeClient:
    """LLM client whose generate() is answered by an external process.

    Same interface as OllamaClient / ClaudeClient (async generate(prompt,
    system) -> str), so it is a drop-in provider.
    """

    def __init__(self, tag: str = "llm"):
        self.tag = tag
        self.dir = bridge_dir()
        self.timeout = float(os.getenv("LLM_BRIDGE_TIMEOUT", "600"))

    async def generate(self, prompt: str, system: str = "") -> str:
        rid = uuid4().hex
        req = self.dir / f"{rid}.request.json"
        tmp = self.dir / f"{rid}.request.tmp"
        resp = self.dir / f"{rid}.response.txt"

        payload = {"id": rid, "tag": self.tag, "system": system, "prompt": prompt}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.rename(req)  # atomic publish so a reader never sees a partial file
        logger.info("[llm_bridge] queued %s (tag=%s, %d chars)", rid, self.tag, len(prompt))

        waited = 0.0
        interval = 0.4
        while waited < self.timeout:
            if resp.exists():
                text = resp.read_text()
                for p in (req, resp):
                    try:
                        p.unlink()
                    except OSError:
                        pass
                logger.info("[llm_bridge] answered %s (%d chars)", rid, len(text))
                return text
            await asyncio.sleep(interval)
            waited += interval

        try:
            req.unlink()
        except OSError:
            pass
        raise TimeoutError(f"LLM bridge timed out after {self.timeout}s waiting for {rid}")


def dev_bridge_client(tag: str = "llm"):
    """Return a LocalBridgeClient when the dev bridge is enabled, else None.

    Provider-selection functions call this first: if it returns a client, use
    it; otherwise fall through to the normal Ollama/Claude selection.
    """
    if dev_bridge_enabled():
        logger.info("[llm_bridge] dev bridge ENABLED — routing %s LLM calls to queue", tag)
        return LocalBridgeClient(tag=tag)
    return None
