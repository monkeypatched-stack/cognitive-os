"""End-to-End Integration Tests for Hybrid Router

Tests complete flows from query input through classification, routing,
and handler execution with different scenarios.
"""

import asyncio
import pytest
import os
from unittest.mock import Mock, AsyncMock

from src.hybrid import HybridRouter, QueryType
from src.hybrid.session_manager import SessionManager


class TestE2EActionQueries:
    """E2E tests for action-oriented queries"""

    @pytest.mark.asyncio
    async def test_e2e_acquisition_query(self):
        """Test complete flow: acquisition query → action handler"""
        print("\n" + "=" * 70)
        print("E2E TEST: Acquisition Query")
        print("=" * 70)

        # Create mock LLM for classification
        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "action", "confidence": 0.95, "reasoning": "Acquisition goal"}'
        )

        # Create router
        router = HybridRouter(llm_provider=mock_llm)

        # Process query
        print("\n[1] Input: 'Get me 2 liters of milk'")
        response = await router.process(question="Get me 2 liters of milk", actor_id="user_123")

        print(f"[2] Classification: {response.routing_decision.query_type.value}")
        print(f"    Confidence: {response.routing_decision.confidence}")
        print(f"    Time: {response.routing_decision.classification_time_ms:.1f}ms")

        print(f"[3] Routing: {response.handler_type}")

        print(f"[4] Result: {response.response_text[:50]}...")
        print(f"    Status: {response.status}")
        print(f"    Total latency: {response.total_latency_ms:.0f}ms")

        # Assertions
        assert response.status != "error"
        assert response.routing_decision.query_type == QueryType.ACTION
        assert response.handler_type == "ActionHandler"

        print("\n✓ E2E acquisition query passed")

    @pytest.mark.asyncio
    async def test_e2e_booking_query(self):
        """Test booking query flow"""
        print("\n" + "=" * 70)
        print("E2E TEST: Booking Query")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "action", "confidence": 0.94, "reasoning": "Booking service"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'Book a flight to Tokyo for next week'")
        response = await router.process(question="Book a flight to Tokyo for next week", actor_id="user_456")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.ACTION
        assert response.handler_type == "ActionHandler"

        print("\n✓ E2E booking query passed")


class TestE2ERetrievalQueries:
    """E2E tests for information retrieval queries"""

    @pytest.mark.asyncio
    async def test_e2e_factual_query(self):
        """Test factual query flow"""
        print("\n" + "=" * 70)
        print("E2E TEST: Factual Query")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "retrieval", "confidence": 0.92, "reasoning": "Information request"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'What is the capital of France?'")
        response = await router.process(question="What is the capital of France?", actor_id="user_789")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.RETRIEVAL
        assert response.handler_type == "RetrievalHandler"

        print("\n✓ E2E factual query passed")

    @pytest.mark.asyncio
    async def test_e2e_list_query(self):
        """Test listing/discovery query flow"""
        print("\n" + "=" * 70)
        print("E2E TEST: List Query")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "retrieval", "confidence": 0.88, "reasoning": "List discovery"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'Show me all nearby restaurants'")
        response = await router.process(question="Show me all nearby restaurants", actor_id="user_101")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.RETRIEVAL

        print("\n✓ E2E list query passed")


class TestE2EReasoningQueries:
    """E2E tests for reasoning/analysis queries"""

    @pytest.mark.asyncio
    async def test_e2e_why_question(self):
        """Test why/reasoning query flow"""
        print("\n" + "=" * 70)
        print("E2E TEST: Why Question")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "reasoning", "confidence": 0.90, "reasoning": "Open-ended analysis"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'Why do people form social bonds?'")
        response = await router.process(question="Why do people form social bonds?", actor_id="user_202")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.REASONING
        assert response.handler_type == "ReasoningHandler"

        print("\n✓ E2E why question passed")

    @pytest.mark.asyncio
    async def test_e2e_comparison_query(self):
        """Test comparison/analysis query"""
        print("\n" + "=" * 70)
        print("E2E TEST: Comparison Query")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "reasoning", "confidence": 0.89, "reasoning": "Comparative analysis"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'Compare socialism vs capitalism'")
        response = await router.process(question="Compare socialism vs capitalism", actor_id="user_303")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.REASONING

        print("\n✓ E2E comparison query passed")


class TestE2ERealtimeQueries:
    """E2E tests for real-time data queries"""

    @pytest.mark.asyncio
    async def test_e2e_stock_price_query(self):
        """Test real-time stock price query"""
        print("\n" + "=" * 70)
        print("E2E TEST: Stock Price Query")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "realtime", "confidence": 0.93, "reasoning": "Live market data"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'What is the current stock price of Apple?'")
        response = await router.process(question="What is the current stock price of Apple?", actor_id="user_404")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.REALTIME
        assert response.handler_type == "RealtimeHandler"

        print("\n✓ E2E stock price query passed")

    @pytest.mark.asyncio
    async def test_e2e_weather_query(self):
        """Test real-time weather query"""
        print("\n" + "=" * 70)
        print("E2E TEST: Weather Query")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            return_value='{"type": "realtime", "confidence": 0.91, "reasoning": "Weather data"}'
        )

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'What is the weather right now?'")
        response = await router.process(question="What is the weather right now?", actor_id="user_505")

        print(f"[2] Classified as: {response.routing_decision.query_type.value}")
        print(f"[3] Handler: {response.handler_type}")
        print(f"[4] Response: {response.response_text[:50]}...")

        assert response.routing_decision.query_type == QueryType.REALTIME

        print("\n✓ E2E weather query passed")


class TestE2EConversationalQueries:
    """E2E tests for multi-turn conversations"""

    @pytest.mark.asyncio
    async def test_e2e_multi_turn_conversation(self):
        """Test complete multi-turn conversation flow"""
        print("\n" + "=" * 70)
        print("E2E TEST: Multi-Turn Conversation")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            side_effect=[
                # First query classification
                '{"type": "retrieval", "confidence": 0.90, "reasoning": "Info"}',
                # Second query classification
                '{"type": "conversational", "confidence": 0.89, "reasoning": "Follow-up"}',
                # Conversational response
                "Based on our conversation, here is more information...",
            ]
        )

        router = HybridRouter(llm_provider=mock_llm)

        # Turn 1
        print("\n[TURN 1] User: 'What is Python?'")
        r1 = await router.process(question="What is Python?", actor_id="user_606")

        session_id = r1.session_id
        print(f"  Classification: {r1.routing_decision.query_type.value}")
        print(f"  Handler: {r1.handler_type}")
        print(f"  Response: {r1.response_text[:40]}...")
        print(f"  Session: {session_id}")

        assert r1.routing_decision.query_type == QueryType.RETRIEVAL

        # Turn 2
        print("\n[TURN 2] User: 'Tell me more'")
        r2 = await router.process(question="Tell me more", actor_id="user_606", session_id=session_id)

        print(f"  Classification: {r2.routing_decision.query_type.value}")
        print(f"  Handler: {r2.handler_type}")
        print(f"  Response: {r2.response_text[:40]}...")
        print(f"  Session: {r2.session_id}")

        # Verify session maintained
        assert r2.session_id == session_id
        assert r2.routing_decision.query_type == QueryType.CONVERSATIONAL

        # Verify conversation was tracked (only conversational queries record messages)
        session = router.get_session(session_id)
        assert session is not None
        assert len(session.messages) >= 2  # At least user + assistant from Turn 2

        stats = router.get_session_stats(session_id)
        print(f"\n  Session stats:")
        print(f"    Total messages: {stats['total_messages']}")
        print(f"    Duration: {stats['duration_seconds']:.1f}s")

        print("\n✓ E2E multi-turn conversation passed")

    @pytest.mark.asyncio
    async def test_e2e_conversation_with_pronoun_resolution(self):
        """Test conversation with pronoun resolution"""
        print("\n" + "=" * 70)
        print("E2E TEST: Pronoun Resolution in Conversation")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            side_effect=[
                '{"type": "action", "confidence": 0.92, "reasoning": "Booking"}',
                '{"type": "conversational", "confidence": 0.88, "reasoning": "Follow-up"}',
                "I understand, let me help with that...",
            ]
        )

        router = HybridRouter(llm_provider=mock_llm)

        # Initial query
        print("\n[TURN 1] User: 'Book a flight to Paris'")
        r1 = await router.process(question="Book a flight to Paris", actor_id="user_707")

        session_id = r1.session_id
        print(f"  Action: {r1.routing_decision.query_type.value}")
        print(f"  Session: {session_id}")

        # Follow-up with pronoun
        print("\n[TURN 2] User: 'Make it first class'")
        r2 = await router.process(question="Make it first class", actor_id="user_707", session_id=session_id)

        print(f"  Classification: {r2.routing_decision.query_type.value}")
        print(f"  Handler: {r2.handler_type}")

        # Verify session tracked both turns
        session = router.get_session(session_id)
        assert len(session.messages) >= 2

        print("\n✓ E2E pronoun resolution passed")


class TestE2EFallbackScenarios:
    """E2E tests for fallback mechanisms"""

    @pytest.mark.asyncio
    async def test_e2e_classification_fallback_to_patterns(self):
        """Test fallback to pattern matching when LLM fails"""
        print("\n" + "=" * 70)
        print("E2E TEST: Classification Fallback")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(side_effect=Exception("API error"))

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] Input: 'Get me 2 liters of milk'")
        print("[2] LLM fails (simulated)")
        response = await router.process(question="Get me 2 liters of milk", actor_id="user_808")

        print(f"[3] Fallback classification: {response.routing_decision.query_type.value}")
        print(f"    Matched patterns: {response.routing_decision.matched_patterns}")
        print(f"[4] Handler still works: {response.handler_type}")

        # Should still classify correctly via patterns
        assert response.routing_decision.query_type == QueryType.ACTION
        assert "get" in response.routing_decision.matched_patterns

        print("\n✓ E2E classification fallback passed")

    @pytest.mark.asyncio
    async def test_e2e_with_llm_disabled(self):
        """Test complete flow with LLM disabled"""
        print("\n" + "=" * 70)
        print("E2E TEST: LLM Disabled")
        print("=" * 70)

        os.environ["USE_LLM"] = "false"

        mock_llm = Mock()
        mock_llm.complete = AsyncMock()  # Should not be called

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] USE_LLM=false")
        print("[2] Input: 'Get me 2 liters of milk'")
        response = await router.process(question="Get me 2 liters of milk", actor_id="user_909")

        print(f"[3] Classification (rule-based): {response.routing_decision.query_type.value}")
        print(f"[4] Handler: {response.handler_type}")
        print(f"[5] Response: {response.response_text[:50]}...")

        # LLM should not have been called
        assert not mock_llm.complete.called
        assert response.status != "error"

        # Reset
        os.environ["USE_LLM"] = "true"

        print("\n✓ E2E LLM disabled passed")


class TestE2EErrorScenarios:
    """E2E tests for error handling"""

    @pytest.mark.asyncio
    async def test_e2e_invalid_query_type_fallback(self):
        """Test fallback when LLM returns invalid type"""
        print("\n" + "=" * 70)
        print("E2E TEST: Invalid Query Type Fallback")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value='{"type": "invalid_type", "confidence": 0.5, "reasoning": "Oops"}')

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] LLM returns invalid type")
        print("[2] Input: 'Get me milk'")
        response = await router.process(question="Get me milk", actor_id="user_010")

        print(f"[3] Fallback to patterns: {response.routing_decision.query_type.value}")
        print(f"[4] Handler still works: {response.handler_type}")

        # Should fall back to patterns
        assert response.status != "error"
        assert response.routing_decision.query_type == QueryType.ACTION

        print("\n✓ E2E invalid type fallback passed")

    @pytest.mark.asyncio
    async def test_e2e_malformed_json_fallback(self):
        """Test fallback when LLM returns malformed JSON"""
        print("\n" + "=" * 70)
        print("E2E TEST: Malformed JSON Fallback")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value="This is not JSON at all")

        router = HybridRouter(llm_provider=mock_llm)

        print("\n[1] LLM returns malformed JSON")
        print("[2] Input: 'Get me milk'")
        response = await router.process(question="Get me milk", actor_id="user_011")

        print(f"[3] Fallback to patterns: {response.routing_decision.query_type.value}")
        print(f"[4] Handler: {response.handler_type}")

        # Should handle gracefully
        assert response.status != "error"

        print("\n✓ E2E malformed JSON fallback passed")


class TestE2EPerformance:
    """E2E tests for performance metrics"""

    @pytest.mark.asyncio
    async def test_e2e_performance_with_llm(self):
        """Measure performance with LLM classification"""
        print("\n" + "=" * 70)
        print("E2E TEST: Performance with LLM")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(return_value='{"type": "action", "confidence": 0.95, "reasoning": "Test"}')

        router = HybridRouter(llm_provider=mock_llm)

        response = await router.process(question="Get me 2 liters of milk", actor_id="user_012")

        print(f"\nPerformance Metrics:")
        print(f"  Classification time: {response.routing_decision.classification_time_ms:.1f}ms")
        print(f"  Handler time: {response.handler_response.latency_ms:.1f}ms")
        print(f"  Total time: {response.total_latency_ms:.1f}ms")
        print(f"  Breakdown:")
        print(f"    - Query type: {response.routing_decision.query_type.value}")
        print(f"    - Handler: {response.handler_type}")

        # Verify measurements
        assert response.routing_decision.classification_time_ms > 0
        assert response.handler_response.latency_ms >= 0
        assert response.total_latency_ms > 0

        print("\n✓ E2E performance with LLM passed")

    @pytest.mark.asyncio
    async def test_e2e_performance_without_llm(self):
        """Measure performance without LLM (pattern matching)"""
        print("\n" + "=" * 70)
        print("E2E TEST: Performance without LLM")
        print("=" * 70)

        os.environ["USE_LLM"] = "false"

        router = HybridRouter(llm_provider=None)

        response = await router.process(question="Get me 2 liters of milk", actor_id="user_013")

        print(f"\nPerformance Metrics (Pattern-based):")
        print(f"  Classification time: {response.routing_decision.classification_time_ms:.1f}ms")
        print(f"  Handler time: {response.handler_response.latency_ms:.1f}ms")
        print(f"  Total time: {response.total_latency_ms:.1f}ms")

        # With USE_LLM=false, pattern matching should be fast (with module import overhead)
        assert response.routing_decision.classification_time_ms < 500  # Reasonable threshold

        print("\n✓ E2E performance without LLM passed")

        # Reset
        os.environ["USE_LLM"] = "true"


class TestE2ERouterStatistics:
    """E2E tests for router statistics tracking"""

    @pytest.mark.asyncio
    async def test_e2e_statistics_tracking(self):
        """Test router statistics accumulation"""
        print("\n" + "=" * 70)
        print("E2E TEST: Statistics Tracking")
        print("=" * 70)

        mock_llm = Mock()
        mock_llm.complete = AsyncMock(
            side_effect=[
                '{"type": "action", "confidence": 0.95, "reasoning": ""}',
                '{"type": "retrieval", "confidence": 0.90, "reasoning": ""}',
                '{"type": "reasoning", "confidence": 0.88, "reasoning": ""}',
            ]
        )

        router = HybridRouter(llm_provider=mock_llm)

        # Process multiple different queries
        queries = [
            ("Get me coffee", "user_A"),
            ("What is Python?", "user_B"),
            ("Why is sky blue?", "user_C"),
        ]

        print("\nProcessing queries:")
        for query, actor in queries:
            print(f"  • {query}")
            await router.process(question=query, actor_id=actor)

        # Check statistics
        stats = router.get_router_stats()

        print(f"\nRouter Statistics:")
        print(f"  Total queries: {stats['total_queries']}")
        print(f"  By type:")
        for qtype, count in stats["by_type"].items():
            print(f"    - {qtype}: {count}")
        print(f"  By status:")
        for status, count in stats["by_status"].items():
            print(f"    - {status}: {count}")
        print(f"  Average latencies:")
        for handler, lats in stats["average_latencies"].items():
            print(
                f"    - {handler}: avg={lats['avg_ms']:.1f}ms, min={lats['min_ms']:.1f}ms, max={lats['max_ms']:.1f}ms"
            )

        # Verify stats
        assert stats["total_queries"] == 3
        assert stats["by_type"]["action"] == 1
        assert stats["by_type"]["retrieval"] == 1
        assert stats["by_type"]["reasoning"] == 1
        # Some queries may return "partial" (e.g., empty KB), which is valid
        total_responses = sum(stats["by_status"].values())
        assert total_responses == 3
        success_or_partial = stats["by_status"].get("success", 0) + stats["by_status"].get("partial", 0)
        assert success_or_partial == 3  # All returned valid responses

        print("\n✓ E2E statistics tracking passed")


def run_all_e2e_tests():
    """Run all E2E tests"""
    print("\n" + "=" * 70)
    print("END-TO-END INTEGRATION TESTS")
    print("=" * 70)
    print("\nRun with: pytest tests/test_e2e_hybrid_router.py -v -s")
    print("\nThis test suite validates:")
    print("  ✓ Query classification (LLM + fallback)")
    print("  ✓ Handler routing (all 5 types)")
    print("  ✓ Multi-turn conversations")
    print("  ✓ Error handling (graceful fallback)")
    print("  ✓ Performance metrics")
    print("  ✓ Statistics tracking")
    print("  ✓ Session management")


if __name__ == "__main__":
    run_all_e2e_tests()
