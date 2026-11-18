"""Corpus tools: ingestion (agentic L1 + deterministic L2), L0/L1/L2 navigation, full-text / grep / vector
search on L1 and L2, and citing L2 fragments as evidence.

Every result carries identifiers to jump to the next level: document_id -> get_l1, fragment_id ->
read_fragment / neighbor_fragment, document_id + lines -> open_source. Lists are paged with a cursor:
the page size bounds one answer, the cursor reaches every result.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.catalog import Catalog
from organizer.results import ToolError, run_tool
from organizer.textutil import file_hash, read_text

from harness.store import TaskStore
from harness.tools_task import _norm, locate
from harness.workspace import HarnessWorkspace

from .index import CorpusIndex
from .levels import Levels

L0_PAGE = 25
GREP_PAGE = 20
GREP_PAGE_CHARS = 4200      # one grep page stays below the harness single-result limit (next_cursor stays visible)
READS_BEFORE_HINT = 5       # deep coaching: this many L2 reads without any evidence -> hint to cite
SECTION_PAGE_CHARS = 4200


def enc_cursor(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


def dec_cursor(c: str | None, default):
    if not c:
        return default
    try:
        obj = json.loads(base64.urlsafe_b64decode(c.encode() + b"=" * (-len(c) % 4)).decode())
    except Exception:  # noqa: BLE001
        obj = None
    if not isinstance(obj, dict) or set(obj) != set(default) or not all(isinstance(v, int) and v >= 0 for v in obj.values()):
        raise ToolError("invalid_cursor", "cursor is not valid: never build a cursor yourself, pass the next_cursor value "
                                          "exactly as returned by the previous page of the SAME call")
    return obj


def numbered(text: str, start: int) -> str:
    return "\n".join(f"{start + i}: {l}" for i, l in enumerate(text.split("\n")))


@dataclass
class CorpusEnv:
    ws: HarnessWorkspace
    catalog: Catalog
    levels: Levels
    index: CorpusIndex
    embedder: object
    store: TaskStore
    task_id: str
    search_log: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def log_search(self, kind: str, level: str, query: str, hits: int) -> None:
        with self._lock:
            self.search_log.append((kind, level))
        self.store.event(self.task_id, "corpus_search", {"kind": kind, "level": level, "query": query[:200], "hits": hits})

    def docs_in_scope(self, folder: str | None = None, document_id: str | None = None) -> list[dict]:
        docs = [d for d in self.levels.l0()["documents"] if d["l1"]]
        if folder:
            docs = [d for d in docs if d["folder"] == folder]
        if document_id:
            docs = [d for d in docs if d["document_id"] == document_id]
        return docs


# ======================================================================= schemas
class PathArg(BaseModel):
    path: str = Field(description="Document path in the task root, e.g. data/storage/st_redis_state.md")


class SectionIn(BaseModel):
    title: str = Field(description="Section title (heading or your own title for documents without headings)")
    start_line: int = Field(description="First line of the section (1-based)")
    end_line: int = Field(description="Last line of the section, inclusive")
    summary: str = Field("", description="1-2 sentences: what this section says (for navigation, not a quote)")


class WriteL1Args(BaseModel):
    document_id: str = Field(description="document_id from the catalog")
    title: str
    description: str = Field(description="One sentence: what the document is")
    summary: str = Field(description="3-6 sentences written by you from the document content; key terms and codes")
    sections: list[SectionIn] = Field(description="Ordered sections covering every non-blank line of the file")


class DocIdArg(BaseModel):
    document_id: str


class NoArgs(BaseModel):
    pass


class OverviewArgs(BaseModel):
    cursor: str | None = Field(None, description="next_cursor from the previous page")


class ListDocsArgs(BaseModel):
    folder: str = Field(description="Topic folder from corpus_overview, e.g. storage")
    cursor: str | None = None


class ReadSectionArgs(BaseModel):
    document_id: str
    section_id: str = Field(description="Section id from get_l1, e.g. s02")
    cursor: str | None = None


class FragmentArg(BaseModel):
    fragment_id: str


class NeighborArgs(BaseModel):
    fragment_id: str
    direction: Literal["prev", "next"] = "next"


class OpenSourceArgs(BaseModel):
    document_id: str
    start_line: int
    end_line: int = Field(description="Inclusive; at most 120 lines per call")


class FulltextArgs(BaseModel):
    query: str
    level: Literal["L1", "L2"] = Field(description="L1 = titles/structure/summaries of documents, L2 = source text fragments")
    folder: str | None = Field(None, description="Restrict to a topic folder")
    document_id: str | None = Field(None, description="Restrict to one document")
    size: int = Field(8, description="Results per page (1..10)")
    cursor: str | None = None


class GrepArgs(BaseModel):
    pattern: str = Field(description="Literal substring, or a Python regex if regex=true")
    level: Literal["L1", "L2"] = Field(description="L1 = L1 records (titles, summaries, section titles); L2 = source text lines")
    regex: bool = False
    ignore_case: bool = True
    folder: str | None = None
    document_id: str | None = None
    cursor: str | None = None


class VectorArgs(BaseModel):
    query: str = Field(description="Natural-language description of what you look for (meaning, not exact words)")
    level: Literal["L1", "L2"]
    folder: str | None = None
    document_id: str | None = None
    k: int = Field(6, description="Number of nearest results (1..10)")


class CiteArgs(BaseModel):
    fragment_id: str
    quote: str = Field(description="EXACT text from the fragment (10..600 chars)")
    claim: str = Field(description="Your conclusion that the quote supports")
    kind: Literal["fact", "decision", "limitation", "approach", "other"] = "fact"


# ======================================================================= tools
class CorpusTools:
    def __init__(self, env: CorpusEnv, coach: bool = False) -> None:
        self.coach = coach                  # deep sub-agent: notes on repeated reads, hint to cite
        self._reads: dict[str, int] = {}
        self.env = env

    # ------------------------------------------------------------- ingestion
    def doc_outline(self, path: str) -> dict:
        e = self.env
        p = e.ws.resolve(path, must_exist=True)
        rel = e.ws.rel(p)
        entry = e.catalog.by_path(rel)
        text = read_text(p)
        lines = text.splitlines()
        heads = [(i, len(m.group(1)), m.group(2).strip()) for i, l in enumerate(lines, 1)
                 if (m := re.match(r"^(#{1,6})\s+(.*)$", l))]
        suggested = None
        if heads:
            level = min(h[1] for h in heads if h[1] > 1) if any(h[1] > 1 for h in heads) else heads[0][1]
            marks = [h for h in heads if h[1] == level]
            suggested = []
            for j, (ln, _lv, title) in enumerate(marks):
                start = 1 if j == 0 else ln
                end = (marks[j + 1][0] - 1) if j + 1 < len(marks) else len(lines)
                suggested.append({"title": title, "start_line": start, "end_line": end})
        status = "not_in_catalog" if not entry else ("empty" if not lines else "ready")
        l1 = e.levels.get_l1(entry["document_id"]) if entry else None
        return {"path": rel, "document_id": entry["document_id"] if entry else None, "status": status,
                "content_hash": file_hash(p), "lines": len(lines), "chars": len(text),
                "headings": [{"line": h[0], "level": h[1], "title": h[2]} for h in heads[:80]],
                "suggested_sections": suggested,
                "l1_exists": bool(l1), "l1_current": bool(l1) and l1["content_hash"] == file_hash(p),
                "hint": ("Read the document (read_lines / read_pages for large files) and write YOUR summary with write_l1. "
                         + ("No headings: define sections by line ranges yourself." if not heads else
                            "suggested_sections follow the headings; adjust titles/ranges if needed."))}

    def write_l1(self, document_id: str, title: str, description: str, summary: str, sections: list) -> dict:
        e = self.env
        res = e.levels.write_l1(document_id, title, description, summary, sections)
        rec = res["record"]
        if not res["unchanged"] or rec["document_id"] not in self._l1_ids():
            text = "\n".join([rec["title"], rec["description"], rec["summary"]] +
                             [f"{s['title']}: {s['summary']}" for s in rec["sections"]])
            e.index.upsert_l1(rec, e.embedder.embed([text])[0])
        e.levels.rebuild_l0()
        e.store.event(e.task_id, "l1_written", {"document_id": document_id, "path": rec["path"], "sections": len(rec["sections"]),
                                                "unchanged": res["unchanged"]})
        return {"document_id": document_id, "path": rec["path"], "sections": [
            {"section_id": s["section_id"], "title": s["title"], "lines": [s["start_line"], s["end_line"]]} for s in rec["sections"]],
            "status": "unchanged" if res["unchanged"] else "written", "next": f"index_document('{document_id}')"}

    def _l1_ids(self) -> set[str]:
        try:
            r = self.env.index.es.search(index=self.env.index.l1, size=10000, _source=False)
            return {h["_id"] for h in r["hits"]["hits"]}
        except Exception:  # noqa: BLE001
            return set()

    def index_document(self, document_id: str) -> dict:
        e = self.env
        frags = e.levels.build_fragments(document_id)
        old = e.levels.get_l2(document_id)
        if old and [f["fragment_id"] for f in old["fragments"]] == [f["fragment_id"] for f in frags] \
                and len(e.index.fragment_ids(document_id)) == len(frags):
            return {"document_id": document_id, "fragments": len(frags), "status": "unchanged (same fragment ids, nothing re-indexed)"}
        vectors = e.embedder.embed([f"{f['section_title']}\n{f['text']}" for f in frags])
        res = e.index.replace_l2(document_id, frags, vectors)
        e.levels.save_l2(document_id, frags)
        e.catalog.set_index_state(document_id, "indexed",
                                  excerpts=[{"excerpt_id": f["fragment_id"], "start_line": f["start_line"],
                                             "end_line": f["end_line"], "summary": f["section_title"]} for f in frags])
        e.levels.rebuild_l0()
        e.store.event(e.task_id, "l2_indexed", {"document_id": document_id, "fragments": len(frags), **res})
        return {"document_id": document_id, "fragments": len(frags), **res,
                "first_fragment": frags[0]["fragment_id"] if frags else None}

    def corpus_status(self) -> dict:
        e = self.env
        l0 = e.levels.rebuild_l0()
        cat = e.catalog.load()
        inbox = [d["current_path"] for d in cat["documents"] if not d["current_path"].startswith("data/")]
        todo_l1 = [d["path"] for d in l0["documents"] if not d["l1"] and not d["is_empty"]]
        todo_l2 = [d["path"] for d in l0["documents"] if d["l1"] and not d["l2_current"]]
        return {"totals": l0["totals"], "folders": l0["folders"], "need_l1": todo_l1[:60], "need_l1_count": len(todo_l1),
                "need_l2": todo_l2[:60], "need_l2_count": len(todo_l2), "empty_documents": [d["path"] for d in l0["documents"] if d["is_empty"]],
                "catalogued_outside_data": inbox}

    def validate_corpus(self) -> dict:
        e = self.env
        e.index.refresh()
        return e.levels.validate(e.index.counts_by_document())

    # ------------------------------------------------------------- L0
    def corpus_overview(self, cursor: str | None = None) -> dict:
        l0 = self.env.levels.l0()
        off = dec_cursor(cursor, {"o": 0})["o"]
        docs = l0["documents"][off: off + L0_PAGE]
        nxt = off + L0_PAGE
        res = {"corpus_id": l0["corpus_id"], "totals": l0["totals"], "folders": l0["folders"],
               "documents_page": [{k: d[k] for k in ("document_id", "path", "folder", "title", "l1", "l2_fragments")}
                                  | ({"service": d["service"]} if d.get("service") else {}) for d in docs],
               "page": [off + 1, off + len(docs)], "next_cursor": enc_cursor({"o": nxt}) if nxt < len(l0["documents"]) else None}
        if l0.get("reports"):
            r = l0["reports"]
            res["reports"] = {"latest": r.get("latest"), "latest_complete": r.get("latest_complete"),
                              "count": len(r.get("history", [])),
                              "note": "generated topic reports (service documents): open with get_l1(latest.document_id)"}
        return res

    def list_documents(self, folder: str, cursor: str | None = None) -> dict:
        docs = [d for d in self.env.levels.l0()["documents"] if d["folder"] == folder]
        if not docs:
            raise ToolError("unknown_folder", f"No documents in folder '{folder}'; see corpus_overview().folders")
        off = dec_cursor(cursor, {"o": 0})["o"]
        page = docs[off: off + L0_PAGE]
        nxt = off + L0_PAGE
        return {"folder": folder, "documents_total": len(docs),
                "documents": [{k: d[k] for k in ("document_id", "path", "title", "description", "sections", "l2_fragments")} for d in page],
                "next_cursor": enc_cursor({"o": nxt}) if nxt < len(docs) else None,
                "next": "get_l1(document_id) to read the structure and summary"}

    # ------------------------------------------------------------- L1
    def _track(self, key: str, res: dict) -> dict:
        """Read bookkeeping that survives compaction: repeated reads and reads without evidence are pointed out."""
        if not self.coach:
            return res
        n = self._reads.get(key, 0) + 1
        self._reads[key] = n
        if n > 1:
            res["note_repeat"] = (f"You already read exactly this {n - 1} time(s) in this task. Do not read it again: "
                                  "cite what you need from it with cite_fragment, or move to another document.")
        reads = sum(self._reads.values())
        if reads >= READS_BEFORE_HINT and not self.env.store.evidence(self.env.task_id):
            res["harness_hint"] = (f"{reads} L2 reads and 0 evidence so far. Now call cite_fragment for every relevant "
                                   "line you have read (exact quote), then continue or finish with submit_result.")
        return res

    def get_l1(self, document_id: str) -> dict:
        e = self.env
        l1 = e.levels.get_l1(document_id)
        if not l1:
            raise ToolError("no_l1", "This document has no L1 yet")
        l2 = e.levels.get_l2(document_id) or {"fragments": []}
        by_sec: dict[str, list[str]] = {}
        for f in l2["fragments"]:
            by_sec.setdefault(f["section_id"], []).append(f["fragment_id"])
        return {"level": "L1", "document_id": document_id, "path": l1["path"], "folder": l1["folder"], "title": l1["title"],
                "description": l1["description"], "summary": l1["summary"],
                "sections": [{**s, "fragments": len(by_sec.get(s["section_id"], [])),
                              "first_fragment_id": (by_sec.get(s["section_id"]) or [None])[0]} for s in l1["sections"]],
                "note": "Summary is navigation, NOT evidence: confirm conclusions by reading L2 (read_section / read_fragment)."}

    # ------------------------------------------------------------- L2
    def read_section(self, document_id: str, section_id: str, cursor: str | None = None) -> dict:
        e = self.env
        l2 = e.levels.get_l2(document_id)
        if not l2:
            raise ToolError("no_l2", "Document is not indexed in L2")
        frs = [f for f in l2["fragments"] if f["section_id"] == section_id]
        if not frs:
            raise ToolError("unknown_section", f"No section {section_id} in this document (see get_l1)")
        off = dec_cursor(cursor, {"o": 0})["o"]
        out, size, i = [], 0, off
        while i < len(frs):
            f = e.levels.with_text(frs[i])
            if out and size + len(f["text"]) > SECTION_PAGE_CHARS:
                break
            out.append({"fragment_id": f["fragment_id"], "lines": [f["start_line"], f["end_line"]],
                        "pages": [f["page_start"], f["page_end"]] if f["page_start"] else None,
                        "text": numbered(f["text"], f["start_line"])})
            size += len(f["text"])
            i += 1
        return self._track(f"s|{document_id}|{section_id}|{off}", {
            "level": "L2", "document_id": document_id, "path": l2["fragments"][0]["path"], "section_id": section_id,
            "section_title": frs[0]["section_title"], "fragments_in_section": len(frs), "fragments": out,
            "next_cursor": enc_cursor({"o": i}) if i < len(frs) else None,
            "back": f"get_l1('{document_id}')"})

    def read_fragment(self, fragment_id: str) -> dict:
        return self._track(f"f|{fragment_id}", self._fragment(fragment_id))

    def _fragment(self, fragment_id: str) -> dict:
        f = self.env.levels.fragment(fragment_id)
        return {"level": "L2", "fragment_id": fragment_id, "document_id": f["document_id"], "path": f["path"],
                "section_id": f["section_id"], "section_title": f["section_title"], "lines": [f["start_line"], f["end_line"]],
                "pages": [f["page_start"], f["page_end"]] if f["page_start"] else None, "ordinal": f["ordinal"],
                "prev_id": f["prev_id"], "next_id": f["next_id"], "text": numbered(f["text"], f["start_line"])}

    def neighbor_fragment(self, fragment_id: str, direction: str = "next") -> dict:
        f = self.env.levels.fragment(fragment_id)
        nid = f["next_id"] if direction == "next" else f["prev_id"]
        if not nid:
            return {"fragment_id": fragment_id, "direction": direction, "neighbor": None,
                    "note": "document boundary reached", "back": f"get_l1('{f['document_id']}')"}
        return self.read_fragment(nid)

    def open_source(self, document_id: str, start_line: int, end_line: int) -> dict:
        e = self.env
        d, p, lines = e.levels.source(document_id)
        if not (isinstance(start_line, int) and isinstance(end_line, int)) or start_line < 1 or end_line < start_line:
            raise ToolError("invalid_argument", "need 1 <= start_line <= end_line")
        if end_line - start_line + 1 > 120:
            raise ToolError("too_many_lines", "at most 120 lines per call")
        end_line = min(end_line, len(lines))
        frs = [f["fragment_id"] for f in (e.levels.get_l2(document_id) or {"fragments": []})["fragments"]
               if f["start_line"] <= end_line and f["end_line"] >= start_line]
        return {"level": "L2", "document_id": document_id, "path": d["current_path"], "lines": [start_line, end_line],
                "content_hash": file_hash(p), "fragments": frs,
                "text": numbered("\n".join(lines[start_line - 1: end_line]), start_line)}

    # ------------------------------------------------------------- search: full-text (Elasticsearch BM25)
    def search_fulltext(self, query: str, level: str, folder: str | None = None, document_id: str | None = None,
                        size: int = 8, cursor: str | None = None) -> dict:
        if not query.strip():
            raise ToolError("invalid_argument", "query must be non-empty")
        size = max(1, min(int(size), 10))
        off = dec_cursor(cursor, {"o": 0})["o"]
        res = self.env.index.fulltext(level, query, size, off, folder, document_id)
        hits = []
        for h in res["hits"]:
            s = h["_source"]
            hl = " … ".join(sum((v for v in h.get("highlight", {}).values()), []))
            if level == "L1":
                hits.append({"document_id": s["document_id"], "path": s["path"], "title": s["title"], "score": round(h["_score"], 3),
                             "match": hl or s["description"], "next": f"get_l1('{s['document_id']}')"})
            else:
                hits.append({"fragment_id": s["fragment_id"], "document_id": s["document_id"], "path": s["path"],
                             "section": s["section_title"], "lines": [s["start_line"], s["end_line"]],
                             "score": round(h["_score"], 3), "match": hl or s["text"][:200],
                             "next": f"read_fragment('{s['fragment_id']}')"})
        self.env.log_search("fulltext", level, query, res["total"])
        nxt = off + size
        return {"search": "fulltext", "level": level, "query": query, "total": res["total"], "hits": hits,
                "next_cursor": enc_cursor({"o": nxt}) if nxt < res["total"] else None}

    # ------------------------------------------------------------- search: grep (literal / regex, exact positions)
    def search_grep(self, pattern: str, level: str, regex: bool = False, ignore_case: bool = True,
                    folder: str | None = None, document_id: str | None = None, cursor: str | None = None) -> dict:
        if not pattern:
            raise ToolError("invalid_argument", "pattern must be non-empty")
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE if ignore_case else 0)
        except re.error as err:
            raise ToolError("invalid_regex", str(err))
        hits = self._grep_all(rx, level, self.env.docs_in_scope(folder, document_id))
        off = dec_cursor(cursor, {"o": 0})["o"]
        page, size = [], 0
        for h in hits[off: off + GREP_PAGE]:
            s = len(json.dumps(h, ensure_ascii=False)) + 2
            if page and size + s > GREP_PAGE_CHARS:
                break
            page.append(h)
            size += s
        self.env.log_search("grep", level, pattern, len(hits))
        nxt = off + len(page)
        return {"search": "grep", "level": level, "pattern": pattern, "regex": regex, "total": len(hits),
                "hits": page, "page": [off + 1, off + len(page)],
                "next_cursor": enc_cursor({"o": nxt}) if nxt < len(hits) else None,
                "order": "corpus order (path, line); no ranking"}

    def _grep_all(self, rx, level: str, docs: list[dict]) -> list[dict]:
        e = self.env
        hits = []
        for d in docs:
            if level == "L1":
                l1 = e.levels.get_l1(d["document_id"]) or {}
                fields = [("title", None, l1.get("title", "")), ("description", None, l1.get("description", "")),
                          ("summary", None, l1.get("summary", ""))]
                for s in l1.get("sections", []):
                    fields += [("section_title", s["section_id"], s["title"]), ("section_summary", s["section_id"], s["summary"])]
                for name, sid, text in fields:
                    for m in rx.finditer(text):
                        a = max(m.start() - 80, 0)
                        hits.append({"document_id": d["document_id"], "path": d["path"], "field": name, "section_id": sid,
                                     "offset": m.start(), "match": m.group(0), "context": text[a: m.end() + 100],
                                     "next": f"get_l1('{d['document_id']}')"})
            else:
                l2 = e.levels.get_l2(d["document_id"]) or {"fragments": []}
                _d, _p, lines = e.levels.source(d["document_id"])
                for i, line in enumerate(lines, 1):
                    for m in rx.finditer(line):
                        f = next((f for f in l2["fragments"] if f["start_line"] <= i <= f["end_line"]), None)
                        hits.append({"document_id": d["document_id"], "path": d["path"], "line": i,
                                     "page": f["page_start"] if f and f["page_start"] else None,
                                     "fragment_id": f["fragment_id"] if f else None, "match": m.group(0),
                                     "text": line[:220]})
        return hits

    # ------------------------------------------------------------- search: vector (Elasticsearch kNN)
    def search_vector(self, query: str, level: str, folder: str | None = None, document_id: str | None = None,
                      k: int = 6) -> dict:
        k = max(1, min(int(k), 10))
        vec = self.env.embedder.embed([query])[0]
        res = self.env.index.knn(level, vec, k, folder, document_id)
        hits = []
        for h in res:
            s = h["_source"]
            if level == "L1":
                hits.append({"document_id": s["document_id"], "path": s["path"], "title": s["title"],
                             "similarity": round(h["_score"], 3), "summary": s["summary"][:300],
                             "next": f"get_l1('{s['document_id']}')"})
            else:
                hits.append({"fragment_id": s["fragment_id"], "document_id": s["document_id"], "path": s["path"],
                             "section": s["section_title"], "lines": [s["start_line"], s["end_line"]],
                             "similarity": round(h["_score"], 3), "text": s["text"][:300],
                             "next": f"read_fragment('{s['fragment_id']}')"})
        self.env.log_search("vector", level, query, len(hits))
        return {"search": "vector", "level": level, "query": query, "k": k, "embedding_model": self.env.embedder.name,
                "hits": hits, "note": "nearest by meaning; verify with the L2 text before citing"}

    # ------------------------------------------------------------- evidence from L2
    def cite_fragment(self, fragment_id: str, quote: str, claim: str, kind: str = "fact") -> dict:
        e = self.env
        f = e.levels.fragment(fragment_id)
        q = _norm(quote or "")
        if not 10 <= len(q) <= 600:
            raise ToolError("invalid_argument", "quote must be 10..600 characters of exact fragment text")
        flines = f["text"].split("\n")
        pos = locate(flines, q)
        if not pos:
            raise ToolError("quote_not_found", "The quote does not occur verbatim in this fragment", fragment_id=fragment_id)
        a, b = f["start_line"] + pos[0] - 1, f["start_line"] + pos[1] - 1
        eid = "ev_" + hashlib.sha1("|".join([e.task_id, fragment_id, q]).encode()).hexdigest()[:12]
        created = e.store.add_evidence({"evidence_id": eid, "task_id": e.task_id, "claim": claim.strip()[:600], "kind": kind,
                                        "source_type": "local", "source": f["path"], "document_id": f["document_id"],
                                        "content_hash": f["content_hash"], "page_start": f["page_start"],
                                        "page_end": f["page_end"], "line_start": a, "line_end": b, "quote": q,
                                        "fragment_id": fragment_id})
        return {"evidence_id": eid, "status": "saved" if created else "already_saved", "document_id": f["document_id"],
                "fragment_id": fragment_id, "path": f["path"], "lines": [a, b]}

    # ------------------------------------------------------------- tool sets
    BUDGET_WARN_CALLS = 6

    def _budget_hint(self, res):
        """Deep sub-agent: when only a few model calls are left, every tool result says so - finish with submit_result."""
        if not self.coach or not isinstance(res, dict):
            return res
        t = self.env.store.get_task(self.env.task_id)
        limit = int((t or {}).get("limits", {}).get("max_model_calls") or 0)
        used = int(self.env.store.usage(self.env.task_id).get("model_calls", 0))
        if limit and limit - used <= self.BUDGET_WARN_CALLS:
            res["harness_budget"] = (f"Only {max(limit - used, 0)} of {limit} model calls left. Stop searching: call "
                                     "submit_result(findings, open_questions) NOW with the evidence you have and list "
                                     "unconfirmed points as open questions.")
        return res

    def _mk(self, name, fn, schema, desc) -> BaseTool:
        def call(**kw):
            return self._budget_hint(fn(**kw))
        return StructuredTool.from_function(func=lambda **kw: run_tool(name, call, **kw), name=name, args_schema=schema,
                                            description=desc)

    def ingest_tools(self) -> list[BaseTool]:
        return [
            self._mk("doc_outline", self.doc_outline, PathArg,
                     "Outline of a document for L1: document_id, lines, headings with line numbers, suggested sections, "
                     "whether L1 already exists for this exact version."),
            self._mk("write_l1", self.write_l1, WriteL1Args,
                     "Write the L1 record of ONE document: your title, description, summary and the sections as line ranges "
                     "covering the whole file. The harness validates ranges against the file and stores L1 + its embedding."),
            self._mk("index_document", self.index_document, DocIdArg,
                     "Build L2 for a document with L1: deterministic fragments inside your sections (stable fragment_id, "
                     "lines, pages), embeddings, idempotent upsert into Elasticsearch, catalog + L0 update."),
            self._mk("corpus_status", self.corpus_status, NoArgs,
                     "What is done and what remains: documents without L1 or with stale L2, empty files, inbox files."),
            self._mk("validate_corpus", self.validate_corpus, NoArgs,
                     "Deterministic check of the whole corpus: schema, ids, positions vs files, ES counts. Call before finishing."),
        ]

    def navigation_tools(self) -> list[BaseTool]:
        return [
            self._mk("corpus_overview", self.corpus_overview, OverviewArgs,
                     "L0: folders and a page of documents of the whole corpus with ids (use next_cursor for more)."),
            self._mk("list_documents", self.list_documents, ListDocsArgs,
                     "L0: ALL documents of one folder (paged) with descriptions and document_id."),
            self._mk("get_l1", self.get_l1, DocIdArg,
                     "L1 of a document: title, description, summary and sections with line ranges and first fragment ids."),
            self._mk("read_section", self.read_section, ReadSectionArgs,
                     "L2: source text of a section, fragment by fragment, with line numbers (paged by next_cursor)."),
            self._mk("read_fragment", self.read_fragment, FragmentArg,
                     "L2: one fragment with document_id, section, lines/pages and prev/next ids."),
            self._mk("neighbor_fragment", self.neighbor_fragment, NeighborArgs, "L2: the previous or next fragment."),
            self._mk("open_source", self.open_source, OpenSourceArgs,
                     "L2: exact source lines of a document by range (<=120 lines) and the fragments covering them."),
        ]

    def search_tools(self) -> list[BaseTool]:
        return [
            self._mk("search_fulltext", self.search_fulltext, FulltextArgs,
                     "Full-text (BM25, Elasticsearch) on L1 (titles, structure, summaries) or L2 (source fragments). Ranked, paged."),
            self._mk("search_grep", self.search_grep, GrepArgs,
                     "grep: literal substring or regex on L1 records or L2 source lines; exact document, field/line, fragment. "
                     "Corpus order, no ranking, paged with next_cursor up to the last hit."),
            self._mk("search_vector", self.search_vector, VectorArgs,
                     "Vector search (Elasticsearch kNN over embeddings) on L1 summaries/structure or L2 fragments; for paraphrases."),
            self._mk("cite_fragment", self.cite_fragment, CiteArgs,
                     "Save evidence: exact quote from an L2 fragment; the harness verifies it and records document_id, "
                     "fragment_id and lines. Cite evidence_id in answers."),
        ]
