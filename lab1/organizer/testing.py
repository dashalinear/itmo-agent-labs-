"""Deterministic scripted chat model for offline tests, graph export and dry runs.

It is NOT an LLM: it replays a fixed list of AIMessages (optionally computed
from the conversation so far). Real demo traces must use the real model.
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
