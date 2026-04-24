"""Pre-fetch the llm-guard DeBERTa-v3 prompt-injection classifier weights.

Run once after `uv sync` / `make install` so the first domain document upload
does not stall waiting for a ~415 MB HuggingFace download.

  python -m scripts.prefetch_sanitizer_model

Idempotent: if the model is already in the HF cache this completes in <2s.
"""

import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def prefetch() -> None:
    try:
        from llm_guard.input_scanners.prompt_injection import PromptInjection, V2_MODEL  # type: ignore[import]
    except ImportError:
        logger.error("llm-guard not installed — run `uv sync` first")
        sys.exit(1)

    logger.info("Initialising PromptInjection scanner (model=%s)...", V2_MODEL.path)
    t0 = time.time()
    PromptInjection(model=V2_MODEL, threshold=0.9)
    elapsed = time.time() - t0
    logger.info("Scanner ready in %.1fs (model cached for subsequent runs)", elapsed)


if __name__ == "__main__":
    prefetch()
