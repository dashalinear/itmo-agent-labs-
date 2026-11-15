"""Model window detection and token counting.

* Window: ``GET {base}/models`` -> ``max_model_len`` (vLLM) / ``context_length``;
  fallback LLM_CONTEXT_WINDOW from .env. The working limit must not exceed it.
* Counting, in order of preference (TOKEN_COUNT_MODE=auto):
  1. server  - vLLM ``POST {root}/tokenize`` with the chat messages AND tool
               schemas: exact prompt length after the model's chat template;
  2. heuristic - characters of the rendered request / chars_per_token + per-message
               overhead, times (1 + margin). chars_per_token is calibrated after
               every real call from usage.input_tokens (exponential average).
Every estimate is logged with its method (store.model_calls, traces).
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import httpx
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

MSG_OVERHEAD_TOKENS = 6


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    chars: int
    method: str


def _content(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, list):
        c = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)


def render_message(m: BaseMessage) -> str:
    parts = [type(m).__name__, _content(m)]
    if isinstance(m, AIMessage) and m.tool_calls:
        parts.append(json.dumps([{"name": tc["name"], "args": tc["args"], "id": tc["id"]} for tc in m.tool_calls],
                                ensure_ascii=False))
    if isinstance(m, ToolMessage):
        parts.append(m.tool_call_id or "")
    return "\n".join(parts)


def detect_window(base_url: str, api_key: str, model: str, timeout: float = 10.0) -> tuple[int | None, str]:
    """Ask an OpenAI-compatible server for the real context window of the model."""
    if not base_url:
        return None, "no base_url"
    try:
        r = httpx.get(base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        r.raise_for_status()
        for item in r.json().get("data", []):
            if item.get("id") == model or len(r.json().get("data", [])) == 1:
                for key in ("max_model_len", "context_length", "max_context_length", "context_window"):
                    if item.get(key):
                        return int(item[key]), f"GET /models -> {key}"
        return None, "server did not report the window"
    except Exception as e:  # noqa: BLE001
        return None, f"window probe failed: {type(e).__name__}"


class TokenCounter:
    def __init__(self, chars_per_token: float = 2.6, margin: float = 0.10, mode: str = "auto",
                 base_url: str = "", api_key: str = "", model: str = "") -> None:
        self.cpt = chars_per_token
        self.margin = margin
        self.mode = mode
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.server_ok: bool | None = None if mode in ("auto", "server") and base_url else False
        self.server_window: int | None = None
        self._tools_cache: dict[int, tuple[str, list]] = {}
        self._lock = threading.Lock()
        self.calibrations = 0

    # -- rendering ----------------------------------------------------------
    def tools_payload(self, tools: Sequence[Any]) -> tuple[str, list]:
        key = id(tools) if not isinstance(tools, list) else hash(tuple(getattr(t, "name", str(t)) for t in tools))
        if key not in self._tools_cache:
            schemas = []
            for t in tools:
                try:
                    schemas.append(t if isinstance(t, dict) else convert_to_openai_tool(t))
                except Exception:  # noqa: BLE001
                    schemas.append({"name": getattr(t, "name", "tool")})
            self._tools_cache[key] = (json.dumps(schemas, ensure_ascii=False), schemas)
        return self._tools_cache[key]

    def heuristic_tokens(self, chars: int, n_messages: int = 0) -> int:
        return int(math.ceil((chars / self.cpt + n_messages * MSG_OVERHEAD_TOKENS) * (1 + self.margin)))

    def text_tokens(self, text: str) -> int:
        return self.heuristic_tokens(len(text))

    def message_tokens(self, m: BaseMessage) -> int:
        return self.heuristic_tokens(len(render_message(m)), 1)

    # -- estimation ---------------------------------------------------------
    def estimate(self, system: str, messages: Sequence[BaseMessage], tools: Sequence[Any] = ()) -> TokenEstimate:
        tools_json, schemas = self.tools_payload(list(tools))
        chars = len(system) + sum(len(render_message(m)) for m in messages) + len(tools_json)
        if self.server_ok is not False:
            n = self._server_count(system, messages, schemas)
            if n is not None:
                return TokenEstimate(int(math.ceil(n * 1.02)), chars, "server_tokenize")
        return TokenEstimate(self.heuristic_tokens(chars, len(messages) + 1), chars, f"heuristic(cpt={self.cpt:.2f})")

    def _server_count(self, system: str, messages: Sequence[BaseMessage], schemas: list) -> int | None:
        try:
            from langchain_openai.chat_models.base import _convert_message_to_dict
            payload = {"model": self.model, "add_generation_prompt": True,
                       "messages": [{"role": "system", "content": system}] + [_convert_message_to_dict(m) for m in messages]}
            if schemas:
                payload["tools"] = schemas
            root = self.base_url.rstrip("/")
            root = root[:-3] if root.endswith("/v1") else root
            r = httpx.post(root + "/tokenize", json=payload, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=10)
            r.raise_for_status()
            data = r.json()
            if data.get("max_model_len"):
                self.server_window = int(data["max_model_len"])
            self.server_ok = True
            return int(data["count"])
        except Exception:  # noqa: BLE001
            if self.mode == "auto":
                self.server_ok = False      # fall back to the calibrated heuristic for the rest of the run
            return None

    def observe(self, chars: int, actual_input_tokens: int | None) -> None:
        """Calibrate chars_per_token from the usage reported by the server."""
        if not actual_input_tokens or actual_input_tokens <= 0 or chars <= 0:
            return
        observed = chars / actual_input_tokens
        with self._lock:
            self.cpt = max(1.2, min(6.0, 0.6 * self.cpt + 0.4 * observed))
            self.calibrations += 1

    @property
    def method(self) -> str:
        return "server_tokenize" if self.server_ok else f"heuristic(cpt={self.cpt:.2f}, margin={self.margin:.0%})"
