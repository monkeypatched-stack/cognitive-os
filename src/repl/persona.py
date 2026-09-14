"""MonkeyBrain CLI voice.

Calm, concise, matter-of-fact. State what is true, state what follows, stop.
No apologies, no blame, no exclamation, no reassurance the user did not ask for.

The refusal line is reserved for one situation: execution cannot proceed because
a required capability does not exist. It is not for validation errors, not for a
missing flag, not for a recoverable warning, and not for a transport failure. If
the operation could succeed on a retry, or after the user corrects an input, it
is not a blocker and the line must not appear.
"""

from __future__ import annotations

# Reserved for genuine execution blockers only. See module docstring.
REFUSAL = "I'm sorry, Dave. I'm afraid I can't do that."

_DIM = "\033[90m"
_RESET = "\033[0m"
_AMBER = "\033[33m"


def refusal(reason: str = "") -> str:
    """The refusal, optionally preceded by the specific cause.

    Use only when a required capability is unavailable and execution therefore
    cannot begin. `reason` should be a plain noun phrase, lowercase, no period.
    """
    lines = []
    if reason:
        lines.append(f"  {reason.rstrip('.')}.")
    lines.append(f"  {_AMBER}{REFUSAL}{_RESET}")
    return "\n".join(lines)


def advisory(answer: str, *, grounded: bool) -> str:
    """An informational answer produced without executing anything.

    The distinction the user needs is not that a model wrote this, but that
    nothing was carried out and no system data was read.
    """
    provenance = (
        "Answer drawn from retrieved context."
        if grounded
        else "Answer drawn from general knowledge, not from system data."
    )
    return "\n".join(
        [
            f"  {_DIM}Advisory — no action was taken.{_RESET}",
            "",
            f"  {answer}",
            "",
            f"  {_DIM}{provenance}{_RESET}",
        ]
    )


def state(label: str, value: str) -> str:
    """A single fact, aligned. `label` is a short noun."""
    return f"  {label:<14}{value}"


def note(message: str) -> str:
    """A neutral observation. Not a warning, not an apology."""
    return f"  {_DIM}{message}{_RESET}"
