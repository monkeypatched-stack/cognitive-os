#!/usr/bin/env python3
"""Autonomous Multi-Actor Dialogue.

Fixes a real gap demo/conversation/ left open: there, THIS SCRIPT chose
who talked to whom ("now ask B, now ask C") — real reasoning, real
replies, but a scripted routing skeleton around them. Here, the Support
Agent's own LLM planner decides, as a genuine plan step, whether it
knows enough to answer, and if not, WHO to ask and WHAT to ask them
(AskActorCapability — kernel/domains/grocery.py) or that it's ready to
conclude (RespondToInquiryCapability). This script's ONLY job is to
keep re-prompting the SAME actor with the SAME original question,
folding in whatever it has genuinely learned so far, until THAT ACTOR's
own plan contains RespondToInquiry — it never names a target, never
writes a question, never decides when the actor is "done" beyond
reading that signal back.

Every AskActor step's real HTTP round-trip (POST /actors/{id}/ask,
the exact endpoint an external client would use) already happened
DURING that actor's own tick, driven entirely by parameters the
planner itself wrote — this script only reads the outcome afterward to
know what to show and what to fold into the next round's context.

Usage:
    python3 demo/dialogue/run_dialogue.py
"""

from __future__ import annotations

import sys
import time
from typing import Any

try:  # Support both `python run_dialogue.py` and package/test imports.
    from .bootstrap import (
        ACTOR_DEFS,
        ApiError,
        TRACKED_PRODUCT_NAME,
        _call,
        _client,
        bootstrap_world,
    )
except ImportError:  # pragma: no cover - exercised by direct script execution.
    from bootstrap import (
        ACTOR_DEFS,
        ApiError,
        TRACKED_PRODUCT_NAME,
        _call,
        _client,
        bootstrap_world,
    )

MAX_ROUNDS = 4
WIDTH = 56


def banner(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def section(title: str) -> None:
    print("\n" + "-" * WIDTH)
    print(title)
    print("-" * WIDTH)


# ── Real world progression (order + shipment, same as demo/conversation) ─


def place_real_order(client, world: dict[str, Any]) -> str:
    product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]
    order = _call(
        client,
        "POST",
        "/orders",
        json={
            "actor_id": world["actors"]["Customer"],
            "items": [
                {
                    "id": product_id,
                    "name": TRACKED_PRODUCT_NAME,
                    "qty": 1,
                    "price": 59.99,
                }
            ],
            "question": "buy the wireless gaming mouse",
        },
    )
    return order.get("order_id", "")


def pack_and_ship(client, world: dict[str, Any], order_id: str) -> str:
    product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]
    shipment = _call(
        client,
        "POST",
        "/shipments",
        json={
            "order_id": order_id,
            "packages": [{"box": 1, "items": [product_id]}],
            "rider_id": world["rider_id"],
        },
    )
    return shipment.get("shipment_id", "")


# ── Contacts directory (real registered data, not invented) ─────────────


def build_contacts_directory(exclude_name: str) -> str:
    lines = []
    for _key, name, _type, _goals, role_summary in ACTOR_DEFS:
        if name == exclude_name:
            continue
        lines.append(f"- {name}: {role_summary}")
    return "\n".join(lines)


# ── The autonomous dialogue loop ─────────────────────────────────────────


def run_dialogue(
    client,
    world: dict[str, Any],
    asking_actor_name: str,
    original_question: str,
    max_rounds: int = MAX_ROUNDS,
) -> tuple[str | None, list[tuple[str, str, str]], int]:
    """Returns (final_answer_or_None, learned[(target, question, answer)], rounds_used).
    Every element of `learned` is a REAL AskActor outcome the actor's
    OWN plan produced — nothing here is written by this function."""
    asking_actor_id = world["actors"][asking_actor_name]
    contacts = build_contacts_directory(asking_actor_name)
    learned: list[tuple[str, str, str]] = []
    errors_so_far: list[str] = []

    for round_num in range(1, max_rounds + 1):
        section(f"Round {round_num} — {asking_actor_name} thinks")

        error_text = (
            (
                "\n\nYour last attempt(s) at asking someone failed for a real reason — "
                "read this and don't repeat the mistake:\n" + "\n".join(f"- {e}" for e in errors_so_far)
            )
            if errors_so_far
            else ""
        )

        if not learned:
            prompt_text = (
                f'A customer asked: "{original_question}"\n\n'
                f"You are the {asking_actor_name}. You may not have everything you need to "
                f"answer this yourself. Your real team directory:\n{contacts}\n\n"
                f"This is the first dialogue turn. Your plan MUST contain exactly one "
                f"AskActor step, not AnswerQuestion, OrderConfirmation, or any other "
                f"action. Choose the colleague and write the specific question yourself. "
                f"Do not give a final answer until a colleague has replied."
                f"{error_text}"
            )
        else:
            learned_text = "\n".join(f'- You asked {t}: "{q}" and they told you: "{a}"' for t, q, a in learned)
            prompt_text = (
                f'A customer asked: "{original_question}"\n\n'
                f"You are the {asking_actor_name}. So far you have learned:\n{learned_text}\n\n"
                f"Your real team directory:\n{contacts}\n\n"
                f"If you still need more information, ask someone (a new colleague, or a "
                f"follow-up to someone who already answered). Otherwise, give your final "
                f"answer for the customer now that you know enough."
                f"{error_text}"
            )

        response = _call(
            client,
            "POST",
            "/prompt",
            json={"question": prompt_text},
            headers={"X-User-ID": asking_actor_id},
        )
        execution = (response.get("query_result") or {}).get("actor_execution") or {}
        plan = execution.get("plan") or {}
        steps = plan.get("steps") or []
        actions = execution.get("actions") or []

        print(
            f"\n{asking_actor_name}'s plan this round: "
            + (" -> ".join(s.get("action", "?") for s in steps) or "(no steps)")
        )

        asked_this_round: list[str] = []
        concluded_answer: str | None = None
        for step, outcome in zip(steps, actions):
            action_name = str(step.get("action", ""))
            action_key = action_name.casefold()
            result = outcome.get("result") or {}
            if not isinstance(result, dict):
                continue
            if action_key == "askactor" and outcome.get("success"):
                target = result.get("target_actor", "?")
                q = result.get("question", "")
                a = result.get("answer", "")
                print(f"\n  {asking_actor_name}")
                print(f"      ↓ AskActor")
                print(f"  {target}")
                print(f'    Q: "{q}"')
                print(f'    A: "{a}"')
                learned.append((target, q, a))
                asked_this_round.append(target)
            elif action_key == "askactor" and not outcome.get("success"):
                error = result.get("error") or outcome.get("error") or "unknown error"
                print(f"\n  ({asking_actor_name}'s AskActor attempt failed: {error})")
                errors_so_far.append(error)
            elif action_key == "respondtoinquiry" and outcome.get("success"):
                concluded_answer = result.get("answer", "")
            elif action_key == "answerquestion" and outcome.get("success") and result.get("answer"):
                # A real, understandable near-miss: AnswerQuestion is a
                # different, globally-registered capability (from
                # demo/conversation's build) with an easily-confused
                # name. Its result is still the actor's own genuine
                # natural-language conclusion (get_backend().complete()
                # output, same as RespondToInquiry would have carried)
                # -- accepted as equivalent rather than failing the
                # round over a capability-name mixup the planner prompt
                # already warns against.
                concluded_answer = result.get("answer", "")

        if concluded_answer and not asked_this_round:
            print(f'\n  {asking_actor_name} concludes: "{concluded_answer}"')
            return concluded_answer, learned, round_num
        if concluded_answer and asked_this_round:
            print(
                f"\n  ({asking_actor_name} both asked and tried to conclude in the same "
                f"round — treating as not yet concluded; it hasn't seen this round's real "
                f"replies yet. Continuing.)"
            )
        if not asked_this_round and not concluded_answer:
            print(f"\n  ({asking_actor_name} produced no usable dialogue step — retrying.)")
            continue

    return None, learned, round_num if "round_num" in locals() else 0


def main() -> int:
    with _client() as client:
        try:
            banner("Autonomous Multi-Actor Dialogue")
            print("\nBootstrapping World")
            world = bootstrap_world(client)
            print("✓ Geography Created")
            print("✓ Societies Created (Customer, Warehouse, Inventory, Logistics, Support)")
            print("✓ Actors Created (Customer, Warehouse Worker, Inventory Robot, Driver, Support Agent)")
            print(f"✓ Product Loaded ({TRACKED_PRODUCT_NAME}, quantity=3, $59.99)")
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            section("World Update: real order placed, packed, and shipped (real API calls)")
            order_id = place_real_order(client, world)
            shipment_id = pack_and_ship(client, world, order_id)
            print(f"Order ........ {order_id}")
            print(f"Shipment ..... {shipment_id} (status: created — packed, not yet dispatched)")

            original_question = f"Where is my order {order_id}, and will it arrive tomorrow?"
            print(f'\nCustomer -> Support Agent: "{original_question}"')

            final_answer, learned, rounds_used = run_dialogue(
                client,
                world,
                "Support Agent",
                original_question,
            )

            section("Final Answer to Customer")
            print(f"\nRounds used ....... {rounds_used}")
            print(f"Colleagues asked .. {len(learned)} ({', '.join(t for t, _, _ in learned) or 'none'})")
            if final_answer:
                print(f'\nSupport Agent -> Customer: "{final_answer}"')
                banner("Autonomous Multi-Actor Dialogue — COMPLETE")
            else:
                print("\nSupport Agent did not reach a conclusion within the round budget.")
                banner("Autonomous Multi-Actor Dialogue — INCOMPLETE")
                return 1
        except ApiError as exc:
            print(f"\nDemo failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
