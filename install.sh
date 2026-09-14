#!/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"

echo "MonkeyBrain Installer"
echo "====================="
echo ""

# Check uv
if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Using uv $(uv --version)"
echo ""

# Create venv
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    uv venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"

# Install all src/ packages
echo ""
echo "Installing src/ packages..."
uv pip install --python "$PYTHON" \
    "$REPO_DIR/packages/cerebellum" \
    "$REPO_DIR/packages/broca" \
    "$REPO_DIR/packages/introspection" \
    "$REPO_DIR/packages/cortex" \
    "$REPO_DIR/packages/homeostasis" \
    "$REPO_DIR/packages/deepdive" \
    "$REPO_DIR/packages/cingulate" \
    "$REPO_DIR/packages/plasticity" \
    "$REPO_DIR/packages/sync" \
    "$REPO_DIR/packages/soma-cli" \
    "$REPO_DIR/packages/sittingface"

# Install services/ dependencies (from the locked, reproducible set)
echo ""
echo "Installing services/ dependencies..."
uv export --frozen --no-dev --no-hashes --no-emit-project --project "$REPO_DIR" | \
    uv pip install --python "$PYTHON" -r -

# Install root package in editable mode
echo ""
echo "Installing monkeybrain-runtime (editable)..."
uv pip install --python "$PYTHON" -e "$REPO_DIR"

# Check Ollama
echo ""
echo "Checking Ollama..."
if command -v ollama &>/dev/null; then
    echo "  Ollama found: $(ollama --version 2>/dev/null || echo 'installed')"
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "  Ollama server: running"
    else
        echo "  Ollama server: not running (start with: ollama serve)"
    fi
else
    echo "  Ollama not found — install from https://ollama.com"
    echo "  Required for local LLM inference (spec discovery, code generation)"
fi

echo ""
echo "Done!"
echo ""
echo "Commands:"
echo "  monkeypatch                                 # Open the interactive shell (login required)"
echo "  monkeybrain status                          # Check services"
echo "  monkeybrain start                           # Start everything"
echo "  monkeybrain stop                            # Stop everything"
echo "  soma --help                                 # Constitutional compiler CLI"
echo "  soma discover 'build me a blog writer'      # Autonomous spec discovery"
