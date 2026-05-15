"""Intent Classifier — unified entry point with Semantic Router + LLM Fallback.

Usage:
    from src.router.intent_classifier import classify_intent
    intent, confidence, source = await classify_intent("帮我找一杯奶茶")
"""

from src.config import config
from src.observability.logger import get_logger
from src.router import llm_router, semantic_router

logger = get_logger("intent_classifier")


def _get_threshold() -> float:
    return config.get("semantic_router", {}).get("threshold", 0.80)


async def classify_intent(query: str) -> tuple[str, float, str]:
    """Classify intent: Semantic Router first, LLM fallback if low confidence.

    Returns (intent, confidence, source) where source is "semantic" or "llm".
    """
    threshold = _get_threshold()

    # Fast path: Semantic Router
    intent, confidence = await semantic_router.classify(query)
    if intent and confidence >= threshold:
        logger.info("intent_resolved", intent=intent, confidence=confidence, source="semantic")
        return intent, confidence, "semantic"

    # Slow path: LLM Fallback
    intent, confidence = await llm_router.classify(query)
    source = "llm"
    logger.info("intent_resolved", intent=intent, confidence=confidence, source=source)
    return intent, confidence, source
