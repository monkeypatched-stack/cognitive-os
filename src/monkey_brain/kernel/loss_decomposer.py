"""Loss Decomposer — decomposes composite loss into components."""

from src.monkey_brain.kernel.config import (
    LOSS_WEIGHT_L_S,
    LOSS_WEIGHT_L_B,
    LOSS_WEIGHT_L_A,
    LOSS_WEIGHT_L_M,
    LOSS_WEIGHT_L_K,
    LOSS_WEIGHT_L_C,
    LOSS_WEIGHT_L_G,
)


class LossDecomposer:
    """Decomposes composite loss into individual components."""

    def __init__(self):
        pass

    def decompose(self, composite_loss: float) -> dict[str, float]:
        return {
            "L_S": composite_loss * LOSS_WEIGHT_L_S,
            "L_B": composite_loss * LOSS_WEIGHT_L_B,
            "L_A": composite_loss * LOSS_WEIGHT_L_A,
            "L_M": composite_loss * LOSS_WEIGHT_L_M,
            "L_K": composite_loss * LOSS_WEIGHT_L_K,
            "L_C": composite_loss * LOSS_WEIGHT_L_C,
            "L_G": composite_loss * LOSS_WEIGHT_L_G,
        }

    def summary(self) -> dict:
        """Get summary of decomposer state."""
        return {"type": "loss_decomposer", "status": "active"}
