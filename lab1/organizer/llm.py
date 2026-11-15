"""Chat model factory: any OpenAI-compatible endpoint (ITMO vLLM qwen3.8-27b)."""
from __future__ import annotations

import re

from langchain_openai import ChatOpenAI

from .config import Settings

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


def build_llm(settings: Settings) -> ChatOpenAI:
    if not settings.llm_base_url or not settings.llm_api_key:
        raise RuntimeError("Set LLM_BASE_URL and LLM_API_KEY in .env (see .env.example)")
    extra_body = None
    if settings.llm_enable_thinking in ("true", "false"):
        # Qwen3 chat template switch (vLLM): enable/disable the reasoning block
        extra_body = {"chat_template_kwargs": {"enable_thinking": settings.llm_enable_thinking == "true"}}
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        extra_body=extra_body,
    )


def strip_thinking(text: str) -> str:
    return _THINK.sub("", text or "").strip()
