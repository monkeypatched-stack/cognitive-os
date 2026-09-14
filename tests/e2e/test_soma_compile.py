"""Tests for somatic chart compilation to prompt.

Tests run in two modes:
  - Unit: directly instantiate SomaticCompiler (no running services needed)
  - E2E:  hit the live MonkeyBrain /somatic/* endpoints
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO / "src"))

AGENTOS_URL = os.getenv("AGENTOS_URL", "http://localhost:8031")


def _reachable(url: str) -> bool:
    try:
        import httpx

        return httpx.get(f"{url}/health", timeout=3.0).status_code < 500
    except Exception:
        return False


# ── Unit tests (no network) ───────────────────────────────────────────────────


class TestSomaticCompilerUnit:
    """Direct SomaticCompiler tests — no running services required."""

    @pytest.fixture(scope="class")
    def compiler(self):
        from sittingface.somatic_compiler import SomaticCompiler

        c = SomaticCompiler()
        c.load_all()
        return c

    def test_charts_dir_resolved(self, compiler):
        assert compiler.charts_dir.exists(), f"charts_dir not found: {compiler.charts_dir}"

    def test_load_all_returns_charts(self, compiler):
        assert len(compiler.charts) > 0, "No charts loaded"

    def test_charts_have_required_fields(self, compiler):
        for chart in compiler.charts:
            assert chart.name, f"Chart missing name: {chart}"
            assert chart.chart_type in (
                "module",
                "capability",
                "agent",
            ), f"Unknown chart_type '{chart.chart_type}' in {chart.name}"
            assert chart.values, f"Chart {chart.name} has empty values"

    def test_compile_prompts_returns_list(self, compiler):
        prompts = compiler.compile_prompts()
        assert isinstance(prompts, list)

    def test_compiled_prompts_have_cot_steps(self, compiler):
        prompts = compiler.compile_prompts()
        for p in prompts:
            assert p.chart_name, "Prompt missing chart_name"
            assert isinstance(p.cot_steps, list), f"cot_steps not a list in {p.chart_name}"
            assert len(p.cot_steps) > 0, f"Prompt {p.chart_name} has no CoT steps"

    def test_compiled_prompts_have_preamble(self, compiler):
        prompts = compiler.compile_prompts()
        for p in prompts:
            assert isinstance(p.preamble, str), f"preamble not str in {p.chart_name}"

    def test_summary_structure(self, compiler):
        compiler.compile_prompts()
        s = compiler.summary()
        assert "total_charts" in s
        assert "by_type" in s
        assert "prompts_compiled" in s
        assert s["total_charts"] == len(compiler.charts)
        assert s["prompts_compiled"] == len(compiler.prompts)

    def test_summary_counts_by_type(self, compiler):
        s = compiler.summary()
        by_type = s["by_type"]
        total = sum(by_type.values())
        assert total == s["total_charts"], f"by_type sum {total} != total_charts {s['total_charts']}"

    def test_capability_charts_loaded(self, compiler):
        caps = [c for c in compiler.charts if c.chart_type == "capability"]
        assert len(caps) > 0, "No capability charts loaded"

    def test_agent_charts_loaded(self, compiler):
        agents = [c for c in compiler.charts if c.chart_type == "agent"]
        assert len(agents) > 0, "No agent charts loaded"

    def test_module_charts_loaded(self, compiler):
        modules = [c for c in compiler.charts if c.chart_type == "module"]
        assert len(modules) > 0, "No module charts loaded"

    def test_each_prompt_maps_to_a_loaded_chart(self, compiler):
        prompts = compiler.compile_prompts()
        chart_names = {c.name for c in compiler.charts}
        for p in prompts:
            assert p.chart_name in chart_names, f"Prompt references unknown chart: {p.chart_name}"

    def test_idempotent_compile(self, compiler):
        """Compiling twice returns the same number of prompts."""
        first = len(compiler.compile_prompts())
        second = len(compiler.compile_prompts())
        assert first == second

    def test_chart_names_are_unique(self, compiler):
        names = [c.name for c in compiler.charts]
        duplicates = {n for n in names if names.count(n) > 1}
        assert not duplicates, f"Duplicate chart names: {duplicates}"

    # ── write_prompts tests ──────────────────────────────────────────────────

    def test_write_prompts_creates_files(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        assert len(written) == len(compiler.prompts)
        for path in written:
            assert path.exists(), f"Expected file not created: {path}"

    def test_written_files_are_markdown(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        for path in written:
            assert path.suffix == ".md", f"Expected .md, got {path.suffix}"
            assert path.name.endswith(".prompt.md")

    def test_written_files_contain_chart_name(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        for path, prompt in zip(written, compiler.prompts):
            content = path.read_text()
            assert prompt.chart_name in content, f"{path.name} missing chart_name header"

    def test_written_files_contain_cot_steps(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        for path, prompt in zip(written, compiler.prompts):
            if prompt.cot_steps:
                content = path.read_text()
                assert "Chain of Thought" in content, f"{path.name} missing CoT section"

    def test_written_files_contain_preamble(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        for path, prompt in zip(written, compiler.prompts):
            if prompt.preamble:
                content = path.read_text()
                assert "Preamble" in content, f"{path.name} missing Preamble section"

    def test_written_files_with_constraints_have_frontmatter(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        for path, prompt in zip(written, compiler.prompts):
            if prompt.constraints:
                content = path.read_text()
                assert content.startswith("---"), f"{path.name} has constraints but no YAML frontmatter"

    def test_write_prompts_idempotent(self, compiler, tmp_path):
        """Writing twice produces the same files (no duplicates)."""
        compiler.compile_prompts()
        first = compiler.write_prompts(tmp_path)
        second = compiler.write_prompts(tmp_path)
        assert len(first) == len(second)
        assert {p.name for p in first} == {p.name for p in second}

    def test_write_prompts_one_file_per_prompt(self, compiler, tmp_path):
        compiler.compile_prompts()
        written = compiler.write_prompts(tmp_path)
        files = list(tmp_path.glob("*.prompt.md"))
        assert len(files) == len(compiler.prompts), f"Expected {len(compiler.prompts)} files, found {len(files)}"

    # ── load_path tests ──────────────────────────────────────────────────────

    def test_load_path_single_file(self, tmp_path):
        """load_path with a values.yaml file loads exactly one chart."""
        from sittingface.somatic_compiler import SomaticCompiler

        # Find a real values.yaml
        compiler = SomaticCompiler()
        compiler.load_all()
        if not compiler.charts:
            pytest.skip("No charts available to test load_path")
        sample = Path(compiler.charts[0].source_path) / "values.yaml"
        if not sample.exists():
            pytest.skip("Chart source path not accessible")

        fresh = SomaticCompiler()
        charts = fresh.load_path(sample)
        assert len(charts) == 1
        assert charts[0].name == compiler.charts[0].name

    def test_load_path_chart_folder(self, tmp_path):
        """load_path with a chart folder loads exactly one chart."""
        from sittingface.somatic_compiler import SomaticCompiler

        compiler = SomaticCompiler()
        compiler.load_all()
        if not compiler.charts:
            pytest.skip("No charts available")
        folder = Path(compiler.charts[0].source_path)
        if not (folder / "values.yaml").exists():
            pytest.skip("Chart folder not accessible")

        fresh = SomaticCompiler()
        charts = fresh.load_path(folder)
        assert len(charts) == 1

    def test_load_path_charts_directory(self):
        """load_path with the full charts dir loads the same set as load_all."""
        from sittingface.somatic_compiler import SomaticCompiler

        compiler = SomaticCompiler()
        all_charts = compiler.load_all()

        fresh = SomaticCompiler()
        path_charts = fresh.load_path(compiler.charts_dir)
        assert len(path_charts) == len(all_charts)

    def test_load_path_missing_raises(self, tmp_path):
        from sittingface.somatic_compiler import SomaticCompiler

        compiler = SomaticCompiler()
        with pytest.raises(FileNotFoundError):
            compiler.load_path(tmp_path / "does_not_exist")


# ── E2E tests (requires live MonkeyBrain) ────────────────────────────────────


@pytest.mark.skipif(not _reachable(AGENTOS_URL), reason=f"MonkeyBrain not running at {AGENTOS_URL}")
class TestSomaticEndpointsE2E:
    """Test the live /somatic/* endpoints."""

    @pytest.fixture(scope="class")
    def brain(self):
        import httpx

        return httpx.Client(base_url=AGENTOS_URL, timeout=15.0)

    def test_charts_endpoint_returns_list(self, brain):
        r = brain.get("/somatic/charts")
        assert r.status_code == 200
        body = r.json()
        charts = body if isinstance(body, list) else body.get("charts", [])
        assert isinstance(charts, list)
        assert len(charts) > 0

    def test_prompts_endpoint_returns_list(self, brain):
        r = brain.get("/somatic/prompts")
        assert r.status_code == 200
        body = r.json()
        prompts = body if isinstance(body, list) else body.get("prompts", [])
        assert isinstance(prompts, list)

    def test_capabilities_endpoint_returns_list(self, brain):
        r = brain.get("/somatic/capabilities")
        assert r.status_code == 200
        body = r.json()
        caps = body if isinstance(body, list) else body.get("capabilities", [])
        assert isinstance(caps, list)
        assert len(caps) > 0

    def test_recompile_returns_summary(self, brain):
        r = brain.post("/somatic/recompile")
        assert r.status_code == 200
        body = r.json()
        assert "charts" in body or "total_charts" in body or "status" in body

    def test_recompile_counts_match_charts(self, brain):
        recompile = brain.post("/somatic/recompile").json()
        charts_resp = brain.get("/somatic/charts").json()
        chart_count = len(charts_resp) if isinstance(charts_resp, list) else len(charts_resp.get("charts", []))
        compiled_count = recompile.get("charts") or recompile.get("total_charts", 0)
        assert compiled_count == chart_count, (
            f"Recompile says {compiled_count} charts but /somatic/charts shows {chart_count}"
        )

    def test_prompts_have_cot_steps_after_recompile(self, brain):
        brain.post("/somatic/recompile")
        r = brain.get("/somatic/prompts")
        assert r.status_code == 200
        body = r.json()
        prompts = body if isinstance(body, list) else body.get("prompts", [])
        for p in prompts:
            steps = p.get("cot_steps") or p.get("steps") or []
            assert isinstance(steps, list), f"Prompt {p.get('chart_name')} has non-list cot_steps"


# ── CLI command test ──────────────────────────────────────────────────────────


class TestSomaCompileCLI:
    """Test the `monkeypatched make compile` command via subprocess."""

    def test_compile_exits_zero(self, tmp_path):
        import subprocess

        result = subprocess.run(
            ["monkeypatched", "soma", "compile", "--output-dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"compile failed:\n{result.stdout}\n{result.stderr}"

    def test_compile_prints_chart_count(self, tmp_path):
        import subprocess

        result = subprocess.run(
            ["monkeypatched", "soma", "compile", "--output-dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert "Charts loaded" in result.stdout or "charts" in result.stdout.lower()

    def test_compile_writes_prompt_files(self, tmp_path):
        import subprocess

        result = subprocess.run(
            ["monkeypatched", "soma", "compile", "--output-dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        files = list(tmp_path.glob("*.prompt.md"))
        assert len(files) > 0, f"No .prompt.md files written to {tmp_path}"

    def test_compile_prompt_files_are_readable_markdown(self, tmp_path):
        import subprocess

        subprocess.run(
            ["monkeypatched", "soma", "compile", "--output-dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        for f in tmp_path.glob("*.prompt.md"):
            content = f.read_text()
            assert len(content) > 0, f"{f.name} is empty"
            assert "#" in content, f"{f.name} has no markdown headings"

    def test_compile_json_output_is_valid(self, tmp_path):
        import subprocess

        result = subprocess.run(
            [
                "monkeypatched",
                "soma",
                "compile",
                "--json",
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "total_charts" in data
        assert "prompts_compiled" in data
        assert "files_written" in data
        assert data["total_charts"] > 0

    def test_compile_json_files_written_matches_disk(self, tmp_path):
        import subprocess

        result = subprocess.run(
            [
                "monkeypatched",
                "soma",
                "compile",
                "--json",
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        files_on_disk = list(tmp_path.glob("*.prompt.md"))
        assert len(data["files_written"]) == len(files_on_disk)

    def test_compile_json_prompts_have_required_keys(self, tmp_path):
        import subprocess

        result = subprocess.run(
            [
                "monkeypatched",
                "soma",
                "compile",
                "--json",
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        for p in data.get("prompts", []):
            assert "chart" in p
            assert "file" in p
            assert "cot_steps" in p
            assert isinstance(p["cot_steps"], int)
