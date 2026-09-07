"""LLM Provider Abstraction Layer

Supports multiple LLM providers:
- Claude (Anthropic API)
- OpenRouter (multi-model API)
- Ollama (local)

Selected via LLM_PROVIDER environment variable.
"""

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Optional
from enum import Enum
import anthropic
import httpx


class LLMProvider(str, Enum):
    """Available LLM providers"""
    CLAUDE = "claude"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1000,
        model: Optional[str] = None
    ) -> str:
        """Get completion from LLM"""
        pass

    @abstractmethod
    def supports_batching(self) -> bool:
        """Whether provider supports batch operations"""
        pass


class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude provider"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude provider"""
        self.client = anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-3-5-sonnet-20241022"
        self.cache_hits = 0
        self.cache_misses = 0

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1000,
        model: Optional[str] = None
    ) -> str:
        """Get completion from Claude"""
        if model is None:
            model = self.model

        response = await asyncio.to_thread(
            self.client.messages.create,
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}]
        )

        return response.content[0].text

    def supports_batching(self) -> bool:
        """Claude supports batching"""
        return True


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider (multi-model)"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize OpenRouter provider"""
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")

        self.base_url = "https://openrouter.ai/api/v1"
        self.model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://github.com/monkeypatched",
                "X-Title": "MonkeyPatched v3.0",
            }
        )

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1000,
        model: Optional[str] = None
    ) -> str:
        """Get completion from OpenRouter"""
        if model is None:
            model = self.model

        response = await self.client.post(
            "/chat/completions",
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message}
                ]
            }
        )

        data = response.json()
        if "error" in data:
            raise ValueError(f"OpenRouter error: {data['error']}")

        return data["choices"][0]["message"]["content"]

    def supports_batching(self) -> bool:
        """OpenRouter doesn't support batching"""
        return False

    async def __aenter__(self):
        """Async context manager"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close HTTP client"""
        await self.client.aclose()


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM provider"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Ollama provider"""
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = os.getenv("OLLAMA_MODEL", "llama2")
        self.client = httpx.AsyncClient(base_url=self.base_url)

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1000,
        model: Optional[str] = None
    ) -> str:
        """Get completion from Ollama"""
        if model is None:
            model = self.model

        # Check if model is available
        try:
            models_response = await self.client.get("/api/tags")
            models_data = models_response.json()
            available_models = [m["name"] for m in models_data.get("models", [])]

            if model not in available_models:
                raise ValueError(
                    f"Model {model} not available in Ollama. "
                    f"Available: {available_models}"
                )
        except Exception as e:
            raise ValueError(f"Failed to connect to Ollama: {e}")

        # Make request to Ollama
        response = await self.client.post(
            "/api/generate",
            json={
                "model": model,
                "prompt": f"{system}\n\nUser: {user_message}",
                "stream": False,
                "num_predict": max_tokens,
            }
        )

        data = response.json()
        return data.get("response", "")

    def supports_batching(self) -> bool:
        """Ollama doesn't support batching"""
        return False

    async def __aenter__(self):
        """Async context manager"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close HTTP client"""
        await self.client.aclose()


class LLMProviderFactory:
    """Factory for creating LLM providers"""

    @staticmethod
    def get_provider(provider_name: Optional[str] = None) -> BaseLLMProvider:
        """Get LLM provider instance

        Provider is selected by:
        1. provider_name parameter
        2. LLM_PROVIDER environment variable
        3. Default: Ollama
        """
        if provider_name is None:
            provider_name = os.getenv("LLM_PROVIDER", LLMProvider.OLLAMA.value)

        provider_name = provider_name.lower()

        if provider_name == LLMProvider.CLAUDE.value:
            return ClaudeProvider()

        elif provider_name == LLMProvider.OPENROUTER.value:
            return OpenRouterProvider()

        elif provider_name == LLMProvider.OLLAMA.value:
            return OllamaProvider()

        else:
            raise ValueError(
                f"Unknown LLM provider: {provider_name}. "
                f"Supported: claude, openrouter, ollama"
            )

    @staticmethod
    def get_provider_async(provider_name: Optional[str] = None) -> BaseLLMProvider:
        """Get async LLM provider"""
        return LLMProviderFactory.get_provider(provider_name)


class LLMConfig:
    """Configuration for LLM usage"""

    def __init__(self):
        """Load configuration from environment"""
        self.provider = os.getenv("LLM_PROVIDER", "ollama").lower()
        self._enabled = None  # Cache for enabled state (checked dynamically)
        self.cache_enabled = os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"
        self.cache_ttl_seconds = int(os.getenv("LLM_CACHE_TTL", "3600"))

        # Provider-specific config
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama2")
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        # Performance config
        self.timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        self.max_retries = int(os.getenv("LLM_MAX_RETRIES", "3"))

    @property
    def enabled(self) -> bool:
        """Check if LLM is enabled (dynamic, checks env var each time)"""
        return os.getenv("USE_LLM", "true").lower() == "true"

    def get_model_for_provider(self) -> str:
        """Get model name for current provider"""
        if self.provider == "claude":
            return self.claude_model
        elif self.provider == "openrouter":
            return self.openrouter_model
        elif self.provider == "ollama":
            return self.ollama_model
        else:
            return self.claude_model

    def __repr__(self) -> str:
        """String representation"""
        return (
            f"LLMConfig("
            f"provider={self.provider}, "
            f"enabled={self.enabled}, "
            f"model={self.get_model_for_provider()}"
            f")"
        )


# Global config instance
llm_config = LLMConfig()


async def get_llm_completion(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1000,
    provider_name: Optional[str] = None
) -> str:
    """Convenience function to get LLM completion

    Uses configured provider or specified one.
    """
    if not llm_config.enabled:
        raise RuntimeError("LLM is disabled (USE_LLM=false)")

    provider = LLMProviderFactory.get_provider(provider_name)

    # provider_name=None means "use the configured default" (LLM_PROVIDER,
    # resolved by LLMConfig above) — the model must match THAT provider,
    # not be hardcoded to Claude's, or a non-Claude default provider gets
    # sent a Claude model id.
    if provider_name is None:
        model = llm_config.get_model_for_provider()
    elif provider_name == "claude":
        model = llm_config.claude_model
    elif provider_name == "openrouter":
        model = llm_config.openrouter_model
    elif provider_name == "ollama":
        model = llm_config.ollama_model
    else:
        model = None

    try:
        result = await asyncio.wait_for(
            provider.complete(system_prompt, user_message, max_tokens, model),
            timeout=llm_config.timeout_seconds
        )
        return result

    except asyncio.TimeoutError:
        raise TimeoutError(
            f"LLM request timed out after {llm_config.timeout_seconds} seconds"
        )
    except Exception as e:
        raise RuntimeError(f"LLM request failed: {e}")
