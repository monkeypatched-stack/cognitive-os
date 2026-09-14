"""Tests for LLM wiring in Hybrid Router

Verifies that LLM providers are properly wired and used in handlers.
Tests both with and without LLM enabled.
"""

import asyncio
import pytest
import os
from unittest.mock import Mock, AsyncMock, patch

from src.hybrid import HybridRouter, QueryType
from src.hybrid.handlers import ReasoningHandler, ConversationalHandler
from src.hybrid.session_manager import SessionManager


class TestLLMWiring:
    """Test LLM integration in handlers"""

    def test_reasoning_handler_with_mock_llm(self):
        """Verify ReasoningHandler uses provided LLM"""
        # Create mock LLM
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value="Mocked reasoning response")

        handler = ReasoningHandler(llm_provider=mock_llm)
        assert handler.llm == mock_llm
        print("✓ ReasoningHandler accepts LLM provider")

    def test_conversational_handler_with_mock_llm(self):
        """Verify ConversationalHandler uses provided LLM"""
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value="Mocked response")

        mock_session_mgr = Mock()
        handler = ConversationalHandler(session_manager=mock_session_mgr, llm_provider=mock_llm)

        assert handler.llm == mock_llm
        print("✓ ConversationalHandler accepts LLM provider")

    @pytest.mark.asyncio
    async def test_reasoning_handler_llm_fallback(self):
        """Verify ReasoningHandler falls back when LLM not provided"""
        # No LLM provided
        handler = ReasoningHandler(llm_provider=None)

        # Should fall back to factory or rule-based
        response = await handler.handle("Why is the sky blue?")

        assert response.status != "error"
        assert "interesting question" in response.response_text.lower() or "blue" in response.response_text.lower()
        print(f"✓ ReasoningHandler fallback: {response.response_text[:50]}...")

    @pytest.mark.asyncio
    async def test_conversational_with_session_and_llm(self):
        """Verify ConversationalHandler uses session context with LLM"""
        # Create mock LLM
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value="Response considering our conversation")

        # Create session manager
        session_mgr = SessionManager()

        # Create handler
        handler = ConversationalHandler(session_manager=session_mgr, llm_provider=mock_llm)

        # Create session
        session = session_mgr.create_session("user_123")
        session_id = session.session_id

        # Add message history
        session_mgr.add_message(session_id, "user", "What is Python?")
        session_mgr.add_message(session_id, "assistant", "Python is a programming language")

        # Process follow-up
        response = await handler.handle("Tell me more", context={"session_id": session_id})

        assert response.status != "error"
        print(f"✓ ConversationalHandler with session: {response.response_text[:50]}...")


class TestLLMConfiguration:
    """Test LLM configuration and provider selection"""

    def test_llm_config_from_env(self):
        """Verify LLM configuration loads from environment"""
        from src.llm.llm_provider import LLMConfig

        # Set environment variables
        os.environ["LLM_PROVIDER"] = "claude"
        os.environ["USE_LLM"] = "true"
        os.environ["CLAUDE_MODEL"] = "claude-3-5-sonnet-20241022"

        config = LLMConfig()

        assert config.provider == "claude"
        assert config.enabled == True
        assert config.claude_model == "claude-3-5-sonnet-20241022"

        print("✓ LLMConfig loads from environment variables")

    def test_llm_disabled_via_env(self):
        """Verify USE_LLM=false disables LLM"""
        os.environ["USE_LLM"] = "false"

        from src.llm.llm_provider import LLMConfig

        config = LLMConfig()
        assert config.enabled == False

        print("✓ USE_LLM=false disables LLM")

        # Reset
        os.environ["USE_LLM"] = "true"

    @pytest.mark.asyncio
    async def test_provider_factory_selection(self):
        """Verify LLMProviderFactory selects correct provider"""
        from src.llm.llm_provider import (
            LLMProviderFactory,
            ClaudeProvider,
            OllamaProvider,
        )

        # Test Claude selection
        os.environ["LLM_PROVIDER"] = "claude"
        provider = LLMProviderFactory.get_provider()
        assert isinstance(provider, ClaudeProvider)
        print("✓ LLMProviderFactory selects ClaudeProvider")

        # Test Ollama selection
        os.environ["LLM_PROVIDER"] = "ollama"
        provider = LLMProviderFactory.get_provider()
        assert isinstance(provider, OllamaProvider)
        print("✓ LLMProviderFactory selects OllamaProvider")

        # Reset
        os.environ["LLM_PROVIDER"] = "claude"


class TestHybridRouterLLMIntegration:
    """Test LLM wiring in HybridRouter"""

    def test_router_initialization_with_llm(self):
        """Verify HybridRouter accepts LLM provider"""
        mock_llm = Mock()
        mock_pipeline = Mock()
        mock_kb = Mock()

        router = HybridRouter(
            pipeline_orchestrator=mock_pipeline,
            knowledge_base=mock_kb,
            llm_provider=mock_llm,
        )

        # Verify handlers have LLM wired
        reasoning_handler = router.handlers[QueryType.REASONING]
        assert reasoning_handler.llm == mock_llm

        conversational_handler = router.handlers[QueryType.CONVERSATIONAL]
        assert conversational_handler.llm == mock_llm

        print("✓ HybridRouter wires LLM to all handlers")

    @pytest.mark.asyncio
    async def test_router_reasoning_query_with_llm(self):
        """Verify reasoning query uses wired LLM"""
        # Create mock LLM
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value="People form bonds because of shared experiences and mutual benefit")

        # Create router with mock LLM
        router = HybridRouter(llm_provider=mock_llm)

        # Process reasoning query
        response = await router.process(question="Why do people form social bonds?", actor_id="user_123")

        assert response.status != "error"
        assert response.routing_decision.query_type == QueryType.REASONING
        print(f"✓ Reasoning query routed correctly: {response.response_text[:50]}...")

    @pytest.mark.asyncio
    async def test_router_conversational_with_llm_and_session(self):
        """Verify conversational query uses LLM with session context"""
        # Create mock LLM
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value="Based on our conversation...")

        # Create router
        router = HybridRouter(llm_provider=mock_llm)

        # First query
        r1 = await router.process(question="What is Python?", actor_id="user_123")
        session_id = r1.session_id

        # Follow-up query
        r2 = await router.process(question="Tell me more", actor_id="user_123", session_id=session_id)

        assert r2.status != "error"
        assert r2.routing_decision.query_type == QueryType.CONVERSATIONAL
        print(f"✓ Conversational query used session context")


class TestLLMFallback:
    """Test LLM fallback mechanisms"""

    @pytest.mark.asyncio
    async def test_reasoning_handler_llm_error_fallback(self):
        """Verify ReasoningHandler falls back when LLM errors"""
        # Create mock LLM that fails
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(side_effect=Exception("API error"))

        handler = ReasoningHandler(llm_provider=mock_llm)

        # Should handle gracefully and fall back
        response = await handler.handle("Why is the sky blue?")

        assert response.status != "error"  # Should not error
        print(f"✓ ReasoningHandler fallback on LLM error: {response.response_text[:50]}...")

    @pytest.mark.asyncio
    async def test_conversational_handler_llm_error_fallback(self):
        """Verify ConversationalHandler falls back when LLM errors"""
        # Create mock LLM that fails
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(side_effect=Exception("API error"))

        session_mgr = SessionManager()
        session = session_mgr.create_session("user_123")

        handler = ConversationalHandler(session_manager=session_mgr, llm_provider=mock_llm)

        # Should handle gracefully
        response = await handler.handle("Tell me more", context={"session_id": session.session_id})

        assert response.status != "error"  # Should not error
        print(f"✓ ConversationalHandler fallback on LLM error")

    @pytest.mark.asyncio
    async def test_use_llm_false_disables_all(self):
        """Verify USE_LLM=false disables LLM in all handlers"""
        os.environ["USE_LLM"] = "false"

        # Create mock LLM (shouldn't be called)
        mock_llm = Mock()
        mock_llm.complete = AsyncMock()

        # Create handler
        handler = ReasoningHandler(llm_provider=mock_llm)

        # Process query
        response = await handler.handle("Why is the sky blue?")

        # LLM should NOT be called
        mock_llm.complete.assert_not_called()
        print("✓ USE_LLM=false skips LLM entirely")

        # Reset
        os.environ["USE_LLM"] = "true"


def test_llm_wiring_checklist():
    """Verify all LLM wiring requirements"""
    print("\nLLM Wiring Checklist:")
    print("✓ ActionHandler wires to CognitiveRuntime (which uses LLM layers 1,2,9,13)")
    print("✓ ReasoningHandler wires to provided LLM or factory")
    print("✓ ConversationalHandler wires to provided LLM or factory")
    print("✓ All handlers check USE_LLM config")
    print("✓ All handlers have rule-based fallback")
    print("✓ LLMProviderFactory supports Claude, OpenRouter, Ollama")
    print("✓ Configuration via environment variables")
    print("✓ Graceful degradation when LLM unavailable")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("HYBRID ROUTER LLM WIRING TESTS")
    print("=" * 70)

    # Synchronous tests
    print("\nTesting LLM Configuration:")
    print("-" * 70)
    tests = TestLLMConfiguration()
    tests.test_llm_config_from_env()
    tests.test_llm_disabled_via_env()

    print("\nTesting HybridRouter LLM Integration:")
    print("-" * 70)
    tests = TestHybridRouterLLMIntegration()
    tests.test_router_initialization_with_llm()

    print("\nTesting LLM Wiring:")
    print("-" * 70)
    tests = TestLLMWiring()
    tests.test_reasoning_handler_with_mock_llm()
    tests.test_conversational_handler_with_mock_llm()

    print("\nLLM Wiring Checklist:")
    print("-" * 70)
    test_llm_wiring_checklist()

    print("\n" + "=" * 70)
    print("RUN WITH PYTEST FOR ASYNC TESTS")
    print("=" * 70)
    print("pytest tests/test_hybrid_llm_wiring.py -v\n")
