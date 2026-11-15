"""Task tools: evidence, report sections, plan, progress and completion.

* save_evidence    - the quote is verified VERBATIM (whitespace-normalized) against the
                     source: a local document version (page/line range) or the stored copy
                     of a fetched web page. Key = hash(task, source, version, quote), so a
                     repeated call (e.g. after resume) returns "already_saved".
* write_report_section - upsert by section_id; unchanged body -> "unchanged".
* complete_task    - the harness checks the completion criteria of the task contract;
                     unmet criteria -> error with the missing items (status stays running).
* stop_partial     - honest partial result: status paused, reason and unverified items kept.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.catalog import Catalog
from organizer.results import ToolError, run_tool

from .artifacts import ArtifactStore
from .budget import Budget
from .report import render_report
from .store import TaskStore
from .tools_docs import load_doc
from .web import canonical_url, url_key
from .workspace import HarnessWorkspace

EVIDENCE_KINDS = ("approach", "limitation", "controversial", "fact", "decision", "other")
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def locate(lines: list[str], quote: str, first: int = 1, last: int | None = None) -> tuple[int, int] | None:
    """Find a whitespace-normalized quote inside lines[first..last]; returns (line_start, line_end)."""
    last = last or len(lines)
    chars, owner = [], []
    prev_space = True
    for ln in range(first, last + 1):
        for ch in lines[ln - 1] + "\n":
            if ch.isspace():
                if not prev_space:
                    chars.append(" ")
                    owner.append(ln)
                prev_space = True
            else:
                chars.append(ch)
                owner.append(ln)
                prev_space = False
    text = "".join(chars)
    q = _norm(quote)
    i = text.find(q)
    if i < 0 or not q:
        return None
    return owner[i], owner[i + len(q) - 1]


class EvidenceArgs(BaseModel):
    claim: str = Field(description="Your claim/finding that the quote supports (one sentence)")
    kind: Literal["approach", "limitation", "controversial", "fact", "decision", "other"] = Field(description="Type of finding")
    source: str = Field(description="Local document path (task root relative) or the URL of a fetched page")
    quote: str = Field(description="EXACT text copied from the source (10..600 chars), not a paraphrase")
    page: int | None = Field(None, description="Page where the quote is (local documents); optional")
    page_end: int | None = Field(None, description="Last page if the quote spans pages")
    line_start: int | None = Field(None, description="Optional line range instead of pages")
    line_end: int | None = Field(None, description="Optional end line")


class SectionArgs(BaseModel):
    section_id: str = Field(description="Stable id, e.g. approaches, limitations, disputed, summary (a-z0-9_-)")
    title: str = Field(description="Section heading")
    body: str = Field(description="Markdown body; cite evidence as [ev_xxxx] or (документ, стр. N)")


class PlanStep(BaseModel):
    title: str
    status: Literal["todo", "doing", "done", "skipped"] = "todo"


class PlanArgs(BaseModel):
    steps: list[PlanStep] = Field(description="Whole plan; replaces the previous one")


class CompleteArgs(BaseModel):
    note: str = Field(description="Short statement why the task is complete")


class PartialArgs(BaseModel):
    reason: str = Field(description="Why you stop with a partial result (no progress, limits, missing sources)")
    unverified: list[str] = Field(default_factory=list, description="Claims/sources that remain unverified")


class NoArgs(BaseModel):
    pass


class TaskTools:
    def __init__(self, ws: HarnessWorkspace, catalog: Catalog, store: TaskStore, task_id: str,
                 artifacts: ArtifactStore, budget: Budget) -> None:
        self.ws = ws
        self.catalog = catalog
        self.store = store
        self.task_id = task_id
        self.artifacts = artifacts
        self.budget = budget

    @property
    def spec(self) -> dict:
        return self.store.get_task(self.task_id)["spec"]

    def report_path(self) -> Path:
        return self.ws.service_path("tasks", self.task_id, "report.md")

    def render(self) -> str:
        path = self.report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = render_report(self.store, self.task_id, self.budget.snapshot())
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return self.ws.rel(path)

    # -- evidence ---------------------------------------------------------------
    def save_evidence(self, claim: str, kind: str, source: str, quote: str, page: int | None = None,
                      page_end: int | None = None, line_start: int | None = None, line_end: int | None = None) -> dict:
        q = _norm(quote or "")
        if not 10 <= len(q) <= 600:
            raise ToolError("invalid_argument", "quote must be 10..600 characters of exact source text")
        if kind not in EVIDENCE_KINDS:
            raise ToolError("invalid_argument", f"kind must be one of {EVIDENCE_KINDS}")
        if not (claim or "").strip():
            raise ToolError("invalid_argument", "claim must be non-empty")
        rec = {"task_id": self.task_id, "claim": claim.strip()[:600], "kind": kind, "quote": q}
        if source.startswith(("http://", "https://")):
            k = url_key(canonical_url(source))
            row = self.store.get_url(self.task_id, k)
            if not row or row["status"] != "done" or not row.get("artifact_id"):
                raise ToolError("source_not_fetched", "Fetch the page with web_fetch before citing it", url=source)
            lines = self.artifacts.text(row["artifact_id"]).split("\n")
            pos = locate(lines, q)
            if not pos:
                raise ToolError("quote_not_found", "The quote does not occur verbatim in the fetched page", url=source)
            rec.update(source_type="web", source=row["final_url"] or row["url"], document_id=row["artifact_id"],
                       content_hash=row["content_hash"], line_start=pos[0], line_end=pos[1])
        else:
            p = self.ws.resolve(source, must_exist=True)
            rel = self.ws.rel(p)
            doc = load_doc(p, rel)
            first, last = 1, len(doc.lines)
            if page is not None:
                first = doc.page_bounds(int(page))[0]
                last = doc.page_bounds(int(page_end or page))[1]
            elif line_start is not None:
                first, last = int(line_start), int(line_end or line_start)
            pos = locate(doc.lines, q, first, last)
            if not pos:
                anywhere = locate(doc.lines, q)
                if anywhere:
                    raise ToolError("quote_not_in_range", "The quote exists but not in the given page/line range",
                                    actual_page=doc.page_of_line(anywhere[0]), actual_lines=list(anywhere))
                raise ToolError("quote_not_found", "The quote does not occur verbatim in the document", path=rel)
            entry = self.catalog.by_path(rel)
            rec.update(source_type="local", source=rel, document_id=entry["document_id"] if entry else None,
                       content_hash=doc.content_hash, page_start=doc.page_of_line(pos[0]),
                       page_end=doc.page_of_line(pos[1]), line_start=pos[0], line_end=pos[1])
        key = "|".join([self.task_id, rec["source"], rec["content_hash"] or "", q])
        rec["evidence_id"] = "ev_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        created = self.store.add_evidence(rec)
        return {"evidence_id": rec["evidence_id"], "status": "saved" if created else "already_saved",
                "source": rec["source"], "page_start": rec.get("page_start"), "page_end": rec.get("page_end"),
                "line_start": rec["line_start"], "line_end": rec["line_end"],
                "evidence_total": len(self.store.evidence(self.task_id))}

    # -- report ---------------------------------------------------------------------
    def write_report_section(self, section_id: str, title: str, body: str) -> dict:
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", section_id or ""):
            raise ToolError("invalid_argument", "section_id must match [a-z0-9_-]{1,40}")
        if len(body or "") > 8000:
            raise ToolError("too_long", "Section body must be <= 8000 characters; split it into sections")
        status = self.store.upsert_section(self.task_id, section_id, title.strip()[:200], body)
        cited = sorted(set(re.findall(r"\bev_[0-9a-f]{12}\b", body)))
        known = {e["evidence_id"] for e in self.store.evidence(self.task_id)}
        return {"section_id": section_id, "status": status, "report": self.render(),
                "cited_evidence": len(cited), "unknown_evidence_ids": [c for c in cited if c not in known]}

    def update_plan(self, steps: list) -> dict:
        plan = [s if isinstance(s, dict) else s.model_dump() for s in steps]
        self.store.update_task(self.task_id, plan=plan)
        return {"plan": plan}

    # -- progress / completion ------------------------------------------------------------
    def criteria(self) -> dict:
        spec = self.spec
        crit = spec.get("criteria", {})
        ev = self.store.evidence(self.task_id)
        docs = self.store.documents(self.task_id)
        scope_docs = [d["path"] for d in docs if any(d["path"].startswith(s.rstrip("/")) for s in spec.get("scope", []))]
        with_ev = {e["source"] for e in ev if e["source_type"] == "local"}
        sections = {s["section_id"] for s in self.store.sections(self.task_id)}
        q = self.store.url_counts(self.task_id)
        web_ev_sources = {e["source"] for e in ev if e["source_type"] == "web"}
        checks = {}
        if "min_evidence" in crit:
            checks["min_evidence"] = (len(ev) >= crit["min_evidence"], f"{len(ev)}/{crit['min_evidence']}")
        if crit.get("evidence_in_every_scope_document"):
            missing = [d for d in scope_docs if d not in with_ev]
            checks["evidence_in_every_scope_document"] = (bool(scope_docs) and not missing,
                                                          f"missing: {missing}" if missing else f"{len(scope_docs)} documents")
        if "required_sections" in crit:
            miss = [s for s in crit["required_sections"] if s not in sections]
            checks["required_sections"] = (not miss, f"missing: {miss}" if miss else "all present")
        if "min_web_pages_read" in crit:
            checks["min_web_pages_read"] = (q["done"] >= crit["min_web_pages_read"], f"{q['done']}/{crit['min_web_pages_read']}")
        if "min_web_sources_cited" in crit:
            checks["min_web_sources_cited"] = (len(web_ev_sources) >= crit["min_web_sources_cited"],
                                               f"{len(web_ev_sources)}/{crit['min_web_sources_cited']}")
        if "min_search_queries" in crit:
            n = len(self.store.searches(self.task_id))
            checks["min_search_queries"] = (n >= crit["min_search_queries"], f"{n}/{crit['min_search_queries']}")
        return {k: {"ok": v[0], "detail": v[1]} for k, v in checks.items()}

    def progress(self) -> dict:
        docs = []
        for d in self.store.documents(self.task_id):
            read = set()
            for r in self.store.ranges(self.task_id, d["path"], d["content_hash"]):
                if r["unit"] == "page":
                    read.update(range(r["start_no"], r["end_no"] + 1))
            docs.append({"path": d["path"], "pages": d["pages"], "pages_read": len(read), "searched": bool(d["searched"]),
                         "status": d["status"]})
        ev = self.store.evidence(self.task_id)
        by_source: dict[str, int] = {}
        for e in ev:
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        task = self.store.get_task(self.task_id)
        return {"status": task["status"], "documents": docs, "evidence": {"total": len(ev), "by_source": by_source},
                "queue": self.store.url_counts(self.task_id), "searches": len(self.store.searches(self.task_id)),
                "sections": [s["section_id"] for s in self.store.sections(self.task_id)],
                "plan": task["plan"], "criteria": self.criteria(), "budget": self.budget.snapshot()}

    def task_progress(self) -> dict:
        return self.progress()

    def complete_task(self, note: str) -> dict:
        crit = self.criteria()
        missing = {k: v["detail"] for k, v in crit.items() if not v["ok"]}
        if missing:
            raise ToolError("criteria_not_met", "Completion criteria are not met; continue the work or call stop_partial",
                            missing=missing)
        self.store.update_task(self.task_id, completion={"completed": True, "note": note, "criteria": crit})
        self.store.set_status(self.task_id, "completed", note[:300])
        return {"status": "completed", "criteria": crit, "report": self.render()}

    def stop_partial(self, reason: str, unverified: list[str] | None = None) -> dict:
        self.store.update_task(self.task_id, completion={"completed": False, "partial": True, "reason": reason,
                                                          "unverified": unverified or [], "criteria": self.criteria()})
        self.store.set_status(self.task_id, "paused", f"partial: {reason}"[:300])
        return {"status": "paused", "reason": reason, "report": self.render()}

    def as_tools(self) -> list[BaseTool]:
        def mk(name, fn, schema, desc):
            return StructuredTool.from_function(func=lambda **kw: run_tool(name, fn, **kw), name=name,
                                                args_schema=schema, description=desc)
        return [
            mk("save_evidence", self.save_evidence, EvidenceArgs,
               "Save a verified finding: exact quote + source (document path with page, or fetched URL). The harness "
               "checks the quote verbatim; returns evidence_id to cite in the report. Idempotent."),
            mk("write_report_section", self.write_report_section, SectionArgs,
               "Create or update one section of the review report (upsert by section_id). Build the review gradually."),
            mk("update_plan", self.update_plan, PlanArgs, "Store your plan (list of steps with status)."),
            mk("task_progress", self.task_progress, NoArgs,
               "Progress of the task from the harness state: pages read per document, evidence, queue, sections, "
               "completion criteria and remaining budget."),
            mk("complete_task", self.complete_task, CompleteArgs,
               "Finish the task. The harness verifies the completion criteria; if they are not met you get the list."),
            mk("stop_partial", self.stop_partial, PartialArgs,
               "Stop with an honest partial result (no useful progress, sources unavailable): saves the reason and "
               "unverified items; the task is NOT marked completed."),
        ]
