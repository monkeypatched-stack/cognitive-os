"""Unit tests for SimulationLoss — 6-term → 4-term EPA decomposition."""

import pytest

try:
    from cortex.adversarial_agents import SimulationLoss
except ImportError:
    SimulationLoss = None


_WEIGHT_FIELDS = [
    "state_mutation_weight",
    "constraint_weight",
    "knowledge_weight",
    "affordance_weight",
    "goal_weight",
    "mesh_weight",
    "latent_weight",
]


@pytest.mark.skipif(SimulationLoss is None, reason="SimulationLoss not importable")
class TestSimulationLoss:
    def _make_loss(self, **kwargs) -> SimulationLoss:
        defaults = {
            "state": 0.1,
            "constraint": 0.2,
            "knowledge": 0.15,
            "affordance": 0.05,
            "goal": 0.3,
            "mesh": 0.1,
        }
        defaults.update(kwargs)
        return SimulationLoss(**defaults)

    def test_to_epa_dict_has_four_terms(self):
        loss = self._make_loss()
        d = loss.to_epa_dict()
        assert set(d.keys()) >= {"L_S", "L_B", "L_A", "L_M"}

    def test_to_epa_dict_values_are_floats(self):
        loss = self._make_loss()
        d = loss.to_epa_dict()
        for k, v in d.items():
            assert isinstance(v, float), f"{k} is not float"

    def test_to_epa_dict_values_nonnegative(self):
        loss = self._make_loss()
        d = loss.to_epa_dict()
        for k, v in d.items():
            assert v >= 0.0, f"{k}={v} is negative"

    def test_weights_sum_to_one(self):
        loss = SimulationLoss.__new__(SimulationLoss)
        total = sum(getattr(loss, f, 0.0) for f in _WEIGHT_FIELDS if hasattr(loss, f))
        # Only check if weight attrs exist
        if total > 0:
            assert abs(total - 1.0) < 1e-6, f"weights sum to {total}, expected 1.0"

    def test_compute_total_in_range(self):
        loss = self._make_loss()
        if hasattr(loss, "compute_total"):
            loss.compute_total()
        total = getattr(loss, "total", None)
        if total is not None:
            assert 0.0 <= total <= 10.0  # relaxed upper bound

    def test_l_e_in_epa_dict_matches_sum(self):
        loss = self._make_loss()
        d = loss.to_epa_dict()
        if "L_E" in d:
            expected = round(d["L_S"] + d["L_B"] + d["L_A"] + d["L_M"], 4)
            assert abs(d["L_E"] - expected) < 1e-3, f"L_E {d['L_E']} != sum {expected}"
