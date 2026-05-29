"""LLM client factory — supports multiple providers with per-agent config."""

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from functools import lru_cache

import httpx

from src.auth.context import get_tenant_id
from src.config import config
from src.observability.cost_tracker import record_usage
from src.observability.logger import get_logger

logger = get_logger("llm_client")

# Retry config
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions API."""

    # Separate connect vs read timeouts: fail fast on unreachable servers,
    # but allow LLM time to generate responses.
    _CONNECT_TIMEOUT = 15.0   # seconds — generous for LLM servers under load
    _READ_TIMEOUT = 120.0     # seconds — LLM can take a while to respond

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 temperature: float = 0.1, max_tokens: int = 2048):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Send chat completion request and return the response message.

        Retries up to _MAX_RETRIES times with exponential backoff on connection errors.
        """
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

        timeout = httpx.Timeout(
            connect=self._CONNECT_TIMEOUT,
            read=self._READ_TIMEOUT,
            write=10.0,
            pool=5.0,
        )

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()

                # Capture token usage
                usage = data.get("usage", {})
                if usage:
                    record_usage(
                        model=self.model,
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                        tenant_id=get_tenant_id() or "",
                    )

                return data["choices"][0]["message"]
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_error = e
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warning("llm_retry", attempt=attempt + 1, max_retries=_MAX_RETRIES,
                               error_type=type(e).__name__, delay=delay)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
            except httpx.HTTPStatusError as e:
                # 4xx/5xx from LLM API — retry on 5xx, raise on 4xx
                if e.response.status_code >= 500:
                    last_error = e
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning("llm_retry", attempt=attempt + 1, max_retries=_MAX_RETRIES,
                                   status=e.response.status_code, delay=delay)
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(delay)
                else:
                    raise

        raise last_error

    async def chat_stream(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncGenerator[str, None]:
        """Stream chat completion — yields content deltas as they arrive.

        Uses OpenAI-compatible SSE format (stream: true).
        Retries on connection errors, same as chat().
        """
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
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        timeout = httpx.Timeout(
            connect=self._CONNECT_TIMEOUT,
            read=self._READ_TIMEOUT,
            write=10.0,
            pool=5.0,
        )

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                return
                            try:
                                chunk = json.loads(data_str)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except (json.JSONDecodeError, IndexError, KeyError):
                                continue
                return  # success, exit retry loop
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_error = e
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warning("llm_stream_retry", attempt=attempt + 1, max_retries=_MAX_RETRIES,
                               error_type=type(e).__name__, delay=delay)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    last_error = e
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning("llm_stream_retry", attempt=attempt + 1, max_retries=_MAX_RETRIES,
                                   status=e.response.status_code, delay=delay)
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(delay)
                else:
                    raise

        raise last_error

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
