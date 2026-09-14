#!/usr/bin/env python3
"""
verify_image_secrets.py — Docker image security assertion.

Build-time gate that inspects a Docker image and rejects it if it contains
.env files, key material, credentials, or other secrets that must never be
embedded in container images.

Usage:
    python scripts/verify_image_secrets.py <image-name:tag>
    python scripts/verify_image_secrets.py cognitiveos-auth:v1.2.3

Exit codes:
    0  — Image is clean (no secrets detected)
    1  — SECURITY VIOLATION: Image contains secrets
    2  — Usage error or image inspection failure

Related files:
    .dockerignore              — Primary control; excludes secrets from build
    .gitignore                 — Excludes secrets from git (keep in sync)
    docker/services/*/Dockerfile  — Must respect .dockerignore
    .github/workflows/ci.yml   — CI gate that runs this script
"""

import sys
import subprocess
import json
import tempfile
import os
from pathlib import Path
from fnmatch import fnmatch

# Forbidden file patterns. Keep synchronized with:
#   - .dockerignore
#   - .gitignore
#   - shell script version (scripts/verify-no-secrets-in-image.sh)
FORBIDDEN_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "id_ed25519.pub",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "*.local.yaml",
    "*.local.yml",
]

# Directories to exclude from search (system/build artifacts, not app code)
EXCLUDED_DIR_PREFIXES = (
    "/usr/",
    "/var/",
    "/sys/",
    "/proc/",
    "/dev/",
    "/etc/ssl/",
    "/etc/ssh/",
    "/.git/",
    "/root/.cache/",
    "/.cache/",
)

# CA bundles shipped by certifi/botocore — not private key material.
ALLOWED_PEM_NAMES = frozenset({"cacert.pem"})
ALLOWED_PEM_DIR_MARKERS = ("certifi", "botocore", "pip/_vendor/certifi")


def should_exclude_path(path: str) -> bool:
    """Return True if path should be excluded from secret scanning."""
    for excluded in EXCLUDED_DIR_PREFIXES:
        if path.startswith(excluded):
            return True
    return False


def is_allowed_pem_file(path: Path) -> bool:
    """CA certificate bundles are not secret key material."""
    if path.name in ALLOWED_PEM_NAMES:
        return True
    path_str = str(path)
    return any(marker in path_str for marker in ALLOWED_PEM_DIR_MARKERS)


def matches_pattern(filename: str, pattern: str) -> bool:
    """Check if filename matches the secret pattern (supports globs)."""
    return fnmatch(filename, pattern)


def extract_image_filesystem(image: str) -> Path:
    """
      Extract Docker image filesystem to a temporary directory.
      Returns the path to the extracted root filesystem.

      Uses ``docker create`` + ``docker export`` so we never need the image
    CMD/ENTRYPOINT to understand ``sleep`` — ``docker run image sleep infinity``
    passes those words as *arguments* to uvicorn/python and fails on CI images.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="docker_inspect_"))
    root = tmpdir / "root"
    root.mkdir(parents=True, exist_ok=True)
    tar_path = tmpdir / "image.tar"

    create = subprocess.run(
        ["docker", "create", image],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if create.returncode != 0:
        raise RuntimeError(f"docker create failed: {create.stderr.strip() or create.stdout.strip() or 'unknown error'}")

    container_id = create.stdout.strip()
    try:
        export = subprocess.run(
            ["docker", "export", container_id, "-o", str(tar_path)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if export.returncode != 0:
            raise RuntimeError(
                f"docker export failed: {export.stderr.strip() or export.stdout.strip() or 'unknown error'}"
            )

        subprocess.run(
            ["tar", "-xf", str(tar_path), "-C", str(root)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return root
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
        raise RuntimeError(f"Failed to extract image filesystem: {detail}") from e
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            timeout=30,
        )


def scan_filesystem_for_secrets(root_path: Path) -> list[str]:
    """
    Scan the extracted filesystem for forbidden file patterns.
    Returns a list of (pattern, file_path) tuples for all matches found.
    """
    matches_found = []

    for pattern in FORBIDDEN_PATTERNS:
        for fspath in root_path.rglob("*"):
            if not fspath.exists():
                continue

            # Build path relative to root for display
            rel_path = fspath.relative_to(root_path)
            full_path = f"/{rel_path}"

            # Skip excluded directories
            if should_exclude_path(full_path):
                continue

            # Check if filename matches the forbidden pattern
            if matches_pattern(fspath.name, pattern):
                if pattern == "*.pem" and is_allowed_pem_file(fspath):
                    continue
                matches_found.append((pattern, str(rel_path)))

    return matches_found


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python verify_image_secrets.py <image-name:tag>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Example:", file=sys.stderr)
        print("  python verify_image_secrets.py cognitiveos-auth:v1.2.3", file=sys.stderr)
        return 2

    image = sys.argv[1]

    print(f"📋 Scanning image for secrets: {image}")
    print("")

    try:
        print("  Extracting image filesystem...")
        root = extract_image_filesystem(image)
        print(f"  ✓ Extracted to {root}")
        print("")

        print("  Scanning for forbidden patterns...")
        matches = scan_filesystem_for_secrets(root)
        print(f"  ✓ Scan complete ({len(FORBIDDEN_PATTERNS)} patterns checked)")
        print("")

        if not matches:
            print("✅ PASS: No secrets detected in image.")
            print("")
            print(f"   Patterns checked: {len(FORBIDDEN_PATTERNS)}")
            print("   Result: Image is safe to push/deploy")
            return 0

        # Security violation found
        print("❌ SECURITY VIOLATION: The following secret files were found in the image:")
        print("")

        # Group matches by pattern for clarity
        by_pattern = {}
        for pattern, filepath in matches:
            if pattern not in by_pattern:
                by_pattern[pattern] = []
            by_pattern[pattern].append(filepath)

        for pattern, files in by_pattern.items():
            print(f"   Pattern: {pattern}")
            for filepath in sorted(files):
                print(f"     - {filepath}")
            print("")

        print("🔒 SECURITY GATE: Image rejected. Do not push.")
        print("")
        print("   Resolution:")
        print("   1. Verify .dockerignore includes all secret file patterns")
        print("   2. Verify no COPY/ADD in Dockerfile re-includes excluded files")
        print("   3. Clean build context and rebuild: docker build --no-cache ...")
        print("   4. Run this verification again")
        return 1

    except RuntimeError as e:
        print(f"❌ ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
