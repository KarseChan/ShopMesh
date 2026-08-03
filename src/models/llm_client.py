"""LLM client factory — supports multiple providers with per-agent config."""

import asyncio
import json
import re
import time
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

# Circuit breaker config
_CIRCUIT_FAIL_THRESHOLD = 5    # consecutive failures to open circuit
_CIRCUIT_RECOVERY_TIME = 30.0  # seconds before half-open

# Reasoning models (MiniMax-M*, DeepSeek-R1, QwQ, …) inline their chain-of-thought
# in `content` as <think>...</think>, with NO separate reasoning field. Downstream
# JSON/text parsing must see only the final answer, so strip it centrally here.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_think(content):
    """Remove <think>...</think> reasoning from a completed content string.

    Non-str (e.g. None when a tool_call has no content) passes through unchanged.
    A dangling unclosed <think> (output truncated mid-reasoning by max_tokens) has
    everything from it dropped, so the caller gets "" and fails loudly rather than
    parsing reasoning as the answer.
    """
    if not isinstance(content, str):
        return content
    cleaned = _THINK_BLOCK_RE.sub("", content)
    idx = cleaned.find("<think>")
    if idx != -1:
        cleaned = cleaned[:idx]
    return cleaned.strip()


class CircuitBreaker:
    """Simple circuit breaker: opens after N consecutive failures, recovers after cooldown."""

    def __init__(self, fail_threshold: int = _CIRCUIT_FAIL_THRESHOLD,
                 recovery_time: float = _CIRCUIT_RECOVERY_TIME):
        self._fail_threshold = fail_threshold
        self._recovery_time = recovery_time
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.time() - self._opened_at >= self._recovery_time:
            return "half_open"
        return "open"

    def allow_request(self) -> bool:
        s = self.state
        if s == "closed":
            return True
        if s == "half_open":
            return True  # allow one probe
        return False  # open — fast-fail

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._fail_threshold:
            self._opened_at = time.time()
            logger.warning("circuit_opened", failures=self._consecutive_failures,
                           recovery_in=self._recovery_time)

    def time_until_recovery(self) -> float:
        if self._opened_at is None:
            return 0.0
        elapsed = time.time() - self._opened_at
        return max(0.0, self._recovery_time - elapsed)


class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions API."""

    # Separate connect vs read timeouts: fail fast on unreachable servers,
    # but allow LLM time to generate responses.
    _CONNECT_TIMEOUT = 5.0    # seconds — fast-fail + retry beats slow-fail
    _READ_TIMEOUT = 120.0     # seconds — LLM can take a while to respond

    _circuit = CircuitBreaker()  # shared across all instances

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
        Circuit breaker fast-fails after consecutive failures.
        """
        if not self._circuit.allow_request():
            wait = self._circuit.time_until_recovery()
            logger.warning("circuit_breaker_open", wait_seconds=round(wait, 1))
            raise httpx.ConnectError(
                f"LLM service circuit breaker open, retry in {wait:.0f}s",
                request=None,
            )

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

                self._circuit.record_success()
                message = data["choices"][0]["message"]
                # Strip reasoning-model <think> blocks so callers (incl. chat_json)
                # parse only the final answer.
                if isinstance(message, dict) and "content" in message:
                    message["content"] = _strip_think(message.get("content"))
                return message
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

        self._circuit.record_failure()
        raise last_error

    async def chat_stream(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncGenerator[str, None]:
        """Stream chat completion — yields content deltas as they arrive.

        Uses OpenAI-compatible SSE format (stream: true).
        Retries on connection errors, same as chat().
        Circuit breaker fast-fails after consecutive failures.
        """
        if not self._circuit.allow_request():
            wait = self._circuit.time_until_recovery()
            logger.warning("circuit_breaker_open", wait_seconds=round(wait, 1))
            raise httpx.ConnectError(
                f"LLM service circuit breaker open, retry in {wait:.0f}s",
                request=None,
            )

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
            "stream_options": {"include_usage": True},
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
                        # <think> stripping across streamed deltas (reasoning models).
                        think_mode = None   # None=undecided, "think"=suppress, "plain"=passthrough
                        think_buf = ""
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                return
                            try:
                                chunk = json.loads(data_str)
                                # Capture usage from final chunk (stream_options.include_usage)
                                usage = chunk.get("usage")
                                if usage:
                                    record_usage(
                                        model=self.model,
                                        input_tokens=usage.get("prompt_tokens", 0),
                                        output_tokens=usage.get("completion_tokens", 0),
                                        tenant_id=get_tenant_id() or "",
                                    )
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                piece = delta.get("content", "")
                                if not piece:
                                    continue
                                think_buf += piece
                                if think_mode is None:
                                    stripped = think_buf.lstrip()
                                    if stripped.startswith("<think>"):
                                        think_mode = "think"
                                    elif stripped and not "<think>".startswith(stripped):
                                        think_mode = "plain"
                                    else:
                                        continue  # ambiguous short prefix — keep buffering
                                if think_mode == "think":
                                    end = think_buf.find("</think>")
                                    if end == -1:
                                        continue  # still reasoning — suppress
                                    think_buf = think_buf[end + len("</think>"):]
                                    think_mode = "plain"
                                if think_buf:
                                    out, think_buf = think_buf, ""
                                    yield out
                            except (json.JSONDecodeError, IndexError, KeyError):
                                continue
                self._circuit.record_success()
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

        self._circuit.record_failure()
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
