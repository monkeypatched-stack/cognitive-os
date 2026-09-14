"""Query Handlers - Different strategies for different query types

Each handler implements a specific strategy for processing queries:
- ActionHandler: CognitiveRuntime-driven goal execution
- RetrievalHandler: Knowledge base lookup
- ReasoningHandler: LLM-based reasoning
- ConversationalHandler: Session-based dialogue
- RealtimeHandler: Live data fetching

LLM WIRING:
-----------
All handlers that use LLM implement this pattern:
1. Try provided LLM provider (passed in __init__)
2. Fall back to LLMProviderFactory.get_provider() (uses env vars)
3. Check USE_LLM config (disable LLM entirely if false)
4. Fall back to rule-based when LLM unavailable

This ensures:
✓ Graceful degradation (works without LLM)
✓ Provider flexibility (swappable via env vars)
✓ Error resilience (automatic fallback on failure)
✓ Cost control (can disable LLM)

Handlers using LLM:
- ActionHandler: Via CognitiveRuntime
- ReasoningHandler: Direct LLM for open-ended reasoning
- ConversationalHandler: LLM with session context

Handlers NOT using LLM:
- RetrievalHandler: Knowledge base search only
- RealtimeHandler: API fetching only
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Any, Dict, List
from dataclasses import dataclass
import asyncio
from datetime import datetime

logger = logging.getLogger("agentos.hybrid.handlers")


@dataclass
class HandlerResponse:
    """Response from any handler"""

    status: str  # "success" | "error" | "partial"
    handler_type: str
    response_text: str
    data: Optional[Any] = None
    confidence: float = 1.0
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseHandler(ABC):
    """Base class for all handlers"""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agentos.hybrid.handlers.{name}")

    @abstractmethod
    async def handle(self, question: str, context: Optional[Dict[str, Any]] = None) -> HandlerResponse:
        """
        Handle a query

        Args:
            question: User's question/request
            context: Optional context dictionary

        Returns:
            HandlerResponse
        """
        pass


class ActionHandler(BaseHandler):
    """Handle action-oriented queries through the Cognitive Runtime pipeline."""

    def __init__(self, pipeline_orchestrator: Optional[Any] = None):
        super().__init__("ActionHandler")
        self.orchestrator = pipeline_orchestrator

    async def handle(self, question: str, context: Optional[Dict[str, Any]] = None) -> HandlerResponse:
        """Process action query through PipelineOrchestrator → CognitiveRuntime."""
        import time

        start_time = time.time()

        try:
            if not self.orchestrator:
                self.logger.warning("[action] Orchestrator not configured, using mock execution")
                return await self._mock_execution(question, context)

            # Build PipelineRequest from transport data
            from src.monkey_brain.kernel.pipeline.contracts import PipelineRequest

            request = PipelineRequest(
                question=question,
                actor_id=(context or {}).get("actor_id", "unknown"),
                session_id=(context or {}).get("session_id", ""),
                metadata={"query_type": (context or {}).get("query_type", "action")},
            )

            # Execute through the new pipeline
            self.logger.info(f"[action] Processing: {question[:60]}")
            pipeline_response = await self.orchestrator.run(request)

            latency = (time.time() - start_time) * 1000
            self.logger.info(f"[action] Completed in {latency:.0f}ms")

            return HandlerResponse(
                status=pipeline_response.status,
                handler_type="ActionHandler",
                response_text=pipeline_response.answer,
                data={
                    "pipeline_response": pipeline_response,
                    "trace": [t.step for t in pipeline_response.trace],
                    "errors": [e.code for e in pipeline_response.errors],
                },
                latency_ms=latency,
            )

        except Exception as e:
            self.logger.error(f"[action] Failed: {e}", exc_info=True)
            latency = (time.time() - start_time) * 1000
            return HandlerResponse(
                status="error",
                handler_type="ActionHandler",
                response_text=f"Action failed: {str(e)}",
                latency_ms=latency,
            )

    async def _mock_execution(self, question: str, context: Optional[Dict[str, Any]]) -> HandlerResponse:
        """Mock execution when pipeline not configured"""
        import time

        start_time = time.time()
        await asyncio.sleep(0.1)  # Simulate some work
        latency = (time.time() - start_time) * 1000

        return HandlerResponse(
            status="success",
            handler_type="ActionHandler",
            response_text=f"[MOCK] Action executed for: {question}",
            data={"message": "Mock execution", "goal_achieved": True},
            latency_ms=latency,
        )


class RetrievalHandler(BaseHandler):
    """Handle pure information retrieval using SittingFace knowledge."""

    def __init__(self, knowledge_base: Optional[Any] = None):
        super().__init__("RetrievalHandler")
        self.kb = knowledge_base
        from src.monkey_brain.kernel.knowledge.sittingface_retrieval import (
            SittingFaceKnowledgeRetriever,
        )

        self._retriever = SittingFaceKnowledgeRetriever()

    async def handle(self, question: str, context: Optional[Dict[str, Any]] = None) -> HandlerResponse:
        """Retrieve information from knowledge base"""
        import time

        start_time = time.time()

        try:
            self.logger.info(f"[retrieval] Searching: {question[:60]}")

            # Extract search terms
            search_terms = self._extract_search_terms(question)

            # Search knowledge base
            results = await self._search_knowledge_base(search_terms)

            if not results:
                latency = (time.time() - start_time) * 1000
                return HandlerResponse(
                    status="partial",
                    handler_type="RetrievalHandler",
                    response_text="No results found in knowledge base",
                    latency_ms=latency,
                )

            # Format response
            response_text = self._format_results(question, results)

            latency = (time.time() - start_time) * 1000
            self.logger.info(f"[retrieval] Found {len(results)} results in {latency:.0f}ms")

            return HandlerResponse(
                status="success",
                handler_type="RetrievalHandler",
                response_text=response_text,
                data=results,
                confidence=0.9,
                latency_ms=latency,
            )

        except Exception as e:
            self.logger.error(f"[retrieval] Failed: {e}", exc_info=True)
            latency = (time.time() - start_time) * 1000
            return HandlerResponse(
                status="error",
                handler_type="RetrievalHandler",
                response_text=f"Retrieval failed: {str(e)}",
                latency_ms=latency,
            )

    def _extract_search_terms(self, question: str) -> List[str]:
        """Extract search terms from question"""
        # Simple extraction - in production use NLP
        stop_words = {"what", "is", "the", "a", "an", "are", "be", "do", "how"}
        terms = [
            word for word in question.lower().split() if word.isalpha() and word not in stop_words and len(word) > 2
        ]
        return terms[:5]  # Limit to 5 terms

    async def _search_knowledge_base(self, terms: List[str]) -> List[Dict[str, Any]]:
        """Search SittingFace charts via the shared retriever."""
        query = " ".join(terms)
        report = await self._retriever.retrieve(query, force=True)
        return [
            {
                "entity": item.source_chart or "chart",
                "relevance": item.relevance_score,
                "data": item.content,
                "method": item.retrieval_method,
                "source": item.source_path,
            }
            for item in report.items
        ]

    def _format_results(self, question: str, results: List[Dict[str, Any]]) -> str:
        """Format search results for user"""
        if not results:
            return "No information found."

        lines = []
        for i, result in enumerate(results, 1):
            lines.append(f"{i}. {result['entity']}: {result['data']}")

        return "\n".join(lines)


class ReasoningHandler(BaseHandler):
    """Handle reasoning/analysis queries using LLM"""

    def __init__(self, llm_provider: Optional[Any] = None):
        super().__init__("ReasoningHandler")
        self.llm = llm_provider

    async def handle(self, question: str, context: Optional[Dict[str, Any]] = None) -> HandlerResponse:
        """Process reasoning query with LLM"""
        import time

        start_time = time.time()

        try:
            self.logger.info(f"[reasoning] Analyzing: {question[:60]}")

            # Prepare LLM prompt
            system_prompt = """You are a thoughtful analyst. Provide nuanced, well-reasoned responses
to open-ended questions. Consider multiple perspectives and explain your thinking."""

            # Get reasoning from LLM
            response = await self._get_llm_response(system_prompt, question)

            latency = (time.time() - start_time) * 1000
            self.logger.info(f"[reasoning] Completed in {latency:.0f}ms")

            return HandlerResponse(
                status="success",
                handler_type="ReasoningHandler",
                response_text=response,
                confidence=0.85,
                latency_ms=latency,
            )

        except Exception as e:
            self.logger.error(f"[reasoning] Failed: {e}", exc_info=True)
            latency = (time.time() - start_time) * 1000
            return HandlerResponse(
                status="error",
                handler_type="ReasoningHandler",
                response_text=f"Reasoning failed: {str(e)}",
                latency_ms=latency,
            )

    async def _get_llm_response(self, system_prompt: str, question: str) -> str:
        """Get response from LLM with proper wiring"""
        from src.llm.llm_provider import llm_config

        # Check if LLM is disabled globally
        if not llm_config.enabled:
            self.logger.info("[reasoning] LLM disabled, using rule-based fallback")
            return await self._get_fallback_response(question)

        # Try provided LLM first
        if self.llm:
            try:
                self.logger.debug("[reasoning] Using provided LLM provider")
                response = await self.llm.complete(system=system_prompt, user_message=question, max_tokens=1000)
                return response
            except Exception as e:
                self.logger.warning(f"[reasoning] Provided LLM failed: {e}, trying factory")

        # Fall back to factory (uses environment variables)
        try:
            from src.llm.llm_provider import LLMProviderFactory, llm_config

            # Check if LLM is enabled
            if not llm_config.enabled:
                self.logger.info("[reasoning] LLM disabled, using rule-based fallback")
                return await self._get_fallback_response(question)

            # Get provider from factory
            provider = LLMProviderFactory.get_provider()
            self.logger.debug(f"[reasoning] Using {llm_config.provider} provider")

            response = await provider.complete(system=system_prompt, user_message=question, max_tokens=1000)
            return response

        except Exception as e:
            self.logger.warning(f"[reasoning] LLM factory failed: {e}, using fallback")
            return await self._get_fallback_response(question)

    async def _get_fallback_response(self, question: str) -> str:
        """Fallback reasoning response (rule-based)"""
        return f"""This is an interesting question about: {question}

Key considerations:
1. Multiple perspectives exist on this topic
2. Context and assumptions matter
3. Further investigation may be needed

To provide a more complete answer, please consider:
- What specific aspects interest you most?
- What background knowledge would be helpful?
"""


class ConversationalHandler(BaseHandler):
    """Handle multi-turn conversational queries"""

    def __init__(self, session_manager: Optional[Any] = None, llm_provider: Optional[Any] = None):
        super().__init__("ConversationalHandler")
        self.session_mgr = session_manager
        self.llm = llm_provider

    async def handle(self, question: str, context: Optional[Dict[str, Any]] = None) -> HandlerResponse:
        """Process conversational query with full context"""
        import time

        start_time = time.time()

        try:
            session_id = context.get("session_id") if context else None
            actor_id = context.get("actor_id", "unknown") if context else "unknown"

            if not session_id and self.session_mgr:
                # Create new session if none exists
                session = self.session_mgr.create_session(actor_id)
                session_id = session.session_id

            self.logger.info(f"[conversational] Processing: {question[:60]} (session: {session_id})")

            # Add user message to session
            if self.session_mgr and session_id:
                self.session_mgr.add_message(session_id, "user", question, query_type="conversational")

            # Resolve pronouns using session context
            resolved_question = question
            if self.session_mgr and session_id:
                resolved_question = self.session_mgr.resolve_pronouns(session_id, question)

            # Get response
            response_text = await self._get_response(resolved_question, session_id)

            # Add assistant response to session
            if self.session_mgr and session_id:
                self.session_mgr.add_message(session_id, "assistant", response_text, query_type="conversational")

            latency = (time.time() - start_time) * 1000
            self.logger.info(f"[conversational] Completed in {latency:.0f}ms")

            return HandlerResponse(
                status="success",
                handler_type="ConversationalHandler",
                response_text=response_text,
                confidence=0.9,
                latency_ms=latency,
                metadata={"session_id": session_id},
            )

        except Exception as e:
            self.logger.error(f"[conversational] Failed: {e}", exc_info=True)
            latency = (time.time() - start_time) * 1000
            return HandlerResponse(
                status="error",
                handler_type="ConversationalHandler",
                response_text=f"Conversation failed: {str(e)}",
                latency_ms=latency,
            )

    async def _get_response(self, question: str, session_id: Optional[str]) -> str:
        """Get conversational response with context"""
        from src.llm.llm_provider import llm_config

        # Check if LLM is disabled globally
        if not llm_config.enabled:
            self.logger.info("[conversational] LLM disabled, using rule-based fallback")
            return "I understand. Can you tell me more?"

        # Get session context if available
        context_str = ""
        if self.session_mgr and session_id:
            session = self.session_mgr.get_session(session_id)
            if session:
                context_str = session.get_context_string()

        system_prompt = f"""You are a helpful conversational assistant.
Maintain context from the conversation history and respond naturally.

Conversation history:
{context_str}

Respond to the user's latest message."""

        # Try provided LLM first
        if self.llm:
            try:
                self.logger.debug("[conversational] Using provided LLM provider")
                response = await self.llm.complete(system=system_prompt, user_message=question, max_tokens=500)
                return response
            except Exception as e:
                self.logger.warning(f"[conversational] Provided LLM failed: {e}, trying factory")

        # Fall back to factory (uses environment variables)
        try:
            from src.llm.llm_provider import LLMProviderFactory, llm_config

            # Check if LLM is enabled
            if not llm_config.enabled:
                self.logger.info("[conversational] LLM disabled, using rule-based fallback")
                return "I understand. Can you tell me more?"

            # Get provider from factory
            provider = LLMProviderFactory.get_provider()
            self.logger.debug(f"[conversational] Using {llm_config.provider} provider")

            response = await provider.complete(system=system_prompt, user_message=question, max_tokens=500)
            return response

        except Exception as e:
            self.logger.warning(f"[conversational] LLM factory failed: {e}, using rule-based")
            return "I understand. Can you tell me more?"


class RealtimeHandler(BaseHandler):
    """Handle real-time data queries from APIs"""

    def __init__(self):
        super().__init__("RealtimeHandler")
        self.data_sources = {}

    def register_source(self, name: str, fetcher_func):
        """Register a data source"""
        self.data_sources[name] = fetcher_func
        self.logger.info(f"[realtime] Registered data source: {name}")

    async def handle(self, question: str, context: Optional[Dict[str, Any]] = None) -> HandlerResponse:
        """Fetch real-time data for query"""
        import time

        start_time = time.time()

        try:
            self.logger.info(f"[realtime] Fetching: {question[:60]}")

            # Detect data source needed
            source = self._detect_source(question)

            if not source:
                latency = (time.time() - start_time) * 1000
                return HandlerResponse(
                    status="partial",
                    handler_type="RealtimeHandler",
                    response_text="No real-time data source matched for this query",
                    latency_ms=latency,
                )

            # Fetch data
            data = await self._fetch_data(source, question)

            # Format response
            response_text = self._format_realtime_data(question, source, data)

            latency = (time.time() - start_time) * 1000
            self.logger.info(f"[realtime] Fetched from {source} in {latency:.0f}ms")

            return HandlerResponse(
                status="success",
                handler_type="RealtimeHandler",
                response_text=response_text,
                data=data,
                confidence=0.95,
                latency_ms=latency,
                metadata={"source": source, "timestamp": datetime.now().isoformat()},
            )

        except Exception as e:
            self.logger.error(f"[realtime] Failed: {e}", exc_info=True)
            latency = (time.time() - start_time) * 1000
            return HandlerResponse(
                status="error",
                handler_type="RealtimeHandler",
                response_text=f"Real-time data fetch failed: {str(e)}",
                latency_ms=latency,
            )

    def _detect_source(self, question: str) -> Optional[str]:
        """Detect which data source to use"""
        question_lower = question.lower()

        source_keywords = {
            "stock": "stock_market",
            "weather": "weather_api",
            "traffic": "traffic_api",
            "price": "price_api",
            "availability": "availability_api",
            "status": "status_api",
        }

        for keyword, source in source_keywords.items():
            if keyword in question_lower:
                return source if source in self.data_sources else None

        return None

    async def _fetch_data(self, source: str, question: str) -> Any:
        """Fetch data from source"""
        if source not in self.data_sources:
            return None

        # Simulate async fetch
        await asyncio.sleep(0.1)
        return {
            "source": source,
            "data": "Real-time data",
            "timestamp": datetime.now().isoformat(),
        }

    def _format_realtime_data(self, question: str, source: str, data: Any) -> str:
        """Format real-time data for display"""
        if not data:
            return "No data available"

        return f"""Real-time data from {source}:
{data.get("data", "N/A")}
Last updated: {data.get("timestamp", "Unknown")}"""
