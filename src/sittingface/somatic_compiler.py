"""SittingFace — Self-bootstrapping pipeline for MonkeyBrain.

Reads somatic charts, registers capabilities/agents into the Runtime,
compiles prompts, runs cingulate review, and generates code.

This is the bridge between somatic charts and the live MonkeyBrain system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SomaticChart:
    """Parsed somatic chart — capability, agent, or module."""

    name: str
    chart_type: str  # module | capability | agent
    values: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""


@dataclass
class CompiledPrompt:
    """A compiled prompt from a somatic chart."""

    chart_name: str
    preamble: str = ""
    cot_steps: list[dict] = field(default_factory=list)
    review_gate: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)


class SomaticCompiler:
    """Compiles somatic charts into MonkeyBrain registrations and prompts."""

    def __init__(self, charts_dir: Path | None = None):
        if charts_dir is None:
            p = Path.cwd()
            for _ in range(10):
                if (p / "somatic").exists():
                    charts_dir = p / "somatic" / "charts"
                    break
                if p.parent == p:
                    break
                p = p.parent
        self.charts_dir = charts_dir or Path("somatic/charts")
        self.charts: list[SomaticChart] = []
        self.prompts: list[CompiledPrompt] = []

    def load_path(self, path: Path) -> list[SomaticChart]:
        """Load from a single values.yaml file, a chart folder, or a charts directory.

        - Single file (values.yaml or *.yaml): load that one chart.
        - Folder with values.yaml: load that single chart.
        - Folder without values.yaml: treat as a charts directory (recurse via load_all).
        """
        self.charts = []
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Chart path not found: {path}")

        if path.is_file():
            # Single values.yaml
            self._load_chart(path.parent, path)
            return self.charts

        # Folder
        values_file = path / "values.yaml"
        if values_file.exists():
            # Single chart folder
            self._load_chart(path, values_file)
            return self.charts

        # Charts directory — delegate to load_all with this path as charts_dir
        original = self.charts_dir
        self.charts_dir = path
        self.load_all()
        self.charts_dir = original
        return self.charts

    def load_all(self) -> list[SomaticChart]:
        """Load all somatic charts from charts_dir."""
        self.charts = []
        if not self.charts_dir.exists():
            return self.charts

        # Load top-level module charts
        for chart_dir in sorted(self.charts_dir.iterdir()):
            if chart_dir.is_dir():
                values_file = chart_dir / "values.yaml"
                if values_file.exists():
                    self._load_chart(chart_dir, values_file)

        # Load cerebellum capabilities
        cap_dir = self.charts_dir / "cerebellum" / "capabilities"
        if cap_dir.exists():
            for d in sorted(cap_dir.iterdir()):
                if d.is_dir() and (d / "values.yaml").exists():
                    self._load_chart(d, d / "values.yaml")

        # Load broca agents
        agent_dir = self.charts_dir / "broca" / "agents"
        if agent_dir.exists():
            for d in sorted(agent_dir.iterdir()):
                if d.is_dir() and (d / "values.yaml").exists():
                    self._load_chart(d, d / "values.yaml")

        return self.charts

    def _load_chart(self, chart_dir: Path, values_file: Path) -> None:
        import yaml

        values = yaml.safe_load(values_file.read_text()) or {}

        has_module = bool(values.get("module", {}).get("name"))
        has_capability = bool(values.get("capability", {}).get("name"))
        has_agent = bool(values.get("agent", {}).get("name"))

        if not (has_module or has_capability or has_agent):
            return

        name = (
            values.get("module", {}).get("name")
            or values.get("capability", {}).get("name")
            or values.get("agent", {}).get("name")
        )
        if has_capability:
            chart_type = "capability"
        elif has_agent:
            chart_type = "agent"
        else:
            chart_type = "module"

        self.charts.append(
            SomaticChart(
                name=name,
                chart_type=chart_type,
                values=values,
                source_path=str(chart_dir),
            )
        )

    def compile_prompts(self) -> list[CompiledPrompt]:
        """Extract ConstitutionalPrompt resources from charts."""
        self.prompts = []
        for chart in self.charts:
            if chart.chart_type == "module":
                prompt = chart.values.get("cot", {})
                if prompt and prompt.get("steps"):
                    self.prompts.append(
                        CompiledPrompt(
                            chart_name=chart.name,
                            preamble=chart.values.get("preamble", {}).get("statement", ""),
                            cot_steps=prompt.get("steps", []),
                            review_gate=chart.values.get("reviewGate", {}),
                            constraints=[inv.get("id", "") for inv in chart.values.get("invariants", [])],
                        )
                    )
        return self.prompts

    def register_capabilities(self, runtime) -> list[str]:
        """Register capabilities from somatic charts into the Runtime."""
        registered = []
        for chart in self.charts:
            if chart.chart_type != "capability":
                continue
            try:
                cap = self._chart_to_capability(chart)
                if cap:
                    runtime.register(cap)
                    registered.append(chart.name)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to register capability {chart.name}: {e}")
        return registered

    def _chart_to_capability(self, chart: SomaticChart):
        """Convert a somatic capability chart into an ICapability."""
        from src.monkey_brain.kernel.capability_interface import ICapability
        from src.monkey_brain.kernel.execute.runtime.state import CapabilityResult

        cap_data = chart.values.get("capability", {})
        operations = chart.values.get("operations", [])

        cap_name = cap_data.get("name", chart.name)
        cap_type = cap_data.get("platform", "generic")
        cap_ops = list(operations)

        def make_execute(nm, ops):
            async def execute(self, state, **kwargs):
                return CapabilityResult(
                    success=True,
                    output={"capability": nm, "operations": len(ops)},
                    metadata={"source": "somatic_chart"},
                )

            return execute

        def make_can_execute(self, state):
            return True

        def make_estimate_reward(self, state):
            return 0.5

        def make_estimate_cost(self, state):
            return 0.1

        cls = type(
            f"Capability_{cap_name}",
            (ICapability,),
            {
                "capability_name": property(lambda self: cap_name),
                "capability_type": property(lambda self: cap_type),
                "name": cap_name,
                "execute": make_execute(cap_name, cap_ops),
                "can_execute": make_can_execute,
                "estimate_reward": make_estimate_reward,
                "estimate_cost": make_estimate_cost,
            },
        )
        return cls()

    def write_prompts(self, output_dir: Path) -> list[Path]:
        """Write each compiled prompt to its own file under output_dir.

        Each file is named <chart_name>.prompt.md and contains:
          - YAML front-matter (constraints, review gate)
          - preamble section
          - numbered chain-of-thought steps
        Returns the list of written paths.
        """
        import yaml as _yaml

        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []

        for prompt in self.prompts:
            safe_name = prompt.chart_name.replace("/", "_").replace(" ", "_")
            path = output_dir / f"{safe_name}.prompt.md"

            front: dict = {}
            if prompt.constraints:
                front["constraints"] = prompt.constraints
            if prompt.review_gate:
                front["review_gate"] = prompt.review_gate

            lines: list[str] = []
            if front:
                lines.append("---")
                lines.append(_yaml.dump(front, default_flow_style=False).rstrip())
                lines.append("---")
                lines.append("")

            lines.append(f"# {prompt.chart_name}")
            lines.append("")

            if prompt.preamble:
                lines.append("## Preamble")
                lines.append("")
                lines.append(prompt.preamble.strip())
                lines.append("")

            if prompt.cot_steps:
                lines.append("## Chain of Thought")
                lines.append("")
                for i, step in enumerate(prompt.cot_steps, 1):
                    if isinstance(step, dict):
                        title = step.get("title") or step.get("name") or step.get("step") or f"Step {i}"
                        desc = step.get("description") or step.get("prompt") or step.get("action") or ""
                        lines.append(f"### {i}. {title}")
                        if desc:
                            lines.append("")
                            lines.append(str(desc).strip())
                        lines.append("")
                    else:
                        lines.append(f"### {i}. {step}")
                        lines.append("")

            path.write_text("\n".join(lines))
            written.append(path)

        return written

    def summary(self) -> dict:
        """Summary of what was loaded and compiled."""
        return {
            "total_charts": len(self.charts),
            "by_type": {
                "module": len([c for c in self.charts if c.chart_type == "module"]),
                "capability": len([c for c in self.charts if c.chart_type == "capability"]),
                "agent": len([c for c in self.charts if c.chart_type == "agent"]),
            },
            "prompts_compiled": len(self.prompts),
            "chart_names": [c.name for c in self.charts],
        }

    def search(self, query: str) -> list[dict]:
        """Keyword search over every loaded chart — name, chart_type, and
        every string/list-of-strings value nested anywhere in `values`
        (module.description, owns/neverOwns, capability.description/tags,
        principles/invariants statements, etc.). Same approach
        SittingFaceRegistry.search() already uses for the chart registry
        (packages/sittingface/sittingface/registry.py) — applied here to
        SomaticCompiler's in-memory loaded charts instead of the on-disk
        registry index, since this is what explore_knowledge_base() actually
        has loaded at query time.

        Returns one dict per matching chart: {name, chart_type, source_path,
        matched_in: [dotted paths of fields that matched, for context]}.
        """
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        results: list[dict] = []
        for chart in self.charts:
            matched_in = []
            if query_lower in chart.name.lower():
                matched_in.append("name")
            if query_lower in chart.chart_type.lower():
                matched_in.append("chart_type")
            matched_in.extend(self._find_matches(chart.values, query_lower))

            if matched_in:
                results.append(
                    {
                        "name": chart.name,
                        "chart_type": chart.chart_type,
                        "source_path": chart.source_path,
                        "matched_in": matched_in,
                    }
                )
        return results

    @staticmethod
    def _find_matches(node: Any, query_lower: str, path: str = "") -> list[str]:
        """Recursively collect dotted-path locations where query_lower
        appears as a substring of a string value (case-insensitive).
        """
        matches: list[str] = []
        if isinstance(node, str):
            if query_lower in node.lower():
                matches.append(path or "value")
        elif isinstance(node, dict):
            for key, value in node.items():
                matches.extend(SomaticCompiler._find_matches(value, query_lower, f"{path}.{key}" if path else str(key)))
        elif isinstance(node, (list, tuple)):
            for i, value in enumerate(node):
                matches.extend(SomaticCompiler._find_matches(value, query_lower, f"{path}[{i}]"))
        return matches
