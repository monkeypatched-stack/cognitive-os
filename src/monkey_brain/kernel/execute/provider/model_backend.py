"""ModelBackend — routes StructuredPromptIR to the right LLM provider.

Currently implements Claude, OpenRouter, GPT, Gemini, Qwen, Ollama,
llama.cpp (a local `llama-server` process, distinct from Ollama) — all
share the same interface. Provider is selected via MODEL_BACKEND env var
(default: ollama).

The PromptCompilerAgent produces a StructuredPromptIR.
ModelBackend.complete() takes it and returns the raw model response string.

Nothing in the runtime knows which provider is active — all model knowledge
lives here.

complete() is async so a caller's cancellation (asyncio.wait_for's timeout,
e.g. CognitiveActor._cognitive_tick's 60s cap) can actually reach the
in-flight network call, not just stop awaiting it. This matters concretely
for Ollama: docs/adr/019-runtime-performance-audit.md's investigation
confirmed live that a timed-out tick's LLM call kept running to
completion in an orphaned background thread when it was wrapped in
asyncio.to_thread — real work continuing to consume Ollama capacity
(now, post the asyncio.gather actor-tick fix, contending with every
other concurrently-ticking actor) minutes after the runtime had already
reported it as failed. The Ollama and OpenRouter paths below use
httpx.AsyncClient directly (a real awaited I/O call — asyncio can
genuinely cancel this), so a cancellation actually aborts the request
instead of merely abandoning it; OpenRouter got the same treatment while
it was briefly the default provider and it was left in place after
switching back to Ollama, since it costs nothing to keep. The other
providers' SDKs (anthropic/openai/google-generativeai) are synchronous
clients with no async counterpart wired in here; their calls run via
asyncio.to_thread as before, unaffected by this change and carrying the
same fire-and-forget limitation MODEL_BACKEND=ollama used to have before
its own fix.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("monkey_brain.model_backend")

_DEFAULT_PROVIDER = os.environ.get("MODEL_BACKEND", "ollama")
_DEFAULT_MODEL_MAP: dict[str, str] = {
    "claude":     "claude-sonnet-4-6",
    "openrouter": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
    "gpt":        "gpt-4o",
    "gemini":     "gemini-1.5-pro",
    "qwen":       "qwen2.5-72b-instruct",
    "ollama":     os.environ.get("OLLAMA_MODEL", "gemma3:latest"),
    # llama-server serves exactly one model per process (whatever -m
    # pointed at on startup) -- this "model" field is only ever echoed
    # back in its OpenAI-compatible response, never used for routing, so
    # it's descriptive/observability metadata, not a live selector.
    "llamacpp":   os.environ.get("LLAMACPP_MODEL", "gemma-3-4b-it-Q4_K_M"),
}


class ModelBackend:
    """Routes compiled prompts to the configured LLM provider.

    Usage:
        backend = ModelBackend()
        response = backend.complete(ir, system="You are...")
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> None:
        self._provider = provider or _DEFAULT_PROVIDER
        self._model = model or _DEFAULT_MODEL_MAP.get(self._provider, "claude-sonnet-4-6")
        self._max_tokens = max_tokens
        self._total_tokens = 0
        self._call_count = 0

    async def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Send prompt to the configured model, return raw text response.

        Genuinely cancellable for the Ollama provider (real awaited I/O —
        see module docstring); the other providers still run their
        synchronous SDK call via asyncio.to_thread, same as calling them
        used to look from the outside, just now behind an async interface
        instead of a sync one so every caller has one calling convention
        regardless of provider.
        """
        self._call_count += 1
        tokens = max_tokens or self._max_tokens

        if self._provider == "claude":
            return await asyncio.to_thread(self._claude, prompt, system, tokens, **kwargs)
        if self._provider == "openrouter":
            return await self._openrouter(prompt, system, tokens, **kwargs)
        if self._provider == "gpt":
            return await asyncio.to_thread(self._gpt, prompt, system, tokens, **kwargs)
        if self._provider == "gemini":
            return await asyncio.to_thread(self._gemini, prompt, system, tokens, **kwargs)
        if self._provider == "qwen":
            return await asyncio.to_thread(self._qwen, prompt, system, tokens, **kwargs)
        if self._provider == "ollama":
            return await self._ollama(prompt, system, tokens, **kwargs)
        if self._provider == "llamacpp":
            return await self._llamacpp(prompt, system, tokens, **kwargs)
        if self._provider == "dev_bridge":
            return await self._dev_bridge(prompt, system, tokens, **kwargs)
        raise ValueError(f"Unknown MODEL_BACKEND provider: {self._provider!r}")

    def stats(self) -> dict[str, Any]:
        return {"provider": self._provider, "model": self._model, "calls": self._call_count, "total_tokens": self._total_tokens}

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    def _claude(self, prompt: str, system: str, max_tokens: int, **kwargs: Any) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        messages = [{"role": "user", "content": prompt}]
        resp = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system or "You are a precise, expert engineering agent.",
            messages=messages,
            **kwargs,
        )
        self._total_tokens += resp.usage.input_tokens + resp.usage.output_tokens
        return resp.content[0].text

    async def _openrouter(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        # A real httpx.AsyncClient call (same reasoning as _ollama above:
        # genuinely cancellable, not a to_thread-wrapped synchronous SDK
        # call) — worth it here specifically because openrouter is now the
        # primary provider, the most-used path a cancellation needs to
        # actually reach. Also sidesteps depending on the `openai` package,
        # which isn't a declared dependency of this project (confirmed:
        # ModuleNotFoundError in the venv this was verified in) — the other
        # OpenRouter call sites added alongside this one (broca agents,
        # codegen_agent.py, classifier.py) all use raw httpx for the same
        # reason.
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        base_url = os.environ.get("OPENROUTER_API_BASE_URL") or os.environ.get(
            "OPENROUTER_API_URL", "https://openrouter.ai/api/v1",
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": os.environ.get("APP_URL", "https://github.com/monkeypatched"),
                    "X-Title": os.environ.get("APP_NAME", "MonkeyBrain"),
                },
                json={"model": self._model, "messages": messages, "max_tokens": max_tokens},
            )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        usage = data.get("usage") or {}
        self._total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        return data["choices"][0]["message"]["content"] or ""

    def _gpt(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        import openai
        client = openai.OpenAI(api_key=api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(model=self._model, messages=messages, max_tokens=max_tokens)
        self._total_tokens += resp.usage.prompt_tokens + resp.usage.completion_tokens
        return resp.choices[0].message.content or ""

    def _gemini(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        full = f"{system}\n\n{prompt}" if system else prompt
        model = genai.GenerativeModel(self._model)
        resp = model.generate_content(full, generation_config={"max_output_tokens": max_tokens})
        return resp.text

    def _qwen(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        # Qwen via OpenAI-compatible endpoint (Alibaba DashScope)
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        base_url = os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY not set")
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(model=self._model, messages=messages, max_tokens=max_tokens)
        self._total_tokens += resp.usage.prompt_tokens + resp.usage.completion_tokens
        return resp.choices[0].message.content or ""

    async def _ollama(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        # Mirrors broca.agents.specification_discovery_agent.OllamaClient's
        # request shape (/api/chat, same message format) so both LLM paths
        # hit the local Ollama server identically.
        #
        # A real httpx.AsyncClient call, not httpx.post() run in a worker
        # thread: this is what actually makes the call cancellable (see
        # module docstring) -- asyncio can abort an in-flight awaited I/O
        # operation; it cannot forcibly stop a synchronous call already
        # running inside asyncio.to_thread's worker thread.
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        import httpx
        # Real gap this closes: Ollama's own default context window is
        # 4096 tokens when no override is given -- confirmed live via the
        # running llama-server process args ("-c 4096"). llm_planner.py's
        # _SYSTEM_PROMPT alone is ~10.3KB (~2000-2600 tokens), and the
        # grounded user prompt grows with an actor's own accumulated
        # history (knowledge/relationships/context events), commonly
        # reaching 900+ more tokens -- leaving too little budget for the
        # model's own multi-step JSON plan response. With --context-shift
        # enabled, Ollama does not error when this overflows; it silently
        # truncates, and a plan response cut off mid-generation can still
        # parse as valid JSON while missing a whole step (confirmed live:
        # repeated "OrderCreation" omissions specifically on longer
        # prompts, e.g. a repeat purchase whose grounding includes a
        # prior order's own relationship). num_ctx here overrides that
        # default per-request, independent of whatever Ollama's own
        # server-level default is configured to.
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": self._model, "messages": messages, "stream": False,
                    "options": {"num_ctx": 8192},
                },
            )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def _llamacpp(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        # A locally-run `llama-server` (llama.cpp's own OpenAI-compatible
        # HTTP server, not Ollama) -- same _openrouter/_ollama real-
        # httpx.AsyncClient reasoning (genuinely cancellable, no `openai`
        # package dependency). Adopted specifically because Ollama's own
        # HTTP round-trip + retry-on-degenerate-confidence (llm_planner.py)
        # could together exceed Kong's agentos-service gateway timeout
        # (confirmed live: a 504 upstream timeout on this exact model/
        # prompt combination) -- llama-server serving the SAME model
        # weights directly, with no Ollama layer in between, is
        # meaningfully faster per call on this hardware.
        base_url = os.environ.get("LLAMACPP_BASE_URL", "http://localhost:8090")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json={"model": self._model, "messages": messages, "max_tokens": max_tokens},
            )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        self._total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        return data["choices"][0]["message"]["content"] or ""

    async def _dev_bridge(self, prompt: str, system: str, max_tokens: int, **_: Any) -> str:
        """Route this completion to an external answerer via a file queue
        instead of any real provider — no API key, no local model. Mirrors
        packages/broca/broca/agents/_llm_bridge.py's protocol exactly (same
        queue dir/timeout env vars, same {id}.request.json / {id}.response.txt
        pair) so both LLM paths in this repo can be answered the same way;
        duplicated rather than imported since monkey_brain and broca are
        separate installable packages and this is the only piece either
        needs from the other.
        """
        import json
        from pathlib import Path
        from uuid import uuid4

        bridge_dir = Path(os.environ.get("LLM_BRIDGE_DIR", "/tmp/mb-llm-bridge"))
        bridge_dir.mkdir(parents=True, exist_ok=True)
        timeout = float(os.environ.get("LLM_BRIDGE_TIMEOUT", "600"))

        rid = uuid4().hex
        req = bridge_dir / f"{rid}.request.json"
        tmp = bridge_dir / f"{rid}.request.tmp"
        resp_path = bridge_dir / f"{rid}.response.txt"

        payload = {"id": rid, "tag": "monkey_brain", "system": system, "prompt": prompt}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.rename(req)  # atomic publish so a reader never sees a partial file
        logger.info("[model_backend] dev bridge queued %s (%d chars)", rid, len(prompt))

        waited = 0.0
        interval = 0.4
        while waited < timeout:
            if resp_path.exists():
                text = resp_path.read_text()
                for p in (req, resp_path):
                    try:
                        p.unlink()
                    except OSError:
                        pass
                logger.info("[model_backend] dev bridge answered %s (%d chars)", rid, len(text))
                return text
            await asyncio.sleep(interval)
            waited += interval

        try:
            req.unlink()
        except OSError:
            pass
        raise TimeoutError(f"LLM dev bridge timed out after {timeout}s waiting for {rid}")


# Module-level default backend — reused across calls to amortise client init
_default_backend: ModelBackend | None = None


def get_backend() -> ModelBackend:
    global _default_backend
    if _default_backend is None:
        _default_backend = ModelBackend()
    return _default_backend
