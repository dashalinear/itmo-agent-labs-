"""Selective reading of large documents: page/line ranges and full-text search.

Pages are taken from explicit markers (``=== Страница N ===``, ``=== Page N ===``,
``<!-- page N -->``, form feed); a file without markers gets virtual pages of
50 lines. Every result carries document_id (lab 1 catalog), content_hash of the
exact version and the page/line range, and every read range is recorded in the
range registry (progress, "return to a found place", no double work on resume).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.catalog import Catalog
from organizer.results import ToolError, run_tool
from organizer.textutil import sha256_bytes

from .config import Limits
from .store import TaskStore
from .workspace import HarnessWorkspace

PAGE_RE = re.compile(r"^\s*(?:=== Страница (\d+) ===|=== Page (\d+) ===|<!--\s*page\s+(\d+)\s*-->)\s*$", re.I)
VIRTUAL_PAGE_LINES = 50
TEXT_SUFFIXES = {".md", ".txt", ".markdown", ".rst", ".csv", ".json", ".html"}


@dataclass
class DocText:
    rel: str
    lines: list[str]
    content_hash: str
    pages: list[tuple[int, int, int]]   # (page_no, first_line, last_line), 1-based inclusive
    paged: bool
    chars: int

    def page_of_line(self, line: int) -> int:
        for no, a, b in self.pages:
            if a <= line <= b:
                return no
        return self.pages[-1][0] if self.pages else 1

    def page_bounds(self, page: int) -> tuple[int, int]:
        for no, a, b in self.pages:
            if no == page:
                return a, b
        raise ToolError("invalid_argument", f"page {page} does not exist (1..{len(self.pages)})")


_cache: dict[tuple[str, int, int], DocText] = {}
_cache_lock = threading.Lock()


def load_doc(path: Path, rel: str) -> DocText:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    marks = []
    for i, line in enumerate(lines, 1):
        m = PAGE_RE.match(line) if "===" in line or "<!--" in line else None
        if m:
            marks.append((int(next(g for g in m.groups() if g)), i))
        elif line.startswith("\f"):
            marks.append((len(marks) + 1, i))
    pages: list[tuple[int, int, int]] = []
    if marks:
        for j, (no, start) in enumerate(marks):
            end = marks[j + 1][1] - 1 if j + 1 < len(marks) else len(lines)
            pages.append((no, start, end))
        if marks[0][1] > 1:  # text before the first marker belongs to page 1
            no, _, end = pages[0]
            pages[0] = (no, 1, end)
        paged = True
    else:
        for k in range(0, max(len(lines), 1), VIRTUAL_PAGE_LINES):
            pages.append((k // VIRTUAL_PAGE_LINES + 1, k + 1, min(k + VIRTUAL_PAGE_LINES, len(lines))))
        paged = False
    doc = DocText(rel, lines, sha256_bytes(raw), pages, paged, len(text))
    with _cache_lock:
        _cache[key] = doc
    return doc


class DocInfoArgs(BaseModel):
    path: str = Field(description="Document path relative to the task root, e.g. data/search/handbook.md")


class ReadPagesArgs(BaseModel):
    path: str = Field(description="Document path relative to the task root")
    start_page: int = Field(description="First page (1-based)")
    end_page: int = Field(description="Last page, inclusive; at most max_pages_per_read pages per call")


class ReadLinesArgs(BaseModel):
    path: str = Field(description="Document path relative to the task root")
    start_line: int = Field(description="First line (1-based)")
    end_line: int = Field(description="Last line, inclusive")


class SearchTextArgs(BaseModel):
    query: str = Field(description="Text to find (case-insensitive substring) or a Python regex if regex=true")
    path: str = Field(".", description="File or folder (searched recursively) relative to the task root")
    regex: bool = Field(False, description="Treat query as a regular expression")
    max_hits: int = Field(20, description="Maximum hits to return (1..100)")


class DocTools:
    def __init__(self, ws: HarnessWorkspace, catalog: Catalog, store: TaskStore, task_id: str, limits: Limits) -> None:
        self.ws = ws
        self.catalog = catalog
        self.store = store
        self.task_id = task_id
        self.limits = limits

    # -- helpers -------------------------------------------------------------
    def open(self, path: str) -> tuple[Path, DocText, str | None]:
        p = self.ws.resolve(path, must_exist=True)
        if p.is_dir():
            raise ToolError("is_directory", f"Expected a file: {path}")
        rel = self.ws.rel(p)
        try:
            doc = load_doc(p, rel)
        except UnicodeDecodeError:
            raise ToolError("not_utf8", f"{rel} is not UTF-8 text")
        entry = self.catalog.by_path(rel)
        doc_id = entry["document_id"] if entry else None
        status = self.store.upsert_document(self.task_id, rel, doc_id, doc.content_hash, len(doc.pages),
                                            len(doc.lines), doc.chars)
        if status == "changed":
            self.store.event(self.task_id, "document_changed", {"path": rel, "content_hash": doc.content_hash,
                                                                 "note": "ranges of the old version are not reused"})
        return p, doc, doc_id

    @staticmethod
    def _int(name: str, v, lo: int = 1) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or v < lo:
            raise ToolError("invalid_argument", f"{name} must be an integer >= {lo}, got {v!r}")
        return v

    def _pages_read(self, rel: str, doc: DocText) -> set[int]:
        done: set[int] = set()
        for r in self.store.ranges(self.task_id, rel, doc.content_hash):
            if r["unit"] == "page":
                done.update(range(r["start_no"], r["end_no"] + 1))
            else:
                done.update({doc.page_of_line(r["start_no"]), doc.page_of_line(r["end_no"])})
        return done

    def _render(self, doc: DocText, first: int, last: int) -> str:
        out, page = [], None
        for i in range(first, last + 1):
            pno = doc.page_of_line(i)
            if pno != page:
                page = pno
                out.append(f"[стр. {pno} | строки {max(first, doc.page_bounds(pno)[0])}–{min(last, doc.page_bounds(pno)[1])}]")
            out.append(f"{i}: {doc.lines[i - 1]}")
        return "\n".join(out)

    # -- tools ---------------------------------------------------------------
    def doc_info(self, path: str) -> dict:
        _p, doc, doc_id = self.open(path)
        read = self._pages_read(doc.rel, doc)
        return {"path": doc.rel, "document_id": doc_id, "content_hash": doc.content_hash, "chars": doc.chars,
                "lines": len(doc.lines), "pages": len(doc.pages), "page_markers": doc.paged,
                "approx_tokens": int(doc.chars / 2.6),
                "pages_read": len(read), "coverage": round(len(read) / max(len(doc.pages), 1), 3),
                "hint": "Never read the whole document: use search_text, then read_pages for the found pages."}

    def read_pages(self, path: str, start_page: int, end_page: int) -> dict:
        self._int("start_page", start_page)
        self._int("end_page", end_page)
        if end_page < start_page:
            raise ToolError("invalid_argument", "end_page must be >= start_page")
        if end_page - start_page + 1 > self.limits.max_pages_per_read:
            raise ToolError("too_many_pages", f"At most {self.limits.max_pages_per_read} pages per call; "
                                              "read in several calls or search first")
        _p, doc, doc_id = self.open(path)
        if end_page > len(doc.pages):
            raise ToolError("invalid_argument", f"Document has {len(doc.pages)} pages")
        a, _ = doc.page_bounds(start_page)
        _, b = doc.page_bounds(end_page)
        new = self.store.add_range(self.task_id, doc.rel, doc.content_hash, "page", start_page, end_page)
        return {"path": doc.rel, "document_id": doc_id, "content_hash": doc.content_hash,
                "pages": [start_page, end_page], "lines": [a, b], "total_pages": len(doc.pages),
                "already_read_before": not new, "text": self._render(doc, a, b)}

    def read_lines(self, path: str, start_line: int, end_line: int) -> dict:
        self._int("start_line", start_line)
        self._int("end_line", end_line)
        if end_line < start_line:
            raise ToolError("invalid_argument", "end_line must be >= start_line")
        if end_line - start_line + 1 > self.limits.max_lines_per_read:
            raise ToolError("too_many_lines", f"At most {self.limits.max_lines_per_read} lines per call")
        _p, doc, doc_id = self.open(path)
        if start_line > len(doc.lines):
            raise ToolError("invalid_argument", f"Document has {len(doc.lines)} lines")
        end_line = min(end_line, len(doc.lines))
        new = self.store.add_range(self.task_id, doc.rel, doc.content_hash, "line", start_line, end_line)
        # the pages these lines belong to count in the progress as partly read ("page_part"); before, progress
        # counted only read_pages and showed 0 pages for A1, where the agent read the handbook by lines
        self.store.add_range(self.task_id, doc.rel, doc.content_hash, "page_part",
                             doc.page_of_line(start_line), doc.page_of_line(end_line))
        return {"path": doc.rel, "document_id": doc_id, "content_hash": doc.content_hash,
                "lines": [start_line, end_line],
                "pages": [doc.page_of_line(start_line), doc.page_of_line(end_line)],
                "already_read_before": not new, "text": self._render(doc, start_line, end_line)}

    def search_text(self, query: str, path: str = ".", regex: bool = False, max_hits: int = 20) -> dict:
        if not query or not query.strip():
            raise ToolError("invalid_argument", "query must be non-empty")
        if isinstance(max_hits, bool) or not isinstance(max_hits, int) or not 1 <= max_hits <= 100:
            raise ToolError("invalid_argument", f"max_hits must be in 1..100, got {max_hits!r}")
        try:
            rx = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        except re.error as e:
            raise ToolError("invalid_regex", str(e))
        root = self.ws.resolve(path, must_exist=True)
        files = [root] if root.is_file() else sorted(
            Path(dp) / n for dp, dirs, names in os.walk(root, followlinks=False)
            if not self.ws.in_service(Path(dp).resolve())
            for n in names if Path(n).suffix.lower() in TEXT_SUFFIXES)
        hits, total, per_file, skipped = [], 0, {}, []
        for f in files:
            if self.ws.is_symlink_escape(f) or not self.ws.contains(f.resolve()) or self.ws.in_service(f.resolve()):
                skipped.append({"path": str(f.name), "reason": "outside_or_service"})
                continue
            if f.resolve() == self.ws.catalog_path:
                continue
            try:
                _p, doc, doc_id = self.open(self.ws.rel(f))
            except ToolError as e:
                skipped.append({"path": self.ws.rel(f), "reason": e.type})
                continue
            self.store.mark_searched(self.task_id, doc.rel)
            for i, line in enumerate(doc.lines, 1):
                m = rx.search(line)
                if not m:
                    continue
                total += 1
                per_file[doc.rel] = per_file.get(doc.rel, 0) + 1
                if len(hits) < max_hits:
                    s = max(m.start() - 100, 0)
                    hits.append({"path": doc.rel, "document_id": doc_id, "page": doc.page_of_line(i), "line": i,
                                 "snippet": line[s: m.end() + 140]})
        return {"query": query, "path": self.ws.rel(root), "files_searched": len(files) - len(skipped),
                "total_hits": total, "returned": len(hits), "has_more": total > len(hits),
                "hits_per_file": per_file, "hits": hits, "skipped": skipped}

    def as_tools(self) -> list[BaseTool]:
        def mk(name, fn, schema, desc):
            return StructuredTool.from_function(func=lambda **kw: run_tool(name, fn, **kw), name=name,
                                                args_schema=schema, description=desc)
        return [
            mk("doc_info", self.doc_info, DocInfoArgs,
               "Size of a document (chars, lines, pages, approx tokens), document_id, content_hash and how many pages "
               "you have already read. Call before reading a large file."),
            mk("read_pages", self.read_pages, ReadPagesArgs,
               "Read a page range of a document (pages from explicit page markers). Lines are numbered; "
               "the result has document_id, content_hash, pages and lines for citing."),
            mk("read_lines", self.read_lines, ReadLinesArgs,
               "Read a line range of a document (numbered lines with page labels)."),
            mk("search_text", self.search_text, SearchTextArgs,
               "Full-text search over a document or folder: hits with path, document_id, page, line and snippet. "
               "Use it to locate places in large documents, then read_pages around the hit."),
        ]
