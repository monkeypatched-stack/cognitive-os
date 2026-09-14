"""ASCII Graph Renderer — renders execution graphs using networkx + phart.

Used by --plan and --simulate to show the DAG structure visually.
"""

from __future__ import annotations


import networkx as nx
import phart


def render_ascii_graph(
    nodes: list[dict],
    edges: list[dict],
    execution_order: list[list[str]] | None = None,
    state: dict | None = None,
    resolved: dict | None = None,
    title: str = "GRAPH",
) -> str:
    """Render an execution graph as an ASCII diagram using phart."""
    if not nodes:
        return "  (empty graph)"

    # Build networkx graph — use agent name as node ID so phart renders it
    G = nx.DiGraph()
    id_to_label: dict[str, str] = {}
    for n in nodes:
        nid = n.get("id", "")
        label = _node_label(n)
        id_to_label[nid] = label
        G.add_node(label)

    for e in edges:
        src = e.get("from", "")
        dst = e.get("to", "")
        src_label = id_to_label.get(src, src)
        dst_label = id_to_label.get(dst, dst)
        if src_label and dst_label:
            G.add_edge(src_label, dst_label)

    # Use phart to render
    renderer = phart.ASCIIRenderer(G)
    graph_art = renderer.render()

    # Build output
    lines = [f"\033[1m═══ {title} ═══\033[0m", ""]
    for line in graph_art.splitlines():
        lines.append(f"  {line}")

    # Legend with status and resolution info
    if resolved or state:
        lines.append("")
        for n in nodes:
            nid = n.get("id", "")
            name = _node_label(n)
            parts = [f"  \033[1m{name}\033[0m"]

            node_state = (state or {}).get(nid, {})
            status = node_state.get("status", "")
            if status:
                sym = {
                    "complete": "\033[32m✔\033[0m",
                    "running": "\033[33m●\033[0m",
                    "failed": "\033[31m✘\033[0m",
                }.get(status, "\033[90m○\033[0m")
                parts.append(f"{sym} {status}")

            if resolved:
                src = resolved.get(n.get("agent", ""), "")
                if src:
                    icon = {
                        "local": "📦",
                        "openclaw": "🦞",
                        "n8n": "⚡",
                        "auto_generated": "🔧",
                        "builtin": "⚙️",
                    }.get(src, "")
                    parts.append(f"{icon} {src}")

            if len(parts) > 1:
                lines.append("  ".join(parts))

    lines.append("")
    lines.append(f"  \033[90mNodes: {len(nodes)}  Edges: {len(edges)}\033[0m")

    return "\n".join(lines)


def render_ascii_simulate_graph(
    nodes: list[dict],
    edges: list[dict],
    execution_order: list[list[str]] | None = None,
    state: dict | None = None,
    grounding_score: float = 0.0,
    needs_correction: bool = False,
) -> str:
    """Render simulate result with graph and scoring info."""
    graph_str = render_ascii_graph(nodes, edges, execution_order, state, title="SIMULATE")

    lines = [graph_str, ""]

    score_color = "\033[32m" if grounding_score >= 0.7 else "\033[33m" if grounding_score >= 0.4 else "\033[31m"
    lines.append(f"  Grounding  {score_color}{grounding_score:.2f}\033[0m")
    if needs_correction:
        lines.append(f"  Status     \033[33mNeeds correction\033[0m")
    else:
        lines.append(f"  Status     \033[32mOK\033[0m")

    return "\n".join(lines)


def _node_label(node: dict) -> str:
    """Extract display label from a node dict."""
    return node.get("name") or node.get("agent") or node.get("label") or node.get("id", "?")
