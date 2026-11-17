"""Agent factories (ingestion, main, deep, exhaustive) for the lab 2 TaskRunner.

Every agent is the prebuilt LangGraph ReAct agent (create_agent) with the lab 2 harness middleware:
  ToolRuntime -> OutputGuard -> CatalogSync(lab 1) -> tool;  Compaction (before_model);  Context -> ModelGuard.
They differ only in system prompt, tool set and budget object (sub-agents: ChainedBudget to the parent).

| agent      | tools                                                                                         |
|------------|-----------------------------------------------------------------------------------------------|
| ingest     | lab 1 file tools + catalog + skills + MCP (Elasticsearch excerpts), lab 2 doc range reading,   |
|            | doc_outline, write_l1, index_document, corpus_status, validate_corpus, plan/progress/complete |
| main       | L0/L1/L2 navigation, full-text/grep/vector on L1+L2, cite_fragment, start/status/result of subagents |
| deep       | the same navigation + search + cite_fragment, update_plan, submit_result, stop_partial        |
| exhaustive | area_define, area_next, area_grep, area_fulltext, record_mention, area_coverage, get_l1,      |
|            | corpus_overview/list_documents, submit_result, stop_partial   (NO top-k / vector / ranked)    |
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from organizer.catalog import Catalog
from organizer.middleware import CatalogSyncMiddleware
from organizer.tools_catalog import CatalogTools

from harness.agent import build_skills
from harness.artifacts import ArtifactStore
from harness.budget import Budget
from harness.config import Limits
from harness.context import HarnessContext
from harness.middleware import (CompactionMiddleware, ContextMiddleware, ModelGuardMiddleware, OutputGuardMiddleware,
                                ToolRuntimeMiddleware)
from harness.tokens import TokenCounter
from harness.tools_docs import DocTools
from harness.tools_task import TaskTools
from harness.workspace import HarnessFileTools, HarnessWorkspace

from .levels import Levels
from .prompts import DEEP_PROMPT, EXHAUSTIVE_PROMPT, MAIN_PROMPT, ingest_prompt
from .subagents import ChainedBudget, SubagentManager, SubmitTools
from .tools_corpus import CorpusEnv, CorpusTools
from .tools_exhaustive import ExhaustiveTools


@dataclass
class Bundle:
    agent: Any
    ctx: HarnessContext
    ws: HarnessWorkspace
    catalog: Catalog
    skills: Any
    docs: DocTools
    task_tools: TaskTools
    tools: list[BaseTool] = field(default_factory=list)
    env: CorpusEnv | None = None
    manager: SubagentManager | None = None


class IngestTaskTools(TaskTools):
    """Completion of an ingestion task = the corpus is complete and valid (checked by code)."""

    def __init__(self, *a, corpus: CorpusTools, folder: str, **kw) -> None:
        super().__init__(*a, **kw)
        self.corpus = corpus
        self.folder = folder

    def criteria(self) -> dict:
        crit = super().criteria()
        st = self.corpus.corpus_status()
        left = []
        if self.folder:
            root = self.ws.resolve(self.folder)
            left = [self.ws.rel(p) for p in root.rglob("*") if p.is_file()] if root.exists() else []
        val = self.corpus.validate_corpus()
        crit["input_folder_organized"] = {"ok": not left, "detail": f"{len(left)} files left" + (f": {left[:5]}" if left else "")}
        crit["l1_l2_complete"] = {"ok": st["need_l1_count"] == 0 and st["need_l2_count"] == 0,
                                  "detail": f"need L1: {st['need_l1_count']}, need L2: {st['need_l2_count']}"}
        crit["validation"] = {"ok": val["ok"], "detail": f"{val['fragments_checked']} fragments checked, {val['problems_total']} problems"}
        return crit


class Lab3:
    """Process-wide objects shared by all agents: config, ES index, embedder, runner."""

    def __init__(self, cfg, index, embedder) -> None:
        self.cfg = cfg
        self.index = index
        self.embedder = embedder
        self.runner = None
        self.managers: dict[str, SubagentManager] = {}

    # ------------------------------------------------------------------ common parts
    def _base(self, cfg, store, task_id, summarizer, window, wsrc, on_event, chained: bool):
        task = store.get_task(task_id)
        limits = Limits.from_dict(task["limits"])
        ws = HarnessWorkspace(Path(task["spec"].get("root") or cfg.root))
        catalog = Catalog(ws)
        parent = task["spec"].get("parent_task_id")
        budget = ChainedBudget(store, task_id, limits, parent) if (chained and parent) else Budget(store, task_id, limits)
        counter = TokenCounter(cfg.chars_per_token, limits.estimate_margin, cfg.token_count_mode,
                               cfg.base.llm_base_url, cfg.base.llm_api_key, cfg.base.llm_model)
        artifacts = ArtifactStore(ws, store, task_id, max_chars=max(limits.max_tool_result_chars - 800, 1000))
        env = CorpusEnv(ws, catalog, Levels(ws, catalog), self.index, self.embedder, store, task_id)
        corpus = CorpusTools(env, coach=task["kind"] == "deep")
        docs = DocTools(ws, catalog, store, task_id, limits)
        return task, limits, ws, catalog, budget, counter, artifacts, env, corpus, docs

    def _assemble(self, kind, cfg, store, task_id, model, summarizer, window, wsrc, on_event, base, tools, prompt,
                  task_tools, skills=None, manager=None):
        task, limits, ws, catalog, budget, counter, artifacts, env, corpus, docs = base
        ctx = HarnessContext(store=store, task_id=task_id, limits=limits, budget=budget, counter=counter, window=window,
                             window_source=wsrc, base_prompt=prompt, ws=ws, artifacts=artifacts, summarizer=summarizer,
                             tools=tools, on_event=on_event, crash_at=cfg.crash_at)
        agent = create_agent(model=model, tools=tools, system_prompt=prompt,
                             middleware=[ToolRuntimeMiddleware(ctx), OutputGuardMiddleware(ctx), CatalogSyncMiddleware(ws, catalog),
                                         CompactionMiddleware(ctx), ContextMiddleware(ctx), ModelGuardMiddleware(ctx)],
                             checkpointer=self.runner.checkpointer, name=f"{kind}_agent")
        return Bundle(agent, ctx, ws, catalog, skills, docs, task_tools, tools, env, manager)

    def _task_tools(self, ws, catalog, store, task_id, artifacts, budget):
        return TaskTools(ws, catalog, store, task_id, artifacts, budget)

    # ------------------------------------------------------------------ factories
    def factory(self, kind: str):
        def make(cfg, store, task_id, model, summarizer, mcp_tools, checkpointer, window, wsrc, provider=None,
                 http_transport=None, on_event=None):
            base = self._base(cfg, store, task_id, summarizer, window, wsrc, on_event, chained=kind in ("deep", "exhaustive"))
            task, limits, ws, catalog, budget, counter, artifacts, env, corpus, docs = base
            tt_lab2 = self._task_tools(ws, catalog, store, task_id, artifacts, budget)
            pick = lambda tools, names: [t for t in tools if t.name in names]  # noqa: E731
            if kind == "ingest":
                skills = build_skills(cfg.skills_dirs)
                fs = HarnessFileTools(ws, catalog, max_chars=5_000_000)
                tt = IngestTaskTools(ws, catalog, store, task_id, artifacts, budget, corpus=corpus,
                                     folder=task["spec"].get("ingest_folder", ""))
                tools = (fs.as_tools() + CatalogTools(ws, catalog).as_tools() + skills.as_tools()
                         + pick(docs.as_tools(), {"doc_info", "read_pages", "read_lines", "search_text"})
                         + artifacts.as_tools() + corpus.ingest_tools()
                         + pick(tt.as_tools(), {"update_plan", "task_progress", "complete_task", "stop_partial"}) + list(mcp_tools))
                return self._assemble(kind, cfg, store, task_id, model, summarizer, window, wsrc, on_event, base, tools,
                                      ingest_prompt(skills.metadata_block()), tt, skills)
            if kind == "main":
                mgr = self.managers.get(task_id) or SubagentManager(self.runner, {k: self.factory(k) for k in ("deep", "exhaustive")},
                                                                    task_id, coverage_fn=self.coverage_of)
                self.managers[task_id] = mgr
                tools = corpus.navigation_tools() + corpus.search_tools() + artifacts.as_tools() + mgr.as_tools()
                return self._assemble(kind, cfg, store, task_id, model, summarizer, window, wsrc, on_event, base, tools,
                                      MAIN_PROMPT, tt_lab2, manager=mgr)
            if kind == "deep":
                sub = SubmitTools(store, task_id, "deep", render_fn=tt_lab2.render)
                tools = (corpus.navigation_tools() + corpus.search_tools() + artifacts.as_tools()
                         + pick(tt_lab2.as_tools(), {"update_plan", "stop_partial"}) + sub.as_tools())
                return self._assemble(kind, cfg, store, task_id, model, summarizer, window, wsrc, on_event, base, tools,
                                      DEEP_PROMPT, tt_lab2)
            if kind == "exhaustive":
                ex = ExhaustiveTools(env, corpus)
                sub = SubmitTools(store, task_id, "exhaustive", coverage_fn=ex.area_coverage, render_fn=tt_lab2.render)
                tools = (ex.as_tools() + pick(corpus.navigation_tools(), {"corpus_overview", "list_documents", "get_l1"})
                         + artifacts.as_tools() + pick(tt_lab2.as_tools(), {"stop_partial"}) + sub.as_tools())
                return self._assemble(kind, cfg, store, task_id, model, summarizer, window, wsrc, on_event, base, tools,
                                      EXHAUSTIVE_PROMPT, tt_lab2)
            raise ValueError(kind)
        return make

    def coverage_of(self, task_id: str) -> dict:
        store = self.runner.store
        task = store.get_task(task_id)
        ws = HarnessWorkspace(Path(task["spec"]["root"]))
        catalog = Catalog(ws)
        env = CorpusEnv(ws, catalog, Levels(ws, catalog), self.index, self.embedder, store, task_id)
        return ExhaustiveTools(env, CorpusTools(env)).area_coverage()
