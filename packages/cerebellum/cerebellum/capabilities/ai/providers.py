"""AI Provider Capabilities — LLM and AI integrations."""

from __future__ import annotations

import os
from cerebellum.capability import Capability


class OpenAICapability(Capability):
    def __init__(self, api_key: str = ""):
        super().__init__(name="openai")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    async def execute(self, state, **kwargs):
        if not self._api_key:
            return {"status": "unavailable", "reason": "OPENAI_API_KEY not configured"}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": state.get("model", "gpt-4"),
                        "messages": [{"role": "user", "content": state.get("prompt", "")}],
                    },
                )
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


class AnthropicCapability(Capability):
    def __init__(self, api_key: str = ""):
        super().__init__(name="anthropic")
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    async def execute(self, state, **kwargs):
        if not self._api_key:
            return {
                "status": "unavailable",
                "reason": "ANTHROPIC_API_KEY not configured",
            }
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model": state.get("model", "claude-3"),
                        "messages": [{"role": "user", "content": state.get("prompt", "")}],
                    },
                )
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


class GeminiCapability(Capability):
    def __init__(self, api_key: str = ""):
        super().__init__(name="gemini")
        self._api_key = api_key

    async def execute(self, state, **kwargs):
        return {"status": "configured", "provider": "google_gemini"}


class AzureOpenAICapability(Capability):
    def __init__(self, endpoint: str = "", api_key: str = ""):
        super().__init__(name="azure_openai")
        self._endpoint = endpoint
        self._api_key = api_key

    async def execute(self, state, **kwargs):
        return {"status": "configured", "provider": "azure_openai"}


class AWSBedrockCapability(Capability):
    def __init__(self, region: str = "us-east-1"):
        super().__init__(name="aws_bedrock")
        self._region = region

    async def execute(self, state, **kwargs):
        return {"status": "configured", "provider": "aws_bedrock"}


class OllamaCapability(Capability):
    def __init__(self, url: str = "http://localhost:11434"):
        super().__init__(name="ollama")
        self._url = url

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._url}/api/chat",
                json={
                    "model": state.get("model", "gemma3:latest"),
                    "messages": [{"role": "user", "content": state.get("prompt", "")}],
                },
            )
            return response.json()


class HuggingFaceCapability(Capability):
    def __init__(self, api_key: str = ""):
        super().__init__(name="huggingface")
        self._api_key = api_key

    async def execute(self, state, **kwargs):
        return {"status": "configured", "provider": "huggingface"}


class OpenRouterCapability(Capability):
    def __init__(self, api_key: str = ""):
        super().__init__(name="openrouter")
        self._api_key = api_key

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": state.get("model", "openai/gpt-4o-mini"),
                    "messages": [{"role": "user", "content": state.get("prompt", "")}],
                },
            )
            return response.json()


class LocalModelCapability(Capability):
    def __init__(self, model_path: str = ""):
        super().__init__(name="local_model")
        self._model_path = model_path

    async def execute(self, state, **kwargs):
        return {"status": "configured", "provider": "local_model"}
