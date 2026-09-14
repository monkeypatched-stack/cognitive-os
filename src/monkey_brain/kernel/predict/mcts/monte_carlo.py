"""Monte Carlo Solver."""

from __future__ import annotations

import random

from src.monkey_brain.kernel.predict.mcts.base import ISolver, SolverClass, SolverResult


class MonteCarloSolver(ISolver):
    """Monte Carlo Tree Search for sampling-based planning.

    Implements UCB1-based tree expansion and simulation.
    """

    name = "monte_carlo"
    solver_class = SolverClass.MONTE_CARLO

    def can_solve(self, problem: dict) -> float:
        return 0.8 if problem.get("type") in ("planning", "simulation", "stochastic") else 0.1

    async def solve(self, problem: dict) -> SolverResult:
        n_simulations = problem.get("n_simulations", 100)
        horizon = problem.get("horizon", 10)
        actions = problem.get("actions", ["a1", "a2"])
        reward_fn = problem.get("reward_fn", lambda s, a: 0.0)
        transition_fn = problem.get("transition_fn", lambda s, a: s)

        if not actions:
            return SolverResult(
                solver_name=self.name,
                solver_class=self.solver_class,
                solution={"policy": None, "value": 0.0, "visits": 0},
                confidence=0.3,
                proof="No actions available — cannot plan",
            )

        initial_state = problem.get("initial_state", {})

        root = {"state": initial_state, "visits": 0, "value": 0.0, "children": {}}
        c_param = 1.414

        for _ in range(n_simulations):
            node = root
            path = []
            while node["children"]:
                best_child = None
                best_score = float("-inf")
                for action, child in node["children"].items():
                    if child["visits"] == 0:
                        best_child = (action, child)
                        break
                    exploitation = child["value"] / child["visits"]
                    import math

                    exploration = c_param * math.sqrt(2 * math.log(max(node["visits"], 1)) / child["visits"])
                    ucb = exploitation + exploration
                    if ucb > best_score:
                        best_score = ucb
                        best_child = (action, child)
                if best_child is None:
                    break
                action, child = best_child
                path.append((action, child))
                node = child

            if not node["children"]:
                for action in actions:
                    next_state = transition_fn(node["state"], action)
                    node["children"][action] = {
                        "state": next_state,
                        "visits": 0,
                        "value": 0.0,
                        "children": {},
                    }
                for action, child in node["children"].items():
                    if child["visits"] == 0:
                        path.append((action, child))
                        node = child
                        break

            state = node["state"]
            total_reward = 0.0
            for step in range(horizon):
                action = random.choice(actions)
                total_reward += reward_fn(state, action)
                state = transition_fn(state, action)

            node["visits"] += 1
            node["value"] += total_reward
            for action, child in reversed(path):
                child["visits"] += 1
                child["value"] += total_reward

        if root["children"]:
            best_action = max(root["children"], key=lambda a: root["children"][a]["visits"])
            best_visits = root["children"][best_action]["visits"] or 1
            best_value = root["children"][best_action]["value"] / best_visits
        else:
            best_action = actions[0] if actions else "none"
            best_value = 0.0

        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={
                "policy": best_action,
                "value": best_value,
                "visits": n_simulations,
            },
            confidence=min(0.7, 0.3 + 0.4 * (1 - 1 / (n_simulations**0.5))),
            proof=f"MCTS {n_simulations} simulations, best action={best_action}, value={best_value:.3f}",
        )
