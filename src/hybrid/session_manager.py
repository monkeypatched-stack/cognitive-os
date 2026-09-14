"""Session Manager - Track conversational context across turns

Manages multi-turn conversations with full context history,
allowing queries to reference previous messages.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger("agentos.hybrid.session_manager")


@dataclass
class Message:
    """Single message in conversation"""

    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime
    query_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "query_type": self.query_type,
            "metadata": self.metadata,
        }


@dataclass
class ConversationContext:
    """Full conversation context"""

    session_id: str
    actor_id: str
    created_at: datetime
    last_updated: datetime
    messages: List[Message] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)  # Shared state
    preferences: Dict[str, Any] = field(default_factory=dict)  # User preferences

    def get_context_string(self) -> str:
        """Get formatted context for LLM"""
        context_lines = [f"Conversation between user and assistant"]
        context_lines.append(f"Session started: {self.created_at.isoformat()}")
        context_lines.append(f"User: {self.actor_id}")
        context_lines.append("\nConversation history:")

        for msg in self.messages[-10:]:  # Last 10 messages
            role = "User" if msg.role == "user" else "Assistant"
            context_lines.append(f"\n{role}: {msg.content}")

        return "\n".join(context_lines)

    def add_variable(self, key: str, value: Any):
        """Store a variable from conversation"""
        self.variables[key] = value
        logger.debug(f"[session {self.session_id}] Stored variable: {key} = {value}")

    def get_variable(self, key: str) -> Optional[Any]:
        """Retrieve a stored variable"""
        return self.variables.get(key)


class SessionManager:
    """Manage conversation sessions and context"""

    def __init__(self, session_timeout_minutes: int = 30):
        """
        Initialize session manager

        Args:
            session_timeout_minutes: Session expiration time
        """
        self.sessions: Dict[str, ConversationContext] = {}
        self.session_timeout = timedelta(minutes=session_timeout_minutes)
        logger.info(f"[session] Manager initialized (timeout: {session_timeout_minutes}min)")

    def create_session(self, actor_id: str, session_id: Optional[str] = None) -> ConversationContext:
        """
        Create new conversation session

        Args:
            actor_id: User/actor ID
            session_id: Optional custom session ID

        Returns:
            New ConversationContext
        """
        if session_id is None:
            import uuid

            session_id = f"session_{uuid.uuid4().hex[:8]}"

        context = ConversationContext(
            session_id=session_id,
            actor_id=actor_id,
            created_at=datetime.now(),
            last_updated=datetime.now(),
        )

        self.sessions[session_id] = context
        logger.info(f"[session {session_id}] Created for actor {actor_id}")
        return context

    def get_session(self, session_id: str) -> Optional[ConversationContext]:
        """Get existing session"""
        if session_id not in self.sessions:
            logger.warning(f"[session {session_id}] Not found")
            return None

        context = self.sessions[session_id]

        # Check timeout
        if datetime.now() - context.last_updated > self.session_timeout:
            logger.info(f"[session {session_id}] Expired (timeout)")
            del self.sessions[session_id]
            return None

        return context

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        query_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Message]:
        """
        Add message to session

        Args:
            session_id: Session ID
            role: "user" or "assistant"
            content: Message content
            query_type: Optional query type
            metadata: Optional metadata

        Returns:
            Message if added, None if session not found
        """
        context = self.get_session(session_id)
        if not context:
            logger.warning(f"[session {session_id}] Cannot add message - session not found")
            return None

        message = Message(
            role=role,
            content=content,
            timestamp=datetime.now(),
            query_type=query_type,
            metadata=metadata or {},
        )

        context.messages.append(message)
        context.last_updated = datetime.now()

        logger.debug(f"[session {session_id}] Added {role} message: {content[:50]}...")
        return message

    def get_last_user_message(self, session_id: str) -> Optional[Message]:
        """Get last user message in session"""
        context = self.get_session(session_id)
        if not context:
            return None

        for msg in reversed(context.messages):
            if msg.role == "user":
                return msg

        return None

    def get_last_assistant_message(self, session_id: str) -> Optional[Message]:
        """Get last assistant message in session"""
        context = self.get_session(session_id)
        if not context:
            return None

        for msg in reversed(context.messages):
            if msg.role == "assistant":
                return msg

        return None

    def get_conversation_history(self, session_id: str, max_messages: int = 20) -> List[Message]:
        """Get conversation history"""
        context = self.get_session(session_id)
        if not context:
            return []

        return context.messages[-max_messages:]

    def resolve_pronouns(self, session_id: str, query: str) -> str:
        """
        Attempt to resolve pronouns in query using context

        Example:
            Previous: "I found 3 stores"
            Current: "Book the first one"
            Resolved: "Book the first store"

        Args:
            session_id: Session ID
            query: Current query with pronouns

        Returns:
            Resolved query with pronouns replaced if possible
        """
        context = self.get_session(session_id)
        if not context or not context.messages:
            return query

        # Simple pronoun resolution
        last_assistant_msg = self.get_last_assistant_message(session_id)
        if not last_assistant_msg:
            return query

        # Extract named entities from last message
        # This is simplified - in production use NER
        pronouns = {
            "it": "the item",
            "that": "that option",
            "them": "those items",
            "there": "that location",
        }

        resolved = query
        for pronoun, placeholder in pronouns.items():
            if pronoun in query.lower():
                # Could extract specific entity from context
                logger.debug(f"[session {session_id}] Attempting to resolve pronoun: {pronoun}")

        return resolved

    def clear_session(self, session_id: str) -> bool:
        """Clear a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"[session {session_id}] Cleared")
            return True
        return False

    def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions"""
        now = datetime.now()
        expired = [sid for sid, context in self.sessions.items() if now - context.last_updated > self.session_timeout]

        for sid in expired:
            del self.sessions[sid]

        if expired:
            logger.info(f"[session] Cleaned up {len(expired)} expired sessions")

        return len(expired)

    def get_session_stats(self, session_id: str) -> Optional[dict]:
        """Get statistics about a session"""
        context = self.get_session(session_id)
        if not context:
            return None

        user_msgs = [m for m in context.messages if m.role == "user"]
        assistant_msgs = [m for m in context.messages if m.role == "assistant"]

        return {
            "session_id": session_id,
            "actor_id": context.actor_id,
            "total_messages": len(context.messages),
            "user_messages": len(user_msgs),
            "assistant_messages": len(assistant_msgs),
            "created_at": context.created_at.isoformat(),
            "last_updated": context.last_updated.isoformat(),
            "duration_seconds": (context.last_updated - context.created_at).total_seconds(),
            "stored_variables": len(context.variables),
        }
