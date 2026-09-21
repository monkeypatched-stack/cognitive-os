#!/usr/bin/env bash
# =============================================================================
# CognitiveOS x Moss: Zero Latency Builder Sprint — Mac Runner
# =============================================================================
# Keeps Mac awake, sets up zero-latency environment, and launches demo / services.

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

BOLD="\033[1m"
GREEN="\033[92m"
CYAN="\033[96m"
YELLOW="\033[93m"
RESET="\033[0m"

echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║  CognitiveOS — YC Fall 2026 x Moss Zero Latency Sprint Runner         ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════════════╝${RESET}\n"

# Prevent macOS sleep while this script runs
if command -v caffeinate >/dev/null 2>&1; then
    echo -e "${GREEN}✓ Activating macOS power assertions (caffeinate -d -i -m)${RESET}"
    caffeinate -d -i -m -w $$ &
    CAFFEINATE_PID=$!
    trap 'kill -9 $CAFFEINATE_PID 2>/dev/null || true' EXIT
fi

# Set default zero-latency emulation if credentials are not configured
if [ -z "$MOSS_PROJECT_ID" ] || [ -z "$MOSS_PROJECT_KEY" ]; then
    echo -e "${YELLOW}ℹ MOSS_PROJECT_ID / KEY not set in environment.${RESET}"
    echo -e "${GREEN}✓ Activating high-speed offline Zero Latency Emulation (MOSS_EMULATION_MODE=1)${RESET}"
    export MOSS_EMULATION_MODE="1"
fi

MODE="${1:-demo}"

case "$MODE" in
    benchmark|demo)
        echo -e "\n${BOLD}Running Zero Latency Benchmark & Verification...${RESET}\n"
        python3 scripts/demo_zero_latency.py
        ;;
    
    ui)
        echo -e "\n${BOLD}Starting Drone Flight & Workspace UI on http://localhost:3000...${RESET}\n"
        cd apps/workspace
        npm run dev
        ;;

    all)
        echo -e "\n${BOLD}Starting CognitiveOS Runtime + UI...${RESET}\n"
        python3 scripts/demo_zero_latency.py
        echo -e "\n${BOLD}${CYAN}Launching Workspace Dashboard...${RESET}"
        cd apps/workspace
        npm run dev
        ;;

    *)
        echo "Usage: $0 [demo | ui | all]"
        echo "  demo - Run terminal Zero Latency benchmark demo"
        echo "  ui   - Start Next.js Drone Flight Dashboard on http://localhost:3000"
        echo "  all  - Run benchmark and start dashboard"
        exit 1
        ;;
esac
