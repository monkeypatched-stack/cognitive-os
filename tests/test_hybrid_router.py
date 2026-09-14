"""Tests for Hybrid Router - Verify query classification and routing

Tests different query types and verifies they are routed to correct handlers.
"""

import asyncio
import pytest
from src.hybrid import HybridRouter, QueryTypeClassifier, QueryType, SessionManager


class TestQueryTypeClassifier:
    """Test query classification"""

    def test_action_query_classification(self):
        """Verify action queries are classified correctly"""
        classifier = QueryTypeClassifier()

        action_queries = [
            "Get me 2 liters of milk",
            "Book a flight to Tokyo",
            "Find a nearby coffee shop",
            "Order pizza with extra cheese",
            "Hire a contractor to fix my roof",
        ]

        for query in action_queries:
            result = classifier.classify(query)
            assert result.query_type == QueryType.ACTION, f"Failed for: {query}"
            assert result.confidence > 0.7, f"Low confidence for: {query}"
            print(f"✓ {query}: {result.query_type.value} (confidence: {result.confidence:.2f})")

    def test_retrieval_query_classification(self):
        """Verify retrieval queries are classified correctly"""
        classifier = QueryTypeClassifier()

        retrieval_queries = [
            "What is the capital of France?",
            "Who won the 2020 World Series?",
            "What's the population of Tokyo?",
            "List all restaurants nearby",
            "Show me flights to Paris",
        ]

        for query in retrieval_queries:
            result = classifier.classify(query)
            assert result.query_type == QueryType.RETRIEVAL, f"Failed for: {query}"
            assert result.confidence > 0.7, f"Low confidence for: {query}"
            print(f"✓ {query}: {result.query_type.value} (confidence: {result.confidence:.2f})")

    def test_reasoning_query_classification(self):
        """Verify reasoning queries are classified correctly"""
        classifier = QueryTypeClassifier()

        reasoning_queries = [
            "Why do people form social bonds?",
            "Explain quantum entanglement",
            "How would society change if gravity reversed?",
            "Compare socialism vs capitalism",
            "What if we could time travel?",
        ]

        for query in reasoning_queries:
            result = classifier.classify(query)
            assert result.query_type == QueryType.REASONING, f"Failed for: {query}"
            assert result.confidence > 0.7, f"Low confidence for: {query}"
            print(f"✓ {query}: {result.query_type.value} (confidence: {result.confidence:.2f})")

    def test_realtime_query_classification(self):
        """Verify real-time queries are classified correctly"""
        classifier = QueryTypeClassifier()

        realtime_queries = [
            "What is the current stock price of Apple?",
            "What's the weather right now?",
            "Show me live traffic conditions",
            "What is the current Bitcoin price?",
            "Check real-time flight availability",
        ]

        for query in realtime_queries:
            result = classifier.classify(query)
            assert result.query_type == QueryType.REALTIME, f"Failed for: {query}"
            assert result.confidence > 0.7, f"Low confidence for: {query}"
            print(f"✓ {query}: {result.query_type.value} (confidence: {result.confidence:.2f})")

    def test_conversational_classification_with_session(self):
        """Verify conversational queries with session context"""
        classifier = QueryTypeClassifier()

        # Conversational queries require session context
        conversational_queries = [
            ("What is the capital of France?", None, QueryType.RETRIEVAL),  # No session
            (
                "That sounds interesting",
                "session_123",
                QueryType.CONVERSATIONAL,
            ),  # With session
            ("Book it", "session_123", QueryType.CONVERSATIONAL),  # With session
        ]

        for query, session_id, expected_type in conversational_queries:
            result = classifier.classify(query, session_id)
            assert result.query_type == expected_type, f"Failed for: {query}"
            print(f"✓ {query} (session: {session_id}): {result.query_type.value}")


class TestSessionManager:
    """Test session management"""

    def test_session_creation(self):
        """Verify session creation"""
        mgr = SessionManager()

        session = mgr.create_session("user_123")
        assert session is not None
        assert session.actor_id == "user_123"
        assert len(session.messages) == 0

        print(f"✓ Created session: {session.session_id}")

    def test_session_message_tracking(self):
        """Verify message tracking in sessions"""
        mgr = SessionManager()

        session = mgr.create_session("user_123")
        session_id = session.session_id

        # Add messages
        mgr.add_message(session_id, "user", "What is the capital of France?")
        mgr.add_message(session_id, "assistant", "The capital of France is Paris")
        mgr.add_message(session_id, "user", "Thank you")

        # Verify messages
        session = mgr.get_session(session_id)
        assert len(session.messages) == 3
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "assistant"

        print(f"✓ Tracked {len(session.messages)} messages in session")

    def test_session_context_string(self):
        """Verify context string generation"""
        mgr = SessionManager()

        session = mgr.create_session("user_123")
        session_id = session.session_id

        mgr.add_message(session_id, "user", "Hi there")
        mgr.add_message(session_id, "assistant", "Hello! How can I help?")
        mgr.add_message(session_id, "user", "Tell me about Python")

        session = mgr.get_session(session_id)
        context = session.get_context_string()

        assert "user" in context.lower()
        assert "python" in context.lower()
        assert "session" in context.lower()

        print(f"✓ Generated context string ({len(context)} chars)")

    def test_session_variables(self):
        """Verify session variable storage"""
        mgr = SessionManager()

        session = mgr.create_session("user_123")
        session_id = session.session_id

        # Store variables
        mgr.get_session(session_id).add_variable("preferred_store", "grocery_store_downtown")
        mgr.get_session(session_id).add_variable("budget", 50.00)

        # Retrieve variables
        session = mgr.get_session(session_id)
        assert session.get_variable("preferred_store") == "grocery_store_downtown"
        assert session.get_variable("budget") == 50.00

        print(f"✓ Stored and retrieved {len(session.variables)} variables")

    def test_session_expiration(self):
        """Verify session expiration"""
        mgr = SessionManager(session_timeout_minutes=1)

        session = mgr.create_session("user_123")
        session_id = session.session_id

        # Session should exist
        assert mgr.get_session(session_id) is not None

        # Manually expire by manipulating time
        from datetime import datetime, timedelta

        session.last_updated = datetime.now() - timedelta(minutes=2)

        # Session should be expired and removed
        assert mgr.get_session(session_id) is None

        print(f"✓ Session expired correctly")


@pytest.mark.asyncio
class TestHybridRouter:
    """Test hybrid router"""

    async def test_router_initialization(self):
        """Verify router initialization"""
        router = HybridRouter()

        assert router.classifier is not None
        assert router.session_mgr is not None
        assert len(router.handlers) == 5

        print("✓ Router initialized with 5 handlers")

    async def test_action_routing(self):
        """Verify action queries are routed to ActionHandler"""
        router = HybridRouter()

        response = await router.process(question="Get me 2 liters of milk", actor_id="user_123")

        assert response.status != "error"
        assert response.handler_type == "ActionHandler"
        assert response.routing_decision.query_type == QueryType.ACTION

        print(f"✓ Action query routed to ActionHandler ({response.total_latency_ms:.0f}ms)")

    async def test_retrieval_routing(self):
        """Verify retrieval queries are routed to RetrievalHandler"""
        router = HybridRouter()

        response = await router.process(question="What is the capital of France?", actor_id="user_123")

        assert response.status != "error"
        assert response.handler_type == "RetrievalHandler"
        assert response.routing_decision.query_type == QueryType.RETRIEVAL

        print(f"✓ Retrieval query routed to RetrievalHandler ({response.total_latency_ms:.0f}ms)")

    async def test_reasoning_routing(self):
        """Verify reasoning queries are routed to ReasoningHandler"""
        router = HybridRouter()

        response = await router.process(question="Why do people form social bonds?", actor_id="user_123")

        assert response.status != "error"
        assert response.handler_type == "ReasoningHandler"
        assert response.routing_decision.query_type == QueryType.REASONING

        print(f"✓ Reasoning query routed to ReasoningHandler ({response.total_latency_ms:.0f}ms)")

    async def test_realtime_routing(self):
        """Verify real-time queries are routed to RealtimeHandler"""
        router = HybridRouter()

        response = await router.process(question="What is the current stock price of Apple?", actor_id="user_123")

        assert response.status != "error"
        assert response.handler_type == "RealtimeHandler"
        assert response.routing_decision.query_type == QueryType.REALTIME

        print(f"✓ Real-time query routed to RealtimeHandler ({response.total_latency_ms:.0f}ms)")

    async def test_conversational_routing(self):
        """Verify conversational queries are routed properly"""
        router = HybridRouter()

        # First query (creates session)
        response1 = await router.process(question="What is the capital of France?", actor_id="user_123")

        session_id = response1.session_id

        # Follow-up query (should be conversational with session)
        response2 = await router.process(question="Thank you", actor_id="user_123", session_id=session_id)

        assert response2.session_id == session_id

        print(f"✓ Conversational flow with session: {session_id}")

    async def test_router_statistics(self):
        """Verify router statistics tracking"""
        router = HybridRouter()

        # Process multiple queries
        queries = [
            "Get me milk",
            "What is the capital?",
            "Why is the sky blue?",
            "Show me live weather",
        ]

        for query in queries:
            await router.process(query, actor_id="user_123")

        stats = router.get_router_stats()

        assert stats["total_queries"] == 4
        assert len(stats["by_type"]) > 0
        assert len(stats["by_status"]) > 0

        print(f"✓ Router statistics: {stats['total_queries']} queries processed")

    async def test_multi_turn_conversation(self):
        """Verify multi-turn conversation flow"""
        router = HybridRouter()

        # Simulate a multi-turn conversation
        turns = [
            ("Get me 2 liters of milk", QueryType.ACTION),
            ("I need it delivered", QueryType.CONVERSATIONAL),
            ("What's the closest store?", QueryType.RETRIEVAL),
            ("Can you book a delivery?", QueryType.CONVERSATIONAL),
        ]

        session_id = None

        for query, expected_type in turns:
            response = await router.process(question=query, actor_id="user_123", session_id=session_id)

            session_id = response.session_id
            # First query creates session, rest should use it
            if session_id:
                # Verify session was used
                session = router.get_session(session_id)
                assert session is not None

        print(f"✓ Multi-turn conversation completed with session: {session_id}")


def test_query_classification_patterns():
    """Test pattern matching in classification"""
    classifier = QueryTypeClassifier()

    test_cases = [
        ("I want to buy milk", QueryType.ACTION),
        ("Can you help me find a doctor?", QueryType.ACTION),
        ("Tell me about quantum physics", QueryType.RETRIEVAL),
        ("Explain why gravity exists", QueryType.REASONING),
        ("What's trending on Twitter right now?", QueryType.REALTIME),
    ]

    for query, expected_type in test_cases:
        result = classifier.classify(query)
        print(
            f"  {query}: {result.query_type.value} "
            f"(expected: {expected_type.value}, "
            f"confidence: {result.confidence:.2f}, "
            f"patterns: {result.matched_patterns})"
        )


if __name__ == "__main__":
    # Run simple synchronous tests
    print("\n" + "=" * 70)
    print("HYBRID ROUTER TESTS")
    print("=" * 70)

    print("\nTesting Query Classification:")
    print("-" * 70)
    test_query_classification_patterns()

    print("\nTesting Query Type Classifier:")
    print("-" * 70)
    classifier_tests = TestQueryTypeClassifier()
    classifier_tests.test_action_query_classification()
    classifier_tests.test_retrieval_query_classification()
    classifier_tests.test_reasoning_query_classification()
    classifier_tests.test_realtime_query_classification()

    print("\nTesting Session Manager:")
    print("-" * 70)
    session_tests = TestSessionManager()
    session_tests.test_session_creation()
    session_tests.test_session_message_tracking()
    session_tests.test_session_context_string()
    session_tests.test_session_variables()
    session_tests.test_session_expiration()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70 + "\n")
