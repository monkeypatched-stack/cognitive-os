"""Generate governance review documents from somatic charts."""

from __future__ import annotations

from pathlib import Path


def generate_architecture_review(charts_dir: Path, output_dir: Path) -> Path:
    """Generate architecture review document from somatic charts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = ["# Architecture Review\n"]
    lines.append("Generated from somatic constitution charts.\n")

    # Collect modules and their dependencies
    modules = {}
    for chart_dir in sorted(charts_dir.iterdir()):
        if not chart_dir.is_dir():
            continue
        values_file = chart_dir / "values.yaml"
        if not values_file.exists():
            continue

        import yaml

        values = yaml.safe_load(values_file.read_text()) or {}
        module = values.get("module", {})
        if not module:
            continue

        name = module.get("name", chart_dir.name)
        layer = module.get("layer", "?")
        alias = module.get("alias", "")
        owns = module.get("owns", [])
        never_owns = module.get("never_owns", [])
        submodules = module.get("submodules", [])
        principles = values.get("principles", [])
        invariants = values.get("invariants", [])

        modules[name] = {
            "layer": layer,
            "alias": alias,
            "owns": owns,
            "never_owns": never_owns,
            "submodules": submodules,
            "principles": principles,
            "invariants": invariants,
        }

    # Layer diagram
    lines.append("## Layer Architecture\n")
    lines.append("```")
    lines.append("Layer 0: meta (constitutional preamble)")
    lines.append("Layer 1: monkey_brain (kernel + runtime)")
    lines.append("Layer 2: cortex, broca, introspection (cognitive core)")
    lines.append("Layer 3: cerebellum (capabilities & drivers)")
    lines.append("Layer 4: homeostasis, deepdive, cingulate (operations)")
    lines.append("Layer 5: sync (edge/cloud)")
    lines.append("```\n")

    # Module details
    lines.append("## Module Dependencies\n")
    for name, data in sorted(modules.items(), key=lambda x: str(x[1]["layer"])):
        lines.append(f"### {name} (Layer {data['layer']})")
        if data["alias"]:
            lines.append(f"- **Alias:** {data['alias']}")
        if data["owns"]:
            lines.append(f"- **Owns:** {', '.join(data['owns'])}")
        if data["never_owns"]:
            lines.append(f"- **Never Owns:** {', '.join(data['never_owns'])}")
        if data["principles"]:
            lines.append("- **Principles:**")
            for p in data["principles"]:
                lines.append(f"  - {p.get('statement', '?')}")
        if data["invariants"]:
            lines.append("- **Invariants:**")
            for inv in data["invariants"]:
                lines.append(f"  - [{inv.get('severity', '?')}] {inv.get('rule', '?')}: {inv.get('statement', '?')}")
        lines.append("")

    # Violations matrix
    lines.append("## Responsibility Violations Matrix\n")
    lines.append("| Module | Owns | Never Owns |")
    lines.append("|--------|------|------------|")
    for name, data in sorted(modules.items(), key=lambda x: str(x[1]["layer"])):
        owns = ", ".join(data["owns"][:3]) + ("..." if len(data["owns"]) > 3 else "")
        never = ", ".join(data["never_owns"][:3]) + ("..." if len(data["never_owns"]) > 3 else "")
        lines.append(f"| {name} | {owns} | {never} |")

    out_file = output_dir / "architecture-review.md"
    out_file.write_text("\n".join(lines))
    return out_file


def generate_governance_review(charts_dir: Path, output_dir: Path) -> Path:
    """Generate governance review document from somatic charts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = ["# Governance Review\n"]
    lines.append("Generated from somatic constitution charts.\n")

    import yaml

    # Collect all invariants across modules
    all_invariants = []
    all_principles = []
    all_deliverables = []

    for chart_dir in sorted(charts_dir.iterdir()):
        if not chart_dir.is_dir():
            continue
        values_file = chart_dir / "values.yaml"
        if not values_file.exists():
            continue

        values = yaml.safe_load(values_file.read_text()) or {}
        module = values.get("module", {})
        name = module.get("name", chart_dir.name)

        for inv in values.get("invariants", []):
            all_invariants.append({"module": name, **inv})
        for p in values.get("principles", []):
            all_principles.append({"module": name, **p})
        for d in values.get("deliverables", []):
            if isinstance(d, str):
                all_deliverables.append({"module": name, "artifact": d})
            else:
                all_deliverables.append({"module": name, **d})

    # Summary
    lines.append("## Summary\n")
    lines.append(f"- **Total Invariants:** {len(all_invariants)}")
    lines.append(f"- **Total Principles:** {len(all_principles)}")
    lines.append(f"- **Total Deliverables:** {len(all_deliverables)}")
    critical = [i for i in all_invariants if i.get("severity") == "critical"]
    high = [i for i in all_invariants if i.get("severity") == "high"]
    lines.append(f"- **Critical Invariants:** {len(critical)}")
    lines.append(f"- **High Invariants:** {len(high)}")
    lines.append("")

    # Critical invariants
    lines.append("## Critical Invariants\n")
    lines.append("| Module | Rule | Statement | Rejection |")
    lines.append("|--------|------|-----------|-----------|")
    for inv in critical:
        lines.append(
            f"| {inv['module']} | {inv.get('rule', '?')} | {inv.get('statement', '?')[:60]} | {inv.get('rejection', '?')[:40]} |"
        )

    # All principles
    lines.append("\n## Principles by Module\n")
    by_module = {}
    for p in all_principles:
        by_module.setdefault(p["module"], []).append(p)
    for mod, principles in sorted(by_module.items()):
        lines.append(f"### {mod}")
        for p in principles:
            lines.append(f"- {p.get('statement', '?')}")
        lines.append("")

    # Deliverables
    lines.append("## Deliverables\n")
    lines.append("| Module | Artifact |")
    lines.append("|--------|----------|")
    for d in all_deliverables:
        lines.append(f"| {d['module']} | {d.get('artifact', '?')} |")

    out_file = output_dir / "governance-review.md"
    out_file.write_text("\n".join(lines))
    return out_file


def generate_coding_standards_review(charts_dir: Path, output_dir: Path) -> Path:
    """Generate coding standards review document."""
    output_dir.mkdir(parents=True, exist_ok=True)

    import yaml

    lines = ["# Coding Standards Review\n"]
    lines.append("Generated from somatic capability and agent charts.\n")

    # Count capabilities and agents
    cap_count = 0
    agent_count = 0
    all_ops = []

    for chart_dir in sorted(charts_dir.rglob("values.yaml")):
        values = yaml.safe_load(chart_dir.read_text()) or {}
        if values.get("capability"):
            cap_count += 1
            ops = values.get("operations", [])
            all_ops.extend([(chart_dir.parent.name, o) for o in ops])
        elif values.get("agent"):
            agent_count += 1

    lines.append("## Summary\n")
    lines.append(f"- **Capabilities:** {cap_count}")
    lines.append(f"- **Agents:** {agent_count}")
    lines.append(f"- **Total Operations:** {len(all_ops)}")
    lines.append("")

    lines.append("## Capability Operations\n")
    lines.append("| Capability | Operation | Method | Description |")
    lines.append("|------------|-----------|--------|-------------|")
    for cap_name, op in all_ops[:30]:
        lines.append(
            f"| {cap_name} | {op.get('name', '?')} | {op.get('method', '?')} | {op.get('description', '?')[:50]} |"
        )

    lines.append("\n## Standards Checklist\n")
    lines.append("- [ ] All public APIs have type hints")
    lines.append("- [ ] All functions have docstrings")
    lines.append("- [ ] No hardcoded secrets")
    lines.append("- [ ] Error handling covers all failure modes")
    lines.append("- [ ] Circuit breakers on external calls")
    lines.append("- [ ] Structured logging with trace IDs")
    lines.append("- [ ] Health checks for all services")
    lines.append("- [ ] Rate limiting on all endpoints")
    lines.append("")

    out_file = output_dir / "coding-standards-review.md"
    out_file.write_text("\n".join(lines))
    return out_file
