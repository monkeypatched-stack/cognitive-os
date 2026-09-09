"""Goal-based classifier — uses LLM to classify questions into goals.

Replaces brittle predicate-based routing with LLM classification.
"""

from __future__ import annotations

import json
import os

from src.monkey_brain.kernel.plan.goals.goal import Goal
from src.monkey_brain.kernel.plan.goals.bootstrap import GoalBootstrap
from src.monkey_brain.kernel.config import GOAL_CLASSIFICATION_MIN_CONFIDENCE


async def classify_goal(question: str) -> Goal | None:
    """Classify a question into a Goal using LLM.

    Reads all registered goals from GoalBootstrap and asks the LLM
    which goal best matches the question.
    """

    bootstrap = GoalBootstrap()
    goals_summary = bootstrap.summary()

    # Build the system prompt with all available goals
    goals_text = "\n".join(
        f"- {name}: inputs={g['required_inputs']}, outputs={g['required_outputs']}, pipeline={g['pipeline_id']}"
        for name, g in goals_summary["goals"].items()
    )

    system_prompt = f"""You are a goal classifier for an industrial manufacturing system.

Available goals:
{goals_text}

Given a question, determine which goal best matches. Return a JSON object with:
- "goal_name": the name of the matching goal (must be one of the available goal names)
- "confidence": a number between 0 and 1

Return ONLY the JSON object, no other text."""

    user_prompt = f"Classify this question: {question}"

    # Try Ollama first, then OpenRouter
    result = await _classify_with_ollama(system_prompt, user_prompt)
    if result is None:
        result = await _classify_with_openrouter(system_prompt, user_prompt)

    if result is None:
        return None

    # Parse the result
    try:
        data = json.loads(result)
        goal_name = data.get("goal_name")
        confidence = float(data.get("confidence", 0))

        if not goal_name or confidence < GOAL_CLASSIFICATION_MIN_CONFIDENCE:
            return None

        goal = bootstrap.get_goal(goal_name)
        return goal

    except (json.JSONDecodeError, ValueError):
        return None


async def _classify_with_openrouter(system_prompt: str, user_prompt: str) -> str | None:
    """Classify using OpenRouter API."""
    import httpx

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.getenv("APP_URL", "http://localhost:8032"),
                    "X-Title": os.getenv("APP_NAME", "GraphRAG Service"),
                },
                json=payload,
            )
        if response.status_code != 200:
            return None
        data = response.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content")
        return str(answer).strip() if answer else None
    except Exception:
        return None


async def _classify_with_ollama(system_prompt: str, user_prompt: str) -> str | None:
    """Classify using Ollama API."""
    import httpx

    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL") or os.getenv("OLLAMA_DEFAULT_MODEL", "gemma3:latest")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "options": {"num_predict": 200},
                },
            )
        if response.status_code != 200:
            return None
        data = response.json()
        answer = data.get("message", {}).get("content")
        return str(answer).strip() if answer else None
    except Exception:
        return None
