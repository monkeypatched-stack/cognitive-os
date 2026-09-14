"""LLM-Enhanced Learning Module

Optional LLM integration for policy improvement analysis.
Uses Claude to analyze learning and suggest policy improvements.
"""

import asyncio
from typing import Optional, Dict, Any, List
import anthropic


class LLMEnhancedLearning:
    """Learning system enhanced with Claude API for policy analysis"""

    def __init__(self, llm_client: Optional[anthropic.Anthropic] = None):
        """Initialize with optional LLM client"""
        self.client = llm_client or anthropic.Anthropic()
        self.learning_history: List[Dict[str, Any]] = []
        self.insights_cache = {}

    async def analyze_and_improve_policy(
        self,
        actor_id: str,
        comparison_result: Dict[str, Any],
        current_policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze learning from comparison and suggest policy improvements

        Uses Claude to:
        - Analyze prediction errors
        - Identify patterns in failures
        - Suggest policy improvements
        - Recommend new strategies
        """
        cache_key = f"{actor_id}_{hash(str(comparison_result))}"
        if cache_key in self.insights_cache:
            return self.insights_cache[cache_key]

        try:
            # Prepare context for LLM
            trajectory_error = comparison_result.get("trajectory_error", 0)
            reward_error = comparison_result.get("reward_error", 0)
            model_accurate = comparison_result.get("model_accurate", False)

            # Call Claude to analyze learning
            response = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-3-5-sonnet-20241022",
                max_tokens=1500,
                system="""You are a policy learning expert. Given prediction errors and current policy,
suggest concrete improvements to the actor's policy.

Respond with JSON:
{
  "error_analysis": {
    "root_causes": ["list of identified root causes"],
    "patterns": ["recurring failure patterns"]
  },
  "policy_improvements": [
    {
      "improvement": "what to change",
      "rationale": "why this helps",
      "expected_impact": "improvement in performance",
      "implementation": "how to implement"
    }
  ],
  "learning_insights": "summary of what was learned",
  "confidence_level": 0.0-1.0,
  "recommended_strategies": [
    "alternative strategy 1",
    "alternative strategy 2"
  ]
}""",
                messages=[
                    {
                        "role": "user",
                        "content": f"""
Actor: {actor_id}
Trajectory Prediction Error: {trajectory_error:.3f}
Reward Prediction Error: {reward_error:.3f}
Model Accuracy: {"Good" if model_accurate else "Needs improvement"}

Current Policy:
{str(current_policy)}

Based on these errors, what should the actor learn and how should it improve?""",
                    }
                ],
            )

            # Parse LLM response
            import json

            response_text = response.content[0].text

            # Extract JSON from response
            start_idx = response_text.find("{")
            end_idx = response_text.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                parsed = json.loads(json_str)

                improvements = {
                    "actor_id": actor_id,
                    "error_analysis": parsed.get("error_analysis", {}),
                    "policy_improvements": parsed.get("policy_improvements", []),
                    "insights": parsed.get("learning_insights", ""),
                    "confidence": parsed.get("confidence_level", 0.5),
                    "alternatives": parsed.get("recommended_strategies", []),
                    "llm_generated": True,
                }

                # Cache and track
                self.insights_cache[cache_key] = improvements
                self.learning_history.append(improvements)

                return improvements
            else:
                # If parsing fails, return minimal response
                return self._default_analysis(actor_id, comparison_result)

        except Exception as e:
            # If LLM fails, return analysis based on error metrics
            print(f"LLM learning analysis failed: {e}. Using default analysis.")
            return self._default_analysis(actor_id, comparison_result)

    def _default_analysis(self, actor_id: str, comparison_result: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback analysis without LLM"""
        trajectory_error = comparison_result.get("trajectory_error", 0)
        reward_error = comparison_result.get("reward_error", 0)

        improvements = []
        if trajectory_error > 0.2:
            improvements.append(
                {
                    "improvement": "Improve state transition model",
                    "rationale": "Trajectory prediction error is high",
                    "expected_impact": "Better planning accuracy",
                    "implementation": "Increase training data, refine features",
                }
            )

        if reward_error > 0.1:
            improvements.append(
                {
                    "improvement": "Recalibrate reward estimation",
                    "rationale": "Reward prediction is inaccurate",
                    "expected_impact": "Better value judgments",
                    "implementation": "Recollect reward examples, adjust weights",
                }
            )

        return {
            "actor_id": actor_id,
            "policy_improvements": improvements,
            "insights": f"Trajectory error: {trajectory_error:.3f}, Reward error: {reward_error:.3f}",
            "confidence": 0.6,
            "llm_generated": False,
        }

    async def recommend_value_adjustments(
        self, actor_id: str, prediction_errors: List[float], actual_rewards: List[float]
    ) -> Dict[str, Any]:
        """Recommend specific value function adjustments using LLM"""
        try:
            # Calculate error statistics
            import statistics

            avg_error = statistics.mean(prediction_errors) if prediction_errors else 0
            max_error = max(prediction_errors) if prediction_errors else 0
            avg_actual = statistics.mean(actual_rewards) if actual_rewards else 0

            # Call Claude for specific recommendations
            response = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=[
                    {
                        "role": "user",
                        "content": f"""
Given these value prediction errors:
- Average error: {avg_error:.3f}
- Max error: {max_error:.3f}
- Average actual reward: {avg_actual:.3f}

Recommend specific adjustments to the value function weights.
Respond with JSON: {{
  "weight_adjustments": {{"weight_name": adjustment_value}},
  "learning_rate": recommended_lr,
  "regularization": regularization_strength,
  "prioritized_states": ["states to focus on"]
}}""",
                    }
                ],
            )

            response_text = response.content[0].text
            import json

            start_idx = response_text.find("{")
            end_idx = response_text.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                return json.loads(json_str)
            else:
                return self._default_adjustments()

        except Exception as e:
            print(f"Value adjustment recommendation failed: {e}")
            return self._default_adjustments()

    def _default_adjustments(self) -> Dict[str, Any]:
        """Default value adjustments without LLM"""
        return {
            "weight_adjustments": {"default": 0.01},
            "learning_rate": 0.001,
            "regularization": 0.01,
            "prioritized_states": ["high_error_states"],
        }

    def get_learning_summary(self) -> Dict[str, Any]:
        """Get summary of learning so far"""
        return {
            "total_improvements_suggested": len(self.learning_history),
            "recent_improvements": (self.learning_history[-5:] if self.learning_history else []),
            "cache_size": len(self.insights_cache),
            "llm_available": self.client is not None,
        }

    def clear_cache(self):
        """Clear insights cache"""
        self.insights_cache.clear()

    def clear_history(self):
        """Clear learning history"""
        self.learning_history.clear()
