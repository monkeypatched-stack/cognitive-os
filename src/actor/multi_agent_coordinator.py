"""Phase 11: Multi-Agent Collaboration - Cooperative Goal Coordination

Enables actors to coordinate on shared goals, communicate capabilities,
and establish trust for collaborative execution.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any
from datetime import datetime
from enum import Enum


class CoordinationMessageType(str, Enum):
    """Types of inter-actor coordination messages"""

    GOAL_PROPOSAL = "goal_proposal"
    GOAL_ACCEPTANCE = "goal_acceptance"
    GOAL_REJECTION = "goal_rejection"
    CAPABILITY_BROADCAST = "capability_broadcast"
    CAPABILITY_QUERY = "capability_query"
    TRUST_UPDATE = "trust_update"
    COORDINATION_REQUEST = "coordination_request"
    STATUS_UPDATE = "status_update"


@dataclass
class ActorCapability:
    """Describes what an actor can do"""

    capability_id: str
    description: str
    success_rate: float  # 0.0 to 1.0
    cost: float  # Resource cost
    duration_seconds: float  # How long it typically takes
    prerequisites: List[str] = field(default_factory=list)  # Other capabilities needed
    published_at: datetime = field(default_factory=datetime.now)

    def is_available(self) -> bool:
        """Check if capability is available"""
        return self.success_rate > 0.5 and self.cost >= 0


@dataclass
class TrustScore:
    """Tracks trust between actors"""

    actor_a: str
    actor_b: str
    score: float = 0.5  # 0.0 to 1.0 (neutral start)
    interactions: int = 0
    successful_collaborations: int = 0
    failed_collaborations: int = 0
    last_updated: datetime = field(default_factory=datetime.now)

    def update_on_success(self) -> None:
        """Increase trust after successful collaboration"""
        self.interactions += 1
        self.successful_collaborations += 1
        self.score = min(1.0, self.score + 0.1)
        self.last_updated = datetime.now()

    def update_on_failure(self) -> None:
        """Decrease trust after failed collaboration"""
        self.interactions += 1
        self.failed_collaborations += 1
        self.score = max(0.0, self.score - 0.2)
        self.last_updated = datetime.now()

    def is_trustworthy(self, threshold: float = 0.5) -> bool:
        """Check if actor meets trust threshold"""
        return self.score >= threshold and self.interactions >= 3


@dataclass
class SharedGoal:
    """Goal shared among multiple actors"""

    goal_id: str
    description: str
    participants: Set[str]  # Actor IDs participating
    leader_actor_id: str
    deadline: Optional[datetime] = None
    status: str = "proposed"  # proposed, accepted, executing, completed, failed
    created_at: datetime = field(default_factory=datetime.now)
    shared_context: Dict[str, Any] = field(default_factory=dict)
    required_capabilities: List[str] = field(default_factory=list)

    def all_accepted(self) -> bool:
        """Check if all participants accepted"""
        return self.status == "accepted"

    def is_overdue(self) -> bool:
        """Check if goal exceeded deadline"""
        if not self.deadline:
            return False
        return datetime.now() > self.deadline


@dataclass
class CoordinationMessage:
    """Message between actors for coordination"""

    message_type: CoordinationMessageType
    sender_actor_id: str
    recipient_actor_ids: List[str]
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    message_id: str = ""

    def __post_init__(self):
        if not self.message_id:
            self.message_id = f"{self.sender_actor_id}_{datetime.now().timestamp()}"


class MultiAgentCoordinator:
    """Coordinates collaboration between actors

    Manages:
    - Shared goal proposals and acceptance
    - Capability discovery and broadcasting
    - Trust tracking between actors
    - Message routing for coordination
    """

    def __init__(self):
        self._actors_capabilities: Dict[str, List[ActorCapability]] = {}
        self._trust_matrix: Dict[tuple, TrustScore] = {}
        self._shared_goals: Dict[str, SharedGoal] = {}
        self._message_queues: Dict[str, asyncio.Queue] = {}
        self._coordination_history: List[CoordinationMessage] = []
        self._actor_peers: Dict[str, Set[str]] = {}

    def register_actor(self, actor_id: str) -> None:
        """Register actor for coordination"""
        self._actors_capabilities[actor_id] = []
        self._message_queues[actor_id] = asyncio.Queue()
        self._actor_peers[actor_id] = set()

    def broadcast_capability(self, actor_id: str, capability: ActorCapability) -> None:
        """Actor broadcasts its capabilities"""
        if actor_id not in self._actors_capabilities:
            self.register_actor(actor_id)

        self._actors_capabilities[actor_id].append(capability)

    def discover_capabilities(
        self, querying_actor_id: str, required_capabilities: List[str]
    ) -> Dict[str, List[ActorCapability]]:
        """Discover which actors have required capabilities"""
        results = {}

        for capability_id in required_capabilities:
            results[capability_id] = []

            for actor_id, capabilities in self._actors_capabilities.items():
                if actor_id == querying_actor_id:
                    continue

                matching = [cap for cap in capabilities if cap.capability_id == capability_id and cap.is_available()]

                results[capability_id].extend(matching)

        return results

    async def propose_shared_goal(self, goal: SharedGoal) -> bool:
        """Propose shared goal to participants"""
        if not goal.participants:
            return False

        self._shared_goals[goal.goal_id] = goal

        # Broadcast proposal to all participants
        message = CoordinationMessage(
            message_type=CoordinationMessageType.GOAL_PROPOSAL,
            sender_actor_id=goal.leader_actor_id,
            recipient_actor_ids=list(goal.participants),
            payload={
                "goal_id": goal.goal_id,
                "description": goal.description,
                "deadline": goal.deadline,
                "required_capabilities": goal.required_capabilities,
            },
        )

        await self._broadcast_message(message)
        return True

    async def accept_shared_goal(self, actor_id: str, goal_id: str) -> bool:
        """Actor accepts participation in shared goal"""
        if goal_id not in self._shared_goals:
            return False

        goal = self._shared_goals[goal_id]
        if actor_id not in goal.participants:
            return False

        # Send acceptance message
        message = CoordinationMessage(
            message_type=CoordinationMessageType.GOAL_ACCEPTANCE,
            sender_actor_id=actor_id,
            recipient_actor_ids=[goal.leader_actor_id],
            payload={"goal_id": goal_id},
        )

        await self._send_message(message)

        # Check if all accepted
        if self._check_all_accepted(goal):
            goal.status = "accepted"

        return True

    async def reject_shared_goal(self, actor_id: str, goal_id: str, reason: str) -> bool:
        """Actor rejects shared goal"""
        if goal_id not in self._shared_goals:
            return False

        goal = self._shared_goals[goal_id]
        message = CoordinationMessage(
            message_type=CoordinationMessageType.GOAL_REJECTION,
            sender_actor_id=actor_id,
            recipient_actor_ids=[goal.leader_actor_id],
            payload={"goal_id": goal_id, "reason": reason},
        )

        await self._send_message(message)
        goal.participants.discard(actor_id)

        return True

    async def start_shared_goal_execution(self, goal_id: str) -> bool:
        """Coordinator starts execution of accepted goal"""
        if goal_id not in self._shared_goals:
            return False

        goal = self._shared_goals[goal_id]
        if not goal.all_accepted():
            return False

        goal.status = "executing"

        # Notify all participants
        message = CoordinationMessage(
            message_type=CoordinationMessageType.STATUS_UPDATE,
            sender_actor_id=goal.leader_actor_id,
            recipient_actor_ids=list(goal.participants),
            payload={
                "goal_id": goal_id,
                "status": "executing",
                "shared_context": goal.shared_context,
            },
        )

        await self._broadcast_message(message)
        return True

    async def update_shared_goal_status(
        self, goal_id: str, status: str, context_updates: Optional[Dict] = None
    ) -> None:
        """Update status of shared goal"""
        if goal_id not in self._shared_goals:
            return

        goal = self._shared_goals[goal_id]
        goal.status = status

        if context_updates:
            goal.shared_context.update(context_updates)

        # Broadcast status update
        message = CoordinationMessage(
            message_type=CoordinationMessageType.STATUS_UPDATE,
            sender_actor_id=goal.leader_actor_id,
            recipient_actor_ids=list(goal.participants),
            payload={
                "goal_id": goal_id,
                "status": status,
                "context": goal.shared_context,
            },
        )

        await self._broadcast_message(message)

    def update_trust(self, actor_a: str, actor_b: str, success: bool) -> None:
        """Update trust between two actors"""
        trust_key = tuple(sorted([actor_a, actor_b]))

        if trust_key not in self._trust_matrix:
            self._trust_matrix[trust_key] = TrustScore(actor_a=actor_a, actor_b=actor_b)

        trust = self._trust_matrix[trust_key]

        if success:
            trust.update_on_success()
        else:
            trust.update_on_failure()

    def get_trust(self, actor_a: str, actor_b: str) -> Optional[TrustScore]:
        """Get trust score between actors"""
        trust_key = tuple(sorted([actor_a, actor_b]))
        return self._trust_matrix.get(trust_key)

    def get_trusted_partners(self, actor_id: str, threshold: float = 0.6) -> List[str]:
        """Get list of trusted partners for actor"""
        trusted = []

        for (a, b), trust in self._trust_matrix.items():
            if a == actor_id and trust.is_trustworthy(threshold):
                trusted.append(b)
            elif b == actor_id and trust.is_trustworthy(threshold):
                trusted.append(a)

        return trusted

    async def receive_message(self, actor_id: str) -> Optional[CoordinationMessage]:
        """Actor receives next coordination message"""
        if actor_id not in self._message_queues:
            return None

        try:
            return self._message_queues[actor_id].get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def receive_message_blocking(self, actor_id: str, timeout: float = 5.0) -> Optional[CoordinationMessage]:
        """Block until message received (with timeout)"""
        if actor_id not in self._message_queues:
            return None

        try:
            return await asyncio.wait_for(self._message_queues[actor_id].get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_shared_goal(self, goal_id: str) -> Optional[SharedGoal]:
        """Get shared goal details"""
        return self._shared_goals.get(goal_id)

    def get_actor_goals(self, actor_id: str) -> List[SharedGoal]:
        """Get all shared goals involving actor"""
        return [goal for goal in self._shared_goals.values() if actor_id in goal.participants]

    def get_stats(self) -> Dict[str, Any]:
        """Get coordination statistics"""
        return {
            "registered_actors": len(self._actors_capabilities),
            "total_capabilities_broadcast": sum(len(caps) for caps in self._actors_capabilities.values()),
            "active_shared_goals": len(
                [g for g in self._shared_goals.values() if g.status in ["proposed", "accepted", "executing"]]
            ),
            "trust_relationships": len(self._trust_matrix),
            "coordination_messages_sent": len(self._coordination_history),
        }

    # Private methods

    async def _broadcast_message(self, message: CoordinationMessage) -> None:
        """Send message to all recipients"""
        self._coordination_history.append(message)

        for recipient_id in message.recipient_actor_ids:
            if recipient_id in self._message_queues:
                await self._message_queues[recipient_id].put(message)

    async def _send_message(self, message: CoordinationMessage) -> None:
        """Send message to specific recipient"""
        self._coordination_history.append(message)

        for recipient_id in message.recipient_actor_ids:
            if recipient_id in self._message_queues:
                await self._message_queues[recipient_id].put(message)

    def _check_all_accepted(self, goal: SharedGoal) -> bool:
        """Check if all goal participants accepted"""
        # In real implementation, would check acceptance receipts
        return True

    def get_coordination_history(self, limit: int = 100) -> List[CoordinationMessage]:
        """Get recent coordination messages"""
        return self._coordination_history[-limit:]


class CollaborativeActor:
    """Mixin for collaborative multi-agent capabilities"""

    def __init__(self):
        self._coordinator: Optional[MultiAgentCoordinator] = None
        self._my_capabilities: List[ActorCapability] = []
        self._active_collaborations: Dict[str, SharedGoal] = {}

    def set_coordinator(self, coordinator: MultiAgentCoordinator) -> None:
        """Link to multi-agent coordinator"""
        self._coordinator = coordinator
        if self._coordinator:
            self._coordinator.register_actor(self.id)

    async def broadcast_capability(self, capability: ActorCapability) -> None:
        """Broadcast what this actor can do"""
        if not self._coordinator:
            return

        self._my_capabilities.append(capability)
        self._coordinator.broadcast_capability(self.id, capability)

    async def discover_collaborators(self, required_capabilities: List[str]) -> Dict[str, List[ActorCapability]]:
        """Find actors with needed capabilities"""
        if not self._coordinator:
            return {}

        return self._coordinator.discover_capabilities(self.id, required_capabilities)

    async def propose_collaboration(
        self,
        goal_description: str,
        collaborator_ids: List[str],
        required_capabilities: List[str],
        deadline: Optional[datetime] = None,
    ) -> Optional[SharedGoal]:
        """Propose shared goal to other actors"""
        if not self._coordinator:
            return None

        goal = SharedGoal(
            goal_id=f"{self.id}_{datetime.now().timestamp()}",
            description=goal_description,
            participants=set(collaborator_ids) | {self.id},
            leader_actor_id=self.id,
            deadline=deadline,
            required_capabilities=required_capabilities,
        )

        await self._coordinator.propose_shared_goal(goal)
        self._active_collaborations[goal.goal_id] = goal

        return goal

    async def receive_collaboration_proposal(self) -> Optional[SharedGoal]:
        """Check for shared goal proposals"""
        if not self._coordinator:
            return None

        message = await self._coordinator.receive_message(self.id)

        if not message:
            return None

        if message.message_type != CoordinationMessageType.GOAL_PROPOSAL:
            return None

        goal_id = message.payload["goal_id"]
        goal = self._coordinator.get_shared_goal(goal_id)

        return goal

    async def accept_collaboration(self, goal_id: str) -> bool:
        """Accept participation in shared goal"""
        if not self._coordinator:
            return False

        success = await self._coordinator.accept_shared_goal(self.id, goal_id)

        if success:
            goal = self._coordinator.get_shared_goal(goal_id)
            if goal:
                self._active_collaborations[goal_id] = goal

        return success

    async def reject_collaboration(self, goal_id: str, reason: str) -> bool:
        """Reject shared goal"""
        if not self._coordinator:
            return False

        return await self._coordinator.reject_shared_goal(self.id, goal_id, reason)

    def get_trusted_collaborators(self) -> List[str]:
        """Get list of trusted partners"""
        if not self._coordinator:
            return []

        return self._coordinator.get_trusted_partners(self.id)

    async def report_collaboration_success(self, goal_id: str, partner_ids: List[str]) -> None:
        """Report successful collaboration with partners"""
        if not self._coordinator:
            return

        for partner_id in partner_ids:
            self._coordinator.update_trust(self.id, partner_id, success=True)

        await self._coordinator.update_shared_goal_status(goal_id, "completed")

    async def report_collaboration_failure(self, goal_id: str, partner_ids: List[str]) -> None:
        """Report failed collaboration"""
        if not self._coordinator:
            return

        for partner_id in partner_ids:
            self._coordinator.update_trust(self.id, partner_id, success=False)

        await self._coordinator.update_shared_goal_status(goal_id, "failed")

    def get_collaboration_stats(self) -> Dict[str, Any]:
        """Get collaboration statistics"""
        return {
            "actor_id": self.id,
            "capabilities": len(self._my_capabilities),
            "active_collaborations": len(self._active_collaborations),
            "trusted_partners": len(self.get_trusted_collaborators()),
        }
