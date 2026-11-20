"""Connect to the Elasticsearch MCP server and expose its tools to the agent.

The server runs as a stdio subprocess for the lifetime of the CLI session
(one persistent MCP session). If it cannot start, the agent gets placeholder
tools with the same names that return ``mcp_unavailable`` observations, so
file organization keeps working and indexing is never reported as successful.
"""
from __future__ import annotations

import contextlib
import json
import sys
from typing import Any, Optional

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, Settings

MCP_TOOL_NAMES = ("upsert_excerpts", "search_excerpts", "update_document_path",
                  "get_document_excerpts", "delete_document_excerpts", "index_stats")


def server_connection(settings: Settings) -> dict:
    return {
        "transport": "stdio",
        "command": settings.mcp_command or sys.executable,
        "args": ["-m", "mcp_es.server"],
        "cwd": str(PROJECT_ROOT),
        "env": {"ES_URL": settings.es_url, "ES_INDEX": settings.es_index,
                "ES_TIMEOUT_S": str(settings.es_timeout_s), "PYTHONPATH": str(PROJECT_ROOT),
                "PYTHONIOENCODING": "utf-8"},
    }


class _AnyArgs(BaseModel):
    model_config = {"extra": "allow"}


class _UpsertArgs(_AnyArgs):
    # required like in the real server: in mcp_down (20.11) the model called the stub with no arguments at all,
    # so the catalog could not tell which document failed; Any, because a JSON string is accepted too
    excerpts: Any = Field(..., description="excerpts built by build_excerpts")
    mode: str = "replace"


class _DocArgs(_AnyArgs):
    document_id: str


def unavailable_tools(reason: str) -> list[BaseTool]:
    def make(name: str) -> BaseTool:
        def _fn(**_kwargs) -> str:
            return json.dumps({"ok": False, "tool": name, "error": {
                "type": "mcp_unavailable",
                "message": f"Elasticsearch MCP server is unavailable: {reason}. Indexing/search did not happen."}},
                ensure_ascii=False)
        schema = {"upsert_excerpts": _UpsertArgs, "update_document_path": _DocArgs,
                  "get_document_excerpts": _DocArgs, "delete_document_excerpts": _DocArgs}.get(name, _AnyArgs)
        return StructuredTool.from_function(func=_fn, name=name, args_schema=schema,
                                            description=f"[UNAVAILABLE] MCP tool {name}",
                                            metadata={"mcp_unavailable": True})
    return [make(n) for n in MCP_TOOL_NAMES]


class MCPConnection:
    """Async context manager yielding (tools, status_message)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self.available = False
        self.status = ""

    async def __aenter__(self) -> list[BaseTool]:
        self._stack = contextlib.AsyncExitStack()
        try:
            client = MultiServerMCPClient({"es": server_connection(self.settings)})
            session = await self._stack.enter_async_context(client.session("es"))
            tools = await load_mcp_tools(session)
            self.available = True
            self.status = f"MCP 'es' connected: {', '.join(t.name for t in tools)}"
            return tools
        except Exception as e:  # noqa: BLE001
            await self._stack.aclose()
            self._stack = None
            self.available = False
            self.status = f"MCP unavailable ({type(e).__name__}: {e})"
            return unavailable_tools(f"{type(e).__name__}")

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
