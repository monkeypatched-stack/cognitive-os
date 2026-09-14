"""Plotly Dashboard — generates an interactive HTML dashboard from DashboardModel.

Usage:
    from src.monkey_brain.dashboard.plotly_render import render_dashboard
    html_path = render_dashboard(dashboard_dict, output_path="/tmp/dashboard.html")
"""

from __future__ import annotations

from typing import Any


def _safe(v: Any, default: Any = 0) -> Any:
    return v if v is not None else default


def render_dashboard(data: dict[str, Any], output_path: str = "/tmp/monkeybrain_dashboard.html") -> str:
    """Render a full Plotly dashboard as a standalone HTML file."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    planning = data.get("planning", {})
    execution = data.get("execution", {})
    simulation = data.get("simulation", {})
    learning = data.get("learning", {})
    bellman = data.get("bellman", {})
    metrics = data.get("metrics", {})
    health = data.get("health", {})
    graph = data.get("graph", {})

    counters = metrics.get("counters", {})
    gauges = metrics.get("gauges", {})
    histograms = metrics.get("histograms", {})

    fig = make_subplots(
        rows=5,
        cols=3,
        subplot_titles=(
            "Planning Graph",
            "Execution Status",
            "Simulation Consensus",
            "Learning Loss",
            "Bellman Q-Values",
            "Health",
            "Counter Metrics",
            "Gauge Metrics",
            "Histogram Distributions",
            "Graph Execution Order",
            "Metric Timeline",
            "Component Latency",
        ),
        specs=[
            [{"type": "pie"}, {"type": "bar"}, {"type": "bar"}],
            [{"type": "bar"}, {"type": "bar"}, {"type": "indicator"}],
            [{"type": "bar"}, {"type": "bar"}, {"type": "bar"}],
            [{"type": "bar"}, {"type": "scatter"}, {"type": "bar"}],
            [{"type": "table"}, {"type": "table"}, {"type": "table"}],
        ],
        vertical_spacing=0.06,
        horizontal_spacing=0.06,
    )

    # ── Row 1: Planning Graph, Execution Status, Simulation Consensus ──

    nodes = graph.get("nodes", [])
    fig.add_trace(
        go.Pie(
            labels=[n.get("agent", n.get("node_id", "?")) for n in nodes],
            values=[1] * len(nodes),
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
        ),
        row=1,
        col=1,
    )

    exec_completed = _safe(execution.get("completed_nodes", 0))
    exec_running = _safe(execution.get("running_nodes", 0))
    exec_failed = _safe(execution.get("failed_nodes", 0))
    fig.add_trace(
        go.Bar(
            x=["Completed", "Running", "Failed"],
            y=[exec_completed, exec_running, exec_failed],
            marker_color=["#00CC96", "#FFA15A", "#EF553B"],
            text=[exec_completed, exec_running, exec_failed],
            textposition="auto",
        ),
        row=1,
        col=2,
    )

    solver_results = simulation.get("solver_results", [])
    if solver_results:
        solver_names = [s.get("solver_name", "?") for s in solver_results]
        solver_confs = [s.get("confidence", 0) for s in solver_results]
        fig.add_trace(
            go.Bar(
                x=solver_names,
                y=solver_confs,
                marker_color="#636EFA",
                text=[f"{c:.2f}" for c in solver_confs],
                textposition="auto",
            ),
            row=1,
            col=3,
        )
    else:
        fig.add_trace(
            go.Bar(
                x=["No solvers"],
                y=[0],
                marker_color="#888888",
            ),
            row=1,
            col=3,
        )

    # ── Row 2: Learning Loss, Bellman Q-Values, Health ──

    loss_names = ["Topological", "Epistemic", "Perplexity"]
    loss_vals = [
        _safe(learning.get("topological_loss", 0)),
        _safe(learning.get("epistemic_loss", 0)),
        _safe(learning.get("perplexity", 0)),
    ]
    fig.add_trace(
        go.Bar(
            x=loss_names,
            y=loss_vals,
            marker_color=["#636EFA", "#EF553B", "#FFA15A"],
            text=[f"{v:.3f}" for v in loss_vals],
            textposition="auto",
        ),
        row=2,
        col=1,
    )

    q_values = bellman.get("q_values", {})
    if q_values:
        sorted_q = sorted(q_values.items(), key=lambda x: -x[1])[:10]
        q_agents = [q[0] for q in sorted_q]
        q_vals = [q[1] for q in sorted_q]
        fig.add_trace(
            go.Bar(
                x=q_agents,
                y=q_vals,
                marker_color="#00CC96",
                text=[f"{v:.2f}" for v in q_vals],
                textposition="auto",
            ),
            row=2,
            col=2,
        )

    components = health.get("components", [])
    if components:
        comp_status = [c.get("status", "unknown") for c in components]
        fig.add_trace(
            go.Indicator(
                mode="number",
                value=sum(1 for s in comp_status if s == "healthy"),
                title={"text": f"Healthy / {len(components)}"},
                number={"font": {"color": "#00CC96"}},
            ),
            row=2,
            col=3,
        )

    # ── Row 3: Counter Metrics, Gauge Metrics, Histogram Distributions ──

    counter_keys = list(counters.keys())[:12]
    counter_vals = [counters[k] for k in counter_keys]
    short_keys = [k.split(".")[-1][:15] for k in counter_keys]
    fig.add_trace(
        go.Bar(
            x=short_keys,
            y=counter_vals,
            marker_color="#AB63FA",
            text=counter_vals,
            textposition="auto",
        ),
        row=3,
        col=1,
    )

    gauge_keys = list(gauges.keys())[:12]
    gauge_vals = [gauges[k] for k in gauge_keys]
    short_gauge_keys = [k.split(".")[-1][:15] for k in gauge_keys]
    fig.add_trace(
        go.Bar(
            x=short_gauge_keys,
            y=gauge_vals,
            marker_color="#19D3F3",
            text=[f"{v:.1f}" for v in gauge_vals],
            textposition="auto",
        ),
        row=3,
        col=2,
    )

    hist_keys = list(histograms.keys())[:8]
    if hist_keys:
        hist_names = [k.split(".")[-1][:15] for k in hist_keys]
        hist_avgs = [histograms[k].get("avg", 0) if isinstance(histograms[k], dict) else 0 for k in hist_keys]
        hist_counts = [histograms[k].get("count", 0) if isinstance(histograms[k], dict) else 0 for k in hist_keys]
        fig.add_trace(
            go.Bar(
                x=hist_names,
                y=hist_avgs,
                name="Avg",
                marker_color="#FF6692",
            ),
            row=3,
            col=3,
        )
        fig.add_trace(
            go.Bar(
                x=hist_names,
                y=hist_counts,
                name="Count",
                marker_color="#B6E880",
            ),
            row=3,
            col=3,
        )

    # ── Row 4: Graph Execution Order, Metric Timeline, Component Latency ──

    execution_order = graph.get("execution_order", [])
    if execution_order:
        step_names = []
        step_counts = []
        id_to_agent = {n.get("node_id", ""): n.get("agent", "") for n in nodes}
        for i, batch in enumerate(execution_order):
            if isinstance(batch, list):
                agents = [id_to_agent.get(nid, nid) for nid in batch]
                step_names.append(f"Step {i + 1}")
                step_counts.append(len(agents))
        fig.add_trace(
            go.Bar(
                x=step_names,
                y=step_counts,
                marker_color="#636EFA",
                text=step_counts,
                textposition="auto",
            ),
            row=4,
            col=1,
        )

    fig.add_trace(
        go.Scatter(
            x=[f"Counter {i}" for i in range(min(len(counters), 20))],
            y=list(counters.values())[:20],
            mode="lines+markers",
            marker=dict(color="#EF553B"),
        ),
        row=4,
        col=2,
    )

    latency_data = {
        "Planner": _safe(metrics.get("planner_latency_ms", 0)),
        "Execution": _safe(metrics.get("execution_latency_ms", 0)),
        "Simulation": _safe(metrics.get("simulation_latency_ms", 0)),
        "Learning": _safe(metrics.get("learning_latency_ms", 0)),
    }
    fig.add_trace(
        go.Bar(
            x=list(latency_data.keys()),
            y=list(latency_data.values()),
            marker_color=["#636EFA", "#00CC96", "#AB63FA", "#FFA15A"],
            text=[f"{v:.0f}ms" for v in latency_data.values()],
            textposition="auto",
        ),
        row=4,
        col=3,
    )

    # ── Row 5: Summary Tables ──

    fig.add_trace(
        go.Table(
            header=dict(values=["Metric", "Value"], fill_color="#636EFA", font_color="white"),
            cells=dict(
                values=[
                    ["Graph ID", "Nodes", "Edges", "Depth", "Intent", "Confidence"],
                    [
                        _safe(graph.get("graph_id", "—")),
                        _safe(graph.get("node_count", 0)),
                        _safe(graph.get("edge_count", 0)),
                        _safe(graph.get("depth", 0)),
                        _safe(planning.get("intent", "—")),
                        f"{_safe(planning.get('intent_confidence', 0)):.2f}",
                    ],
                ]
            ),
        ),
        row=5,
        col=1,
    )

    fig.add_trace(
        go.Table(
            header=dict(values=["Agent", "Q-Value"], fill_color="#00CC96", font_color="white"),
            cells=dict(
                values=[
                    list(q_values.keys())[:8],
                    [f"{v:.3f}" for v in list(q_values.values())[:8]],
                ]
            ),
        ),
        row=5,
        col=2,
    )

    fig.add_trace(
        go.Table(
            header=dict(values=["Component", "Status"], fill_color="#AB63FA", font_color="white"),
            cells=dict(
                values=[
                    [c.get("name", "?") for c in components],
                    [c.get("status", "?") for c in components],
                ]
            ),
        ),
        row=5,
        col=3,
    )

    fig.update_layout(
        height=2200,
        width=1600,
        title_text=f"MonkeyBrain Cognitive Dashboard — {data.get('run_id', 'current')}",
        title_font_size=20,
        showlegend=False,
        template="plotly_dark",
    )

    fig.write_html(output_path, include_plotlyjs=True)
    return output_path
