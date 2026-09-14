"""Hybrid Router - Route queries to appropriate handlers based on type

Central orchestrator that:
1. Classifies incoming queries
2. Routes to appropriate handler
3. Aggregates responses
4. Manages sessions
"""

import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from uuid import uuid4
import time

from src.hybrid.query_classifier import QueryTypeClassifier, QueryType
from src.hybrid.session_manager import SessionManager
from src.hybrid.handlers import (
    BaseHandler,
    ActionHandler,
    RetrievalHandler,
    ReasoningHandler,
    ConversationalHandler,
    RealtimeHandler,
    HandlerResponse,
)
from src.introspection.lemon import get_lemon

logger = logging.getLogger("agentos.hybrid.router")


@dataclass
class RoutingDecision:
    """Decision made by router"""

    query_type: QueryType
    handler_name: str
    confidence: float
    classification_time_ms: float
    matched_patterns: List[str]
    run_id: str = ""


@dataclass
class RouterResponse:
    """Final response from hybrid router"""

    status: str  # "success" | "error"
    response_text: str
    handler_type: str
    routing_decision: RoutingDecision
    handler_response: Optional[HandlerResponse]
    total_latency_ms: float
    run_id: str = ""
    session_id: Optional[str] = None


# this is the router it is intiated on boot and routes the question based on the question type
# not all questions require an action the 15 phase action pipeline is triggered only for questions
# that may require action to be taken a question can be a
# 1. action handler
# 2. information retrieval
# 3. reasoning
# 4. conversational
# 5. realtime


# TODO: add complete transaction too call it think mode
# to do something properly we need to
# 1. have a conversation
# 2. retreive information
# 3. get realtime events
# 4. reason over the available information
# 5. take action
class HybridRouter:
    """Route queries to appropriate handlers"""

    def __init__(
        self,
        pipeline_orchestrator: Optional[Any] = None,
        knowledge_base: Optional[Any] = None,
        llm_provider: Optional[Any] = None,
        session_timeout_minutes: int = 30,
    ):
        """
        Initialize hybrid router

        Args:
            pipeline_orchestrator: CognitiveRuntime for actions
            knowledge_base: Knowledge base for retrieval
            llm_provider: LLM provider for reasoning
            session_timeout_minutes: Session expiration time
        """
        self.classifier = QueryTypeClassifier(llm_provider=llm_provider, use_llm=True)
        self.session_mgr = SessionManager(session_timeout_minutes)

        # Initialize handlers
        self.handlers: Dict[QueryType, BaseHandler] = {
            QueryType.ACTION: ActionHandler(pipeline_orchestrator),
            QueryType.RETRIEVAL: RetrievalHandler(knowledge_base),
            QueryType.REASONING: ReasoningHandler(llm_provider),
            QueryType.CONVERSATIONAL: ConversationalHandler(self.session_mgr, llm_provider),
            QueryType.REALTIME: RealtimeHandler(),
        }

        self.llm = llm_provider
        self.stats = {
            "total_queries": 0,
            "by_type": {},
            "by_status": {},
            "handler_latencies": {},
        }

        logger.info("[router] Hybrid router initialized")

    def register_realtime_source(self, name: str, fetcher_func):
        """Register a real-time data source"""
        handler = self.handlers[QueryType.REALTIME]
        if isinstance(handler, RealtimeHandler):
            handler.register_source(name, fetcher_func)
            logger.info(f"[router] Registered real-time source: {name}")

    async def process(
        self, question: str, actor_id: str = "unknown", session_id: Optional[str] = None
    ) -> RouterResponse:
        """
        Process a query through hybrid router

        Args:
            question: User's question/request
            actor_id: User/actor ID
            session_id: Optional session ID for conversational queries

        Returns:
            RouterResponse with handler response
        """

        run_id = str(uuid4())

        start_time = time.time()
        self.stats["total_queries"] += 1

        try:
            logger.info(f"[router] Processing: {question[:60]} (actor: {actor_id}, run: {run_id})")

            # Clean up expired sessions
            self.session_mgr.cleanup_expired_sessions()

            # Create session if not exists (for multi-turn tracking)
            if not session_id:
                session = self.session_mgr.create_session(actor_id)
                session_id = session.session_id

            # Classify query type (with LLM if available)
            classification_start = time.time()

            # classify the question using LLM or pattern matching
            classification = await self.classifier.classify(question, session_id)
            classification_time = (time.time() - classification_start) * 1000

            # Track statistics
            query_type = classification.query_type
            self.stats["by_type"][query_type.value] = self.stats["by_type"].get(query_type.value, 0) + 1

            # Observability for lemon
            lemon = get_lemon()
            if lemon:
                lemon.counter("hybrid_router.query")
                lemon.counter(f"hybrid_router.query.{query_type.value}")
                lemon.histogram("hybrid_router.classification_time_ms", classification_time)

            logger.info(
                f"[router] Classified as {query_type.value} "
                f"(confidence: {classification.confidence:.2f}, "
                f"patterns: {', '.join(classification.matched_patterns)})"
            )

            # Get appropriate handler
            handler = self.handlers.get(query_type)
            if not handler:
                logger.error(f"[router] No handler for query type: {query_type}")
                return RouterResponse(
                    status="error",
                    response_text=f"No handler available for {query_type.value} queries",
                    handler_type="none",
                    routing_decision=RoutingDecision(
                        query_type=query_type,
                        handler_name="none",
                        confidence=0.0,
                        classification_time_ms=classification_time,
                        matched_patterns=classification.matched_patterns,
                        run_id=run_id,
                    ),
                    handler_response=None,
                    total_latency_ms=(time.time() - start_time) * 1000,
                    run_id=run_id,
                    session_id=session_id,
                )

            # Prepare context
            context = {
                "actor_id": actor_id,
                "session_id": session_id,
                "run_id": run_id,
                "query_type": query_type.value,
                "classification": classification,
            }

            # Execute handler
            logger.debug(f"[router] Routing to {handler.name}")

            # trigger the pipeline for the actor run
            handler_response = await handler.handle(question, context)

            # Track handler latency
            handler_type = handler_response.handler_type
            if handler_type not in self.stats["handler_latencies"]:
                self.stats["handler_latencies"][handler_type] = []
            self.stats["handler_latencies"][handler_type].append(handler_response.latency_ms)

            lemon = get_lemon()
            if lemon:
                lemon.histogram(
                    f"hybrid_router.handler.{handler_type}.latency_ms",
                    handler_response.latency_ms,
                )

            # Track status
            status = handler_response.status
            self.stats["by_status"][status] = self.stats["by_status"].get(status, 0) + 1

            total_latency = (time.time() - start_time) * 1000

            lemon = get_lemon()
            if lemon:
                lemon.counter(f"hybrid_router.status.{status}")
                lemon.histogram("hybrid_router.total_latency_ms", total_latency)

            logger.info(
                f"[router] Completed with {status} "
                f"({handler_response.latency_ms:.0f}ms handler + "
                f"{total_latency - handler_response.latency_ms:.0f}ms routing)"
            )

            # Prepare routing decision
            routing_decision = RoutingDecision(
                query_type=query_type,
                handler_name=handler.name,
                confidence=classification.confidence,
                classification_time_ms=classification_time,
                matched_patterns=classification.matched_patterns,
                run_id=run_id,
            )

            # Extract session_id if set by handler
            final_session_id = handler_response.metadata.get("session_id") if handler_response.metadata else session_id

            return RouterResponse(
                status=handler_response.status,
                response_text=handler_response.response_text,
                handler_type=handler_response.handler_type,
                routing_decision=routing_decision,
                handler_response=handler_response,
                total_latency_ms=total_latency,
                run_id=run_id,
                session_id=final_session_id,
            )

        except Exception as e:
            logger.error(f"[router] Unexpected error: {e}", exc_info=True)
            self.stats["by_status"]["error"] = self.stats["by_status"].get("error", 0) + 1

            lemon = get_lemon()
            if lemon:
                lemon.counter("hybrid_router.error")
                lemon.counter("hybrid_router.status.error")

            return RouterResponse(
                status="error",
                response_text=f"Router error: {str(e)}",
                handler_type="none",
                routing_decision=RoutingDecision(
                    query_type=QueryType.ACTION,
                    handler_name="none",
                    confidence=0.0,
                    classification_time_ms=0.0,
                    matched_patterns=[],
                    run_id=run_id,
                ),
                handler_response=None,
                total_latency_ms=(time.time() - start_time) * 1000,
                run_id=run_id,
                session_id=session_id,
            )

    def get_session(self, session_id: str):
        """Get session context"""
        return self.session_mgr.get_session(session_id)

    def get_session_stats(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a session"""
        return self.session_mgr.get_session_stats(session_id)

    def get_router_stats(self) -> Dict[str, Any]:
        """Get router statistics"""
        stats = self.stats.copy()

        # Calculate average latencies
        avg_latencies = {}
        for handler_type, latencies in stats["handler_latencies"].items():
            if latencies:
                avg_latencies[handler_type] = {
                    "avg_ms": sum(latencies) / len(latencies),
                    "min_ms": min(latencies),
                    "max_ms": max(latencies),
                    "count": len(latencies),
                }

        stats["average_latencies"] = avg_latencies
        return stats

    def print_stats(self):
        """Print router statistics"""
        stats = self.get_router_stats()

        print("\n" + "=" * 70)
        print("HYBRID ROUTER STATISTICS")
        print("=" * 70)

        print(f"\nTotal queries: {stats['total_queries']}")

        print("\nBy query type:")
        for qtype, count in stats["by_type"].items():
            pct = (count / stats["total_queries"] * 100) if stats["total_queries"] > 0 else 0
            print(f"  {qtype:<15} {count:>5} ({pct:>5.1f}%)")

        print("\nBy status:")
        for status, count in stats["by_status"].items():
            pct = (count / stats["total_queries"] * 100) if stats["total_queries"] > 0 else 0
            print(f"  {status:<15} {count:>5} ({pct:>5.1f}%)")

        print("\nAverage latencies:")
        for handler_type, latencies in stats["average_latencies"].items():
            print(
                f"  {handler_type:<25} "
                f"avg: {latencies['avg_ms']:>6.1f}ms "
                f"min: {latencies['min_ms']:>6.1f}ms "
                f"max: {latencies['max_ms']:>6.1f}ms"
            )

        print("\n" + "=" * 70 + "\n")
