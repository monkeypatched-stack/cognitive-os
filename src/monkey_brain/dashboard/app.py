"""Dash App — live interactive cognitive dashboard.

Runs a Dash server that polls the MonkeyBrain API and renders
all gauges, histograms, and charts in real-time.

Usage:
    python -m src.monkey_brain.dashboard.app [--port 8050] [--host 0.0.0.0]
"""

from __future__ import annotations

import json
import argparse
import urllib.request
from typing import Any

import plotly.graph_objects as go
from dash import Dash, html, dcc, Input, Output

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8050
DEFAULT_API = "http://localhost:8031"


def _fetch_dashboard(api_url: str) -> dict[str, Any]:
    try:
        req = urllib.request.Request(
            f"{api_url}/api/v1/agentos/dashboard/current",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {
            "error": str(e),
            "planning": {},
            "execution": {},
            "simulation": {},
            "learning": {},
            "bellman": {},
            "metrics": {},
            "health": {},
            "graph": {},
            "mutations": [],
            "timeline": [],
            "consensus": {},
            "observability": {},
        }


def _fetch_observability(api_url: str) -> dict[str, Any]:
    try:
        req = urllib.request.Request(
            f"{api_url}/api/v1/agentos/observability",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {}


def _safe(v: Any, default: Any = 0) -> Any:
    return v if v is not None else default


def build_figures(data: dict, obs: dict) -> list[go.Figure]:
    """Build all dashboard figures from data."""
    execution = data.get("execution", {})
    simulation = data.get("simulation", {})
    learning = data.get("learning", {})
    bellman = data.get("bellman", {})
    metrics = data.get("metrics", {})
    health = data.get("health", {})
    graph = data.get("graph", {})
    mutations = data.get("mutations", [])

    counters = metrics.get("counters", {})
    gauges = metrics.get("gauges", {})
    histograms = metrics.get("histograms", {})

    figs = []

    # ── 1. Planning Graph (Pie) ──
    nodes = graph.get("nodes", [])
    fig1 = go.Figure(
        go.Pie(
            labels=[n.get("agent", n.get("node_id", "?")) for n in nodes] or ["No Data"],
            values=[1] * len(nodes) or [1],
            hole=0.4,
            textinfo="label",
            marker=dict(
                colors=[
                    "#636EFA",
                    "#EF553B",
                    "#00CC96",
                    "#AB63FA",
                    "#FFA15A",
                    "#19D3F3",
                    "#FF6692",
                    "#B6E880",
                ]
            ),
        )
    )
    fig1.update_layout(title="Planning Graph", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig1)

    # ── 2. Execution Status (Bar) ──
    fig2 = go.Figure(
        go.Bar(
            x=["Completed", "Running", "Failed"],
            y=[
                _safe(execution.get("completed_nodes", 0)),
                _safe(execution.get("running_nodes", 0)),
                _safe(execution.get("failed_nodes", 0)),
            ],
            marker_color=["#00CC96", "#FFA15A", "#EF553B"],
            text=[
                _safe(execution.get("completed_nodes", 0)),
                _safe(execution.get("running_nodes", 0)),
                _safe(execution.get("failed_nodes", 0)),
            ],
            textposition="auto",
        )
    )
    fig2.update_layout(title="Execution Status", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig2)

    # ── 3. Simulation Consensus (Bar) ──
    solver_results = simulation.get("solver_results", [])
    if solver_results:
        fig3 = go.Figure(
            go.Bar(
                x=[s.get("solver_name", "?") for s in solver_results],
                y=[s.get("confidence", 0) for s in solver_results],
                marker_color="#636EFA",
                text=[f"{s.get('confidence', 0):.2f}" for s in solver_results],
                textposition="auto",
            )
        )
    else:
        fig3 = go.Figure(go.Bar(x=["No solvers"], y=[0], marker_color="#888888"))
    fig3.update_layout(title="Simulation Consensus", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig3)

    # ── 4. Learning Loss (Bar) ──
    fig4 = go.Figure(
        go.Bar(
            x=["Topological", "Epistemic", "Perplexity"],
            y=[
                _safe(learning.get("topological_loss", 0)),
                _safe(learning.get("epistemic_loss", 0)),
                _safe(learning.get("perplexity", 0)),
            ],
            marker_color=["#636EFA", "#EF553B", "#FFA15A"],
            text=[
                f"{_safe(learning.get('topological_loss', 0)):.3f}",
                f"{_safe(learning.get('epistemic_loss', 0)):.3f}",
                f"{_safe(learning.get('perplexity', 0)):.3f}",
            ],
            textposition="auto",
        )
    )
    fig4.update_layout(title="Learning Loss", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig4)

    # ── 5. Bellman Q-Values (Bar) ──
    q_values = bellman.get("q_values", {})
    if q_values:
        sorted_q = sorted(q_values.items(), key=lambda x: -x[1])[:10]
        fig5 = go.Figure(
            go.Bar(
                x=[q[0] for q in sorted_q],
                y=[q[1] for q in sorted_q],
                marker_color="#00CC96",
                text=[f"{q[1]:.2f}" for q in sorted_q],
                textposition="auto",
            )
        )
    else:
        fig5 = go.Figure(go.Bar(x=["No data"], y=[0], marker_color="#888888"))
    fig5.update_layout(title="Bellman Q-Values", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig5)

    # ── 6. Health (Indicator) ──
    components = health.get("components", [])
    healthy = sum(1 for c in components if c.get("status") == "healthy")
    fig6 = go.Figure(
        go.Indicator(
            mode="number",
            value=healthy,
            title={"text": f"Healthy / {len(components)}"},
            number={"font": {"color": "#00CC96", "size": 48}},
        )
    )
    fig6.update_layout(title="Health", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig6)

    # ── 7. Counter Metrics (Bar) ──
    counter_keys = sorted(counters.keys())[:15]
    fig7 = go.Figure(
        go.Bar(
            x=[k.split(".")[-1][:20] for k in counter_keys],
            y=[counters[k] for k in counter_keys],
            marker_color="#AB63FA",
            text=[counters[k] for k in counter_keys],
            textposition="auto",
        )
    )
    fig7.update_layout(
        title="Counter Metrics",
        height=300,
        margin=dict(t=40, b=20, l=20, r=20),
        xaxis_tickangle=-45,
    )
    figs.append(fig7)

    # ── 8. Gauge Metrics (Bar) ──
    gauge_keys = sorted(gauges.keys())[:15]
    fig8 = go.Figure(
        go.Bar(
            x=[k.split(".")[-1][:20] for k in gauge_keys],
            y=[gauges[k] for k in gauge_keys],
            marker_color="#19D3F3",
            text=[f"{gauges[k]:.1f}" for k in gauge_keys],
            textposition="auto",
        )
    )
    fig8.update_layout(
        title="Gauge Metrics",
        height=300,
        margin=dict(t=40, b=20, l=20, r=20),
        xaxis_tickangle=-45,
    )
    figs.append(fig8)

    # ── 9. Histogram Distributions (Grouped Bar) ──
    hist_keys = sorted(histograms.keys())[:10]
    if hist_keys:
        hist_avgs = [histograms[k].get("avg", 0) if isinstance(histograms[k], dict) else 0 for k in hist_keys]
        hist_counts = [histograms[k].get("count", 0) if isinstance(histograms[k], dict) else 0 for k in hist_keys]
        fig9 = go.Figure()
        fig9.add_trace(
            go.Bar(
                x=[k.split(".")[-1][:20] for k in hist_keys],
                y=hist_avgs,
                name="Avg",
                marker_color="#FF6692",
            )
        )
        fig9.add_trace(
            go.Bar(
                x=[k.split(".")[-1][:20] for k in hist_keys],
                y=hist_counts,
                name="Count",
                marker_color="#B6E880",
            )
        )
        fig9.update_layout(
            barmode="group",
            title="Histogram Distributions",
            height=300,
            margin=dict(t=40, b=20, l=20, r=20),
            xaxis_tickangle=-45,
        )
    else:
        fig9 = go.Figure(go.Bar(x=["No data"], y=[0], marker_color="#888888"))
        fig9.update_layout(
            title="Histogram Distributions",
            height=300,
            margin=dict(t=40, b=20, l=20, r=20),
        )
    figs.append(fig9)

    # ── 10. Graph Execution Order (Bar) ──
    execution_order = graph.get("execution_order", [])
    step_names, step_counts = [], []
    for i, batch in enumerate(execution_order):
        if isinstance(batch, list):
            step_names.append(f"Step {i + 1}")
            step_counts.append(len(batch))
    if step_names:
        fig10 = go.Figure(
            go.Bar(
                x=step_names,
                y=step_counts,
                marker_color="#636EFA",
                text=step_counts,
                textposition="auto",
            )
        )
    else:
        fig10 = go.Figure(go.Bar(x=["No data"], y=[0], marker_color="#888888"))
    fig10.update_layout(title="Graph Execution Order", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig10)

    # ── 11. Component Latency (Bar) ──
    latency_data = {
        "Planner": _safe(metrics.get("planner_latency_ms", 0)),
        "Execution": _safe(metrics.get("execution_latency_ms", 0)),
        "Simulation": _safe(metrics.get("simulation_latency_ms", 0)),
        "Learning": _safe(metrics.get("learning_latency_ms", 0)),
    }
    fig11 = go.Figure(
        go.Bar(
            x=list(latency_data.keys()),
            y=list(latency_data.values()),
            marker_color=["#636EFA", "#00CC96", "#AB63FA", "#FFA15A"],
            text=[f"{v:.0f}ms" for v in latency_data.values()],
            textposition="auto",
        )
    )
    fig11.update_layout(title="Component Latency", height=300, margin=dict(t=40, b=20, l=20, r=20))
    figs.append(fig11)

    # ── 12. Mutations Timeline (Scatter) ──
    if mutations:
        mut_ts = [m.get("timestamp", "")[11:19] for m in mutations[-10:]]
        mut_ops = [m.get("operation", "")[:30] for m in mutations[-10:]]
        mut_actors = [m.get("runtime", "") for m in mutations[-10:]]
        fig12 = go.Figure(
            go.Scatter(
                x=list(range(len(mutations[-10:]))),
                y=[1] * len(mutations[-10:]),
                mode="markers+text",
                text=mut_ops,
                textposition="top center",
                hovertext=mut_ts,
                marker=dict(
                    size=20,
                    color=[
                        {
                            "execute": "#00CC96",
                            "simulate": "#AB63FA",
                            "learn": "#FFA15A",
                        }.get(a, "#888888")
                        for a in mut_actors
                    ],
                ),
                textfont=dict(size=10),
            )
        )
    else:
        fig12 = go.Figure()
        fig12.add_annotation(text="No mutations recorded", showarrow=False, font=dict(size=20))
    fig12.update_layout(
        title="Mutation Timeline",
        height=300,
        margin=dict(t=40, b=20, l=20, r=20),
        xaxis_visible=False,
        yaxis_visible=False,
    )
    figs.append(fig12)

    return figs


def create_app(api_url: str = DEFAULT_API) -> Dash:
    app = Dash(__name__)
    app.title = "MonkeyBrain Cognitive Dashboard"

    app.layout = html.Div(
        [
            html.H1(
                "MonkeyBrain Cognitive Dashboard",
                style={
                    "textAlign": "center",
                    "color": "#f2f5fa",
                    "backgroundColor": "#1a1a2e",
                    "padding": "10px",
                },
            ),
            dcc.Interval(id="interval", interval=5000, n_intervals=0),
            html.Div(id="graphs-container", style={"padding": "10px"}),
        ],
        style={"backgroundColor": "#0d1117", "minHeight": "100vh"},
    )

    @app.callback(
        Output("graphs-container", "children"),
        Input("interval", "n_intervals"),
    )
    def update_dashboard(n):
        data = _fetch_dashboard(api_url)
        obs = _fetch_observability(api_url)
        figs = build_figures(data, obs)

        rows = []
        for i in range(0, len(figs), 3):
            row_figs = figs[i : i + 3]
            cols = []
            for fig in row_figs:
                cols.append(
                    html.Div(
                        dcc.Graph(figure=fig, style={"height": "350px"}),
                        style={
                            "width": "33.33%",
                            "display": "inline-block",
                            "verticalAlign": "top",
                        },
                    )
                )
            rows.append(html.Div(cols, style={"display": "flex", "flexWrap": "wrap"}))

        status = data.get("status", "unknown")
        run_id = data.get("run_id", "—")[:12]
        header = html.Div(
            [
                html.Span(
                    f"Status: {status}",
                    style={"color": "#00CC96", "marginRight": "20px"},
                ),
                html.Span(f"Run: {run_id}", style={"color": "#888888", "marginRight": "20px"}),
                html.Span(f"Refreshes every 5s", style={"color": "#555"}),
            ],
            style={
                "textAlign": "center",
                "padding": "5px",
                "color": "#f2f5fa",
                "backgroundColor": "#161b22",
            },
        )

        return [header] + rows

    return app


def main():
    parser = argparse.ArgumentParser(description="MonkeyBrain Cognitive Dashboard")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind")
    parser.add_argument("--api", default=DEFAULT_API, help="MonkeyBrain API URL")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    app = create_app(api_url=args.api)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    print(f"Polling API at {args.api}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
