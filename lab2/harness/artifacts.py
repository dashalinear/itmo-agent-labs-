"""Content-addressed artifacts: full tool outputs and web material copies on disk.

artifact_id = "art_" + sha256(text)[:20], file .harness/artifacts/<id>.txt
(atomic write). The same content always gets the same id, so re-running a tool
after resume never creates a second artifact. The model reads the rest of an
offloaded output with ``read_artifact`` (line ranges) and ``search_artifact``;
both work in a new process because the id is stable and the file is durable.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.results import ToolError, run_tool

from .store import TaskStore
from .workspace import HarnessWorkspace

_ID = re.compile(r"^art_[0-9a-f]{20}$")


def artifact_id_for(text: str) -> str:
    return "art_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


class ReadArtifactArgs(BaseModel):
    artifact_id: str = Field(description="Artifact id returned by an offloaded tool result, e.g. art_0123abcd...")
    start_line: int = Field(1, description="1-based first line to return")
    max_lines: int = Field(60, description="Number of lines to return (1..200)")


class SearchArtifactArgs(BaseModel):
    artifact_id: str = Field(description="Artifact id")
    query: str = Field(description="Substring to find (case-insensitive); regex=true for a Python regex")
    regex: bool = Field(False, description="Treat query as a regular expression")
    max_hits: int = Field(20, description="Maximum hits (1..100)")


class ArtifactStore:
    def __init__(self, ws: HarnessWorkspace, store: TaskStore, task_id: str, max_chars: int = 5000) -> None:
        self.ws = ws
        self.store = store
        self.task_id = task_id
        self.dir = ws.service_path("artifacts")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_chars = max_chars

    def save(self, text: str, kind: str, source: str, meta: dict | None = None) -> dict:
        aid = artifact_id_for(text)
        path = self.dir / f"{aid}.txt"
        created = False
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
            created = True
        lines = text.count("\n") + 1 if text else 0
        self.store.add_artifact(aid, self.task_id, kind, source, len(text), lines,
                                self.ws.rel(path), meta or {})
        return {"artifact_id": aid, "chars": len(text), "lines": lines, "created": created}

    def _path(self, artifact_id: str) -> Path:
        if not isinstance(artifact_id, str) or not _ID.match(artifact_id):
            raise ToolError("invalid_argument", f"Not an artifact id: {artifact_id!r}")
        p = self.dir / f"{artifact_id}.txt"
        if not p.is_file():
            raise ToolError("not_found", f"Artifact {artifact_id} does not exist")
        return p

    def text(self, artifact_id: str) -> str:
        return self._path(artifact_id).read_text(encoding="utf-8")

    def read(self, artifact_id: str, start_line: int = 1, max_lines: int = 60) -> dict:
        for name, v, hi in (("start_line", start_line, 10 ** 9), ("max_lines", max_lines, 200)):
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0 or v > hi:
                raise ToolError("invalid_argument", f"{name} must be an integer in 1..{hi}, got {v!r}")
        lines = self.text(artifact_id).split("\n")
        if start_line > len(lines):
            raise ToolError("invalid_argument", f"start_line {start_line} > total lines {len(lines)}")
        out, size, end = [], 0, start_line - 1
        for i in range(start_line - 1, min(len(lines), start_line - 1 + max_lines)):
            row = f"{i + 1}: {lines[i]}"
            if size + len(row) > self.max_chars and out:
                break
            out.append(row[: self.max_chars])
            size += len(row) + 1
            end = i + 1
        return {"artifact_id": artifact_id, "start_line": start_line, "end_line": end, "total_lines": len(lines),
                "has_more": end < len(lines), "next_start_line": end + 1 if end < len(lines) else None,
                "text": "\n".join(out)}

    def search(self, artifact_id: str, query: str, regex: bool = False, max_hits: int = 20) -> dict:
        if not query:
            raise ToolError("invalid_argument", "query must be non-empty")
        if isinstance(max_hits, bool) or not isinstance(max_hits, int) or not 1 <= max_hits <= 100:
            raise ToolError("invalid_argument", f"max_hits must be in 1..100, got {max_hits!r}")
        try:
            rx = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        except re.error as e:
            raise ToolError("invalid_regex", str(e))
        hits, total = [], 0
        for i, line in enumerate(self.text(artifact_id).split("\n"), 1):
            m = rx.search(line)
            if m:
                total += 1
                if len(hits) < max_hits:
                    s = max(m.start() - 120, 0)
                    hits.append({"line": i, "snippet": line[s: m.end() + 160]})
        return {"artifact_id": artifact_id, "query": query, "hits": hits, "total_hits": total,
                "has_more": total > len(hits)}

    def as_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                func=lambda **kw: run_tool("read_artifact", self.read, **kw), name="read_artifact",
                args_schema=ReadArtifactArgs,
                description="Read lines of a stored artifact (full text of an offloaded tool result or a fetched web page). "
                            "Use next_start_line to continue. Works after restarts."),
            StructuredTool.from_function(
                func=lambda **kw: run_tool("search_artifact", self.search, **kw), name="search_artifact",
                args_schema=SearchArtifactArgs,
                description="Find lines in a stored artifact by substring/regex; returns line numbers and snippets."),
        ]
