"""LLMPlanner — a domain-agnostic planner that asks an LLM to synthesize a
plan from a goal and observed facts, instead of hardcoding scoring rules.

Centralized-planning constitution: no business decision, workflow
selection, prioritization, optimization, or sequencing may be hardcoded
anywhere — that's exclusively the planner's job. This planner formats the
goal and every fact as plain text and asks the backend to decide; it never
interprets what a fact means (no domain vocabulary, no "store:item"
parsing, no cost/travel/availability formulas) — that reasoning is the
model's job, reading the same facts a human would.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from typing import Any

from src.monkey_brain.kernel.compile.error_recovery import (
    CircuitBreaker,
    CircuitBreakerConfig,
)
from src.monkey_brain.kernel.pipeline.belief_state import Goal, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.llm_plan_cache import (
    get_cached_response,
    put_cached_response,
)

# Gate 11 (production readiness): CircuitBreaker existed (kernel/compile/
# error_recovery.py) but was only ever re-exported, never instantiated or
# called anywhere in the codebase — confirmed via grep. This is the real
# call site it belongs on: the per-actor LLM backend call this session's
# Gate 9 findings (docs/adr/016-performance-gate9.md) already identified
# as the dominant, serial, per-actor cost. Without this, a genuinely down
# or hanging backend makes every actor individually discover that the
# slow way (each one waits out its own 30s SocietyRuntime.tick_one_actor
# cap before failing); with it, once the backend has failed
# failure_threshold times in a row, subsequent calls fail in ~0ms instead
# of each waiting out their own timeout — plan()'s existing except block
# already treats any backend exception (including the breaker's own
# fast-fail RuntimeError) as an infrastructure failure and returns a
# zero-confidence Plan, so this needed no other code changes to be safe.
_llm_backend_breaker = CircuitBreaker(
    "llm_planner.backend",
    CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60),
)

logger = logging.getLogger("agentos.pipeline.llm_planner")

# See LLMPlanner.plan()'s retry loop for why this exists: bounds retries
# on a malformed-JSON PARSE failure specifically, distinct from (and much
# more limited than) the backend circuit breaker's own failure budget.
_MAX_PARSE_ATTEMPTS = 3

_SYSTEM_PROMPT = (
    "You are a planner. Given a goal and a set of observed facts about the "
    "world, produce a plan that satisfies the goal's success criteria. "
    "Reason about whatever the facts and goal imply matters — cost, "
    "distance, availability, preference, timing, or anything else present "
    "in the facts. There are no fixed weights or rules to apply; use your "
    "judgment. If the actor's active memberships/permissions (given below, "
    "when present) show they lack a permission a step would need (e.g. "
    "spending shared/household funds), do not plan that step for them — "
    "or, if you do, tag it with the required_permission string so it can "
    "be verified. When a capability exposes candidates, make the business "
    "selection yourself and pass the chosen entity IDs and quantities as "
    "parameters; capabilities must not rank or choose on your behalf. "
    'Facts and relevant knowledge below may include "id=..." for an '
    "entity — that id is the real, valid identifier to reference: put it "
    'in that step\'s "parameters" field as '
    '{"selection": [{"id": "<the id>", "qty": 1}]} (qty defaults '
    'to 1 if not stated). Leave "parameters" as {} for a step with '
    "nothing to select. "
    "If 'AskActor' is in the available actions and you genuinely lack "
    "information you need from a specific colleague to proceed, use it "
    'with parameters {"target_actor": "<their actor_id>", '
    '"question": "<your own natural-language question to them>"} — '
    "write a real, specific question, not a placeholder. When the person "
    'you want appears in the "Reachable colleagues" list below, '
    '"target_actor" MUST be their exact actor_id from that list (copy it '
    'verbatim, e.g. "a1b2c3d4..."), NOT their name — a name is easy to '
    "abbreviate or misspell, an actor_id is exact and can never be "
    'ambiguous. Only fall back to a plain role name (e.g. "Driver") when '
    "addressing a role rather than a specific listed person, and never use "
    'an entity id like "id=..." or a bare id string such as '
    '"rider_ab12..." or "product_ab12..." you may see elsewhere in the '
    "facts below — those identify things, not who to ask. "
    "AskActor only reaches someone you share a real affiliation or society "
    "with — naming someone unreachable is not silently ignored, it is "
    "DENIED, and the real reason becomes a fact for your next round (act "
    "on it: ask someone you can actually reach, or explain the denial to "
    "whoever needs to know). "
    "If 'DelegateTask' is in the available actions and you need a specific "
    "reachable colleague to actually DO something (not just answer a "
    'question — e.g. "have Raj pick up the order"), use it with '
    'parameters {"target_actor": "<their exact actor_id from Reachable '
    'colleagues, same rule as AskActor>", "tasks": [{"capability": '
    '"<a real action name from Available actions>", "parameters": {}, '
    '"depends_on": []}]} — "tasks" is a list because a delegation can '
    "be a real multi-step chain, not just a single action; each entry's "
    'own "capability"/"parameters"/"depends_on" follow the exact '
    "same rules as a normal plan step. When the delegated work is a "
    'purchase ("have Raj pick up eggs and milk"), "tasks" MUST be the '
    "exact same chain, in the exact same order, as a normal purchase plan "
    "below — starting with 'ProductSelection', never skipping straight to "
    "'OrderCreation' (found live, repeatedly: a delegated purchase chain "
    "starting at 'OrderCreation' with no product ever selected, so it "
    'fails immediately with "no products selected"). Example — '
    '"delegate picking up bananas to Raj": {"target_actor": "<Raj\'s '
    'actor_id>", "tasks": [{"capability": "ProductSelection", '
    '"parameters": {"selection": [{"id": "<bananas id>", "qty": '
    '1}]}, "depends_on": []}, {"capability": "OrderCreation", '
    '"parameters": {}, "depends_on": [0]}, {"capability": '
    '"PaymentConfirmation", "parameters": {}, "depends_on": [1]}, '
    '{"capability": "Payment", "parameters": {}, "depends_on": [2]}, '
    '{"capability": "OrderConfirmation", "parameters": {}, '
    '"depends_on": [3]}]. '
    "If 'BroadcastToAffiliation' is in the "
    "available actions and your request is for WHOEVER is available in a "
    'group rather than one specific named colleague (e.g. "can someone '
    'help pack this order", "everyone in the warehouse stop"), use it '
    'instead of guessing a name — parameters {"message": "<your own '
    'natural-language request>"}; it reaches every real, currently-'
    "eligible participant, not just one. "
    "If 'RespondToInquiry' "
    "is in the available actions and you already have enough information "
    "(including anything a colleague told you in a fact below, e.g. "
    '"X told you: ...") to give a final answer, use it with parameters '
    '{"answer": "<your own natural-language answer, written for the '
    'person who asked>"} — use exactly this action name, "RespondToInquiry", '
    "to conclude a dialogue like this one; a different, similarly-named "
    'action such as "AnswerQuestion" (if listed) is unrelated and will '
    "NOT deliver your answer to the person waiting for it. "
    "Do not use AskActor and RespondToInquiry in the "
    "SAME plan — you have not seen an AskActor reply yet when you write "
    "this plan, so wait for it (it will appear as a fact next time you're "
    "asked) before responding. Talking to a colleague or answering someone "
    'needs no special permission — leave "required_permission" empty '
    '("") for AskActor, BroadcastToAffiliation, RespondToInquiry, '
    "EvaluateStrategy, CompeteForResource, and RecordAgreement steps "
    "specifically, even if "
    "you would tag a permission on other steps in the same plan. "
    "If 'EvaluateStrategy' is listed and you have real, distinct options to "
    "weigh (not just one obvious choice), use it with parameters "
    '{"candidates": [{"name": "...", "attributes": {"cost": -12, '
    '"speed": 0.9}}, ...]} — one entry per real option, using real '
    "numbers from the facts (negate cost/duration-like numbers so higher "
    "is always better); it returns real utility scores as a fact for your "
    "NEXT round, it does not choose for you. If 'CompeteForResource' is "
    "listed and you need a scarce resource another actor might also want, "
    'use it with parameters {"resource_id": "<the real id>", "qty": 1} '
    '— use exactly this action name, "CompeteForResource", for this; a '
    'different, similarly-purposed action such as "InventoryReserve" (if '
    "listed) is a separate, unrelated capability. It tells you the real "
    "win/lose outcome as a fact; explain that real outcome yourself "
    "afterward, don't assume you won. Leave this step's "
    '"required_permission" empty ("") — as already said above, '
    "CompeteForResource is one of the actions that never needs one. "
    "If 'RecordAgreement' "
    "is listed and a negotiation you were part of just concluded, use it "
    'with parameters {"entity_id": "<the real order/shipment id>", '
    '"agreement": {"with": "<who>", "terms": "<what was agreed, in '
    'your own words>"}} to make it persistent. '
    "If 'GetAgreements' is listed and you need to recall what was agreed "
    "on an order/shipment from an earlier round — yours or a "
    'counterparty\'s — use it with parameters {"entity_id": "<the real '
    'order/shipment id>"}; it returns the real, previously-recorded '
    "agreements as a fact for your NEXT round, it does not summarize or "
    'invent what was agreed. Leave this step\'s "required_permission" '
    'empty ("") too — it\'s a read, like EvaluateStrategy. '
    "If 'ReportWorldPerturbation' is listed and the goal is reporting a "
    "real change to a store/product's own state (a fire, a stockout, a "
    "closure — not a new purchase), use it with parameters "
    '{"entity_id": "<the real id from a fact above>", "description": '
    '"<what happened, in your own words>", "impact_attributes": '
    '{"<the real attribute this changes>": <its new value>}} — e.g. '
    'reporting a product out of stock is {"quantity": 0}. '
    "impact_attributes must be a non-empty dict of REAL attribute names "
    'you can see on that entity in the facts (e.g. "quantity", '
    '"is_open"), never an invented field name. '
    "If 'Takeoff', 'Waypoint', and/or 'Land' are in the available actions "
    "(a robot/drone actor), extract the REAL numbers the user actually "
    'gave you and put them in that step\'s own "parameters" — do NOT '
    'leave "parameters" as {} for these the way you would for an '
    "action that genuinely takes none (like 'OrderCreation' above); a "
    "flight command with no altitude/coordinates is not the same kind of "
    "step. 'Takeoff' takes {\"height_m\": <target altitude in meters, "
    'from what the user said, e.g. "take off to 15 meters" means '
    "height_m=15; default 2.0 only if truly unspecified>}. 'Waypoint' "
    'takes {"x": <target x offset in meters>, "y": <target y offset '
    'in meters>, "height_m": <altitude to fly at, carry forward the '
    'same value Takeoff used unless told otherwise>} — e.g. "fly to '
    'coordinate x=5, y=5" means {"x": 5, "y": 5, ...}, copied '
    "verbatim, never left at 0 when the user named a real number. A "
    'goal naming TWO destinations ("fly to x=5,y=5, then fly back to '
    "x=0,y=0\") is TWO separate 'Waypoint' steps, each with its OWN "
    "correct x/y — the second step's parameters are NOT a copy of the "
    "first's. 'Land' takes {} (no parameters, like 'OrderCreation' "
    "above — it lands at the current position, wherever that is). "
    "If 'NegotiatePrice' is "
    "listed and a real price is being bargained over, use it with real "
    'numbers as parameters {"listed_price": ..., "min_seller_price": ..., '
    '"buyer_target_price": ...} — it computes the real agreed price for '
    "you (never invent a negotiated price yourself; use this action so it's "
    "mathematically bounded by the real floor/ceiling). If the goal ALSO "
    'wants to actually buy that product after haggling ("negotiate the '
    'price of milk and then buy it"), add "product_id": "<the real '
    "product id you're negotiating over>\" to NegotiatePrice's parameters "
    "and place it AFTER 'ProductSelection' (so the product and its real "
    "listed price are already known) and BEFORE 'OrderCreation' — a "
    "successful deal then becomes the real price OrderCreation actually "
    "charges for that item, not just a number reported back to you. "
    'Worked example — "negotiate milk down from its listed price, floor '
    '$3.00, then buy it":\n'
    '[{"action": "ProductSelection", "parameters": {"selection": '
    '[{"id": "<milk id>", "qty": 1}]}, "depends_on": []}, '
    '{"action": "NegotiatePrice", "parameters": {"listed_price": <milk\'s '
    'real listed price>, "min_seller_price": 3.00, "buyer_target_price": '
    '<your opening offer>, "product_id": "<milk id>"}, "depends_on": [0]}, '
    '{"action": "OrderCreation", "parameters": {}, "depends_on": [1]}, '
    '{"action": "PaymentConfirmation", "parameters": {}, "depends_on": [2]}, '
    '{"action": "Payment", "parameters": {}, "depends_on": [3]}, '
    '{"action": "OrderConfirmation", "parameters": {}, "depends_on": [4]}]\n'
    'Omit "product_id" only when the goal is purely about the '
    "negotiation itself, with no purchase to follow. If 'NegotiateTerms' "
    "is listed and you're bargaining over a real non-price number (a delay "
    "in hours, a priority score), use it with real numbers as parameters "
    '{"high_side_opening": ..., "high_side_floor": ..., '
    '"low_side_opening": ...} — the side with a real floor it won\'t go '
    'below is "high_side", the other is "low_side"; it computes the '
    "real bounded outcome. "
    "If 'Counterfactual' is listed and the question asks \"what if\" "
    "(a store closing, a price changing) or asks you to compare buying "
    "options, use it alone as a single-step plan with parameters {} — it "
    "reads your question itself, you supply nothing. Never combine it "
    "with a purchase chain: asking what-if must never itself buy "
    "anything. If 'Explain' is listed and the question asks why a PAST "
    'purchase was made the way it was ("why did you choose X"), use it '
    "alone with parameters {} — it reads the real decision trace from "
    "that past order, never a fresh guess. If 'Nutrition' is listed and "
    'the goal is meeting a nutrition/macro target ("I need 120g protein '
    'this week") rather than naming specific products, use it alone with '
    "parameters {} — it builds the real shopping list itself from actual "
    "product nutrition data; do not also plan 'ProductSelection' steps "
    "for this. "
    "If 'CancelOrder', 'ReturnOrder', 'ApproveReturn', or 'RefundOrder' is "
    "listed and the goal is about an order that already exists (not a new "
    "purchase), find that real order's id in the facts below — look for a "
    'line like "Order ORD-... created" — and use parameters '
    '{"order_id": "<that real order id>"} (add "reason": "<why>" for '
    'ReturnOrder/RefundOrder, or "amount": <a real number> for a partial '
    'RefundOrder). The order id always starts with "ORD-"; it is never '
    "the actor's own id, a product id, or a payment/delivery id, even "
    "when those appear nearby in the same facts. "
    "If the goal is to buy/order/purchase a specific product (not a "
    "negotiation or a question), and 'ProductSelection', 'OrderCreation', "
    "'PaymentConfirmation', 'Payment', 'OrderConfirmation', and/or "
    "'Delivery' are listed, prefer THESE over the negotiation/dialogue "
    "actions above — that's what they're for. If the request explicitly "
    'says it\'s acting "on behalf of" someone else (a different named '
    "person, not the actor making the request) and 'DelegationCheck' is "
    "listed, put it FIRST, before everything else, with parameters {} — "
    "it verifies a real, currently-active permission to act for that "
    "person and denies the whole request if none exists; never skip it "
    'for an explicit "on behalf of X" request just because you assume '
    'the delegation is fine. Leave this step\'s "required_permission" '
    'empty ("") — it\'s a security gate, not a business action. '
    "If 'HouseholdCognition' "
    "and/or 'SocialSourcing' are ALSO listed, put them FIRST, before "
    "'ProductSelection' (parameters {} for both — they read the goal "
    "themselves, you supply nothing) — they check whether the household "
    "already has enough at home, or can borrow/negotiate it from someone "
    "nearby, before ProductSelection buys it from a store; skip whichever "
    "of the two isn't in the 'Available actions' list. Real order, one "
    "step each, skip whichever don't apply: 'ProductSelection' (pick the real "
    'product, parameters {"selection": [{"id": "<id from a fact '
    'above>", "qty": 1}]}, as already described above — see Compound '
    "Goal Decomposition below for requests naming more than one distinct "
    "product) -> "
    "'OrderCreation' (parameters {}, it reads the selection you just made) "
    "-> 'PaymentConfirmation' (parameters {}, checks the order can "
    "actually be paid for) -> 'Payment' (parameters {}, actually charges "
    "it — a separate step from PaymentConfirmation, both belong in the "
    "same plan) -> 'OrderConfirmation' (parameters {}, finalizes the "
    "order) -> 'Delivery' (parameters {}, arranges pickup/delivery, only "
    "if the goal implies delivery rather than pickup). 'InventoryReserve' "
    "is NOT a step you plan — it happens automatically on the inventory "
    "side once an order exists; do not include it. "
    'Worked example — "buy bananas" (single product, exactly this '
    'shape, only "id" and "qty" differ for a different product):\n'
    '[{"action": "ProductSelection", "parameters": {"selection": '
    '[{"id": "<bananas id>", "qty": 1}]}, "depends_on": []}, '
    '{"action": "OrderCreation", "parameters": {}, "depends_on": [0]}, '
    '{"action": "PaymentConfirmation", "parameters": {}, "depends_on": [1]}, '
    '{"action": "Payment", "parameters": {}, "depends_on": [2]}, '
    '{"action": "OrderConfirmation", "parameters": {}, "depends_on": [3]}, '
    '{"action": "Delivery", "parameters": {}, "depends_on": [4]}]\n'
    "Found live, repeatedly: 'OrderCreation' silently dropped and "
    "'OrderConfirmation' appearing twice instead (once in OrderCreation's "
    "own position, again in its real position) — a real order can never "
    "be confirmed before it exists, so this specific mistake breaks "
    "every step after it. Before finishing your answer, check your own "
    "steps list against the worked example above: 'OrderCreation' must "
    "appear exactly once, immediately after 'ProductSelection', and "
    "'OrderConfirmation' must appear exactly once, immediately before "
    "'Delivery' (or last, if there is no Delivery step) — never earlier. "
    "When a list of 'Available actions' is given below, each step's "
    '"action" field MUST be exactly one of those names, verbatim — '
    "steps using an action outside that list cannot actually execute. "
    "If no such list is given, choose whatever action name best "
    "describes the step. "
    '"action" is an internal capability name (implementation detail); '
    '"description" is what a person following this plan should see. '
    'Write "description" in plain business language describing WHAT '
    'the step accomplishes for the goal (e.g. "Check if the product is '
    'in stock", "Reserve one unit for this order", "Notify the '
    'warehouse team") — never the action name itself, an internal '
    "mechanism, or how it's implemented (e.g. do not write things like "
    '"Call EvaluateStrategy" or "Broadcast to affiliation group"). '
    "Optionally, if one step can only meaningfully happen after another "
    "specific step in THIS SAME plan succeeds (not just because it's "
    "listed later), list that other step's 0-based position in "
    '"depends_on" (e.g. a payment step that requires an earlier order- '
    'creation step to have succeeded: "depends_on": [1] if order '
    'creation is steps[1]). Leave "depends_on" empty (the default) for '
    "an ordinary sequential step with no special prerequisite beyond "
    "normal plan order. "
    "ONE ACTION = ONE STEP. NEVER put two different items/actions in one "
    'step. "Buy milk and pizza" names TWO actions, not one — do not '
    "write a single 'ProductSelection' step with both milk and pizza in "
    "its \"selection\" list; write TWO 'ProductSelection' steps, one per "
    "item. This applies to every kind of action, not just "
    "'ProductSelection'. "
    'Worked example — "buy pizza and then buy milk" becomes:\n'
    '[{"action": "ProductSelection", "description": "Select pizza", '
    '"parameters": {"selection": [{"id": "<pizza id>", "qty": 1}]}, '
    '"depends_on": []}, '
    '{"action": "ProductSelection", "description": "Select milk", '
    '"parameters": {"selection": [{"id": "<milk id>", "qty": 1}]}, '
    '"depends_on": [0]}]\n'
    "Step order always matches the order the user named the actions in "
    "(pizza named first above -> pizza is step 0). The step for whichever "
    'action was named SECOND gets "depends_on": [<index of the step '
    'named first>] — writing the order in "description" is not enough, '
    '"depends_on" is the only field that actually enforces it. "Buy '
    'milk and pizza" is the same pattern with milk and pizza swapped '
    '(milk is step 0, pizza is step 1 with "depends_on": [0]). ONLY '
    'skip "depends_on" (leave every step\'s "depends_on": []) when the '
    "user says the actions are independent/separate/parallel/at the same "
    'time, e.g. "buy milk and pizza independently". '
    '"cost" and "confidence" below are placeholders showing the '
    "field's TYPE, not values to copy verbatim — cost is your own real "
    "estimate (0.0 only when a step genuinely costs nothing, e.g. "
    "AskActor), and confidence is your own real estimate of how likely "
    "THIS SPECIFIC step is to succeed, generally well above 0 for an "
    "ordinary, well-formed step (0.7-0.95 is typical); leaving every "
    "step's confidence at exactly 0.0 reads as \"I have no idea if any "
    'of this will work" and gets the whole plan rejected outright, '
    "even when the steps themselves are otherwise correct. "
    "Respond with ONLY a JSON object of this shape, no other "
    "text:\n"
    '{"steps": [{"action": "...", "description": "...", '
    '"expected_outcome": "...", "cost": <your real cost estimate>, '
    '"confidence": <your real 0.0-1.0 confidence for this step>, '
    '"required_permission": "resource:action or empty if none needed", '
    '"parameters": {}, "depends_on": []}], '
    '"summary": "...", "confidence": <your real overall confidence>}'
)

_NO_PERMISSION_SYNONYMS = frozenset(
    {
        "none",
        "n/a",
        "na",
        "null",
        "no permission needed",
        "no permission required",
        "not needed",
        "not required",
        "empty",
    }
)


def _normalize_depends_on(value: Any, *, own_index: int, step_count: int) -> tuple[int, ...]:
    """Same boundary role as _normalize_required_permission above: the
    model's free-text JSON becomes a structured PlanStep.depends_on right
    here. Deliberately conservative — an invalid reference (out of range,
    non-integer, or a step depending on itself) is DROPPED, not raised or
    clamped, so a confused model can never corrupt a plan's dependency
    graph into something risk.py would mis-evaluate; dropping just means
    that one entry falls back to no-explicit-dependency (this system's
    existing, safe default), never a fabricated one."""
    if not isinstance(value, list):
        return ()
    seen: list[int] = []
    for entry in value:
        if not isinstance(entry, int) or isinstance(entry, bool):
            continue
        if entry < 0 or entry >= step_count or entry == own_index:
            continue
        if entry not in seen:
            seen.append(entry)
    return tuple(seen)


def _normalize_required_permission(value: Any) -> str:
    """The system prompt asks for "empty if none needed", but a model
    doesn't always return a true empty string for that — it commonly
    writes the word "none" (or a close synonym) instead. This is the
    boundary where the model's free-text JSON becomes a structured
    PlanStep, so it's normalized right here: belief_runtime.py's
    execution-stage gate (`if step.required_permission: ...`) treats any
    non-empty string as a real permission requirement, and a stray
    "none" would otherwise deny every step of an unpermissioned plan.

    MB-3060: a weaker model sometimes echoes the JSON schema's own
    placeholder text back verbatim — "resource:delivery or empty if
    none needed" — substituting only the "action" word, not realizing
    "or empty if none needed" was instructional, not literal content to
    reproduce. A real permission string would never legitimately end
    with that exact phrase, so it's stripped the same way a bare "none"
    is. Same root cause, observed live in separate runs, with different
    literal echoes each time: "empty" (the schema placeholder's other
    half) and "none granted" (echoed from the "Active memberships:
    ... permissions=(none granted)" fact line the prompt renders when
    an actor genuinely has no granted permissions). Enumerating every
    string a model might echo doesn't scale, so the real backstop below
    is structural: every legitimate value is "resource:action" (the
    format this same system prompt asks for) — anything without that
    colon cannot be a real permission string, full stop, regardless of
    the exact wording. _NO_PERMISSION_SYNONYMS still catches the most
    common cases first since it's cheaper and self-documenting, but the
    colon check is what actually closes the class of bug.
    """
    text = str(value or "").strip()
    if text.lower() in _NO_PERMISSION_SYNONYMS:
        return ""
    if text.lower().endswith("or empty if none needed"):
        return ""
    if ":" not in text:
        return ""
    return text


_HEIGHT_METERS_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)\s*m(?:eter)?s?\b", re.IGNORECASE)
_XY_COORDINATE_PATTERN = re.compile(
    r"x\s*=\s*(-?\d+(?:\.\d+)?)\s*,?\s*y\s*=\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _backfill_px4_parameters(steps: tuple[PlanStep, ...], source_text: str) -> tuple[PlanStep, ...]:
    """Same boundary-normalization principle as _normalize_required_
    permission/_normalize_depends_on above, for a different failure mode:
    confirmed live, repeatedly, against gemma-3-4b-it that adding explicit
    "Takeoff takes {height_m: ...}, Waypoint takes {x, y, height_m: ...}"
    guidance to the system prompt (matching every other capability's own
    worked example) was NOT enough on its own — the model reliably names
    the right actions in the right order, but still leaves "parameters"
    at {} (or repeats the same x=0,y=0 for every Waypoint step) even when
    the user's own request named real numbers explicitly, e.g. "fly to
    x=5, y=5" flew nowhere and "take off to a height of 15 meters" only
    reached ~1.7m (Takeoff's own hardcoded 2.0m default). Unlike
    required_permission's fix (a fixed exemption set), there's no
    action-invariant constant to fall back to here -- the real values are
    request-specific -- so this extracts them straight from the ORIGINAL
    request text instead of trusting the model's own "parameters" field,
    the same "verify structurally, don't just hope the prompt worked"
    philosophy as every other boundary-normalization function in this
    file.

    Only backfills a step whose OWN parameters are still at the
    structural default (height_m<=0, or x==0 and y==0 both) — a model
    that DID correctly populate real values is never overridden. Multiple
    "x=.. y=.." pairs in the text are consumed in order, one per Waypoint
    step that needs one (own_index tracks position), matching the
    system prompt's own "TWO separate 'Waypoint' steps" guidance for a
    there-and-back mission.
    """
    xy_pairs = _XY_COORDINATE_PATTERN.findall(source_text)
    heights = [float(m) for m in _HEIGHT_METERS_PATTERN.findall(source_text)]
    default_height = heights[0] if heights else None

    xy_index = 0
    new_steps = []
    for step in steps:
        if step.action not in ("Takeoff", "Waypoint"):
            new_steps.append(step)
            continue
        params = dict(step.parameters)
        changed = False
        if step.action == "Waypoint":
            x = float(params.get("x", 0.0) or 0.0)
            y = float(params.get("y", 0.0) or 0.0)
            if xy_index < len(xy_pairs):
                if x == 0.0 and y == 0.0:
                    x_str, y_str = xy_pairs[xy_index]
                    params["x"] = float(x_str)
                    params["y"] = float(y_str)
                    changed = True
                xy_index += 1
        height_m = float(params.get("height_m", 0.0) or 0.0)
        if height_m <= 0.0 and default_height is not None:
            params["height_m"] = default_height
            changed = True
        if changed:
            step = replace(step, parameters=params)
        new_steps.append(step)
    return tuple(new_steps)


class LLMPlanner:
    """The PlanningEngine (kernel/pipeline/planner.py::PlanningEngine
    Protocol) — dual-accepting a PlanningContext or the legacy (belief,
    goal[, context]) shape — so it's a drop-in wherever one is
    constructed, e.g. CognitiveRuntime(planning_engine=LLMPlanner()).
    """

    def __init__(self, backend: Any = None) -> None:
        if backend is None:
            from src.monkey_brain.kernel.execute.provider.model_backend import (
                get_backend,
            )

            backend = get_backend()
        self._backend = backend

    async def plan(
        self,
        context_or_belief: Any,
        goal: Goal | None = None,
        runtime_context: Any = None,
    ) -> Plan:
        """Dual-accepting call shape:

            plan(planning_context)
            plan(belief, goal, context=None)

        Async because the backend call inside (ModelBackend.complete()) is
        a real awaited I/O call for the Ollama provider, not a synchronous
        one hidden behind asyncio.to_thread -- see model_backend.py's
        module docstring for why that distinction is the actual fix for
        the timed-out-tick-keeps-running-anyway problem docs/adr/
        019-runtime-performance-audit.md's investigation found live.
        Callers: kernel/pipeline/belief_runtime.py::_generate_plan
        (awaits directly when the injected planning_engine is itself
        async, detected via asyncio.iscoroutinefunction — see there) and
        kernel/pipeline/planning/deja_vu.py (bridges via asyncio.run()
        since it runs inside its own worker thread with no event loop).
        """
        from src.monkey_brain.kernel.pipeline.planning.domain import PlanningContext

        if isinstance(context_or_belief, PlanningContext):
            context = context_or_belief
        else:
            context = PlanningContext.from_legacy(context_or_belief, goal, runtime_context)

        resolved_goal = context.goal
        goal_id = ""
        if resolved_goal is not None:
            goal_id = getattr(resolved_goal, "goal_id", "") or str(context.metadata.get("execution_id", "") or "")
        if resolved_goal is None or not getattr(resolved_goal, "name", ""):
            return Plan(goal="", confidence=0.0, planner="llm", metadata={"goal_id": goal_id})

        # Operator-activated promoted plans bypass LLM — deterministic replay
        # of a verified recipe. Learning never activates; see
        # capability_promotion.activate_promoted_capability().
        from src.monkey_brain.kernel.pipeline.learning.capability_promotion import (
            try_resolve_promoted_plan,
        )
        from src.monkey_brain.kernel.pipeline.learning.domain import (
            scoped_goal_signature,
        )

        # Must match learning/phi.py's _goal_signature exactly (same
        # scoped_goal_signature call) — that's the key CapabilityPromotionTracker
        # actually promoted candidates under. tenant_id read the same way
        # capture.py's Provenance does: state.belief.tenant_id, stashed here as
        # context.metadata["_legacy_belief"].
        legacy_belief = context.metadata.get("_legacy_belief")
        tenant_id = getattr(legacy_belief, "tenant_id", "") or "default"
        promoted_plan = try_resolve_promoted_plan(scoped_goal_signature(resolved_goal.name, tenant_id))
        if promoted_plan is not None:
            if goal_id and not promoted_plan.metadata.get("goal_id"):
                promoted_plan = Plan(
                    goal=promoted_plan.goal,
                    steps=promoted_plan.steps,
                    confidence=promoted_plan.confidence,
                    planner=promoted_plan.planner,
                    metadata={**promoted_plan.metadata, "goal_id": goal_id},
                )
            return promoted_plan

        facts = list(getattr(legacy_belief, "facts", ())) if legacy_belief is not None else []

        # Performance analysis instrumentation only (measurement, not a
        # behavior change) -- separates prompt-construction/LLM-call/parse
        # time within this one planner call, since belief_runtime.py's own
        # asyncio.to_thread wrapper only sees the whole thing as one blob.
        # Stashed on context.metadata (mutable dict, already used for
        # _planner_prompt/_prompt_tokens the same way) so
        # belief_runtime.py::_generate_plan can read it back after this
        # to_thread call returns.
        stage_timings_ms: dict[str, float] = {}
        metadata = context.metadata if isinstance(getattr(context, "metadata", None), dict) else None

        # Semantic (goal-similarity) cache, checked BEFORE the exact-match
        # llm_plan_cache below and before even building the prompt — a hit
        # here skips LLM planning entirely, including the (still real,
        # still slow) exact-match cache path for a byte-different prompt.
        # get_moss_plan_cache() returns None outright when MOSS_PROJECT_ID/
        # KEY aren't configured, so this is a no-op cost when Moss isn't in
        # use. See moss_plan_cache.py's own module docstring for why this
        # is deliberately approximate (a similarity match, not an exact
        # one) and why that trade-off is acceptable for a cache.
        # Lazy import, same convention this file already uses for
        # kernel.pipeline.planning.* (see PlanningContext/
        # try_resolve_promoted_plan above): kernel.pipeline.planning's own
        # __init__.py imports LLMPlanner (via .integration), so a
        # module-level import here would be a circular import back into
        # this not-yet-fully-defined module.
        import os

        from src.monkey_brain.kernel.pipeline.planning.moss_plan_cache import (
            get_moss_plan_cache,
        )

        # Confirmed live, and genuinely dangerous, not just wasteful: Moss
        # matches on GOAL SEMANTIC SIMILARITY, which is exactly right for a
        # grocery-style goal (two differently-worded requests to buy milk
        # SHOULD reuse the same plan) but wrong for a robot/drone mission
        # carrying explicit safety-critical numeric parameters in the goal
        # text itself -- "fly to x=5, y=5" and "fly to x=10, y=20" embed as
        # nearly identical goals (same intent, different numbers) despite
        # needing completely different execution. A stale semantic hit
        # from an EARLIER mission's cached plan silently replayed the
        # WRONG coordinates/altitude here -- the drone never actually
        # left the origin despite this exact request's own freshly-
        # generated plan (confirmed correct via direct inspection) having
        # the right x=5/y=5/height_m=15 values the whole time; the cache
        # hit just never let that correct plan get used. Skipped entirely
        # (both read and the store_plan write below) for a robot-class
        # process — same ACTOR_NODE_CLASS convention context_engine.py's
        # own domain-scoping already uses.
        moss_cache = get_moss_plan_cache() if os.environ.get("ACTOR_NODE_CLASS", "cloud") != "robot" else None
        facts_text = "; ".join(str(getattr(f, "description", "") or getattr(f, "entity", "")) for f in facts)
        if moss_cache is not None:
            cached_plan = await moss_cache.get_similar_plan(resolved_goal.name, facts_text)
            if cached_plan is not None:
                if metadata is not None:
                    metadata["_moss_cache_hit"] = True
                cached_summary = ""
                if isinstance(cached_plan.metadata, dict):
                    cached_summary = str(cached_plan.metadata.get("summary", ""))
                return Plan(
                    goal=resolved_goal.name,
                    steps=cached_plan.steps,
                    expected_outcomes=cached_plan.expected_outcomes,
                    confidence=cached_plan.confidence,
                    risk=cached_plan.risk,
                    goal_state=resolved_goal.name,
                    planner="llm",
                    metadata={
                        "summary": cached_summary,
                        "goal_id": goal_id,
                        "moss_cache_hit": True,
                    },
                )

        prompt_build_started = time.perf_counter()
        prompt = self._build_prompt(resolved_goal, facts, context)
        stage_timings_ms["prompt_build_ms"] = round((time.perf_counter() - prompt_build_started) * 1000, 3)
        # Keep the exact input beside the execution-time context artifact.
        # The debugger must not regenerate or approximate this later.
        if metadata is not None:
            metadata["_planner_prompt"] = prompt
            metadata["_prompt_tokens"] = len(prompt.split())
        logger.info("[llm_planner] prompt for goal=%r:\n%s", resolved_goal.name, prompt)

        # A backend call failure (timeout, connection error, the circuit
        # breaker's own fast-fail) is an infrastructure concern — fail
        # closed immediately, same as always, no retry (retrying past an
        # open breaker would defeat its purpose). A malformed-JSON PARSE
        # failure is different: observed live, repeatedly, against a real
        # local model — the same prompt sampled again commonly comes back
        # well-formed, so a couple of bounded retries turns a real
        # flakiness rate (~1 in 5-8 calls, seen in production logs) into a
        # near-zero one, without masking a genuinely broken backend (that
        # still fails on the backend-call branch above, not here).
        parsed: dict | None = None
        parse_error: Exception | None = None
        llm_call_ms = 0.0
        llm_call_count = 0
        response_parse_ms = 0.0
        # Minimal Lemon metrics layer: one llm.calls.total/llm.call.duration_ms
        # per real backend attempt (not per plan() call — the retry loop
        # below can call the backend up to _MAX_PARSE_ATTEMPTS times for one
        # planning request, and "how often are LLM calls made" means the
        # real call count). provider/model are getattr-defaulted since
        # self._backend can be a test/fake backend with neither attribute.
        from src.monkey_brain.kernel.compile import _obs

        llm_provider = getattr(self._backend, "_provider", "unknown")
        llm_model = getattr(self._backend, "_model", "unknown")
        for attempt in range(_MAX_PARSE_ATTEMPTS):
            # Cache is only ever checked on the FIRST attempt of a given
            # plan() call. This is deliberately not "check every attempt":
            # llm_plan_cache only ever writes a raw response that already
            # parsed successfully (see its own module docstring), so a
            # cache hit here is guaranteed to parse the same way it did
            # when cached (same input, same deterministic parser) — a
            # later attempt existing at all means the FIRST one (real or
            # cached) already failed to parse, so re-checking the cache
            # would just replay that same failure forever instead of
            # letting the retry get a fresh sample.
            cached_raw = get_cached_response(llm_model, _SYSTEM_PROMPT, prompt) if attempt == 0 else None
            llm_call_started = time.perf_counter()
            this_call_ms = 0.0  # stays 0.0 for a cache hit — no real call was made to time
            if cached_raw is not None:
                raw = cached_raw
                logger.info(
                    "[llm_planner] cache hit for goal=%r — skipping backend call",
                    resolved_goal.name,
                )
            else:
                try:
                    raw = await _llm_backend_breaker.acall(self._backend.complete, prompt, system=_SYSTEM_PROMPT)
                except Exception as exc:
                    this_call_ms = (time.perf_counter() - llm_call_started) * 1000
                    llm_call_ms += this_call_ms
                    llm_call_count += 1
                    _obs.counter(
                        "llm.calls.total",
                        provider=llm_provider,
                        model=llm_model,
                        operation="planning",
                        status="error",
                    )
                    _obs.histogram(
                        "llm.call.duration_ms",
                        this_call_ms,
                        provider=llm_provider,
                        model=llm_model,
                        operation="planning",
                    )
                    logger.warning("[llm_planner] planning failed: %s", exc)
                    stage_timings_ms["llm_call_ms"] = round(llm_call_ms, 3)
                    stage_timings_ms["llm_call_count"] = llm_call_count
                    stage_timings_ms["response_parse_ms"] = round(response_parse_ms, 3)
                    if metadata is not None:
                        metadata["_stage_timings_ms"] = stage_timings_ms
                    return Plan(
                        goal=resolved_goal.name,
                        confidence=0.0,
                        planner="llm",
                        metadata={"error": str(exc)},
                    )
                this_call_ms = (time.perf_counter() - llm_call_started) * 1000
                llm_call_ms += this_call_ms
                llm_call_count += 1
            parse_started = time.perf_counter()
            try:
                parsed = self._parse(raw)
                response_parse_ms += (time.perf_counter() - parse_started) * 1000
            except Exception as exc:
                response_parse_ms += (time.perf_counter() - parse_started) * 1000
                parse_error = exc
                _obs.counter(
                    "llm.calls.total",
                    provider=llm_provider,
                    model=llm_model,
                    operation="planning",
                    status="invalid_response",
                )
                _obs.histogram(
                    "llm.call.duration_ms",
                    this_call_ms,
                    provider=llm_provider,
                    model=llm_model,
                    operation="planning",
                )
                logger.warning(
                    "[llm_planner] plan parse failed (attempt %d/%d): %s",
                    attempt + 1,
                    _MAX_PARSE_ATTEMPTS,
                    exc,
                )
                continue

            # Well-formed JSON is not the same as a USEFUL plan: a known
            # failure mode of small local models (this module's own
            # _SYSTEM_PROMPT explicitly warns against it, see the
            # "leaving every step's confidence at exactly 0.0" comment
            # above) is copying the prompt's own "confidence": 0.0
            # placeholder verbatim into every step instead of estimating
            # a real value — syntactically valid, semantically empty.
            # Confirmed live against gemma3:latest: a plan with real,
            # reasonable steps came back with every step AND the
            # top-level confidence at exactly 0.0, which always fails the
            # validator's confidence_below_threshold/risk_too_high checks
            # downstream (risk is derived as 1 - confidence below) no
            # matter how sound the steps are. That deserves exactly the
            # same bounded resample a parse failure already gets above —
            # same rationale ("the same prompt sampled again commonly
            # comes back well-formed") — except on the FINAL attempt,
            # where accepting what we have and letting the validator give
            # its real rejection reason beats a generic "planning failed".
            _raw_steps_preview = parsed.get("steps", [])
            has_real_confidence = bool(parsed.get("confidence", 0.0) or 0.0) or any(
                isinstance(s, dict) and float(s.get("confidence", 0.0) or 0.0) > 0.0 for s in _raw_steps_preview
            )
            if _raw_steps_preview and not has_real_confidence and attempt < _MAX_PARSE_ATTEMPTS - 1:
                parse_error = ValueError("degenerate plan: every step confidence was 0.0")
                _obs.counter(
                    "llm.calls.total",
                    provider=llm_provider,
                    model=llm_model,
                    operation="planning",
                    status="degenerate_confidence",
                )
                _obs.histogram(
                    "llm.call.duration_ms",
                    this_call_ms,
                    provider=llm_provider,
                    model=llm_model,
                    operation="planning",
                )
                logger.warning(
                    "[llm_planner] plan parsed but every step confidence was 0.0 (attempt %d/%d) — resampling",
                    attempt + 1,
                    _MAX_PARSE_ATTEMPTS,
                )
                continue

            if cached_raw is None:
                _obs.counter(
                    "llm.calls.total",
                    provider=llm_provider,
                    model=llm_model,
                    operation="planning",
                    status="success",
                )
                _obs.histogram(
                    "llm.call.duration_ms",
                    this_call_ms,
                    provider=llm_provider,
                    model=llm_model,
                    operation="planning",
                )
                put_cached_response(llm_model, _SYSTEM_PROMPT, prompt, raw)
            break
        stage_timings_ms["llm_call_ms"] = round(llm_call_ms, 3)
        stage_timings_ms["llm_call_count"] = llm_call_count
        stage_timings_ms["response_parse_ms"] = round(response_parse_ms, 3)
        if metadata is not None:
            metadata["_stage_timings_ms"] = stage_timings_ms
        if parsed is None:
            logger.warning(
                "[llm_planner] planning failed after %d attempts: %s",
                _MAX_PARSE_ATTEMPTS,
                parse_error,
            )
            return Plan(
                goal=resolved_goal.name,
                confidence=0.0,
                planner="llm",
                metadata={"error": str(parse_error)},
            )

        _raw_steps = parsed.get("steps", [])
        steps = tuple(
            PlanStep(
                action=str(s.get("action", "")),
                description=str(s.get("description", "")),
                expected_outcome=str(s.get("expected_outcome", "")),
                cost=float(s.get("cost", 0.0) or 0.0),
                confidence=float(s.get("confidence", 0.0) or 0.0),
                required_permission=_normalize_required_permission(s.get("required_permission", "")),
                parameters=(s.get("parameters") if isinstance(s.get("parameters"), dict) else {}),
                depends_on=_normalize_depends_on(s.get("depends_on"), own_index=i, step_count=len(_raw_steps)),
            )
            for i, s in enumerate(_raw_steps)
        )
        # See _backfill_px4_parameters's own docstring: prompt guidance
        # alone was confirmed live not to be enough for a small model to
        # reliably carry real Takeoff/Waypoint numbers through from the
        # user's own request into "parameters" -- this only ever touches
        # a step whose own parameters are still at the structural
        # default, so a model that DID populate real values is untouched.
        steps = _backfill_px4_parameters(
            steps,
            f"{resolved_goal.name} {resolved_goal.description or ''}",
        )
        # Same boundary-normalization principle as _normalize_required_
        # permission/_normalize_depends_on above: real, repeatedly observed
        # model behavior, not a hypothetical. A longer, multi-step plan
        # (e.g. two independent ProductSelection steps plus a checkout
        # chain) reliably comes back with the top-level "confidence"/
        # "summary" fields blank or 0.0 even though every individual step
        # carries its own real, non-zero confidence — collapsing
        # overall_confidence to 0.0 in that case discards real information
        # the model DID provide and gets a genuinely viable plan rejected
        # by the validator (confidence_below_threshold) for a reason that
        # has nothing to do with the plan's actual content. Falling back to
        # the minimum step confidence (the plan is only as strong as its
        # weakest step) only kicks in when the top-level field is truly
        # absent/zero; a model that deliberately reports low overall
        # confidence alongside confident steps is not touched.
        overall_confidence = float(parsed.get("confidence", 0.0) or 0.0)
        if overall_confidence <= 0.0 and steps:
            step_confidences = [s.confidence for s in steps if s.confidence > 0.0]
            if step_confidences:
                overall_confidence = min(step_confidences)
        summary = str(parsed.get("summary", ""))
        goal_id = getattr(resolved_goal, "goal_id", "") or str(context.metadata.get("execution_id", "") or "")
        final_plan = Plan(
            goal=resolved_goal.name,
            steps=steps,
            expected_outcomes=(summary,) if summary else (),
            confidence=overall_confidence,
            risk=max(0.0, 1.0 - overall_confidence),
            goal_state=resolved_goal.name,
            planner="llm",
            metadata={"summary": summary, "goal_id": goal_id},
        )
        if moss_cache is not None and steps:
            # Fresh, real, non-empty plan the LLM actually just produced —
            # index it for a future similar goal to reuse. store_plan()
            # itself never raises (see its own docstring), so a slow or
            # failed Moss write degrades to "not cached this time", never
            # to a broken response for THIS call.
            await moss_cache.store_plan(resolved_goal.name, facts_text, final_plan)
        return final_plan

    def _build_prompt(self, goal: Goal, facts: list, context: Any = None) -> str:
        lines = [f"Goal: {goal.name}"]
        if goal.description:
            lines.append(f"Description: {goal.description}")
        if goal.success_criteria:
            lines.append(f"Success criteria: {', '.join(goal.success_criteria)}")
        lines.append("")
        lines.append("Observed facts:")
        for f in facts:
            if f.attribute == "event":
                # f.value (event.description) already names the actor/entity
                # in readable form (e.g. "Actor Raj Sharma executed 2
                # action(s)") — the raw id in f.entity is redundant here and
                # only invites the model to echo an id instead of a name.
                lines.append(f"- {f.attribute}={f.value} (confidence={f.confidence})")
            else:
                lines.append(f"- {f.entity} {f.attribute}={f.value} (confidence={f.confidence})")

        # MB-3060: which action names are actually executable — plain
        # facts about what exists, same principle as everything else in
        # this prompt (the kernel never decides WHICH action to take,
        # only what's true/available); PlanningContext.available_capabilities
        # was already a real field nothing populated or rendered before this.
        available_capabilities = getattr(context, "available_capabilities", ())
        if available_capabilities:
            lines.append("")
            lines.append('Available actions (a step\'s "action" must be one of these, verbatim):')
            for name in available_capabilities:
                lines.append(f"- {name}")

        # Retrieved context (Context-Aware Personalized Planning refactor,
        # kernel/pipeline/planning/context_engine.py) — plain text, same as
        # facts above: the kernel never interprets what an experience or
        # piece of knowledge means, it only makes the content available for
        # the model to reason about. This is what makes two actors with
        # different retrieved context produce genuinely different plans.
        for label, retrieved_items in (
            ("Relevant experiences", getattr(context, "relevant_experiences", ())),
            ("Relevant knowledge", getattr(context, "relevant_knowledge", ())),
            (
                "External knowledge (SittingFace)",
                getattr(context, "relevant_external_knowledge", ()),
            ),
            ("Relevant relationships", getattr(context, "relevant_relationships", ())),
            # Real-Time World Changes refactor (Context Stream spec):
            # incoming messages and negotiation updates are now their own
            # PlanningContext fields (re-bucketed from the same context-
            # stream events "Recent world events" below draws from, not a
            # new retrieval) — surfaced as their own labeled sections so
            # the LLM can distinguish "someone asked/told me something"
            # and "a negotiation I'm party to just changed" from generic
            # world grounding.
            ("Incoming messages", getattr(context, "incoming_messages", ())),
            ("Negotiation updates", getattr(context, "negotiation_updates", ())),
            # Context Grounding: real, recent world events (e.g. a reported
            # perturbation like "Warehouse A fire") — without this, the LLM
            # never actually sees them even though ContextConstructionEngine
            # now retrieves them; the plan would keep "planning blind" to
            # real, already-known world changes, which is the exact gap
            # this field exists to close.
            ("Recent world events", getattr(context, "relevant_context_events", ())),
        ):
            items = retrieved_items or ()
            if not items:
                continue
            lines.append("")
            lines.append(f"{label}:")
            for item in items:
                source_note = f", source={item.source}" if getattr(item, "source", "") else ""
                lines.append(f"- {item.content} (confidence={item.confidence}{source_note})")

        locations = getattr(context, "relevant_locations", ()) or ()
        objects = getattr(context, "relevant_objects", ()) or ()
        resources = getattr(context, "available_resources", ()) or ()
        if locations or objects or resources:
            lines.append("")
            lines.append("World state:")
            for location in locations:
                lines.append(f"- location: {location}")
            for obj in objects:
                lines.append(f"- object: {obj}")
            for resource in resources:
                lines.append(f"- resource: {resource}")

        # Active memberships (Membership as a First-Class Runtime Resource
        # refactor) carry a trust_score per society — plain text, same
        # principle as experiences/knowledge above: the kernel doesn't
        # decide what trust means for planning, it only makes the number
        # available so the model can weigh it if relevant.
        memberships = (getattr(context, "metadata", None) or {}).get("active_memberships") or ()
        if memberships:
            lines.append("")
            lines.append("Active memberships:")
            for m in memberships:
                name = m.get("society_name") or m.get("society_id", "")
                perms = m.get("permissions", [])
                lines.append(
                    f"- society={name} roles={m.get('roles', [])} "
                    f"trust_score={m.get('trust_score', 0.5)} "
                    f"permissions={perms if perms else '(none granted)'}"
                )

        # Reachable colleagues (context_engine.py::_retrieve_reachable_
        # colleagues) — the same real, unambiguous actor_id AskActor
        # resolves against internally, given here directly instead of
        # asking the model to reconstruct an exact display-name string
        # (a name mismatch, e.g. "Raj" vs. the real "Raj Sharma", is a
        # confirmed live failure mode this closes).
        colleagues = (getattr(context, "metadata", None) or {}).get("reachable_colleagues") or ()
        if colleagues:
            lines.append("")
            lines.append("Reachable colleagues (use actor_id as target_actor for AskActor):")
            for c in colleagues:
                lines.append(
                    f"- {c.get('name', '')}: actor_id={c.get('actor_id', '')} (society={c.get('society_name', '')})"
                )

        # Active governance policies (kernel/society/governance.py) for
        # whatever society this goal activated — e.g. a household budget
        # cap. Same principle throughout: the kernel surfaces the policy's
        # content, it never enforces or interprets it itself.
        policies = getattr(context, "active_policies", ()) or ()
        if policies:
            lines.append("")
            lines.append("Active policies:")
            for p in policies:
                rules = ", ".join(getattr(p, "rules", ()) or ())
                metadata = getattr(p, "metadata", {}) or {}
                detail = f" metadata={metadata}" if metadata else ""
                lines.append(f"- {getattr(p, 'name', '')}: {rules}{detail}".rstrip(": "))

        # Shared goals (a society's shared_goals — e.g. a household's
        # shared shopping list) that other members may have already
        # contributed to. This is what lets the planner merge one actor's
        # individual goal with what the group already needs, rather than
        # planning as if this actor were the household's only member.
        shared_goals = (getattr(context, "metadata", None) or {}).get("shared_goals") or ()
        if shared_goals:
            lines.append("")
            lines.append("Shared goals (from this actor's society/household):")
            for g in shared_goals:
                lines.append(f"- {g}")

        shared_resources = (getattr(context, "metadata", None) or {}).get("shared_resources") or {}
        if shared_resources:
            lines.append("")
            lines.append("Shared household resources:")
            for name, value in shared_resources.items():
                lines.append(f"- {name}={value}")

        network_facts = (getattr(context, "metadata", None) or {}).get("commerce_network_facts") or ()
        if network_facts:
            lines.append("")
            lines.append("Commerce network knowledge:")
            for fact in network_facts:
                lines.append(f"- {fact}")
        return "\n".join(lines)

    def _parse(self, raw: str) -> dict:
        start = raw.find("{")
        if start < 0:
            raise ValueError(f"no JSON object found in LLM response: {raw!r}")

        candidate = raw[start:]
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(candidate)
            return value
        except json.JSONDecodeError:
            # Small local models commonly emit a trailing comma before a
            # closing object/array, or append prose after otherwise valid JSON.
            # Repair only that unambiguous JSON formatting error, then decode
            # the first complete object and ignore trailing commentary.
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            value, _ = decoder.raw_decode(repaired)
            return value
