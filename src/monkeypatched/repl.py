"""Compatibility shim for older launchers that import `monkeypatched.repl`."""

from repl.repl import run_repl


def main() -> None:
    run_repl()


if __name__ == "__main__":
    main()
