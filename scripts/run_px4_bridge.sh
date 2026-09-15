#!/usr/bin/env bash
# Start one ros_bridge_server bound to one PX4 vehicle.
#
#   run_px4_bridge.sh <instance> [port]
#
# An adapter binds to exactly one PX4_NAMESPACE, so a fleet of N drones needs
# N bridge processes -- one per namespace. Multi-vehicle Isaac runs put the
# vehicles on PX4 instances 1..N, whose uXRCE-DDS namespaces are px4_1..px4_N,
# so <instance> here is the PX4 instance number and the default port is
# 9009+instance (instance 1 -> 9010, instance 2 -> 9011, ...).
set -e

INSTANCE="${1:?usage: run_px4_bridge.sh <instance> [port]}"
PORT="${2:-$((9009 + INSTANCE))}"
# Repo root, resolved relative to this script's own location -- not a
# hardcoded, other-machine path into a stale worktree copy (was
# "/home/varun/cognitive-os/.claude/worktrees/isaac-gui-lowres"). Still
# overridable via WORKTREE for anyone who really wants a different checkout.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE="${WORKTREE:-$(dirname "$SCRIPT_DIR")}"

source /opt/ros/humble/setup.bash
# px4_msgs is not in the base ROS install; it lives in a colcon overlay
# workspace whose location is inherently machine-specific (wherever you
# built it) -- there is no universally-correct default, so this is required
# rather than silently defaulting to one developer's own path.
: "${PX4_MSGS_WS:?set PX4_MSGS_WS to your px4_msgs colcon workspace (the directory containing install/local_setup.bash)}"
source "${PX4_MSGS_WS}/install/local_setup.bash"

cd "$WORKTREE"
export PX4_NAMESPACE="px4_${INSTANCE}"
export ACTOR_ID="drone${INSTANCE}"

# Arrival tolerances are judged against PX4's estimate, so in simulation they
# have to cover EKF drift as well as control error. Measured with four drones
# in the Rivermark city: every vehicle reached an 8m outbound waypoint inside
# the stock 0.5m, then all four settled ~1.2m from the origin on the way home
# -- the same direction each time, i.e. accumulated drift rather than four
# independent misses. "home" is the EKF origin, so it is the one waypoint with
# no slack for that. Override per-run if you want the stock values back.
export PX4_POSITION_TOLERANCE_M="${PX4_POSITION_TOLERANCE_M:-3.5}"
export PX4_ALTITUDE_TOLERANCE_M="${PX4_ALTITUDE_TOLERANCE_M:-0.6}"

echo "bridge: instance=${INSTANCE} ns=${PX4_NAMESPACE} actor=${ACTOR_ID} port=${PORT}"
exec python3 -m uvicorn src.monkey_brain.kernel.edge.ros_bridge_server:app \
     --host 127.0.0.1 --port "${PORT}"
