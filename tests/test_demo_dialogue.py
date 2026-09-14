from __future__ import annotations

from demo.dialogue.run_dialogue import run_dialogue
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner


class _Response:
    status_code = 200
    text = "{}"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, **kwargs: dict) -> _Response:
        self.calls.append((method, path, kwargs))
        if len(self.calls) == 1:
            return _Response(
                {
                    "query_result": {
                        "actor_execution": {
                            "plan": {"steps": [{"action": "AskActor"}]},
                            "actions": [
                                {
                                    "success": True,
                                    "result": {
                                        "target_actor": "Warehouse Worker",
                                        "question": "Has the order been dispatched?",
                                        "answer": "It is packed but not dispatched.",
                                    },
                                }
                            ],
                        }
                    },
                }
            )
        return _Response(
            {
                "query_result": {
                    "actor_execution": {
                        "plan": {"steps": [{"action": "RespondToInquiry"}]},
                        "actions": [
                            {
                                "success": True,
                                "result": {
                                    "answer": "The order is packed but has not been dispatched.",
                                },
                            }
                        ],
                    }
                },
            }
        )


def test_autonomous_dialogue_carries_real_reply_into_next_turn(capsys) -> None:
    client = _Client()
    world = {
        "actors": {"Support Agent": "support-id"},
    }

    answer, learned, rounds = run_dialogue(
        client,
        world,
        "Support Agent",
        "Where is my order?",
        max_rounds=3,
    )

    assert answer == "The order is packed but has not been dispatched."
    assert learned == [
        (
            "Warehouse Worker",
            "Has the order been dispatched?",
            "It is packed but not dispatched.",
        )
    ]
    assert rounds == 2
    assert len(client.calls) == 2
    assert client.calls[0][0:2] == ("POST", "/prompt")
    assert client.calls[0][2]["headers"] == {"X-User-ID": "support-id"}
    assert "It is packed but not dispatched." in client.calls[1][2]["json"]["question"]
    assert "Warehouse Worker" in capsys.readouterr().out


def test_planner_accepts_common_model_json_formatting_drift() -> None:
    parsed = LLMPlanner()._parse(
        'Here is the plan: {"steps": [{"action": "AskActor",}], "confidence": 0.8,}\nI hope this helps.'
    )

    assert parsed["steps"][0]["action"] == "AskActor"
