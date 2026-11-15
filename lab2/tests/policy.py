"""Deterministic stand-in for the LLM in offline tests (NOT a demo of the real model).

PolicyModel follows a fixed plan. The position in the plan is taken from the
"step:N" marker of the last AI message in the CURRENT state, so the plan
continues correctly after compaction and after a restart in a new process.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


def tc(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": "x", "type": "tool_call"}


class PolicyModel(BaseChatModel):
    plan: list[Any]
    delay: float = 0.0
    systems: list[str] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "policy"

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        if messages and isinstance(messages[0], SystemMessage):
            self.systems.append(str(messages[0].content))
        k = -1
        for m in reversed(messages):
            if isinstance(m, AIMessage) and str(m.content).startswith("step:"):
                k = int(str(m.content).split()[0].split(":")[1])
                break
        n = k + 1
        self.calls += 1
        if n >= len(self.plan):
            return AIMessage(content=f"step:{n} (plan finished)")
        item = self.plan[n]
        if isinstance(item, str):
            return AIMessage(content=f"step:{n} {item}")
        calls = [{**c, "id": f"c{n}_{i}_{uuid.uuid4().hex[:6]}"} for i, c in enumerate(item)]
        return AIMessage(content=f"step:{n}", tool_calls=calls)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._generate(messages)


class SummaryModel(BaseChatModel):
    """Summarizer stub: keeps the user constraints and facts it sees (like a faithful summarizer)."""
    inputs: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "summary"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        text = "\n".join(str(m.content) for m in messages)
        self.inputs.append(text)
        facts = sorted({w for w in ("АС-01", "АС-02", "АС-03", "INC-2026-017", "nDCG@10") if w in text})
        body = ("## Цель\nобзор документов поиска\n## Ограничения пользователя\n[C1] [C2] [C3] [C4] [C5]\n"
                f"## Подтверждённые факты (с источниками)\n{', '.join(facts) or '—'}\n## Прогресс\nчитаются страницы\n")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=body, usage_metadata={"input_tokens": len(text) // 3, "output_tokens": 60, "total_tokens": len(text) // 3 + 60}))])
