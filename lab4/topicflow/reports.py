"""Corpus freeze and report publication (deterministic code, called from activities).

Run identity: run_key = Temporal workflow id of the TopicReport run (a Schedule gives every period its own id,
'<schedule workflow id>-<scheduled time>'). Everything derived from it is stable:

  workspace/.harness/lab4/runs/<slug>/manifest.json   frozen L0 document list (written once per run_key)
  workspace/.harness/lab4/runs/<slug>/topics.json     the agent's topic map (topics.py)
  harness task TOP-<slug>                             the agent task (budget, checkpoints, evidence)
  workspace/data/_reports/topics_<slug>.md            the report document (one per run_key)
  .harness/corpus/reports.json -> L0 'reports'        history of all reports + pointer to the latest

Publication is idempotent: the Markdown is rendered only from saved state (no 'now()'), written atomically to a
path derived from the run key, registered in the catalog by path (same document_id on every retry), its L1/L2 are
rebuilt with stable ids, and the history entry is keyed by run_key. A retried publish therefore rewrites the same
bytes into the same document instead of creating a second report.
"""
from __future__ import annotations

from . import _paths  # noqa: F401  (labs 1-3 on sys.path)

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from organizer.catalog import Catalog
from organizer.textutil import file_hash

from harness.workspace import HarnessWorkspace

from navsearch.levels import Levels
from navsearch.tools_corpus import CorpusTools

from .topics import _atomic_json, coverage, load_state

REPORTS_FOLDER = "_reports"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(run_key: str) -> str:
    """Readable, filesystem-safe and stable: 'lab4-topics-run-2026-11-17T10:30:00Z' -> '20261117T103000Z'."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", run_key)
    tail = re.sub(r"[^A-Za-z0-9]+", "-", run_key).strip("-")
    if m:
        y, mo, d, h, mi, s = m.groups()
        prefix = "manual-" if "manual" in run_key else ""
        return f"{prefix}{y}{mo}{d}T{h}{mi}{s}Z"
    return tail[-40:]


def run_dir(ws: HarnessWorkspace, run_key: str) -> Path:
    return ws.service_path("lab4") / "runs" / slug(run_key)


def report_rel(run_key: str) -> str:
    return f"data/{REPORTS_FOLDER}/topics_{slug(run_key)}.md"


def topic_task_id(run_key: str) -> str:
    return f"TOP-{slug(run_key)}"


# ---------------------------------------------------------------------------------------------- freeze
def freeze_corpus(ws: HarnessWorkspace, run_key: str, workflow_id: str, run_id: str) -> dict:
    """Fix the document list of this run from L0 (once). Service reports and empty files are excluded."""
    d = run_dir(ws, run_key)
    p = d / "manifest.json"
    if p.exists():
        m = json.loads(p.read_text(encoding="utf-8"))
        return {"run_key": run_key, "documents": len(m["documents"]), "skipped": len(m["skipped"]),
                "excluded_service": len(m["excluded_service"]), "frozen_at": m["frozen_at"], "already_frozen": True}
    cat = Catalog(ws)
    l0 = Levels(ws, cat).rebuild_l0()
    docs, skipped, service = [], [], []
    for x in l0["documents"]:
        if x.get("service") or x["folder"] == REPORTS_FOLDER:
            service.append(x["path"])
        elif x["is_empty"]:
            skipped.append({"document_id": x["document_id"], "path": x["path"], "reason": "пустой файл: нет текста для анализа"})
        elif not x["l1"]:
            skipped.append({"document_id": x["document_id"], "path": x["path"], "reason": "нет L1: документ ещё не проиндексирован"})
        else:
            docs.append({k: x[k] for k in ("document_id", "path", "folder", "title", "description", "content_hash", "l2_fragments")})
    m = {"run_key": run_key, "workflow_id": workflow_id, "run_id": run_id, "frozen_at": now_iso(),
         "l0_generated_at": l0["generated_at"], "l0_documents": len(l0["documents"]),
         "documents": docs, "skipped": skipped, "excluded_service": service}
    _atomic_json(p, m)
    return {"run_key": run_key, "documents": len(docs), "skipped": len(skipped), "excluded_service": len(service),
            "frozen_at": m["frozen_at"], "already_frozen": False}


# ---------------------------------------------------------------------------------------------- render
def _registry_path(ws: HarnessWorkspace) -> Path:
    return ws.service_path("corpus") / "reports.json"


def load_registry(ws: HarnessWorkspace) -> dict:
    p = _registry_path(ws)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"history": [], "latest": None}


def _previous(ws: HarnessWorkspace, run_key: str) -> dict | None:
    hist = [h for h in load_registry(ws)["history"] if h["run_key"] != run_key and h["frozen_at"] < _frozen_at(ws, run_key)]
    return max(hist, key=lambda h: h["frozen_at"]) if hist else None


def _frozen_at(ws: HarnessWorkspace, run_key: str) -> str:
    return json.loads((run_dir(ws, run_key) / "manifest.json").read_text(encoding="utf-8"))["frozen_at"]


def render(ws: HarnessWorkspace, run_key: str, meta: dict, task: dict | None, evidence: list[dict]) -> tuple[str, dict]:
    d = run_dir(ws, run_key)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    st = load_state(d)
    cov = coverage(m, st, evidence)
    ev = {e["evidence_id"]: e for e in evidence}
    by_id = {x["document_id"]: x for x in m["documents"]}
    status = (task or {}).get("status") or "not started"
    complete = status == "completed" and cov["complete"]
    reasons = []
    if not complete:
        if task is None:
            reasons.append("агент анализа не запускался")
        elif status != "completed":
            reasons.append(f"задача агента {task['task_id']} не завершена: {status} — {task.get('status_reason') or ''}".strip())
        if cov["not_examined"]:
            reasons.append(f"не просмотрено документов: {len(cov['not_examined'])}")
        if cov["unassigned"]:
            reasons.append(f"документов вне тем: {len(cov['unassigned'])}")
        if cov["topics_without_evidence"]:
            reasons.append(f"тем без подтверждения L2: {len(cov['topics_without_evidence'])}")
        if meta.get("agent_error"):
            reasons.append(f"ошибка Activity: {meta['agent_error'][:300]}")
    prev = _previous(ws, run_key)
    prev_docs = set()
    if prev:
        try:
            prev_docs = {x["document_id"] for x in json.loads((ws.service_path("lab4") / "runs" / slug(prev["run_key"])
                                                                / "manifest.json").read_text(encoding="utf-8"))["documents"]}
        except OSError:
            prev_docs = set()
    new_docs = [x for x in m["documents"] if prev and x["document_id"] not in prev_docs]
    usage = meta.get("usage") or {}

    L = [f"# Обзор тем корпуса — {m['frozen_at'][:10]}", ""]
    L += ["**Статус: ПОЛНЫЙ**" if complete else "**Статус: ЧАСТИЧНЫЙ** — результат неполный, причины ниже", ""]
    L += ["## Запуск", "",
          "| поле | значение |", "|---|---|",
          f"| run key (Workflow ID) | `{run_key}` |",
          f"| Temporal run ID | `{m['run_id']}` |",
          f"| источник запуска | {meta.get('source', 'schedule')} |",
          f"| список корпуса зафиксирован | {m['frozen_at']} (L0 от {m['l0_generated_at']}) |",
          f"| задача harness | `{(task or {}).get('task_id', topic_task_id(run_key))}` — {status} |",
          f"| вызовы модели / tools / токены | {usage.get('model_calls', '—')} / {usage.get('tool_calls', '—')} / {usage.get('tokens', '—')} |",
          f"| предыдущий отчёт | {('`' + prev['path'] + '`') if prev else 'нет — это первый отчёт'} |", ""]
    if reasons:
        L += ["## Почему отчёт частичный", ""] + [f"- {r}" for r in reasons] + [""]
    L += ["## Состав и покрытие корпуса", "",
          f"- документов в L0 на момент фиксации: {m['l0_documents']}",
          f"- в анализе (зафиксированный список): **{cov['documents_total']}**",
          f"- просмотрено L1: **{cov['examined']} из {cov['documents_total']}**; не удалось прочитать: {cov['unreadable']}",
          f"- тем: **{cov['topics']}**; подтверждений из L2: {cov['evidence']}",
          f"- исключены служебные отчёты: {len(m['excluded_service'])}; пропущены: {len(m['skipped'])}", ""]
    if prev:
        L += [f"- новые документы с прошлого отчёта: {len(new_docs)}"
              + (": " + ", ".join(f"`{x['path']}`" for x in new_docs) if new_docs else ""), ""]
    L += ["## Темы", ""]
    if not st["topics"]:
        L += ["_Темы не выделены._", ""]
    for k, t in sorted(st["topics"].items(), key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 0):
        new_mark = " (новая тема: все документы появились после прошлого отчёта)" if prev and t["document_ids"] and all(
            i not in prev_docs for i in t["document_ids"]) else ""
        L += [f"### {k}. {t['title']}{new_mark}", "", t["description"], "", "Документы:", ""]
        for i in t["document_ids"]:
            x = by_id.get(i, {"path": "?", "title": "?"})
            L.append(f"- `{i}` — `{x['path']}` — {x['title']}")
        L += ["", "Подтверждения из L2:", ""]
        evs = [ev[e] for e in t["evidence_ids"] if e in ev]
        if not evs:
            L.append("- _нет подтверждения из исходного текста_")
        for e in evs:
            L.append(f"- «{e['quote']}» — `{e['source']}`, строки {e['line_start']}–{e['line_end']}, "
                     f"fragment `{e['fragment_id']}` ({e['evidence_id']})")
        L.append("")
    if st["merges"]:
        L += ["## Объединённые темы", ""] + [f"- «{x['from_title']}» → {x['into']}: {x['reason']}" for x in st["merges"]] + [""]
    if st.get("summary"):
        L += ["## Итог агента", "", st["summary"], ""]
    left = cov["not_examined"] + [{"document_id": k, "path": by_id[k]["path"], "reason": v} for k, v in st["unreadable"].items()
                                  if k in by_id]
    L += ["## Непроверенные документы и ошибки", ""]
    if not left and not m["skipped"]:
        L.append("- нет")
    for x in cov["not_examined"]:
        L.append(f"- `{x['path']}` — не просмотрен: {'; '.join(reasons) or 'анализ не завершён'}")
    for k, v in st["unreadable"].items():
        if k in by_id:
            L.append(f"- `{by_id[k]['path']}` — не удалось прочитать: {v}")
    for x in m["skipped"]:
        L.append(f"- `{x['path']}` — пропущен: {x['reason']}")
    for x in cov["unassigned"]:
        L.append(f"- `{x['path']}` — просмотрен, но не отнесён ни к одной теме")
    L += ["", "## Зафиксированный список документов", "", "| document_id | путь | просмотрен |", "|---|---|---|"]
    for x in m["documents"]:
        mark = "да" if x["document_id"] in st["examined"] else ("не прочитан" if x["document_id"] in st["unreadable"] else "нет")
        L.append(f"| `{x['document_id']}` | `{x['path']}` | {mark} |")
    L += ["", "Служебные отчёты (`data/_reports`) в анализ не входят. Документы, добавленные после фиксации списка, "
          "войдут в следующий отчёт.", ""]
    info = {"complete": complete, "status_label": "complete" if complete else "partial", "topics": cov["topics"],
            "documents_frozen": cov["documents_total"], "documents_examined": cov["examined"], "evidence": cov["evidence"],
            "new_documents": [x["path"] for x in new_docs], "reasons": reasons, "frozen_at": m["frozen_at"],
            "topic_titles": [t["title"] for t in st["topics"].values()]}
    return "\n".join(L), info


# ---------------------------------------------------------------------------------------------- publish
def publish(ws: HarnessWorkspace, corpus: CorpusTools, run_key: str, meta: dict, task: dict | None,
            evidence: list[dict], fault=None) -> dict:
    text, info = render(ws, run_key, meta, task, evidence)
    rel = report_rel(run_key)
    p = ws.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    same = existed and p.read_text(encoding="utf-8") == text
    if not same:
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, p)
    if fault:
        fault("after_write")                    # simulated crash between writing the file and registering it at L0
    cat = Catalog(ws)
    entry = cat.register_existing(rel)          # by path: the same document_id on every retry
    h = file_hash(p)
    if entry.get("content_hash") != h:          # re-rendered after a state change: same document, new version
        data = cat.load()
        e = Catalog._find(data, document_id=entry["document_id"])
        e.update(content_hash=h, size_bytes=p.stat().st_size, line_count=len(text.splitlines()), is_empty=False)
        cat.save(data)
    out = corpus.doc_outline(rel)
    title = text.splitlines()[0].lstrip("# ").strip()
    secs = out["suggested_sections"] or [{"title": "Отчёт", "start_line": 1, "end_line": out["lines"]}]
    corpus.write_l1(entry["document_id"], title,
                    f"Служебный тематический отчёт по корпусу ({info['status_label']}), run {run_key}",
                    f"Темы: {'; '.join(info['topic_titles']) or 'нет'}. Просмотрено {info['documents_examined']} из "
                    f"{info['documents_frozen']} документов.", secs)
    corpus.index_document(entry["document_id"])
    reg = load_registry(ws)
    item = {"run_key": run_key, "workflow_id": meta.get("workflow_id"), "run_id": meta.get("run_id"),
            "document_id": entry["document_id"], "path": rel, "status": info["status_label"], "topics": info["topics"],
            "documents_frozen": info["documents_frozen"], "documents_examined": info["documents_examined"],
            "frozen_at": info["frozen_at"], "task_id": (task or {}).get("task_id")}
    hist = [h for h in reg["history"] if h["run_key"] != run_key] + [item]
    hist.sort(key=lambda h: h["frozen_at"])
    reg = {"history": hist, "latest": hist[-1]}
    _atomic_json(_registry_path(ws), reg)
    Levels(ws, cat).rebuild_l0()
    return {**item, "rewritten": existed and not same, "already_published": same, "reasons": info["reasons"],
            "new_documents": info["new_documents"], "report_count": len(hist)}
