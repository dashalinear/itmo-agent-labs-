"""Assemble the harnessed agent and the baseline (lab 1) agent.

Harness agent = the SAME prebuilt ReAct graph (langchain.agents.create_agent) as
in lab 1, with lab 1 tools + harness tools and the harness middleware:

  tool wrappers (outer -> inner): ToolRuntime -> OutputGuard -> CatalogSync(lab 1) -> tool
  before_model:                   Compaction
  model wrappers (outer -> inner): Context -> ModelGuard -> model

Baseline agent = lab 1 agent as it was (lab 1 tools, ToolGuard truncation,
ModelCallLimit), plus a passive MeterMiddleware that only measures.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ModelRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from organizer.agent import build_local_tools
from organizer.catalog import Catalog
from organizer.middleware import CatalogSyncMiddleware, ToolGuardMiddleware
from organizer.pathguard import Workspace
from organizer.prompts import build_system_prompt
from organizer.skills import SkillRegistry, parse_skill
from organizer.tools_catalog import CatalogTools

from .artifacts import ArtifactStore
from .budget import Budget
from .config import HarnessConfig, Limits
from .context import HarnessContext
from .middleware import (CompactionMiddleware, ContextMiddleware, ModelGuardMiddleware, OutputGuardMiddleware,
                         ToolRuntimeMiddleware)
from .prompts import build_harness_prompt
from .store import TaskStore
from .tokens import TokenCounter
from .tools_docs import DocTools
from .tools_research import ResearchTools
from .tools_task import TaskTools
from .web import DdgsProvider, FixtureProvider, HttpFetcher, SearchProvider
from .workspace import HarnessFileTools, HarnessWorkspace


def build_skills(dirs: Sequence[Path]) -> SkillRegistry:
    reg = SkillRegistry(dirs[0])
    for d in dirs[1:]:
        for p in sorted(Path(d).glob("*/SKILL.md")):
            s = parse_skill(p)
            reg.skills[s.name] = s
    return reg


def make_provider(cfg: HarnessConfig, name: str | None = None) -> SearchProvider:
    name = (name or cfg.search_provider).lower()
    if name == "fixture":
        return FixtureProvider(cfg.serp_path, cfg.fixture_base_url)
    return DdgsProvider(cfg.search_backend)


@dataclass
class HarnessBundle:
    agent: Any
    ctx: HarnessContext
    ws: HarnessWorkspace
    catalog: Catalog
    skills: SkillRegistry
    docs: DocTools
    research: ResearchTools
    task_tools: TaskTools
    tools: list[BaseTool] = field(default_factory=list)


def build_harness_agent(cfg: HarnessConfig, store: TaskStore, task_id: str, model: BaseChatModel,
                        summarizer: BaseChatModel | None, mcp_tools: Sequence[BaseTool], checkpointer,
                        window: int, window_source: str, provider: SearchProvider | None = None,
                        http_transport=None, on_event=None) -> HarnessBundle:
    task = store.get_task(task_id)
    limits = Limits.from_dict(task["limits"])
    ws = HarnessWorkspace(Path(task["spec"].get("root") or cfg.root))
    catalog = Catalog(ws)
    budget = Budget(store, task_id, limits)
    counter = TokenCounter(cfg.chars_per_token, limits.estimate_margin, cfg.token_count_mode,
                           cfg.base.llm_base_url, cfg.base.llm_api_key, cfg.base.llm_model)
    artifacts = ArtifactStore(ws, store, task_id, max_chars=max(limits.max_tool_result_chars - 800, 1000))
    skills = build_skills(cfg.skills_dirs)
    fs = HarnessFileTools(ws, catalog, max_chars=5_000_000)       # full results; OutputGuard bounds them
    docs = DocTools(ws, catalog, store, task_id, limits)
    research = ResearchTools(store, task_id, budget, limits, HttpFetcher(budget, limits, http_transport),
                             provider or make_provider(cfg, task["spec"].get("search_provider")), artifacts)
    task_tools = TaskTools(ws, catalog, store, task_id, artifacts, budget)
    local = (fs.as_tools() + CatalogTools(ws, catalog).as_tools() + skills.as_tools() + docs.as_tools()
             + artifacts.as_tools() + research.as_tools() + task_tools.as_tools())
    tools = [*local, *mcp_tools]
    base_prompt = build_harness_prompt(skills.metadata_block())
    ctx = HarnessContext(store=store, task_id=task_id, limits=limits, budget=budget, counter=counter, window=window,
                         window_source=window_source, base_prompt=base_prompt, ws=ws, artifacts=artifacts,
                         summarizer=summarizer, tools=tools, on_event=on_event, crash_at=cfg.crash_at)
    agent = create_agent(
        model=model, tools=tools, system_prompt=base_prompt,
        middleware=[ToolRuntimeMiddleware(ctx), OutputGuardMiddleware(ctx), CatalogSyncMiddleware(ws, catalog),
                    CompactionMiddleware(ctx), ContextMiddleware(ctx), ModelGuardMiddleware(ctx)],
        checkpointer=checkpointer, name="research_harness")
    return HarnessBundle(agent, ctx, ws, catalog, skills, docs, research, task_tools, tools)


# ------------------------------------------------------------------ baseline (lab 1 agent)
class MeterMiddleware(AgentMiddleware):
    """Passive measurement for the baseline: no limits, no changes to requests."""

    def __init__(self, counter: TokenCounter) -> None:
        super().__init__()
        self.counter = counter
        self.calls: list[dict] = []

    async def awrap_model_call(self, request: ModelRequest, handler):
        from .middleware import _text
        system = _text(request.system_message.content) if request.system_message else ""
        est = self.counter.estimate(system, request.messages, request.tools)
        t0 = time.perf_counter()
        rec = {"estimated_input": est.tokens, "messages": len(request.messages)}
        try:
            resp = await handler(request)
        except Exception as e:  # noqa: BLE001 - recorded, then re-raised (the baseline has no recovery)
            rec.update(status="error", error=f"{type(e).__name__}: {e}"[:400], duration_s=round(time.perf_counter() - t0, 2))
            self.calls.append(rec)
            raise
        result = resp if isinstance(resp, AIMessage) else next(
            (m for m in (getattr(resp, "result", None) or []) if isinstance(m, AIMessage)), None)
        u = (getattr(result, "usage_metadata", None) or {}) if result is not None else {}
        rec.update(status="ok", actual_input=u.get("input_tokens"), actual_output=u.get("output_tokens"),
                   duration_s=round(time.perf_counter() - t0, 2))
        self.calls.append(rec)
        return resp


def build_baseline_agent(cfg: HarnessConfig, model: BaseChatModel, mcp_tools: Sequence[BaseTool], checkpointer,
                         root: Path, model_calls_limit: int, counter: TokenCounter):
    from dataclasses import replace
    settings = replace(cfg.base, workspace=root)
    ws = Workspace(root)
    catalog = Catalog(ws)
    skills = SkillRegistry(cfg.skills_dirs[0])
    local = build_local_tools(settings, ws, catalog, skills)
    meter = MeterMiddleware(counter)
    agent = create_agent(
        model=model, tools=[*local, *mcp_tools], system_prompt=build_system_prompt(skills.metadata_block()),
        middleware=[ToolGuardMiddleware(timeout_s=settings.tool_timeout_s, max_chars=settings.max_tool_output_chars),
                    CatalogSyncMiddleware(ws, catalog),
                    ModelCallLimitMiddleware(run_limit=model_calls_limit, exit_behavior="end"), meter],
        checkpointer=checkpointer, name="file_organizer_baseline")
    return agent, meter, [*local, *mcp_tools]
