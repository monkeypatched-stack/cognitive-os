"""Claude Code-style theming for the monkeypatched CLI.

Clean, minimal aesthetic with the signature orange accent. Full-width
panels, consistent styling across all commands.
"""

from __future__ import annotations

import os
import shutil
import sys as _sys
from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

# Claude Code keeps color use restrained: mostly plain foreground text, a
# single warm terracotta accent (Anthropic's brand color) for the logo,
# prompt, and highlights, dim grey for secondary/meta text, and red/green
# reserved for errors/success. No rainbow syntax-highlight palette.
_THEME = Theme(
    {
        "accent": "bold #D97757",
        "accent.dim": "#D97757",
        "muted": "grey58",
        "success": "bold #6BBF7B",
        "warning": "bold #D9A257",
        "error": "bold #E5636B",
        "heading": "bold #D97757",
        "path": "grey70",
        "git": "grey58",
        "command": "bold white",
        "agent": "bold #D97757",
        "bullet": "#D97757",
    }
)

console = Console(theme=_THEME, highlight=False, force_terminal=True, file=_sys.stdout)

MONKEY_LOGO = "🐒"


def _term_width() -> int:
    """Terminal width, default 80."""
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except Exception:
        return 80


def _term_height() -> int:
    """Terminal height, default 24."""
    try:
        return shutil.get_terminal_size((80, 24)).lines
    except Exception:
        return 24


def _cwd_label() -> str:
    """Short CWD label: ~ for home, basename otherwise."""
    cwd = os.getcwd()
    home = os.path.expanduser("~" + "monkeypatched")  # avoid accidental expansion of ~ in a string literal
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        return "~" + cwd[len(home) :]
    return os.path.basename(cwd) or cwd


def print_banner(subtitle: str = "") -> None:
    """The monkey banner — shown at the start of long-running/interactive
    commands (start, serve, monitor, login), not on every single command.
    Claude Code's welcome chrome is a small rounded box, not full-width —
    the box exists to frame a couple of lines, not to fill the terminal."""
    body = Text()
    body.append(f"{MONKEY_LOGO} ", style="accent")
    body.append("monkeypatched", style="heading")
    body.append("\n", style="muted")
    body.append("Cognitive Operating System CLI", style="muted")
    if subtitle:
        body.append("\n" + subtitle, style="muted")
    console.print(Panel(body, border_style="accent", box=ROUNDED, expand=False, padding=(0, 2)))


def print_startup_art() -> None:
    """Startup welcome — a small rounded box (Claude Code style), followed
    by plain, unboxed tip lines. Claude Code reserves box chrome for the
    single welcome moment; everything after it is flat text."""
    welcome = Text()
    welcome.append(f"{MONKEY_LOGO} ", style="accent")
    welcome.append("Welcome to monkeypatched", style="heading")
    welcome.append("\n")
    welcome.append("your cognitive OS shell", style="muted")
    console.print(Panel(welcome, border_style="accent", box=ROUNDED, expand=False, padding=(0, 2)))

    tip = Text()
    tip.append("  ", style="muted")
    tip.append("Type ", style="muted")
    tip.append("help", style="command")
    tip.append(" for commands, ", style="muted")
    tip.append("exit", style="command")
    tip.append(" to quit.", style="muted")
    console.print(tip)

    ready = Text()
    ready.append("  ⏺ ", style="bullet")
    ready.append("Ready", style="success")
    console.print(ready)


def prompt_text() -> str:
    """Plain prompt string for input(): ~/path ❯"""
    return f"  {_cwd_label()} ❯ "


def _print_marked(marker: str, marker_style: str, message: str, message_style: str | None = None) -> None:
    """Print `{marker} {message}` without treating `message` as markup —
    message is often dynamic (API error bodies, exception text) and may
    contain literal '[...]' that would otherwise confuse Rich's parser (the
    same class of crash `rich_markup_mode=None` works around for Typer)."""
    text = Text()
    text.append(f"{marker} ", style=marker_style)
    text.append(message, style=message_style)
    console.print(text)


def print_success(message: str) -> None:
    _print_marked("⏺", "success", message)


def print_error(message: str) -> None:
    _print_marked("⏺", "error", message, message_style="error")


def print_warning(message: str) -> None:
    _print_marked("⏺", "warning", message)


def print_info(message: str) -> None:
    console.print(Text(message, style="muted"))


def print_command(cmd: str, description: str = "") -> None:
    """Print a command reference line: command in bold white, description muted."""
    text = Text()
    text.append(f"  {cmd}", style="command")
    if description:
        text.append(f"  — {description}", style="muted")
    console.print(text)


def print_agent(name: str, status: str = "ready") -> None:
    """Print an agent status line with a terracotta bullet, matching how
    Claude Code marks tool/agent activity."""
    text = Text()
    text.append("  ⏺ ", style="bullet")
    text.append(name, style="agent")
    text.append(f"  {status}", style="muted")
    console.print(text)


def print_section(title: str) -> None:
    """Print a section header. Claude Code keeps these as plain bold text,
    no full-width rule."""
    text = Text()
    text.append(f"  {title}", style="heading")
    console.print(text)


def print_kv(key: str, value: str, key_style: str = "muted", value_style: str = "accent") -> None:
    """Print a key-value pair."""
    text = Text()
    text.append(f"  {key}: ", style=key_style)
    text.append(value, style=value_style)
    console.print(text)
