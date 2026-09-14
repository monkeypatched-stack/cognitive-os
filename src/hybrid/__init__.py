"""Hybrid Router Module - Route queries to specialized handlers

This module implements a hybrid routing system that classifies incoming queries
and routes them to the most appropriate handler:

- ActionHandler: CognitiveRuntime-driven goal execution
- RetrievalHandler: Knowledge base lookup for pure information queries
- ReasoningHandler: LLM-based reasoning for open-ended analysis
- ConversationalHandler: Session-based dialogue for multi-turn conversations
- RealtimeHandler: API fetchers for live data (stocks, weather, etc.)

Example usage:

    from src.hybrid.hybrid_router import HybridRouter

    # Initialize router
    router = HybridRouter(
        pipeline_orchestrator=pipeline,
        knowledge_base=kb,
        llm_provider=llm
    )

    # Process queries (automatically routes)
    response = await router.process("Get me 2 liters of milk", actor_id="user_123")
    response = await router.process("What is the capital of France?", actor_id="user_123")
    response = await router.process("Why do people form social bonds?", actor_id="user_123")

    # View statistics
    router.print_stats()
"""

from src.hybrid.query_classifier import (
    QueryTypeClassifier,
    QueryType,
    QueryClassification,
)
from src.hybrid.session_manager import SessionManager, ConversationContext, Message
from src.hybrid.handlers import (
    BaseHandler,
    ActionHandler,
    RetrievalHandler,
    ReasoningHandler,
    ConversationalHandler,
    RealtimeHandler,
    HandlerResponse,
)
from src.hybrid.hybrid_router import HybridRouter, RoutingDecision, RouterResponse

__all__ = [
    "HybridRouter",
    "QueryTypeClassifier",
    "QueryType",
    "QueryClassification",
    "SessionManager",
    "ConversationContext",
    "Message",
    "RoutingDecision",
    "RouterResponse",
    "BaseHandler",
    "ActionHandler",
    "RetrievalHandler",
    "ReasoningHandler",
    "ConversationalHandler",
    "RealtimeHandler",
    "HandlerResponse",
]
