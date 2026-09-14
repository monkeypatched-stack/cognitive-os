"""IntentCompilerAgent — Stage 1 of the execution graph pipeline.

Transforms natural language intent into a structured Goal IR.

Pipeline:
    Natural Language → Intent Compiler → Goal IR → Graph Synthesizer → Execution Graph

The Intent Compiler extracts:
- Intent type (purchase, build, query, coordinate, etc.)
- Domain (shopping, software, research, etc.)
- Entities (products, people, services)
- Constraints (budget, quantity, preferences)
- Relationships between entities
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ._base import BaseETASSAgent
from .goal_ir import GoalIR, EntityIR, ConstraintIR, RelationshipIR

logger = logging.getLogger("broca.agents.intent_compiler")


class IntentCompilerAgent(BaseETASSAgent):
    """Compiles natural language intent into a structured Goal IR.

    This is the first stage of the execution graph pipeline. It uses an LLM
    to extract structured information from raw natural language, producing
    a GoalIR that the Graph Synthesizer can consume.
    """

    agent_type = "intent_compiler"
    description = "Compiles natural language intent into structured Goal IR"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._llm = self._select_provider()

    def _select_provider(self):
        """Select LLM provider (same logic as GraphGeneratorAgent)."""
        import os
        from ._llm_bridge import dev_bridge_client
        from ._llm_cache import maybe_cache

        bridge = dev_bridge_client("intent_compiler")
        if bridge is not None:
            return maybe_cache(bridge, "intent_compiler")

        # Try Claude if configured
        if os.environ.get("SPEC_DISCOVERY_PROVIDER", "").lower() == "claude":
            try:
                import anthropic

                anthropic.Anthropic()
                from .graph_generator_agent import ClaudeClient

                return maybe_cache(ClaudeClient(), "intent_compiler")
            except Exception:
                pass

        # Try Ollama
        try:
            import socket

            s = socket.socket()
            s.settimeout(1)
            reachable = s.connect_ex(("localhost", 11434)) == 0
            s.close()
            if reachable:
                from .graph_generator_agent import OllamaClient

                return maybe_cache(
                    OllamaClient(model=os.environ.get("OLLAMA_MODEL", "gemma3:latest")),
                    "intent_compiler",
                )
        except Exception:
            pass

        # Fallback to Claude
        try:
            import anthropic

            anthropic.Anthropic()
            from .graph_generator_agent import ClaudeClient

            return maybe_cache(ClaudeClient(), "intent_compiler")
        except Exception:
            pass

        return None

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> GoalIR:
        """Extract structured Goal IR from natural language intent."""
        intent = context.get("intent") or context.get("question") or context.get("specification", {}).get("goal", "")

        if not intent:
            self._reward(False, 0.0)
            return GoalIR(intent_type="unknown", domain="general", goal="")

        # Step 1: Extract structured information via LLM
        if self._llm is not None:
            try:
                goal_ir = await self._extract_goal_ir(intent)
                self._reward(True, 0.8)
                return goal_ir
            except Exception as e:
                logger.warning("[intent_compiler] LLM extraction failed: %s", e)

        # Step 2: Fallback to rule-based extraction
        goal_ir = self._rule_based_extraction(intent)
        self._reward(True, 0.5)
        return goal_ir

    async def _extract_goal_ir(self, intent: str) -> GoalIR:
        """Use LLM to extract structured Goal IR from natural language."""
        prompt = self._build_extraction_prompt(intent)

        raw = await self._llm.generate(
            prompt,
            system=(
                "You are an expert intent analyzer. Extract structured information "
                "from natural language and return ONLY valid JSON matching the schema."
            ),
        )

        # Parse JSON response
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("No JSON found in LLM response")

        data = json.loads(m.group(0))

        # Convert to GoalIR
        return self._dict_to_goal_ir(data, intent)

    def _build_extraction_prompt(self, intent: str) -> str:
        return f"""Analyze this natural language intent and extract structured information:

"{intent}"

Return a JSON object with this exact structure:
{{
    "intent_type": "purchase|build|query|coordinate|manage|other",
    "domain": "shopping|software|research|finance|other",
    "entities": [
        {{
            "name": "entity name",
            "type": "product|person|service|resource|other",
            "attributes": {{"key": "value"}}
        }}
    ],
    "constraints": [
        {{
            "type": "budget|preference|time|quantity|quality|other",
            "description": "human-readable constraint description",
            "parameters": {{"key": "value"}}
        }}
    ],
    "relationships": [
        {{
            "source": "entity name",
            "target": "entity name",
            "type": "requires|coordinates|shares|uses|other"
        }}
    ],
    "goal_tree": [
        {{
            "id": "goal_N",
            "text": "specific goal description",
            "priority": "high|medium|low",
            "beneficiary": "person or role (optional)",
            "parent_id": "parent goal id (optional, null for root goals)",
            "condition": "conditional logic like 'budget_permits' (optional)",
            "attributes": {{"key": "value"}}
        }}
    ]
}}

Rules:
- Extract ALL entities mentioned (products, people, services)
- Identify constraints (budget limits, quantities, preferences)
- Map relationships between entities
- Extract goal_tree: decompose intent into a hierarchy of sub-goals with priority, beneficiary, and conditions
- Be specific with attributes (quantities, types, etc.)
- Return ONLY the JSON object, no explanation
"""

    def _dict_to_goal_ir(self, data: dict, intent: str) -> GoalIR:
        """Convert extracted dict to GoalIR dataclass."""
        from .goal_ir import GoalNode

        entities = [
            EntityIR(
                name=e.get("name", ""),
                entity_type=e.get("type", "unknown"),
                attributes=e.get("attributes", {}),
            )
            for e in data.get("entities", [])
        ]

        constraints = [
            ConstraintIR(
                constraint_type=c.get("type", "unknown"),
                description=c.get("description", ""),
                parameters=c.get("parameters", {}),
            )
            for c in data.get("constraints", [])
        ]

        relationships = [
            RelationshipIR(
                source=r.get("source", ""),
                target=r.get("target", ""),
                relationship_type=r.get("type", "unknown"),
            )
            for r in data.get("relationships", [])
        ]

        goal_tree = [
            GoalNode(
                id=g.get("id", ""),
                text=g.get("text", ""),
                priority=g.get("priority", "medium"),
                beneficiary=g.get("beneficiary"),
                parent_id=g.get("parent_id"),
                condition=g.get("condition"),
                attributes=g.get("attributes", {}),
            )
            for g in data.get("goal_tree", [])
        ]

        return GoalIR(
            intent_type=data.get("intent_type", "unknown"),
            domain=data.get("domain", "general"),
            goal=intent,
            entities=entities,
            constraints=constraints,
            relationships=relationships,
            goal_tree=goal_tree,
        )

    def _rule_based_extraction(self, intent: str) -> GoalIR:
        """Fallback rule-based extraction when LLM is unavailable."""
        intent_lower = intent.lower()

        # Detect intent type
        if any(w in intent_lower for w in ["buy", "purchase", "order", "get"]):
            intent_type = "purchase"
        elif any(w in intent_lower for w in ["build", "create", "make", "develop"]):
            intent_type = "build"
        elif any(w in intent_lower for w in ["find", "search", "query", "look"]):
            intent_type = "query"
        elif any(w in intent_lower for w in ["coordinate", "manage", "organize"]):
            intent_type = "coordinate"
        else:
            intent_type = "unknown"

        # Detect domain
        if any(w in intent_lower for w in ["milk", "pizza", "grocery", "food", "shop"]):
            domain = "shopping"
        elif any(w in intent_lower for w in ["code", "app", "software", "program"]):
            domain = "software"
        else:
            domain = "general"

        # Extract simple entities (products)
        entities = []
        # Look for quantity patterns
        quantity_pattern = r"(\d+)\s*(liters?|kg|lbs?|oz|pieces?|boxes?)"
        matches = re.findall(quantity_pattern, intent_lower)
        for qty, unit in matches:
            # Try to find the product name (word after quantity)
            idx = intent_lower.find(f"{qty} {unit}")
            if idx >= 0:
                after = intent_lower[idx + len(f"{qty} {unit}") :].strip()
                product = after.split()[0] if after.split() else "item"
                entities.append(
                    EntityIR(
                        name=product,
                        entity_type="product",
                        attributes={"quantity": qty, "unit": unit},
                    )
                )

        # Detect budget constraint
        constraints = []
        if any(w in intent_lower for w in ["budget", "cost", "price", "spend", "afford"]):
            constraints.append(
                ConstraintIR(
                    constraint_type="budget",
                    description="Shared household budget constraint",
                    parameters={},
                )
            )

        # Detect coordination
        relationships = []
        if any(w in intent_lower for w in ["coordinate", "shared", "together", "both"]):
            relationships.append(
                RelationshipIR(
                    source="orders",
                    target="budget",
                    relationship_type="coordinates",
                )
            )

        from .goal_ir import GoalNode

        goal_tree = []
        for idx, entity in enumerate(entities):
            goal_tree.append(
                GoalNode(
                    id=f"goal_{idx + 1}",
                    text=f"Obtain {entity.name}",
                    priority="high" if idx == 0 else "medium",
                    beneficiary=None,
                    parent_id=None,
                    condition=None,
                    attributes={"entity": entity.name, "type": entity.entity_type},
                )
            )

        return GoalIR(
            intent_type=intent_type,
            domain=domain,
            goal=intent,
            entities=entities,
            constraints=constraints,
            relationships=relationships,
            goal_tree=goal_tree,
        )
