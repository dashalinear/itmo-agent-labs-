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
import re
import threading
from dataclasses import dataclass
from pathlib import Path
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


WINDOW_KEYS = ("max_model_len", "context_length", "max_context_length", "context_window", "max_position_embeddings")
_CTX_ERR = re.compile(r"(?:maximum context length is|max_model_len\W{0,3}|context length of|context window of)\s*(\d{3,8})", re.I)


def _find_key(obj: Any) -> tuple[str, int] | None:
    if isinstance(obj, dict):
        for k in WINDOW_KEYS:
            v = obj.get(k)
            if isinstance(v, (int, str)) and str(v).isdigit() and int(v) > 0:
                return k, int(v)
        for v in obj.values():
            hit = _find_key(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v)
            if hit:
                return hit
    return None


class _Prober:
    """Measures the context window from the server's BEHAVIOUR (no reliance on error wording).

    A request is accepted (HTTP 200) iff prompt_tokens + max_tokens <= window (vLLM-style validation);
    exact prompt sizes come from usage.prompt_tokens of accepted answers. Accepted probes ask for a
    one-word answer with thinking disabled, so they are short; rejected ones cost nothing.
    """

    def __init__(self, base: str, headers: dict, model: str, timeout: float) -> None:
        self.base, self.headers, self.model, self.timeout = base, headers, model, timeout
        self.client = httpx.Client(timeout=timeout, headers=headers)     # one keep-alive connection for all probes
        self.extra = {"chat_template_kwargs": {"enable_thinking": False}}
        self.requests = 0
        self.last_error = ""

    def call(self, content: str, max_tokens: int) -> tuple[bool, int | None]:
        body = {"model": self.model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
                "temperature": 0, **self.extra}
        self.requests += 1
        r = self.client.post(self.base + "/chat/completions", json=body)
        if r.status_code == 200:
            return True, int((r.json().get("usage") or {}).get("prompt_tokens") or 0) or None
        if r.status_code in (400, 413, 422):
            self.last_error = r.text[:300]
            return False, None
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

    def ready(self) -> bool:
        ok, _ = self.call("Reply with one word: ok", 8)
        if not ok:                                    # a gateway may reject the vLLM-specific field
            self.extra = {}
            ok, _ = self.call("Reply with one word: ok", 8)
        return ok

    def threshold(self, content: str, hi_cap: int = 4_194_304, start: int = 1024) -> tuple[int | None, int | None]:
        """Largest accepted max_tokens for this prompt and its prompt_tokens; (None, p) if never rejected."""
        lo, p = 8, None
        m = start
        while m <= hi_cap:
            ok, pt = self.call(content, m)
            if not ok:
                break
            lo, p = m, pt or p
            m *= 2
        else:
            return None, p
        hi = m
        while hi - lo > 1:
            mid = (lo + hi) // 2
            ok, pt = self.call(content, mid)
            if ok:
                lo, p = mid, pt or p
            else:
                hi = mid
        if p is None:
            _, p = self.call(content, 8)
        return lo, p

    def prompt_limit(self, cap_units: int = 300_000) -> tuple[int | None, str]:
        """Largest accepted prompt (max_tokens=1) by growing padding; window >= prompt_tokens + 1."""
        unit = "ok "
        best, n, rejected_at = None, 2048, None
        while n <= cap_units:
            ok, pt = self.call(unit * n + "\nReply with one word: ok", 1)
            if not ok:
                rejected_at = n
                break
            best = (n, pt)
            n *= 2
        if rejected_at is None:
            return (best[1] + 1 if best and best[1] else None), "no rejection up to the probe cap (lower bound)"
        lo = best[0] if best else 0
        hi = rejected_at
        while hi - lo > 32:
            mid = (lo + hi) // 2
            ok, pt = self.call(unit * mid + "\nReply with one word: ok", 1)
            if ok:
                lo, best = mid, (mid, pt)
            else:
                hi = mid
        return (best[1] + 1 if best and best[1] else None), "largest accepted prompt + 1 (precision ~32 tokens, rounded down)"


def measure_window(base_url: str, api_key: str, model: str, timeout: float = 180.0) -> tuple[int | None, str]:
    base = base_url.rstrip("/")
    pr = _Prober(base, {"Authorization": f"Bearer {api_key}"}, model, timeout)
    try:
        return _measure(pr)
    finally:
        pr.client.close()


def _measure(pr: "_Prober") -> tuple[int | None, str]:
    if not pr.ready():
        return None, f"probe: even a tiny request was rejected: {pr.last_error[:150]}"
    t1, p1 = pr.threshold("Reply with one word: ok")
    if t1 is not None and p1:
        pad = "ok " * 1500 + "\nReply with one word: ok"
        t2, p2 = pr.threshold(pad, hi_cap=t1, start=max(min(t1 // 2, 1 << 20), 8))
        if t2 is not None and p2 and abs((t1 + p1) - (t2 + p2)) <= 32:
            return t1 + p1, (f"measured: max accepted max_tokens {t1} + prompt {p1} = {t1 + p1}, confirmed with "
                             f"prompt {p2} -> {t2 + p2} ({pr.requests} probe requests)")
        note = f"max_tokens is capped at {t1} independently of the prompt (output cap, not the window)"
    else:
        note = "max_tokens is not validated by the server"
    w, how = pr.prompt_limit()
    if w:
        return w, f"measured by prompt length: {how}; {note} ({pr.requests} probe requests)"
    return None, f"probe could not measure the window: {note}; last rejection: {pr.last_error[:150]}"


def detect_window(base_url: str, api_key: str, model: str, timeout: float = 10.0, probe: bool = True,
                  cache: Path | None = None, refresh: bool = False) -> tuple[int | None, str]:
    """Ask an OpenAI-compatible server for the REAL context window of the model.

    1. GET {base}/models: max_model_len / context_length / ... of the model entry;
    2. POST {root}/tokenize (vLLM): response field max_model_len;
    3. rejection message for an impossible max_tokens ("maximum context length is N");
    4. measurement from behaviour (_Prober): accepted/rejected requests + usage.prompt_tokens.
    Results of 3-4 are cached in `cache` (per base_url + model) so the probes run once.
    """
    if not base_url:
        return None, "no base_url"
    if cache and cache.exists() and not refresh:
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("base_url") == base_url and c.get("model") == model and c.get("window"):
                return int(c["window"]), f"{c['source']} [cached {c['measured_at']}]"
        except Exception:  # noqa: BLE001
            pass
    headers = {"Authorization": f"Bearer {api_key}"}
    base = base_url.rstrip("/")
    notes = []
    try:
        r = httpx.get(base + "/models", headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json().get("data", [])
        items = [i for i in data if i.get("id") == model] or data[:1]
        hit = _find_key(items)
        if hit:
            return hit[1], f"GET /models -> {hit[0]}"
        notes.append("GET /models: no window field")
    except Exception as e:  # noqa: BLE001
        notes.append(f"GET /models failed: {type(e).__name__}")
    root = base[:-3] if base.endswith("/v1") else base
    try:
        r = httpx.post(root + "/tokenize", json={"model": model, "prompt": "ping"}, headers=headers, timeout=timeout)
        if r.status_code == 200 and r.json().get("max_model_len"):
            return int(r.json()["max_model_len"]), "POST /tokenize -> max_model_len"
        notes.append(f"POST /tokenize: HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"POST /tokenize failed: {type(e).__name__}")
    if not probe:
        return None, "; ".join(notes)
    w, src = None, ""
    try:
        r = httpx.post(base + "/chat/completions", headers=headers, timeout=max(timeout, 30.0),
                       json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                             "max_tokens": 10_000_000, "temperature": 0})
        m = _CTX_ERR.search(r.text)
        if r.status_code >= 400 and m:
            w, src = int(m.group(1)), f"rejection message (HTTP {r.status_code}) -> {m.group(0)[:60]}"
        else:
            notes.append(f"impossible max_tokens: HTTP {r.status_code}, text: {r.text[:160]!r}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"rejection probe failed: {type(e).__name__}")
    if not w:
        try:
            w, src = measure_window(base_url, api_key, model)
        except Exception as e:  # noqa: BLE001
            notes.append(f"measurement failed: {type(e).__name__}: {e}"[:200])
    if w:
        src = f"{src}; before: {'; '.join(notes)}" if notes else src
        if cache:
            from datetime import datetime, timezone
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"base_url": base_url, "model": model, "window": w, "source": src,
                                         "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        return w, src
    if src:
        notes.append(src)
    return None, "; ".join(notes)


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
