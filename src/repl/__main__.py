"""Entry point for `python -m repl` — starts the interactive REPL."""

from repl.repl import run_repl


def main() -> None:
    run_repl()


if __name__ == "__main__":
    main()
