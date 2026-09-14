"""SittingFace — Self-bootstrapping pipeline for MonkeyBrain."""

try:
    import logging
    from src.sittingface.somatic_compiler import (
        SomaticCompiler,
        SomaticChart,
        CompiledPrompt,
    )

    logger = logging.getLogger("sittingface.__init__")

except ImportError:
    from sittingface.somatic_compiler import (
        SomaticCompiler,
        SomaticChart,
        CompiledPrompt,
    )

__all__ = ["SomaticCompiler", "SomaticChart", "CompiledPrompt"]
