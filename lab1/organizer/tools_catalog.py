"""Catalog and excerpt-preparation tools (local, deterministic).

* catalog_view    - inspect catalog entries + reconciliation report
* catalog_update  - set title / description / note of a document
* build_excerpts  - turn chosen line ranges into exact, verifiable excerpt
                    records (text, hash, stable excerpt_id) for MCP upsert
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from mcp_es.schema import excerpt_key

from .catalog import STATUSES, Catalog
from .pathguard import Workspace
from .results import ToolError, run_tool
from .textutil import file_hash, read_text, split_lines

MAX_EXCERPT_LINES = 60


class CatalogViewArgs(BaseModel):
    folder: Optional[str] = Field(None, description="Only documents under this path prefix, e.g. 'data/courses'")
    status: Optional[str] = Field(None, description=f"Filter by indexing_status: {', '.join(STATUSES)}")
    document_id: Optional[str] = Field(None, description="Return one document in full")


class CatalogUpdateArgs(BaseModel):
    document_id: str
    title: Optional[str] = Field(None, description="Human-readable title")
    description: Optional[str] = Field(None, description="1-2 sentence summary of the document content")
    note: Optional[str] = Field(None, description="Classification rationale, ambiguity, etc.")


class Range(BaseModel):
    start_line: int = Field(description="1-based first line (inclusive)")
    end_line: int = Field(description="1-based last line (inclusive)")
    summary: Optional[str] = Field(None, description="Optional short description of the excerpt")


class BuildExcerptsArgs(BaseModel):
    document_id: str
    ranges: Optional[list[Range]] = Field(
        None, description="Line ranges chosen by content. Omit to reuse the ranges already indexed "
                          "for this unchanged document version (idempotent re-indexing).")


COMPACT = ("document_id", "current_path", "title", "category", "description", "indexing_status",
           "is_empty", "content_hash")


class CatalogTools:
    def __init__(self, ws: Workspace, catalog: Catalog) -> None:
        self.ws = ws
        self.catalog = catalog

    def catalog_view(self, folder: str | None = None, status: str | None = None,
                     document_id: str | None = None) -> dict:
        cat = self.catalog.load()
        if document_id:
            doc = self.catalog.get(document_id)
            if not doc:
                raise ToolError("not_found", f"Unknown document_id {document_id}")
            return {"document": doc}
        docs = cat["documents"]
        if folder:
            prefix = folder.strip().replace("\\", "/").rstrip("/") + "/"
            docs = [d for d in docs if d["current_path"].startswith(prefix)]
        if status:
            docs = [d for d in docs if d["indexing_status"] == status]
        counts: dict[str, int] = {}
        for d in cat["documents"]:
            counts[d["indexing_status"]] = counts.get(d["indexing_status"], 0) + 1
        return {"catalog_path": self.ws.rel(self.ws.catalog_path), "total_documents": len(cat["documents"]),
                "status_counts": counts, "returned": len(docs),
                "documents": [{k: d.get(k) for k in COMPACT} for d in docs],
                "reconcile": self.catalog.reconcile()}

    def catalog_update(self, document_id: str, title=None, description=None, note=None) -> dict:
        try:
            doc = self.catalog.update(document_id, title=title, description=description, note=note)
        except KeyError:
            raise ToolError("not_found", f"Unknown document_id {document_id}")
        return {"document": {k: doc.get(k) for k in COMPACT + ("note",)}}

    def build_excerpts(self, document_id: str, ranges: list | None = None) -> dict:
        doc = self.catalog.get(document_id)
        if not doc:
            raise ToolError("not_found", f"Unknown document_id {document_id}")
        path = self.ws.resolve(doc["current_path"], must_exist=True)
        current_hash = file_hash(path)
        if current_hash != doc["content_hash"]:
            raise ToolError("hash_mismatch", "File content changed since cataloguing; re-cataloguing is out of scope")
        lines = split_lines(read_text(path))
        if not lines or not "".join(lines).strip():
            raise ToolError("empty_document", "Empty document: nothing to index (kept in catalog)")
        reused = False
        if not ranges:
            if not doc.get("excerpts"):
                raise ToolError("no_ranges", "Provide ranges: this document has not been indexed before")
            ranges = [{"start_line": e["start_line"], "end_line": e["end_line"], "summary": e.get("summary")}
                      for e in doc["excerpts"]]
            reused = True
        ranges = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in ranges]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records, seen = [], set()
        for r in sorted(ranges, key=lambda r: (r["start_line"], r["end_line"])):
            s, e = r["start_line"], r["end_line"]
            if not (1 <= s <= e <= len(lines)):
                raise ToolError("invalid_range", f"Range {s}-{e} outside 1..{len(lines)}")
            if e - s + 1 > MAX_EXCERPT_LINES:
                raise ToolError("range_too_long", f"Range {s}-{e} exceeds {MAX_EXCERPT_LINES} lines; choose focused excerpts")
            text = "\n".join(lines[s - 1:e])
            if not text.strip():
                raise ToolError("empty_excerpt", f"Lines {s}-{e} are blank")
            key = excerpt_key(document_id, current_hash, s, e)
            if key in seen:
                continue
            seen.add(key)
            records.append({"excerpt_id": key, "document_id": document_id,
                            "current_path": doc["current_path"], "title": doc["title"],
                            "category": doc["category"], "text": text, "start_line": s,
                            "end_line": e, "content_hash": current_hash,
                            "summary": r.get("summary"), "indexed_at": now})
        return {"document_id": document_id, "reused_previous_ranges": reused, "count": len(records),
                "excerpts": records,
                "next_step": "Pass `excerpts` unchanged to the MCP tool upsert_excerpts."}

    def as_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                func=lambda **kw: run_tool("catalog_view", self.catalog_view, **kw),
                name="catalog_view", args_schema=CatalogViewArgs,
                description="Show catalog documents (document_id, current_path, title, category, status) "
                            "with filters, plus a reconciliation report (missing / unregistered files)."),
            StructuredTool.from_function(
                func=lambda **kw: run_tool("catalog_update", self.catalog_update, **kw),
                name="catalog_update", args_schema=CatalogUpdateArgs,
                description="Set title, description (short content summary) or note (classification "
                            "rationale) of a catalogued document. IDs, paths and hashes are managed by code."),
            StructuredTool.from_function(
                func=lambda **kw: run_tool("build_excerpts", self.build_excerpts, **kw),
                name="build_excerpts", args_schema=BuildExcerptsArgs,
                description="Build exact excerpt records for chosen 1-based inclusive line ranges of a "
                            "catalogued document: verified text, content_hash, stable excerpt_id and metadata. "
                            "Omit ranges to reuse previously indexed ranges. Then call upsert_excerpts."),
        ]
