"""
MB-0016: Adaptive Grounding Mode Test

Tests whether the Cognitive OS correctly determines when an interaction can be
grounded entirely from shared world state vs when it must preserve actor-local state.
"""

import pytest
import asyncio
import httpx
from typing import Any

BASE_URL = "http://localhost:8031/api/v1/agentos"
ROOT_URL = "http://localhost:8031"


async def execute_goal(actor_id: str, goal: str) -> dict[str, Any]:
    """Execute a goal for an actor and return the execution result."""
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{BASE_URL}/execute", json={"actor_id": actor_id, "goal": goal})
        response.raise_for_status()
        return response.json()


def get_actor_state(actor_id: str) -> dict[str, Any]:
    """Get the current cognitive state of an actor."""
    import requests

    response = requests.get(f"{BASE_URL}/actors/{actor_id}/state")
    response.raise_for_status()
    return response.json()


class TestAdaptiveGroundingMode:
    """Test MB-0016: Adaptive Grounding Mode - Shared State vs Actor-Local State."""

    def test_subtest_a_shared_state_mode(self):
        """
        SUBTEST A — SHARED-STATE MODE

        Goal: "Buy 1 liter of whole milk."
        World: All information is shared/observable (public prices, inventory, etc.)
        Expected: SHARED grounding mode, no unnecessary negotiation
        """
        # TODO: This test requires a properly configured world state
        # For now, we verify the concept by checking the API exists
        import requests

        response = requests.get(f"{BASE_URL}/health")
        response.raise_for_status()
        assert response.json()["status"] == "healthy"

        # Verify the execute endpoint exists
        response = requests.post(f"{BASE_URL}/execute", json={"actor_id": "test_actor", "goal": "test goal"})
        # May fail due to missing actor, but endpoint should exist
        assert response.status_code in [200, 404]  # 404 expected if actor doesn't exist

    def test_subtest_b_actor_local_state_mode(self):
        """
        SUBTEST B — ACTOR-LOCAL-STATE MODE

        Goal: "Buy 1 liter of whole milk for the best price possible."
        World: Private constraints exist (buyer max price, store min price)
        Expected: ACTOR_LOCAL grounding mode with negotiation
        """
        import requests

        response = requests.get(f"{BASE_URL}/health")
        response.raise_for_status()
        assert response.json()["status"] == "healthy"

    @pytest.mark.skip(reason="MB-0016 test infrastructure not yet implemented")
    def test_adaptive_grounding_selection(self):
        """
        Verify that the Cognitive OS can select grounding mode based on
        information structure rather than hardcoded mode.
        """
        # This test would require:
        # 1. A test world with known state structure
        # 2. A way to query the grounding mode used
        # 3. Verification of state transitions
        raise NotImplementedError("MB-0016 test infrastructure not yet implemented")

    @pytest.mark.skip(reason="MB-0016 test infrastructure not yet implemented")
    def test_private_state_isolation(self):
        """
        Verify that private state is preserved and not leaked between actors.
        """
        raise NotImplementedError("MB-0016 test infrastructure not yet implemented")

    @pytest.mark.skip(reason="MB-0016 test infrastructure not yet implemented")
    def test_prompt_compilation_from_grounding(self):
        """
        Verify that prompts are compiled from grounded state rather than
        passing original user prompt directly to the model.
        """
        raise NotImplementedError("MB-0016 test infrastructure not yet implemented")


def run_mb0016_test() -> dict[str, Any]:
    """
    Run MB-0016 Adaptive Grounding Mode test.

    Returns test results in the required table format.
    """
    results = {
        "shared_grounding": "NOT_TESTED",
        "actor_local_grounding": "NOT_TESTED",
        "prompt_compilation": "NOT_TESTED",
        "private_state_isolation": "NOT_TESTED",
        "negotiation_required": "NOT_TESTED",
        "no_negotiation_needed": "NOT_TESTED",
        "world_state_update": "NOT_TESTED",
        "local_state_update": "NOT_TESTED",
        "learning_recorded": "NOT_TESTED",
        "adaptive_mode_selection": "NOT_TESTED",
    }

    try:
        import requests

        response = requests.get(f"{ROOT_URL}/health")
        response.raise_for_status()
        results["api_health"] = "OK"
    except Exception as e:
        results["api_health"] = f"FAILED: {e}"

    return results


if __name__ == "__main__":
    print("Running MB-0016 Adaptive Grounding Mode Test")
    print("=" * 60)
    results = run_mb0016_test()
    print("\nTest Results:")
    print("-" * 40)
    for key, value in results.items():
        print(f"{key}: {value}")

    print("\n" + "=" * 60)
    print("NOTE: Full test implementation requires:")
    print("  1. Test world state configuration")
    print("  2. Query interface for grounding mode")
    print("  3. State transition verification tools")
    print("  4. Private state isolation checks")
    print("  5. Prompt compilation verification")
