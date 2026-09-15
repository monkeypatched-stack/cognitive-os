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
_WAYPOINT_NAME_RE = re.compile(r"\b(?:waypoint|point|zone|position|marker)\s+(\w+)\b", re.IGNORECASE)
_COORD_RE = re.compile(
    r"x\s*=?\s*-?\d+(\.\d+)?\s*[, ]\s*y\s*=?\s*-?\d+(\.\d+)?"
    r"|\(\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*\)",
    re.IGNORECASE,
)

# TEST/DEMO-ONLY waypoint name -> local NED (x, y) offset. Deliberately the
# same manual-substitution convention scripts/demo_swarm_mission.py already
# uses for its own A/B/C waypoints (that script's own comment: no real
# named-waypoint resolver exists anywhere in this codebase) -- NOT a new
# navigation architecture, just the same disclaimer-labeled constant reused
# from the real voice path instead of only a standalone demo script. A
# named waypoint not in this dict still passes as "actionable" (unedited,
# same as today) -- the planner may then produce whatever parameters its
# own default/backfill logic yields; only a KNOWN test waypoint gets a
# deterministic coordinate.
_TEST_WAYPOINT_COORDINATES: dict[str, tuple[float, float]] = {
    "alpha": (8.0, 0.0),
    "bravo": (28.0, 0.0),
    "charlie": (8.0, 20.0),
}


def _enrich_with_known_waypoint_coordinates(text: str) -> str:
    """If the only spatial anchor is a named waypoint this repo's test/demo
    registry above knows about, append its (x, y) as literal `x=.. y=..`
    text -- the EXISTING, already-real _backfill_px4_parameters() regex in
    kernel/pipeline/llm_planner.py already deterministically fills a
    Waypoint capability's parameters from exactly this literal-coordinate
    text (proven today for a spoken/typed command that already contains
    "x=5, y=5"); this only makes a NAMED waypoint reach that same existing
    mechanism instead of being left for the LLM to guess. Text with its own
    literal coordinates already present is left untouched."""
    if _COORD_RE.search(text):
        return text
    match = _WAYPOINT_NAME_RE.search(text)
    if not match:
        return text
    coordinates = _TEST_WAYPOINT_COORDINATES.get(match.group(1).lower())
    if coordinates is None:
        return text
    x, y = coordinates
    return f"{text} (x={x}, y={y})"


@dataclass(frozen=True)
class VoiceIntentResult:
    """kind is one of:
    - "non_actionable": not a command at all (filler, empty, ambient noise
      mis-transcription) -- dropped, nothing is created.
    - "ambiguous": looks like a consequential movement command but names its
      destination only as an unresolved pronoun -- clarification_reason is
      set, NO goal is created, nothing reaches the planner or governance.
    - "actionable": forwarded to the planner as goal_text. Unedited except
      for one narrow, explicit case: a transcript whose only spatial anchor
      is a named waypoint this module's own small test/demo registry
      recognizes (_TEST_WAYPOINT_COORDINATES) gets that waypoint's (x, y)
      appended as literal "x=.. y=.." text, so the EXISTING planner-side
      coordinate backfill (kernel/pipeline/llm_planner.py's
      _backfill_px4_parameters, already real for a command that spells out
      coordinates itself) has something deterministic to resolve instead of
      guessing. This module still never chooses a capability, still never
      summarizes/restructures/interprets the command's meaning, and an
      unrecognized waypoint name passes through byte-for-byte as before.
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

    return VoiceIntentResult(
        kind="actionable", transcript=text, goal_text=_enrich_with_known_waypoint_coordinates(text)
    )
