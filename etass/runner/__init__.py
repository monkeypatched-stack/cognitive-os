"""ETASS Runner — submits the ETASS orchestration prompt to MonkeyBrain.

MonkeyBrain owns the pipeline.  ETASS is the specification.
"""

from .prompt import ETASS_PROMPT
from .submit import submit_to_monkeybrain

__all__ = ["ETASS_PROMPT", "submit_to_monkeybrain"]
