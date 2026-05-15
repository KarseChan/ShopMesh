"""Router Agent — dispatch to specialist agents based on intent classification.

Integrates with the fast/slow intent classifier (T1.1):
- Semantic Router (fast): high confidence → direct route
- LLM Fallback (slow): low confidence → classify then route

Routing table is configurable in config.yaml under `router.routes`.
"""

from src.config import config
from src.observability.logger import get_logger
from src.router.intent_classifier import classify_intent

logger = get_logger("router")

# Agent handler registry — populated by register_agent()
_agent_registry: dict[str, callable] = {}


def register_agent(name: str, handler) -> None:
    """Register a specialist agent handler."""
    _agent_registry[name] = handler


def _get_routes() -> dict[str, str]:
    """Get intent → agent_name mapping from config."""
    return config.get("router", {}).get("routes", {})


def _get_fallback() -> str:
    """Get fallback agent name."""
    return config.get("router", {}).get("fallback", "general_agent")


def _get_threshold() -> float:
    """Get semantic router confidence threshold."""
    return config.get("router", {}).get("semantic_threshold", 0.80)


async def route(user_input: str) -> dict:
    """Classify intent and route to the appropriate specialist agent.

    Returns:
        {
            "intent": str,
            "confidence": float,
            "source": str,          # "semantic" or "llm"
            "agent": str,           # agent name that was routed to
            "result": any,          # result from the specialist agent
            "fallback": bool,       # True if used fallback agent
        }
    """
    # Step 1: Classify intent (fast/slow system)
    intent, confidence, source = await classify_intent(user_input)
    threshold = _get_threshold()

    routes = _get_routes()
    fallback_name = _get_fallback()

    # Step 2: Determine target agent
    agent_name = routes.get(intent)
    used_fallback = False

    if not agent_name:
        # Unknown intent → fallback
        agent_name = fallback_name
        used_fallback = True
        logger.info("route_fallback", intent=intent, reason="unknown_intent",
                     agent=agent_name)
    elif source == "semantic" and confidence >= threshold:
        # Fast path: Semantic Router with high confidence → direct route
        logger.info("route_fast", intent=intent, confidence=confidence,
                     agent=agent_name, source=source)
    else:
        # Slow path: LLM classification result
        logger.info("route_slow", intent=intent, confidence=confidence,
                     agent=agent_name, source=source)

    # Step 3: Execute specialist agent
    handler = _agent_registry.get(agent_name)
    if handler:
        try:
            result = await handler(user_input, intent=intent, confidence=confidence)
        except Exception as e:
            logger.error("agent_execution_failed", agent=agent_name, error=str(e))
            # Fallback to general agent on failure
            if agent_name != fallback_name:
                fallback_handler = _agent_registry.get(fallback_name)
                if fallback_handler:
                    result = await fallback_handler(user_input, intent=intent, confidence=confidence)
                    used_fallback = True
                    agent_name = fallback_name
                else:
                    result = {"error": str(e)}
            else:
                result = {"error": str(e)}
    else:
        # Agent not registered yet — return routing info only
        logger.warning("agent_not_registered", agent=agent_name)
        result = None

    return {
        "intent": intent,
        "confidence": confidence,
        "source": source,
        "agent": agent_name,
        "result": result,
        "fallback": used_fallback,
    }


async def route_with_context(user_input: str, context: dict | None = None) -> dict:
    """Route with additional context (entities, session state, etc.).

    Context is passed through to the specialist agent.
    """
    intent, confidence, source = await classify_intent(user_input)
    routes = _get_routes()
    fallback_name = _get_fallback()

    agent_name = routes.get(intent, fallback_name)
    used_fallback = agent_name == fallback_name and intent not in routes

    handler = _agent_registry.get(agent_name)
    if handler:
        try:
            result = await handler(user_input, intent=intent, confidence=confidence,
                                    context=context)
        except Exception as e:
            logger.error("agent_execution_failed", agent=agent_name, error=str(e))
            result = {"error": str(e)}
    else:
        result = None

    return {
        "intent": intent,
        "confidence": confidence,
        "source": source,
        "agent": agent_name,
        "result": result,
        "fallback": used_fallback,
    }
