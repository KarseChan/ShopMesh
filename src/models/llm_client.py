"""LLM client for OpenAI-compatible APIs."""

import json

import httpx

from src.config import config


class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions API."""

    def __init__(self):
        cfg = config["llm"]["default"]
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = cfg.get("api_key", "")
        self.temperature = cfg.get("temperature", 0.1)
        self.max_tokens = cfg.get("max_tokens", 2048)

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
        # Try to extract JSON from markdown code blocks or raw content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
