"""Agent middleware (LangChain v1 ``wrap_tool_call`` hooks; no graph rewiring).

ToolGuardMiddleware   - outermost: per-tool timeout, exception -> error
                        observation, bounded output size (local and MCP tools).
CatalogSyncMiddleware - around MCP index tools: validates excerpts against the
                        source file before the write, and records the outcome
                        (indexed / error / pending_sync) in catalog.json after it.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from .catalog import Catalog
from .pathguard import Workspace
from .results import parse
from .textutil import file_hash, lines_range


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _error_msg(request: ToolCallRequest, type_: str, message: str, **extra) -> ToolMessage:
    body = json.dumps({"ok": False, "tool": request.tool_call["name"],
                       "error": {"type": type_, "message": message, **extra}}, ensure_ascii=False)
    return ToolMessage(content=body, tool_call_id=request.tool_call["id"],
                       name=request.tool_call["name"], status="error")


# a failed tool envelope, compact (local tools) or indented (the MCP server returns json.dumps(..., indent=2))
_FAILED_RX = re.compile(r'\s*\{\s*"ok"\s*:\s*false')


class ToolGuardMiddleware(AgentMiddleware):
    def __init__(self, timeout_s: float = 30.0, max_chars: int = 12000) -> None:
        super().__init__()
        self.timeout_s = timeout_s
        self.max_chars = max_chars

    def _bound(self, msg):
        if not isinstance(msg, ToolMessage):
            return msg
        text = _content_text(msg.content)
        if _FAILED_RX.match(text):
            msg.status = "error"            # the trace shows a failed tool as an error, not as "success"
        if len(text) > self.max_chars:
            msg.content = (text[: self.max_chars] +
                           f"\n...[tool output truncated by harness: {len(text)} chars > {self.max_chars}; "
                           "narrow the request (head/tail/grep, smaller size)]")
        return msg

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]):
        try:
            return self._bound(handler(request))
        except Exception as e:  # noqa: BLE001 - becomes an observation
            return _error_msg(request, "tool_exception", f"{type(e).__name__}: {e}"[:800])

    async def awrap_tool_call(self, request: ToolCallRequest,
                              handler: Callable[[ToolCallRequest], Awaitable[Any]]):
        try:
            return self._bound(await asyncio.wait_for(handler(request), timeout=self.timeout_s))
        except asyncio.TimeoutError:
            return _error_msg(request, "timeout", f"Tool exceeded {self.timeout_s}s and was abandoned")
        except Exception as e:  # noqa: BLE001
            return _error_msg(request, "tool_exception", f"{type(e).__name__}: {e}"[:800])


class CatalogSyncMiddleware(AgentMiddleware):
    INDEX_TOOLS = {"upsert_excerpts", "update_document_path", "delete_document_excerpts"}

    def __init__(self, ws: Workspace, catalog: Catalog) -> None:
        super().__init__()
        self.ws = ws
        self.catalog = catalog

    # -- validation before write ---------------------------------------------
    def _validate(self, excerpts: list[dict]) -> list[str]:
        problems = []
        if not excerpts:
            return ["excerpts list is empty"]
        for i, ex in enumerate(excerpts):
            ex = ex if isinstance(ex, dict) else getattr(ex, "model_dump", lambda: {})()
            doc = self.catalog.get(ex.get("document_id", ""))
            tag = f"#{i} ({ex.get('document_id')}:{ex.get('start_line')}-{ex.get('end_line')})"
            if not doc:
                problems.append(f"{tag}: unknown document_id")
                continue
            if ex.get("current_path") != doc["current_path"]:
                problems.append(f"{tag}: current_path {ex.get('current_path')} != catalog {doc['current_path']}")
                continue
            if ex.get("category") != doc.get("category"):
                problems.append(f"{tag}: category {ex.get('category')} != catalog {doc.get('category')}")
                continue
            try:
                path = self.ws.resolve(doc["current_path"], must_exist=True)
                if ex.get("content_hash") != file_hash(path):
                    problems.append(f"{tag}: content_hash does not match the current file")
                    continue
                s, e = int(ex.get("start_line", 0)), int(ex.get("end_line", 0))
                if lines_range(path, s, e) != ex.get("text"):
                    problems.append(f"{tag}: text differs from source lines {s}-{e}")
            except Exception as err:  # noqa: BLE001
                problems.append(f"{tag}: {type(err).__name__}: {err}")
        return problems

    # -- catalog bookkeeping after the MCP call ---------------------------------
    def _after(self, name: str, args: dict, msg) -> None:
        if not isinstance(msg, ToolMessage):
            return
        env = parse(msg.content) or {}
        ok = env.get("ok") is True and msg.status != "error"
        err = None if ok else (env.get("error", {}) or {}).get("message") or _content_text(msg.content)[:300]
        if name == "upsert_excerpts":
            by_doc: dict[str, list[dict]] = {}
            items_in = args.get("excerpts", None) or []
            if isinstance(items_in, str):
                # the model sometimes sends the list as a JSON string
                try:
                    items_in = json.loads(items_in)
                except ValueError:
                    items_in = []
            for ex in items_in if isinstance(items_in, list) else []:
                ex = ex if isinstance(ex, dict) else getattr(ex, "model_dump", lambda: {})()
                if not self.catalog.get(ex.get("document_id") or ""):
                    continue                          # garbage from the model (only reaches here if MCP is down)
                by_doc.setdefault(ex["document_id"], []).append(
                    {"excerpt_id": ex.get("excerpt_id"), "start_line": ex.get("start_line"),
                     "end_line": ex.get("end_line"), "summary": ex.get("summary")})
            for doc_id, items in by_doc.items():
                if ok:
                    if args.get("mode", "replace") == "merge":
                        old = (self.catalog.get(doc_id) or {}).get("excerpts", [])
                        known = {(o["start_line"], o["end_line"]) for o in items}
                        items = [o for o in old if (o["start_line"], o["end_line"]) not in known] + items
                    self.catalog.set_index_state(doc_id, "indexed", excerpts=sorted(items, key=lambda x: x["start_line"]))
                else:
                    self.catalog.set_index_state(doc_id, "error", error=f"upsert failed: {err}")
        elif name == "update_document_path":
            doc = self.catalog.get(args.get("document_id", ""))
            if doc:
                # category the index now holds: reported by the server, else what the server derives
                # from the path (data/<cat>/...), else the explicit argument
                index_category = env.get("category") if "category" in env else (
                    Catalog.category_of(str(args.get("new_path") or "")) or args.get("category"))
                if not ok:
                    self.catalog.set_index_state(doc["document_id"], "pending_sync", error=f"path sync failed: {err}")
                elif doc["current_path"] != args.get("new_path"):
                    self.catalog.set_index_state(doc["document_id"], "pending_sync",
                                                 error=f"new_path differs from catalog current_path {doc['current_path']}")
                elif index_category != doc.get("category"):
                    self.catalog.set_index_state(doc["document_id"], "pending_sync",
                                                 error=f"index category {index_category} differs from catalog "
                                                       f"category {doc.get('category')}")
                else:
                    self.catalog.set_index_state(doc["document_id"], "indexed")
        elif name == "delete_document_excerpts" and ok:
            self.catalog.set_index_state(args.get("document_id", ""), "not_indexed", excerpts=[])

    def _pre(self, request: ToolCallRequest):
        name = request.tool_call["name"]
        if (getattr(request.tool, "metadata", None) or {}).get("mcp_unavailable"):
            # MCP is down: answer at once, without validating excerpts; they cannot be written anyway
            return None
        if name == "upsert_excerpts":
            problems = self._validate(list(request.tool_call["args"].get("excerpts", [])))
            if problems:
                return _error_msg(request, "excerpt_validation_failed",
                                  "Excerpts were NOT written. Rebuild them with build_excerpts.",
                                  problems=problems[:10])
        return None

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in self.INDEX_TOOLS:
            return handler(request)
        rejected = self._pre(request)
        if rejected:
            return rejected
        msg = handler(request)
        self._after(name, request.tool_call["args"], msg)
        return msg

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in self.INDEX_TOOLS:
            return await handler(request)
        rejected = self._pre(request)
        if rejected:
            return rejected
        try:
            msg = await handler(request)
        except asyncio.CancelledError:  # outer timeout: never mark success
            self._after(name, request.tool_call["args"],
                        _error_msg(request, "timeout", "index call cancelled by timeout"))
            raise
        except Exception as e:  # noqa: BLE001 - MCP transport failure
            msg = _error_msg(request, "mcp_unavailable", f"{type(e).__name__}: {e}"[:500])
        self._after(name, request.tool_call["args"], msg)
        return msg
