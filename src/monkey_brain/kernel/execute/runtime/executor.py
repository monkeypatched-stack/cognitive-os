"""Unified Executor — entry point for question execution.

Delegates all work to the execution package:
  Routing / classification → routing.py
  Phase orchestration      → phases.py
  Models                   → models.py
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.execute.models import ExecutionMode, ExecutionResult
from src.monkey_brain.kernel.execute.orchestration.routing import (
    create_goal,
    normalize_question,
    resolve_goal_from_intent,
    unsupported_response,
)
from src.monkey_brain.kernel.plan.workload.policy import get_policy
from src.monkey_brain.kernel.plan.goals.executor import GoalExecutor

logger = logging.getLogger("agentos.executor")


class UnifiedExecutor:
    """Receives a question and returns an ExecutionResult."""

    async def execute(
        self,
        question: str,
        mongo_client: Any,
        question_source: str = ExecutionMode.QUERY,
        steps: list | None = None,
        intent: dict | None = None,
        run_id: str = "",
        capture: dict | None = None,
    ) -> ExecutionResult:
        """Classify, route, and execute. Returns ExecutionResult (also unpackable as 4-tuple).

        intent   : a previously-resolved intent dict (e.g. from a prior /plan
                   call). When given, skips classification entirely and
                   reuses this exact intent — the goal is frozen once
                   execution starts, it is never re-derived from the question.
        run_id   : correlates this call with the run that produced `intent`
                   (or starts a new run if not chained from one), threaded
                   into lemon tracing for end-to-end traceability.
        capture  : optional out-param — when given a dict, this call stores
                   the resolved intent, goal, and normalized question into it
                   (capture["intent"], capture["goal"], capture["normalized"])
                   so a /plan-style caller can compile them into an IntentIR
                   for a later /execute call.
        """
        lemon = getattr(get_policy(), "_lemon", None)
        try:
            if intent is not None:
                # Goal already resolved by a prior call — reuse it verbatim.
                normalized = normalize_question(question)
                goal = resolve_goal_from_intent(normalized, intent)
            else:
                # 1. Classify the question and create a goal.
                # Only reached when no `intent` was handed in — i.e. no prior
                # /plan call to freeze the goal from (see docstring above).
                # FL - STEP 6
                normalized, intent, goal = create_goal(question, lemon=lemon, trace_id=run_id)
        except Exception as e:
            logger.error("run=%r create_goal failed: %s", run_id, e)
            if lemon:
                lemon.counter("execution.routing.error")
                lemon.error(str(e), component="executor.create_goal", run_id=run_id)
            return ExecutionResult("Error classifying and routing the question.", [], [], False)

        if not intent or goal is None:
            # No intent matched — try world graph fallback
            world_answer = await self._world_graph_fallback(question, mongo_client)
            if world_answer:
                return world_answer
            if lemon:
                lemon.counter("execution.routing.unsupported")
            return ExecutionResult(*unsupported_response())

        if capture is not None:
            capture["intent"] = intent
            capture["goal"] = goal
            capture["normalized"] = normalized

        # Mode-specific gating lives in GoalExecutor.execute, which receives `mode`
        # below: QUERY and QA_BACKGROUND are read-only (refuse CREATE/UPDATE/DELETE
        # goals), PLANNING is preview-only (never executes), EXECUTE permits
        # mutations. SIMULATION never reaches here — /predict's simulate route
        # uses PredictEngine directly, not UnifiedExecutor.
        try:
            mode = ExecutionMode(question_source)
        except ValueError:
            # Invalid execution mode — default to QUERY (fail closed: read-only)
            mode = ExecutionMode.QUERY

        goal_executor = GoalExecutor()

        # 2. Execute the goal and return the result
        # FL - STEP 15
        answer, semantic_hits, graph_paths, llm_answered = await goal_executor.execute(
            goal=goal,
            mongo_client=mongo_client,
            question=normalized,
            plan_steps=steps or [],
            mode=mode,
            run_id=run_id,
        )

        # 3. Return the execution result
        return ExecutionResult(answer, semantic_hits, graph_paths, llm_answered)

    async def _world_graph_fallback(self, question: str, mongo_client: Any) -> ExecutionResult | None:
        """Search the world graph for states matching the question.

        When no intent matches, this fallback searches the CognitionEngine's
        world graph for states that match the natural language question, then
        executes a cognitive cycle through those states.
        """
        try:
            # Get the CognitionEngine from the request context
            from src.monkey_brain.api import bootstrap

            arch = getattr(bootstrap, "_architecture", None)
            if arch is None or not hasattr(arch, "_world"):
                return None

            world = arch._world
            if not hasattr(world, "states") or not world.states():
                return None

            # Search for matching states
            start_state, goal_state = self._find_world_states(question, world)
            if not start_state:
                return None

            logger.info(
                "[executor] world_graph_fallback: %r → start=%r, goal=%r",
                question,
                start_state,
                goal_state,
            )

            # Build answer from world graph traversal
            path = self._trace_path(start_state, goal_state, world)
            if not path:
                return None

            answer = self._format_world_answer(question, path, world)
            return ExecutionResult(answer, [], path, False)

        except Exception as e:
            logger.debug("[executor] world_graph_fallback failed: %s", e)
            return None

    def _find_world_states(self, question: str, world: Any) -> tuple[str, str]:
        """Find start and goal states in the world graph matching the question."""
        all_states = world.states()
        if not all_states:
            return "", ""

        q_lower = question.lower()

        # 1. Exact match
        if question in all_states:
            goal = self._find_terminal_from(question, world)
            return question, goal

        # 2. Substring match
        for state in all_states:
            if state.lower() in q_lower or q_lower in state.lower():
                goal = self._find_terminal_from(state, world)
                return state, goal

        # 3. Token overlap
        q_tokens = set(q_lower.split())
        best_state = None
        best_score = 0
        for state in all_states:
            s_tokens = set(state.lower().replace("_", " ").split())
            overlap = len(q_tokens & s_tokens)
            if overlap > best_score:
                best_score = overlap
                best_state = state

        if best_state and best_score > 0:
            goal = self._find_terminal_from(best_state, world)
            return best_state, goal

        # 4. Fallback: entry → terminal
        entry = self._find_entry_state(world)
        terminal = self._find_terminal_state(world)
        return entry, terminal

    def _find_entry_state(self, world: Any) -> str:
        """Find a state with no predecessors."""
        all_states = world.states()
        for state in all_states:
            has_predecessor = False
            for other in all_states:
                if other != state:
                    for succ, _ in world.successors(other):
                        if succ == state:
                            has_predecessor = True
                            break
                if has_predecessor:
                    break
            if not has_predecessor:
                return state
        return all_states[0] if all_states else ""

    def _find_terminal_state(self, world: Any) -> str:
        """Find a state with no successors."""
        all_states = world.states()
        for state in all_states:
            if not list(world.successors(state)):
                return state
        return all_states[-1] if all_states else ""

    def _find_terminal_from(self, start: str, world: Any) -> str:
        """BFS from start to find the farthest reachable terminal state."""
        from collections import deque

        visited = {start}
        queue = deque([start])
        farthest = start

        while queue:
            current = queue.popleft()
            successors = list(world.successors(current))
            if not successors:
                farthest = current
            for succ, _ in successors:
                if succ not in visited:
                    visited.add(succ)
                    queue.append(succ)
                    farthest = succ

        return farthest

    def _trace_path(self, start: str, goal: str, world: Any) -> list[tuple[str, str]]:
        """Trace the path from start to goal through the world graph."""
        from src.monkey_brain.kernel.compile.tensor import Feature

        path = []
        cur = start
        seen = set()

        for _ in range(64):
            if cur is None or cur in seen:
                break
            seen.add(cur)
            succs = {d: world.feature(cur, d, Feature.PROBABILITY) for d, _ in world.successors(cur)}
            if not succs:
                break
            actual = max(succs, key=succs.get)
            path.append((cur, actual))
            cur = actual
            if cur == goal:
                break

        return path

    def _format_world_answer(self, question: str, path: list[tuple[str, str]], world: Any) -> str:
        """Format the world graph traversal as a readable answer."""
        if not path:
            return f"No path found for '{question}'"

        lines = [f"Executed plan for '{question}':", ""]
        for i, (src, dst) in enumerate(path, 1):
            lines.append(f"  {i}. {src} → {dst}")

        reached = path[-1][1]
        lines.append("")
        lines.append(f"Final state: {reached}")
        return "\n".join(lines)


def get_executor() -> UnifiedExecutor:
    return UnifiedExecutor()
