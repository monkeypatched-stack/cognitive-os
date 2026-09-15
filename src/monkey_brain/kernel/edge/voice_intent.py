"""Voice -> Goal/Intent gate (CognitiveOS LiveKit Voice Command Integration,
spec sections 9/10/26).

This module does NOT interpret what a spoken command means -- that stays
the existing LLMPlanner's job (kernel/pipeline/llm_planner.py), reached the
same way a typed goal is: ActorRuntime.add_goal(text) -> SocietyRuntime.
tick_one_actor() -> LLMPlanner picks from the actor's registered
capabilities (Arm/Takeoff/Waypoint/Land for a drone actor, kernel/domains/
robot.py) and governance runs exactly as it does for any other goal.
Building a second, voice-side NLU/planner here would violate the spec's own
"do not build a second agent" rule (section 3) and its literal acceptance
criterion (section 26): microphone -> LiveKit -> ObservationProvider ->
CognitiveOS -> Actor -> Goal -> Planner -> Governance -> Executor, never
microphone -> LLM -> drone API directly.

What this module DOES do is the one judgment call that must happen BEFORE a
goal is created at all: refuse to forward a spatially-ambiguous,
consequential command as a goal (section 10's own example -- "send the
drone over there" must not become a goal the planner then has to guess a
waypoint for). This is a narrow, explainable heuristic gate, not a second
understanding layer: everything that passes it is forwarded to the planner
completely unedited -- nothing here ever chooses a waypoint, a target, or a
capability on the planner's behalf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ambient-mic false positives / non-commands -- dropped silently rather than
# turned into a goal (there is no useful clarification to ask for "um").
_FILLER_RE = re.compile(r"^\s*(um+|uh+|hi|hey|hello|test(ing)?|okay|ok)[.!]?\s*$", re.IGNORECASE)

# A spoken movement/action verb -- only commands that look like one of
# these are candidates for the ambiguity check at all; "what altitude are
# we at" is a question, not a consequential command, and should pass
# through to the planner (which can itself answer or no-op) rather than be
# blocked here.
_ACTION_VERB_RE = re.compile(r"\b(send|go|fly|move|take|navigate|head|drive|launch)\b", re.IGNORECASE)

# A demonstrative standing in for a location, with nothing else in the
# sentence resolving what it refers to.
_DEMONSTRATIVE_RE = re.compile(r"\b(over\s+there|there|that\s+place|here)\b", re.IGNORECASE)

# A concrete spatial anchor: a named waypoint ("waypoint alpha", "point
# bravo"), or explicit coordinates ("x=5 y=7", "(5,7)") -- anything that
# gives the planner something real to resolve, as opposed to a bare
# pronoun.
_WAYPOINT_NAME_RE = re.compile(r"\b(waypoint|point|zone|position|marker)\s+\w+\b", re.IGNORECASE)
_COORD_RE = re.compile(
    r"x\s*=?\s*-?\d+(\.\d+)?\s*[, ]\s*y\s*=?\s*-?\d+(\.\d+)?"
    r"|\(\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VoiceIntentResult:
    """kind is one of:
    - "non_actionable": not a command at all (filler, empty, ambient noise
      mis-transcription) -- dropped, nothing is created.
    - "ambiguous": looks like a consequential movement command but names its
      destination only as an unresolved pronoun -- clarification_reason is
      set, NO goal is created, nothing reaches the planner or governance.
    - "actionable": forwarded to the planner verbatim as goal_text -- this
      module does not edit, summarize, or structure it further.
    """

    kind: str
    transcript: str
    goal_text: str | None = None
    clarification_reason: str | None = None


def interpret_voice_transcript(transcript: str) -> VoiceIntentResult:
    text = (transcript or "").strip()
    if not text or _FILLER_RE.match(text):
        return VoiceIntentResult(kind="non_actionable", transcript=text)

    looks_like_action = bool(_ACTION_VERB_RE.search(text))
    has_demonstrative = bool(_DEMONSTRATIVE_RE.search(text))
    has_anchor = bool(_WAYPOINT_NAME_RE.search(text) or _COORD_RE.search(text))

    if looks_like_action and has_demonstrative and not has_anchor:
        return VoiceIntentResult(
            kind="ambiguous",
            transcript=text,
            clarification_reason=(
                f'"{text}" names a destination only as a pronoun '
                '("there"/"that place"/"here") with no waypoint name or '
                "coordinate given -- which waypoint should I use?"
            ),
        )

    return VoiceIntentResult(kind="actionable", transcript=text, goal_text=text)
