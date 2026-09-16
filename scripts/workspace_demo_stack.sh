#!/bin/bash
# Deploy/teardown for the CognitiveOS Workspace (apps/workspace) frontend
# ONLY -- builds the image, loads it into the existing kind cluster, applies
# deploy/k8s/workspace-deployment.yaml, and port-forwards it to the host.
#
# Deliberately scoped to just this one Deployment/Service: the cluster
# already runs agentos/auth/mongodb/kong/3 drone actors with PX4 sim pods
# (shared state, not this script's to touch) -- `down` never runs anything
# broader than `kubectl delete -f deploy/k8s/workspace-deployment.yaml`.
#
# Usage: scripts/workspace_demo_stack.sh {up|down|status}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CLUSTER_NAME="${KIND_CLUSTER_NAME:-cognitiveos-clean2}"
NAMESPACE="monkeybrain"
IMAGE="monkeybrain/workspace:latest"
LOCAL_PORT="${WORKSPACE_PORT:-3000}"
# In-cluster Kong Service DNS name -- baked into the image at BUILD time
# (NEXT_PUBLIC_* vars are resolved once by `next build`, a K8s Deployment
# env var override has no effect on an already-built image; see the
# Dockerfile's own comment). Override via WORKSPACE_API_TARGET if this
# namespace/Service name ever changes.
API_TARGET="${WORKSPACE_API_TARGET:-http://kong-proxy.$NAMESPACE.svc.cluster.local}"
# Browser-reachable LiveKit signaling URL -- baked in at build time for the
# same reason API_TARGET is (see comment above). Points at the port-forward
# scripts/livekit_demo_stack.sh sets up on the host (default port 7880);
# override via WORKSPACE_LIVEKIT_URL if that port or a different LiveKit
# deployment is used. Left unset here means the frontend shows its own
# "no LiveKit server configured" error rather than silently breaking --
# voice/camera are optional features of this app, not required to boot it.
LIVEKIT_URL="${WORKSPACE_LIVEKIT_URL:-ws://localhost:7880}"
MANIFEST="$PROJECT_DIR/deploy/k8s/workspace-deployment.yaml"
PID_FILE="/tmp/workspace_demo_port_forward.pid"
LOG_FILE="/tmp/workspace_demo_port_forward.log"

usage() {
    echo "Usage: $0 {up|down|status}"
    echo ""
    echo "  up      Build the workspace image, load it into the kind cluster,"
    echo "          apply its Deployment/Service, and port-forward to"
    echo "          http://localhost:\$WORKSPACE_PORT (default 3000)."
    echo "  down    Stop the port-forward and delete ONLY the workspace"
    echo "          Deployment/Service -- everything else in the cluster"
    echo "          (drones, agentos, auth, mongodb, kong, ...) is untouched."
    echo "  status  Show the workspace Deployment/Service/Pod and"
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
    # Also free the port itself -- a prior manual `kubectl port-forward`
    # (started outside this script, e.g. while debugging) or a stale
    # process from a rollout that killed the pod out from under a running
    # port-forward (confirmed live: kubectl port-forward does not
    # auto-reattach to a Deployment's replacement pod) leaves the port
    # bound with no PID file to track it. Scoped to LISTEN sockets only
    # (lsof -sTCP:LISTEN), never touching an unrelated established
    # connection through the port.
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

    echo "==> Building workspace image ($IMAGE) with NEXT_PUBLIC_API_TARGET=$API_TARGET, NEXT_PUBLIC_LIVEKIT_URL=$LIVEKIT_URL..."
    docker build \
        --build-arg "NEXT_PUBLIC_API_TARGET=$API_TARGET" \
        --build-arg "NEXT_PUBLIC_LIVEKIT_URL=$LIVEKIT_URL" \
        -f "$PROJECT_DIR/docker/services/workspace/Dockerfile" -t "$IMAGE" "$PROJECT_DIR"

    echo "==> Loading image into kind cluster '$CLUSTER_NAME'..."
    kind load docker-image "$IMAGE" --name "$CLUSTER_NAME"

    echo "==> Applying $MANIFEST..."
    kubectl apply -f "$MANIFEST"

    # The image tag never changes (always "latest"), so a re-`apply` with
    # an unchanged Deployment spec does NOT make Kubernetes notice the
    # freshly-loaded image content and recreate the pod on its own
    # (confirmed live -- a stale pod kept running the OLD build after a
    # `kind load` alone). Always force a rollout so `up` is idempotent and
    # genuinely picks up whatever was just built, whether this is a first
    # deploy or a re-run after a code change.
    echo "==> Restarting rollout to pick up the freshly loaded image..."
    kubectl rollout restart deployment/workspace -n "$NAMESPACE"

    echo "==> Waiting for rollout..."
    kubectl rollout status deployment/workspace -n "$NAMESPACE" --timeout=180s

    stop_port_forward
    echo "==> Starting port-forward on http://localhost:$LOCAL_PORT..."
    nohup kubectl port-forward -n "$NAMESPACE" svc/workspace "$LOCAL_PORT:3000" \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2

    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Port-forward failed to start -- check $LOG_FILE" >&2
        exit 1
    fi

    echo ""
    echo "=========================================="
    echo "  Workspace ready: http://localhost:$LOCAL_PORT"
    echo "  Login: admin@example.com / Admin@12345678"
    echo "  (scripts/seed_user_role_permissions.py -- run it once against"
    echo "   the cluster's mongodb if this is a fresh deployment)"
    echo "  Voice/camera on the drone-flight page need"
    echo "  scripts/livekit_demo_stack.sh up running too (LIVEKIT_URL=$LIVEKIT_URL)."
    echo "=========================================="
}

cmd_down() {
    stop_port_forward
    echo "==> Deleting ONLY the workspace Deployment/Service..."
    kubectl delete -f "$MANIFEST" --ignore-not-found
    echo "Torn down. Nothing else in the cluster was touched."
}

cmd_status() {
    require_cluster
    echo "==> Workspace resources in namespace '$NAMESPACE':"
    kubectl get deployment,svc,pods -n "$NAMESPACE" -l app=workspace 2>&1 || true
    echo ""
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Port-forward: running (PID $(cat "$PID_FILE")) -> http://localhost:$LOCAL_PORT"
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
