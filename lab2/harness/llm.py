"""Chat models for the harness: the agent model and the compaction summarizer.

Both are ChatOpenAI clients of the same OpenAI-compatible endpoint (ITMO vLLM).
Client-side retries are DISABLED (max_retries=0): the harness retries itself in
ModelGuardMiddleware, so every attempt is visible and charged to the budget.
max_tokens of the agent model equals the response reserve used in the window check.
"""
from __future__ import annotations

from dataclasses import replace

from organizer.llm import build_llm

from .config import HarnessConfig, Limits


def build_models(cfg: HarnessConfig, limits: Limits):
    agent_settings = replace(cfg.base, llm_max_tokens=limits.response_reserve_tokens, llm_max_retries=0,
                             llm_timeout_s=limits.llm_timeout_s)
    model = build_llm(agent_settings)
    summ_settings = replace(cfg.base, llm_max_tokens=limits.summary_max_tokens, llm_max_retries=0,
                            llm_timeout_s=limits.llm_timeout_s, llm_temperature=0.0,
                            llm_enable_thinking="false" if cfg.base.llm_enable_thinking != "true" else "true")
    summarizer = build_llm(summ_settings)
    return model, summarizer
