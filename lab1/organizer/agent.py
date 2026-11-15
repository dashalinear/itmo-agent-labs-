"""Assemble the prebuilt LangGraph ReAct agent (langchain.agents.create_agent).

We do NOT implement nodes, edges, reducers or the loop: create_agent builds the
graph  START -> model -> (tools -> model)* -> END  and the middleware only adds
hooks (before_model limit check, wrap_tool_call guards).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from .catalog import Catalog
from .config import Settings
from .middleware import CatalogSyncMiddleware, ToolGuardMiddleware
from .pathguard import Workspace
from .prompts import build_system_prompt
from .skills import SkillRegistry
from .tools_catalog import CatalogTools
from .tools_fs import FileTools
from .tools_web import WebTools


@dataclass
class AgentBundle:
    agent: object
    ws: Workspace
    catalog: Catalog
    skills: SkillRegistry
    local_tools: list[BaseTool]
    mcp_tools: list[BaseTool]
    system_prompt: str


def build_local_tools(settings: Settings, ws: Workspace, catalog: Catalog,
                      skills: SkillRegistry) -> list[BaseTool]:
    fs = FileTools(ws, catalog, max_chars=settings.max_tool_output_chars)
    web = WebTools(settings.web_timeout_s, settings.web_max_bytes, settings.web_max_redirects,
                   max_chars=settings.max_tool_output_chars)
    return fs.as_tools() + web.as_tools() + CatalogTools(ws, catalog).as_tools() + skills.as_tools()


def build_agent(settings: Settings, model: BaseChatModel, mcp_tools: Sequence[BaseTool] = (),
                checkpointer=None) -> AgentBundle:
    ws = Workspace(settings.workspace)
    catalog = Catalog(ws)
    skills = SkillRegistry(settings.skills_dir)
    local = build_local_tools(settings, ws, catalog, skills)
    system_prompt = build_system_prompt(skills.metadata_block())
    agent = create_agent(
        model=model,
        tools=[*local, *mcp_tools],
        system_prompt=system_prompt,
        middleware=[
            ToolGuardMiddleware(timeout_s=settings.tool_timeout_s, max_chars=settings.max_tool_output_chars),
            CatalogSyncMiddleware(ws, catalog),
            ModelCallLimitMiddleware(run_limit=settings.model_calls_per_turn, exit_behavior="end"),
        ],
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        name="file_organizer",
    )
    return AgentBundle(agent, ws, catalog, skills, local, list(mcp_tools), system_prompt)
