"""Exhaustive search tools: NO top-k, NO vector selection, NO ranking that could exclude documents.

  area_define(folder | document_ids)  fixes the finite area from L0: every document and ALL its L2 fragments
  area_next(cursor)                   next fragments of the area in corpus order (path, ordinal), full text;
                                      delivered fragments go to the coverage registry (state.db ranges, unit=fragment)
  area_grep(pattern, cursor)          literal/regex over ALL source lines of the area, paged to the last hit
  area_fulltext(query, cursor)        Elasticsearch full-text MATCH as a filter, sorted by (path, ordinal) with
                                      search_after: every matching fragment, no score, no size limit overall
  record_mention(fragment_id, quote)  verified evidence of one mention
  area_coverage()                     read / total documents and fragments, remaining ranges, resume cursor

The batch returned for a given cursor is deterministic, so a tools step replayed after a crash returns the
same fragments again (nothing is skipped). Completion requires every fragment of the area to be delivered.
"""
from __future__ import annotations

import json
import re

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.results import ToolError, run_tool

from .levels import _atomic_json, now_iso
from .tools_corpus import CorpusEnv, CorpusTools, dec_cursor, enc_cursor, numbered

NEXT_CHARS = 5200
NEXT_MAX = 8
AREA_GREP_PAGE = 15
AREA_FT_PAGE = 15


class AreaArgs(BaseModel):
    folder: str | None = Field(None, description="Topic folder of the corpus (from L0)")
    document_ids: list[str] | None = Field(None, description="Or an explicit list of document_id")


class NextArgs(BaseModel):
    cursor: str | None = Field(None, description="next_cursor of the previous batch; omit to start at the first unread fragment")


class AreaGrepArgs(BaseModel):
    pattern: str
    regex: bool = False
    ignore_case: bool = True
    cursor: str | None = None


class AreaFtArgs(BaseModel):
    query: str
    cursor: str | None = None


class MentionArgs(BaseModel):
    fragment_id: str
    quote: str = Field(description="Exact text of the mention from the fragment")
    note: str = Field(description="What is mentioned (e.g. the limitation in your words, with its code if any)")


class NoArgs(BaseModel):
    pass


class ExhaustiveTools:
    def __init__(self, env: CorpusEnv, corpus: CorpusTools) -> None:
        self.env = env
        self.corpus = corpus

    # ------------------------------------------------------------------ area
    def _area_path(self):
        return self.env.ws.service_path("tasks", self.env.task_id, "area.json")

    def area(self) -> dict:
        p = self._area_path()
        if not p.exists():
            raise ToolError("no_area", "Define the area first: area_define(folder=...)")
        return json.loads(p.read_text(encoding="utf-8"))

    def area_define(self, folder: str | None = None, document_ids: list[str] | None = None) -> dict:
        e = self.env
        p = self._area_path()
        if p.exists():
            a = json.loads(p.read_text(encoding="utf-8"))
            return {"status": "already_defined", **self._area_view(a), "note": "The area of this task is fixed; continue with area_next."}
        docs = e.levels.l0()["documents"]
        if folder:
            docs = [d for d in docs if d["folder"] == folder]
        elif document_ids:
            docs = [d for d in docs if d["document_id"] in set(document_ids)]
        else:
            raise ToolError("invalid_argument", "give folder or document_ids")
        if not docs:
            raise ToolError("empty_area", "No documents match; see corpus_overview")
        frags = []
        for d in docs:
            frags += [f["fragment_id"] for f in (e.levels.get_l2(d["document_id"]) or {"fragments": []})["fragments"]]
        a = {"folder": folder, "documents": [{"document_id": d["document_id"], "path": d["path"], "title": d["title"],
                                              "fragments": d["l2_fragments"], "has_l2": d["l2_current"]} for d in docs],
             "fragment_ids": frags, "defined_at": now_iso()}
        _atomic_json(p, a)
        e.store.event(e.task_id, "area_defined", {"folder": folder, "documents": len(docs), "fragments": len(frags)})
        return {"status": "defined", **self._area_view(a)}

    @staticmethod
    def _area_view(a: dict) -> dict:
        return {"folder": a["folder"], "documents": [{k: d[k] for k in ("document_id", "path", "fragments", "has_l2")} for d in a["documents"]],
                "documents_total": len(a["documents"]), "fragments_total": len(a["fragment_ids"])}

    def _read_set(self) -> set[str]:
        return {r["path"] for r in self.env.store.ranges(self.env.task_id) if r["unit"] == "fragment"}

    def _frag_index(self) -> dict[str, dict]:
        a = self.area()
        idx = {}
        for d in a["documents"]:
            for f in (self.env.levels.get_l2(d["document_id"]) or {"fragments": []})["fragments"]:
                idx[f["fragment_id"]] = f
        return idx

    # ------------------------------------------------------------------ full reading
    def area_next(self, cursor: str | None = None) -> dict:
        """A batch counts as READ only when the agent asks for the next one with next_cursor (acknowledgement):
        a batch delivered right before a budget stop / crash is delivered again on resume (cursor=None)."""
        e = self.env
        a = self.area()
        ids = a["fragment_ids"]
        idx = self._frag_index()
        if cursor:
            c = dec_cursor(cursor, {"p": 0, "from": 0})
            pos = c["p"]
            for fid in ids[c.get("from", pos): pos]:            # acknowledge the previous batch
                f = idx[fid]
                e.store.add_range(e.task_id, fid, f["content_hash"], "fragment", f["ordinal"], f["ordinal"])
        else:
            read = self._read_set()
            pos = next((i for i, fid in enumerate(ids) if fid not in read), len(ids))
        if pos >= len(ids):
            return {"done": True, "fragments": [], "coverage": self._coverage_short(),
                    "note": "All fragments of the area have been read and acknowledged."}
        out, size, i = [], 0, pos
        while i < len(ids) and len(out) < NEXT_MAX:
            f = e.levels.with_text(idx[ids[i]])
            if out and size + len(f["text"]) > NEXT_CHARS:
                break
            out.append({"fragment_id": f["fragment_id"], "document_id": f["document_id"], "path": f["path"],
                        "section": f["section_title"], "lines": [f["start_line"], f["end_line"]],
                        "text": numbered(f["text"], f["start_line"])})
            size += len(f["text"])
            i += 1
        return {"done": False, "batch": [pos + 1, i], "of": len(ids), "fragments": out,
                "next_cursor": enc_cursor({"p": i, "from": pos}), "coverage": self._coverage_short(),
                "instruction": "Record every mention in this batch with record_mention, then call area_next(next_cursor); "
                               "the batch counts as read with that call. done=true comes after the last batch."}

    # ------------------------------------------------------------------ exhaustive grep / full-text passes
    def area_grep(self, pattern: str, regex: bool = False, ignore_case: bool = True, cursor: str | None = None) -> dict:
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE if ignore_case else 0)
        except re.error as err:
            raise ToolError("invalid_regex", str(err))
        a = self.area()
        docs = [{"document_id": d["document_id"], "path": d["path"]} for d in a["documents"] if d["has_l2"]]
        hits = self.corpus._grep_all(rx, "L2", docs)
        off = dec_cursor(cursor, {"o": 0})["o"]
        page = hits[off: off + AREA_GREP_PAGE]
        nxt = off + AREA_GREP_PAGE
        self.env.store.event(self.env.task_id, "area_grep_page", {"pattern": pattern, "page": [off + 1, off + len(page)],
                                                                  "total": len(hits)})
        return {"pattern": pattern, "total": len(hits), "page": [off + 1, off + len(page)], "hits": page,
                "done": nxt >= len(hits), "next_cursor": enc_cursor({"o": nxt}) if nxt < len(hits) else None,
                "note": "Read pages until done=true. Grep finds only literal matches: semantic mentions need area_next."}

    def area_fulltext(self, query: str, cursor: str | None = None) -> dict:
        a = self.area()
        ids = [d["document_id"] for d in a["documents"] if d["has_l2"]]
        after = dec_cursor(cursor, {"a": None})["a"]
        res = self.env.index.scan_matches(query, ids, after, AREA_FT_PAGE)
        hits = res["hits"]
        out = [{"fragment_id": h["_source"]["fragment_id"], "document_id": h["_source"]["document_id"],
                "path": h["_source"]["path"], "lines": [h["_source"]["start_line"], h["_source"]["end_line"]],
                "match": " … ".join(h.get("highlight", {}).get("text", []))} for h in hits]
        nxt = hits[-1]["sort"] if len(hits) == AREA_FT_PAGE else None
        self.env.store.event(self.env.task_id, "area_fulltext_page", {"query": query, "returned": len(out), "total": res["total"]})
        return {"query": query, "total": res["total"], "hits": out, "order": "path, ordinal (no score, no cut-off)",
                "done": nxt is None, "next_cursor": enc_cursor({"a": nxt}) if nxt else None}

    # ------------------------------------------------------------------ mentions and coverage
    def record_mention(self, fragment_id: str, quote: str, note: str) -> dict:
        if fragment_id not in set(self.area()["fragment_ids"]):
            raise ToolError("outside_area", "This fragment is not part of the fixed area")
        return self.corpus.cite_fragment(fragment_id, quote, note, kind="limitation")

    def _coverage_short(self) -> dict:
        a = self.area()
        read = self._read_set()
        n = len(a["fragment_ids"])
        r = sum(1 for f in a["fragment_ids"] if f in read)
        return {"fragments_read": r, "fragments_total": n, "complete": r == n}

    def area_coverage(self) -> dict:
        a = self.area()
        read = self._read_set()
        idx = self._frag_index()
        docs, first_unread = [], None
        for d in a["documents"]:
            frs = (self.env.levels.get_l2(d["document_id"]) or {"fragments": []})["fragments"]
            unread = [f for f in frs if f["fragment_id"] not in read]
            docs.append({"document_id": d["document_id"], "path": d["path"], "fragments": len(frs),
                         "read": len(frs) - len(unread), "status": "done" if not unread else ("partial" if len(unread) < len(frs) else "not_started"),
                         "unread_lines": [[f["start_line"], f["end_line"]] for f in unread][:20],
                         "has_l2": d["has_l2"]})
        for i, fid in enumerate(a["fragment_ids"]):
            if fid not in read:
                first_unread = i
                break
        ev = self.env.store.evidence(self.env.task_id)
        short = self._coverage_short()
        return {**short, "documents_total": len(docs), "documents_done": sum(1 for d in docs if d["status"] == "done"),
                "documents": docs, "mentions_recorded": len(ev),
                "resume_cursor": enc_cursor({"p": first_unread}) if first_unread is not None else None,
                }

    def as_tools(self) -> list[BaseTool]:
        def mk(name, fn, schema, desc):
            return StructuredTool.from_function(func=lambda **kw: run_tool(name, fn, **kw), name=name, args_schema=schema,
                                                description=desc)
        return [
            mk("area_define", self.area_define, AreaArgs,
               "Fix the finite area from L0 (a folder or document ids): every document and ALL its L2 fragments."),
            mk("area_next", self.area_next, NextArgs,
               "Next fragments of the area in corpus order with full text; continue with next_cursor until done=true. "
               "Every delivered fragment is recorded in the coverage registry."),
            mk("area_grep", self.area_grep, AreaGrepArgs,
               "Literal/regex search over ALL source lines of the area; paged in corpus order; read every page until done."),
            mk("area_fulltext", self.area_fulltext, AreaFtArgs,
               "Full-text match over the area as a FILTER (no ranking, no top-k), ordered by path; read every page until done."),
            mk("record_mention", self.record_mention, MentionArgs,
               "Save one found mention as verified evidence (exact quote from a fragment of the area)."),
            mk("area_coverage", self.area_coverage, NoArgs,
               "Coverage registry: fragments read / total per document, unread line ranges, resume_cursor, mentions recorded."),
        ]
