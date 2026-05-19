"""LLM client factory — supports multiple providers with per-agent config."""

import json
import re
from functools import lru_cache

import httpx

from src.config import config


class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions API."""

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 temperature: float = 0.1, max_tokens: int = 2048):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Send chat completion request and return the response message."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]

    async def chat_json(self, messages: list[dict]) -> dict:
        """Chat and parse JSON from the response content."""
        msg = await self.chat(messages)
        content = msg.get("content", "")
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        # Fallback: find first { ... } block via regex
        json_str = content.strip()
        if not json_str.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", json_str)
            if match:
                json_str = match.group(0)

        parsed = json.loads(json_str)
        # Normalize keys: strip whitespace/newlines/embedded quotes that LLM may inject
        if isinstance(parsed, dict):
            parsed = {k.strip().strip('"').strip("'").strip(): v for k, v in parsed.items()}
        return parsed


@lru_cache(maxsize=8)
def get_llm(agent_name: str = "default") -> LLMClient:
    """Factory: get LLM client by agent name. Instance is cached.

    Looks up config["llm"][agent_name], falls back to config["llm"]["default"].
    Each agent can override model / base_url / api_key independently.
    """
    llm_cfg = config["llm"]
    cfg = llm_cfg.get(agent_name, llm_cfg["default"])
    return LLMClient(
        model=cfg["model"],
        base_url=cfg["base_url"],
        api_key=cfg.get("api_key", ""),
        temperature=cfg.get("temperature", 0.1),
        max_tokens=cfg.get("max_tokens", 2048),
    )
