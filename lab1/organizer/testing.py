"""Deterministic scripted chat model for offline tests, graph export and dry runs.

It replays a fixed list of AIMessages (optionally computed from the
conversation so far) instead of calling the LLM.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence, Union

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

Step = Union[AIMessage, Callable[[Sequence[BaseMessage]], AIMessage]]


class ScriptedChatModel(BaseChatModel):
    steps: list[Any]
    loop_last: bool = False
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # tools are ignored: calls are scripted
        return self

    def _generate(self, messages: list[BaseMessage], stop=None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs) -> ChatResult:
        if self.calls >= len(self.steps):
            if not self.loop_last or not self.steps:
                msg = AIMessage(content="(script finished)")
                return ChatResult(generations=[ChatGeneration(message=msg)])
            step = self.steps[-1]
        else:
            step = self.steps[self.calls]
        self.calls += 1
        msg = step(messages) if callable(step) else step
        # fresh ids for every emission (looping steps would otherwise repeat ids)
        msg = AIMessage(content=msg.content,
                        tool_calls=[{**tc, "id": f"{tc['id']}_{self.calls}"} for tc in msg.tool_calls])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def call(name: str, args: dict, cid: str = "c") -> dict:
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


LOOP_HINTS = ("снова и снова", "пока он не появится", "again and again")


class DryRunChatModel(BaseChatModel):
    """Stand-in model for `python -m organizer.cli --dry-run-model` (plumbing checks of the demo
    scripts without the ITMO LLM). Per user turn it calls `ls input` once and then answers with a
    fixed text; for a turn that asks to re-check input "again and again" it keeps calling `ls`
    until the execution limit stops it. It does not organize anything."""

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "dry-run"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs) -> ChatResult:
        self.calls += 1
        last_human = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
        text = str(messages[last_human].content).lower() if last_human >= 0 else ""
        tool_steps = sum(1 for m in messages[last_human + 1:] if m.type == "ai")
        if any(h in text for h in LOOP_HINTS) or tool_steps == 0:
            msg = AIMessage(content="", tool_calls=[call("ls", {"path": "input"}, f"dry_{self.calls}")])
        else:
            msg = AIMessage(content=f"[dry run] ответ-заглушка на: {text[:80]}")
        return ChatResult(generations=[ChatGeneration(message=msg)])
