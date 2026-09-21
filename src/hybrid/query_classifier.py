"""Query Type Classifier - Route queries to appropriate handlers

Classifies incoming queries into 5 types:
1. action - Goal-driven, requires execution (e.g., "Get me milk")
2. retrieval - Pure information lookup (e.g., "What is the capital of France?")
3. reasoning - Open-ended thinking (e.g., "Why do people form bonds?")
4. conversational - Multi-turn dialogue (e.g., Follow-up questions)
5. realtime - Live data (e.g., "What's the stock price now?")
"""

import logging
import json
from enum import Enum
from typing import Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("agentos.hybrid.query_classifier")


class QueryType(str, Enum):
    """Query type enumeration"""

    ACTION = "action"
    RETRIEVAL = "retrieval"
    REASONING = "reasoning"
    CONVERSATIONAL = "conversational"
    REALTIME = "realtime"


@dataclass
class QueryClassification:
    """Result of query type classification"""

    query_type: QueryType
    confidence: float
    reasoning: str
    matched_patterns: list


class QueryTypeClassifier:
    """Classify incoming query to determine appropriate handler"""

    # Action intent keywords
    ACTION_KEYWORDS = {
        "get",
        "buy",
        "purchase",
        "order",
        "acquire",
        "book",
        "reserve",
        "hire",
        "find",
        "search",
        "locate",
        "build",
        "create",
        "make",
        "cook",
        "prepare",
        "schedule",
        "arrange",
        "plan",
        "organize",
        "move",
        "transfer",
        "send",
        "deliver",
        "pick",
    }

    # Retrieval question keywords
    RETRIEVAL_KEYWORDS = {
        "what",
        "who",
        "when",
        "where",
        "which",
        "list",
        "show",
        "tell",
        "explain",
        "describe",
        "summarize",
        "count",
        "how many",
        "how much",
    }

    # Reasoning question keywords
    REASONING_KEYWORDS = {
        "why",
        "how",
        "explain",
        "compare",
        "contrast",
        "analyze",
        "what if",
        "would",
        "should",
        "could",
        "think about",
        "discuss",
    }

    # Real-time data keywords
    REALTIME_KEYWORDS = {
        "current",
        "now",
        "today",
        "live",
        "recent",
        "latest",
        "real-time",
        "stock",
        "weather",
        "traffic",
        "status",
    }

    # Conversational markers
    CONVERSATIONAL_MARKERS = {
        "that",
        "it",
        "them",
        "there",
        "also",
        "instead",
        "furthermore",
        "agreed",
        "right",
        "okay",
        "thanks",
        "please",
        "sorry",
    }

    def __init__(self, llm_provider: Optional[Any] = None, use_llm: bool = True):
        """
        Initialize classifier

        Args:
            llm_provider: Optional LLM provider for classification
            use_llm: Whether to use LLM when available
        """
        from src.llm.llm_provider import llm_config

        self.llm = llm_provider
        # Respect USE_LLM environment variable
        self.use_llm = use_llm and llm_config.enabled
        logger.info("[classifier] Initialized with LLM: " + ("enabled" if self.use_llm else "disabled"))

    async def classify(self, question: str, session_id: Optional[str] = None) -> QueryClassification:
        """
        Classify query type using LLM or pattern matching

        Args:
            question: User's question/request
            session_id: Session ID if conversational context exists

        Returns:
            QueryClassification with type, confidence, and reasoning
        """
        question_lower = question.lower().strip()

        # Try LLM-based classification first (if enabled and available)
        if self.use_llm:
            llm_result = await self._classify_with_llm(question)
            if llm_result:
                return llm_result

        # Fall back to pattern-based classification
        logger.debug("[classifier] LLM classification not available, using pattern matching")
        return self._classify_with_patterns(question_lower, session_id)

    async def _classify_with_llm(self, question: str) -> Optional[QueryClassification]:
        """
        Classify query using LLM

        Args:
            question: User's question

        Returns:
            QueryClassification if LLM available, None to fall back to patterns
        """
        from src.llm.llm_provider import llm_config

        # Check if LLM disabled globally first (before trying any LLM)
        if not llm_config.enabled:
            logger.debug("[classifier] LLM disabled via USE_LLM=false")
            return None

        # Try provided LLM
        if self.llm:
            try:
                return await self._llm_classification_request(self.llm, question)
            except Exception as e:
                logger.warning(f"[classifier] Provided LLM failed: {e}")

        # Try factory LLM (from env vars)
        try:
            from src.llm.llm_provider import LLMProviderFactory

            # Get provider from factory
            provider = LLMProviderFactory.get_provider()
            logger.debug(f"[classifier] Using LLM provider: {llm_config.provider}")

            return await self._llm_classification_request(provider, question)

        except Exception as e:
            logger.warning(f"[classifier] LLM factory failed: {e}, falling back to patterns")
            return None

    async def _llm_classification_request(self, provider: Any, question: str) -> Optional[QueryClassification]:
        """
        Make classification request to LLM

        Args:
            provider: LLM provider
            question: User's question

        Returns:
            QueryClassification or None if failed
        """

        system_prompt = """You are a query classifier. Classify the user's query into one of these types:
        1. action - Goal-driven, requires execution (e.g., "Get me milk", "Book a flight")
        2. retrieval - Pure information lookup (e.g., "What is the capital?")
        3. reasoning - Open-ended thinking (e.g., "Why do people form bonds?")
        4. conversational - Multi-turn dialogue response (e.g., "Tell me more", "Thank you")
        5. realtime - Requires live data (e.g., "What's the stock price now?")

        Return JSON: {"type": "action|retrieval|reasoning|conversational|realtime", "confidence": 0.0-1.0, "reasoning": "explanation"}
        
        """

        try:
            response_text = await provider.complete(
                system=system_prompt,
                user_message=f"Classify this query: {question}",
                max_tokens=200,
            )

            # Parse LLM response
            parsed = self._parse_llm_response(response_text)

            if not parsed:
                logger.warning("[classifier] Failed to parse LLM response")
                return None

            # Map string type to QueryType enum
            type_str = parsed.get("type", "action").lower()
            try:
                query_type = QueryType(type_str)
            except ValueError:
                logger.warning(f"[classifier] Invalid type from LLM: {type_str}")
                return None

            confidence = float(parsed.get("confidence", 0.5))
            reasoning = parsed.get("reasoning", "LLM classification")

            logger.info(f"[classifier] LLM classified as {query_type.value} (confidence: {confidence:.2f})")

            return QueryClassification(
                query_type=query_type,
                confidence=confidence,
                reasoning=reasoning,
                matched_patterns=["llm"],
            )

        except Exception as e:
            logger.error(f"[classifier] LLM request failed: {e}")
            return None

    def _parse_llm_response(self, response_text: str) -> Optional[dict]:
        """Parse JSON from LLM response"""
        try:
            # Try direct JSON parsing
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from text
        try:
            start_idx = response_text.find("{")
            end_idx = response_text.rfind("}") + 1

            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        logger.warning(f"[classifier] Could not parse LLM response: {response_text[:100]}")
        return None

    def _classify_with_patterns(self, question_lower: str, session_id: Optional[str]) -> QueryClassification:
        """
        Classify query using pattern matching (rule-based fallback)

        Args:
            question_lower: Lowercase question
            session_id: Session ID if available

        Returns:
            QueryClassification based on patterns
        """
        # Check for action keywords first (higher priority)
        if self._is_action(question_lower):
            return QueryClassification(
                query_type=QueryType.ACTION,
                confidence=0.9,
                reasoning="Detected action-oriented intent",
                matched_patterns=self._find_patterns(question_lower, self.ACTION_KEYWORDS),
            )

        # Check for real-time data keywords
        if self._is_realtime(question_lower):
            return QueryClassification(
                query_type=QueryType.REALTIME,
                confidence=0.85,
                reasoning="Detected real-time data keywords",
                matched_patterns=self._find_patterns(question_lower, self.REALTIME_KEYWORDS),
            )

        # Check for reasoning keywords
        if self._is_reasoning(question_lower):
            return QueryClassification(
                query_type=QueryType.REASONING,
                confidence=0.85,
                reasoning="Detected reasoning/analysis intent",
                matched_patterns=self._find_patterns(question_lower, self.REASONING_KEYWORDS),
            )

        # Check for retrieval keywords
        if self._is_retrieval(question_lower):
            return QueryClassification(
                query_type=QueryType.RETRIEVAL,
                confidence=0.85,
                reasoning="Detected information retrieval intent",
                matched_patterns=self._find_patterns(question_lower, self.RETRIEVAL_KEYWORDS),
            )

        # Check for conversational markers last (fallback - requires session)
        if session_id and self._is_conversational(question_lower):
            return QueryClassification(
                query_type=QueryType.CONVERSATIONAL,
                confidence=0.9,
                reasoning="Detected conversational markers in session context",
                matched_patterns=self._find_patterns(question_lower, self.CONVERSATIONAL_MARKERS),
            )

        # Default to action for ambiguous cases
        return QueryClassification(
            query_type=QueryType.ACTION,
            confidence=0.5,
            reasoning="No clear patterns matched, defaulting to action",
            matched_patterns=[],
        )

    def _is_action(self, question: str) -> bool:
        """Check if question is action-oriented"""
        # Check for action keywords
        if any(keyword in question for keyword in self.ACTION_KEYWORDS):
            return True

        # Check for imperative mood (starts with verb)
        imperative_starts = ["get", "buy", "find", "book", "order", "make", "do"]
        if any(question.startswith(verb) for verb in imperative_starts):
            return True

        # Check for "I want to" or "I need to"
        if any(phrase in question for phrase in ["i want", "i need", "i'd like", "can you"]):
            return True

        return False

    def _is_retrieval(self, question: str) -> bool:
        """Check if question is pure information retrieval"""
        # Check for retrieval keywords
        retrieval_starts = [
            "what",
            "who",
            "when",
            "where",
            "which",
            "how many",
            "how much",
        ]
        if any(question.startswith(q) for q in retrieval_starts):
            return True

        # Check for "Tell me" or "Show me"
        if any(phrase in question for phrase in ["tell me", "show me", "list", "describe"]):
            return True

        return False

    def _is_reasoning(self, question: str) -> bool:
        """Check if question requires reasoning/analysis"""
        # Check for reasoning keywords
        if any(keyword in question for keyword in self.REASONING_KEYWORDS):
            return True

        # Check for open-ended questions
        if any(phrase in question for phrase in ["why", "how would", "what if", "explain"]):
            return True

        return False

    def _is_realtime(self, question: str) -> bool:
        """Check if question needs real-time data"""
        # Check for real-time keywords
        if any(keyword in question for keyword in self.REALTIME_KEYWORDS):
            return True

        # Check for specific real-time data sources
        realtime_entities = [
            "stock",
            "weather",
            "traffic",
            "price",
            "availability",
            "status",
        ]
        if any(entity in question for entity in realtime_entities):
            return True

        return False

    def _is_conversational(self, question: str) -> bool:
        """Check if question is conversational follow-up"""
        # Short questions/responses
        if len(question.split()) <= 5:
            return True

        # Check for conversational markers
        if any(marker in question for marker in self.CONVERSATIONAL_MARKERS):
            return True

        # Check for pronouns referring to previous context
        conversational_pronouns = ["that", "it", "them", "those", "that one"]
        if any(pronoun in question for pronoun in conversational_pronouns):
            return True

        return False

    def _find_patterns(self, question: str, keyword_set: set) -> list:
        """Find which patterns matched in the question"""
        matched = []
        for keyword in keyword_set:
            if keyword in question:
                matched.append(keyword)
        return matched[:5]  # Return top 5 matches

    def get_handler_recommendation(self, classification: QueryClassification) -> str:
        """Get human-readable handler recommendation"""
        recommendations = {
            QueryType.ACTION: "ActionHandler (CognitiveRuntime)",
            QueryType.RETRIEVAL: "RetrievalHandler (knowledge base)",
            QueryType.REASONING: "ReasoningHandler (LLM chat)",
            QueryType.CONVERSATIONAL: "ConversationalHandler (session manager)",
            QueryType.REALTIME: "RealtimeHandler (API fetcher)",
        }
        return recommendations.get(classification.query_type, "ActionHandler")
