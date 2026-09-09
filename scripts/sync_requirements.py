#!/usr/bin/env python3
"""Regenerate requirements.txt from pyproject.toml's [project.dependencies].

pyproject.toml is the single source of truth for the root package's
third-party dependency list. requirements.txt exists only because
docker/services/agentos/Dockerfile (and other Dockerfiles) COPY it in
before the rest of the source tree, to preserve Docker layer caching --
it must never be hand-edited.

Run directly, or via the `sync-requirements` pre-commit hook.
"""

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"

HEADER = """\
# GENERATED FILE -- do not edit by hand.
#
# Mirrors pyproject.toml's [project.dependencies] verbatim. Regenerate with:
#   python scripts/sync_requirements.py
#
# Optional extras declared in pyproject.toml's [project.optional-dependencies]
# (dev, enterprise, soma, ros) are deliberately excluded -- they are only
# meant to install when explicitly requested.

"""


def main() -> int:
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    generated = HEADER + "\n".join(deps) + "\n"

    if "--check" in sys.argv:
        current = REQUIREMENTS.read_text() if REQUIREMENTS.exists() else ""
        if current != generated:
            print("requirements.txt is out of date with pyproject.toml.")
            print("Run: python scripts/sync_requirements.py")
            return 1
        return 0

    REQUIREMENTS.write_text(generated)
    print(f"Wrote {REQUIREMENTS} from {PYPROJECT} ({len(deps)} dependencies).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
