#!/bin/bash
# Deploy/teardown for the LiveKit server (voice/camera demo,
# docs/VOICE_DRONE_FLIGHT_DEMO.md) ONLY -- applies
# deploy/k8s/livekit-deployment.yaml, wires the matching dev credentials
# onto the agentos Deployment (agentos is the one thing in the cluster that
# actually calls out to this server -- LiveKitVoiceObservationProvider /
# LiveKitVideoObservationProvider / create_livekit_room_token all read
# LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET from its env), and
# port-forwards the server itself to the host so a browser can reach it.
#
# Deliberately scoped like scripts/workspace_demo_stack.sh: `down` only
# deletes deploy/k8s/livekit-deployment.yaml's own resources -- it does NOT
# touch agentos (its LIVEKIT_* env vars are left as-is; harmless when the
# server is down, and saves a redundant rollout on the next `up`).
#
# Usage: scripts/livekit_demo_stack.sh {up|down|status}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CLUSTER_NAME="${KIND_CLUSTER_NAME:-cognitiveos-clean2}"
NAMESPACE="monkeybrain"
LOCAL_PORT="${LIVEKIT_PORT:-7880}"
MANIFEST="$PROJECT_DIR/deploy/k8s/livekit-deployment.yaml"
PID_FILE="/tmp/livekit_demo_port_forward.pid"
LOG_FILE="/tmp/livekit_demo_port_forward.log"

# Must match deploy/k8s/livekit-deployment.yaml's own LIVEKIT_KEYS env var
# exactly -- SIMULATION/DEMO-ONLY dev credentials, see that file's header.
LIVEKIT_API_KEY="devkey"
LIVEKIT_API_SECRET="devsecret_at_least_32_characters_long"

# The CAMERA publish bridge (kernel/edge/livekit_video_adapter.py::
# RosCameraToLiveKitBridge) lives in each drone's px4-sim-<actor> Pod's
# ros-bridge container, NOT the cognitiveos-actor-<actor> Pod -- confirmed
# live that the actor Pod's own image (monkeybrain/agentos:latest) has no
# ROS 2/rclpy installed at all in this deployment topology
# (ROS_ADAPTER_KIND=remote_http), so RosCameraToLiveKitBridge's `import
# rclpy` always failed there. ros_bridge_server.py's own module docstring
# has the full explanation. Only actors rendered with a camera-equipped
# PX4_SIM_MODEL (e.g. gz_x500_mono_cam) get DRONE_CAMERA_ENABLED=true here
# -- harmless but pointless for one without a camera sensor.
CAMERA_ACTOR_DEPLOYMENTS=(
    "px4-sim-drone-a"
)

usage() {
    echo "Usage: $0 {up|down|status}"
    echo ""
    echo "  up      Apply the LiveKit Deployment/Service, wire its dev"
    echo "          credentials onto agentos (kubectl set env + rollout"
    echo "          restart), and port-forward to"
    echo "          ws://localhost:\$LIVEKIT_PORT (default 7880)."
    echo "  down    Stop the port-forward and delete ONLY the LiveKit"
    echo "          Deployment/Service -- agentos and everything else in"
    echo "          the cluster is untouched."
    echo "  status  Show the LiveKit Deployment/Service/Pod and"
    echo "          port-forward state."
    exit 1
}

require_cluster() {
    if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
        echo "kind cluster '$CLUSTER_NAME' not found. Set KIND_CLUSTER_NAME or create it first." >&2
        exit 1
    fi
}

stop_port_forward() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "==> Stopping port-forward (PID $pid)..."
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
    # Scoped to LISTEN sockets only -- see workspace_demo_stack.sh's own
    # comment on this exact same pattern (never touch an established
    # connection through the port, just a stale/orphaned listener).
    local stale_pids
    stale_pids="$(lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$stale_pids" ]; then
        echo "==> Freeing port $LOCAL_PORT (stale listener PID(s): $stale_pids)..."
        kill $stale_pids 2>/dev/null || true
        sleep 1
    fi
}

cmd_up() {
    require_cluster

    echo "==> Applying $MANIFEST..."
    kubectl apply -f "$MANIFEST"

    echo "==> Waiting for LiveKit rollout..."
    kubectl rollout status deployment/livekit -n "$NAMESPACE" --timeout=120s

    echo "==> Wiring LiveKit credentials onto agentos..."
    kubectl set env deployment/agentos -n "$NAMESPACE" \
        LIVEKIT_URL="ws://livekit.$NAMESPACE.svc.cluster.local:7880" \
        LIVEKIT_API_KEY="$LIVEKIT_API_KEY" \
        LIVEKIT_API_SECRET="$LIVEKIT_API_SECRET"
    kubectl rollout restart deployment/agentos -n "$NAMESPACE"
    echo "==> Waiting for agentos rollout..."
    kubectl rollout status deployment/agentos -n "$NAMESPACE" --timeout=120s

    echo "==> Wiring camera + LiveKit credentials onto px4-sim ros-bridge container(s)..."
    for deploy in "${CAMERA_ACTOR_DEPLOYMENTS[@]}"; do
        if ! kubectl get deployment "$deploy" -n "$NAMESPACE" >/dev/null 2>&1; then
            echo "    (skipping $deploy -- not deployed in this cluster)"
            continue
        fi
        echo "    $deploy (container: ros-bridge)..."
        kubectl set env "deployment/$deploy" -n "$NAMESPACE" -c ros-bridge \
            DRONE_CAMERA_ENABLED=true \
            LIVEKIT_URL="ws://livekit.$NAMESPACE.svc.cluster.local:7880" \
            LIVEKIT_API_KEY="$LIVEKIT_API_KEY" \
            LIVEKIT_API_SECRET="$LIVEKIT_API_SECRET" >/dev/null
        kubectl rollout restart "deployment/$deploy" -n "$NAMESPACE" >/dev/null
    done
    for deploy in "${CAMERA_ACTOR_DEPLOYMENTS[@]}"; do
        kubectl get deployment "$deploy" -n "$NAMESPACE" >/dev/null 2>&1 || continue
        kubectl rollout status "deployment/$deploy" -n "$NAMESPACE" --timeout=180s
    done

    stop_port_forward
    echo "==> Starting port-forward on ws://localhost:$LOCAL_PORT..."
    nohup kubectl port-forward -n "$NAMESPACE" svc/livekit "$LOCAL_PORT:7880" \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2

    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Port-forward failed to start -- check $LOG_FILE" >&2
        exit 1
    fi

    echo ""
    echo "=========================================="
    echo "  LiveKit ready: ws://localhost:$LOCAL_PORT"
    echo "  agentos wired with matching dev credentials."
    echo "  Now (re)run: WORKSPACE_LIVEKIT_URL=ws://localhost:$LOCAL_PORT \\"
    echo "               scripts/workspace_demo_stack.sh up"
    echo "  so the frontend build picks up NEXT_PUBLIC_LIVEKIT_URL."
    echo "=========================================="
}

cmd_down() {
    stop_port_forward
    echo "==> Deleting ONLY the LiveKit Deployment/Service..."
    kubectl delete -f "$MANIFEST" --ignore-not-found
    echo "Torn down. agentos's LIVEKIT_* env vars were left in place (harmless while the server is down)."
}

cmd_status() {
    require_cluster
    echo "==> LiveKit resources in namespace '$NAMESPACE':"
    kubectl get deployment,svc,pods -n "$NAMESPACE" -l app=livekit 2>&1 || true
    echo ""
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Port-forward: running (PID $(cat "$PID_FILE")) -> ws://localhost:$LOCAL_PORT"
    else
        echo "Port-forward: not running"
    fi
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *) usage ;;
esac
