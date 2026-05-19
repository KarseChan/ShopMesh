"""Intent Classifier — unified entry point with Semantic Router + LLM Fallback.

Three-layer intent structure:
- user_goal: What the user wants (recommend/find/compare/detail/order)
- task_type: Task complexity (shopping_advice vs product_search)
- execution_hint: How to execute (contextual_search vs direct_search)

Usage:
    from src.router.intent_classifier import classify_intent
    intent_dict, confidence, source = await classify_intent("帮我找一杯奶茶")
    # intent_dict = {"user_goal": "find_product", "task_type": "product_search", "execution_hint": "direct_search"}
"""

from src.config import config
from src.observability.logger import get_logger
from src.router import llm_router, semantic_router

logger = get_logger("intent_classifier")

# Default intent when both layers fail
_DEFAULT_INTENT = {
    "user_goal": "find_product",
    "task_type": "product_search",
    "execution_hint": "direct_search",
}


def _get_threshold() -> float:
    return config.get("router", {}).get("semantic_threshold", 0.80)


async def classify_intent(query: str) -> tuple[dict, float, str]:
    """Classify intent: Semantic Router first, LLM fallback if low confidence.

    Returns (intent_dict, confidence, source) where source is "semantic" or "llm".
    If both layers fail, returns default intent with 0.0 confidence.
    """
    threshold = _get_threshold()

    # Fast path: Semantic Router
    intent_dict, confidence = await semantic_router.classify(query)
    if intent_dict and confidence >= threshold:
        logger.info("intent_resolved",
                     user_goal=intent_dict.get("user_goal"),
                     task_type=intent_dict.get("task_type"),
                     confidence=confidence, source="semantic")
        return intent_dict, confidence, "semantic"

    # Slow path: LLM Fallback
    intent_dict, confidence = await llm_router.classify(query)
    source = "llm"

    # Final fallback: if both layers return empty, default
    if not intent_dict:
        intent_dict = _DEFAULT_INTENT.copy()
        confidence = 0.0
        source = "default"

    logger.info("intent_resolved",
                 user_goal=intent_dict.get("user_goal"),
                 task_type=intent_dict.get("task_type"),
                 execution_hint=intent_dict.get("execution_hint"),
                 confidence=confidence, source=source)
    return intent_dict, confidence, source
