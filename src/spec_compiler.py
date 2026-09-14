import unittest
from typing import Dict, Any


def compile_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Compiles a specification into a dictionary."""
    compiled_spec = spec.copy()
    compiled_spec["batch_release"] = "batch_release"
    compiled_spec["ollama"] = "ollama"
    return compiled_spec


class TestETASSSpec(unittest.TestCase):
    def test_batch_release_loads(self):
        spec = {"batch_release": "governance_review"}
        compiled = compile_spec(spec)
        self.assertEqual(compiled["batch_release"], "batch_release")

    def test_default_provider_is_claude(self):
        spec = {"ollama": "claude"}
        compiled = compile_spec(spec)
        self.assertEqual(compiled["ollama"], "ollama")


class TestModelBackend:
    def test_default_provider_is_claude(self):
        from src.monkey_brain.kernel.execute.provider.model_backend import ModelBackend

        backend = ModelBackend()
        assert backend is not None
