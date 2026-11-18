"""DRY RUN ONLY: scripted stand-ins for the LLM, used by ``run_final_lab2.ps1 -DryRun``.

The harness loads this file when HARNESS_SCRIPTED_MODEL points to it (TaskRunner._models). It lets the campus
scenario script run end to end offline: the same CLI commands, specs, crash points, cancel/deadline/HTTP limits and
post-processing scripts, but the model follows a fixed plan (tests/policy.PolicyModel). The traces of a dry run go
to a temporary folder and are never committed; session_start of such a trace has "dry_run_scripted_model".

    build(task_id, limits) -> (model, summarizer)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2 / "tests"))
sys.path.insert(0, str(LAB2))

import harness  # noqa: E402,F401
from harness.tools_docs import load_doc  # noqa: E402
from policy import PolicyModel, SummaryModel, tc  # noqa: E402

H = "data/search/search_engineering_handbook.md"
L = "data/search/ir_course_lectures.md"
I = "data/search/search_incident_reports.md"
BASE = os.getenv("FIXTURE_BASE_URL", "http://127.0.0.1:8765").rstrip("/")


def _quote(rel: str, page: int, marker: str, n: int = 260) -> str:
    """An exact substring of the corpus line that contains `marker` on `page` (verbatim, as save_evidence needs)."""
    doc = load_doc(LAB2 / "corpus" / Path(rel).name, rel)
    a, b = doc.page_bounds(page)
    for i in range(a, b + 1):
        line = doc.lines[i - 1]
        if marker in line:
            s = line.lstrip("-*> #").strip()
            return s[:n]
    raise LookupError(f"{marker!r} not on page {page} of {rel}")


def _disputed_pages(k: int = 2) -> list[int]:
    doc = load_doc(LAB2 / "corpus" / Path(H).name, H)
    out = []
    for no, a, b in doc.pages:
        if any("Спорное" in doc.lines[i - 1] for i in range(a, b + 1)):
            out.append(no)
        if len(out) == k:
            break
    return out


def _ev(source, page, quote, claim, kind):
    return tc("save_evidence", {"claim": claim, "kind": kind, "source": source, "page": page, "quote": quote})


def local_plan(extra_reading: int = 0) -> list:
    dec = [(code, p, _quote(H, p, f"Решение архитектурного совета {code}")) for code, p in
           (("АС-01", 4), ("АС-02", 261), ("АС-03", 517))]
    dp = _disputed_pages()
    plan = [
        [tc("doc_info", {"path": H})],
        [tc("search_text", {"query": "Решение архитектурного совета АС-", "path": "data/search"})],
        [tc("search_text", {"query": "Ограничение", "path": "data/search", "max_hits": 40})],   # big result -> artifact
        [tc("read_pages", {"path": H, "start_page": p, "end_page": p}) for _c, p, _q in dec],
        [_ev(H, p, q, f"{c}: решение совета", "decision") for c, p, q in dec],
        [tc("search_text", {"query": "Решение кафедры", "path": "data/search"}),
         tc("search_text", {"query": "INC-2026-017", "path": "data/search"})],
        [_ev(L, 47, _quote(L, 47, "Решение кафедры"), "Решение кафедры о метрике", "decision"),
         _ev(I, 17, _quote(I, 17, "лимит кэша"), "INC-2026-017: лимит кэша снижен", "fact"),
         _ev(I, 17, _quote(I, 17, "Вывод разбора"), "кэш не всегда снижает p95", "limitation")],
        [_ev(H, p, _quote(H, p, "Спорное"), f"спорное утверждение, стр. {p}", "controversial") for p in dp],
    ]
    plan += [[tc("read_pages", {"path": H, "start_page": p, "end_page": p + 1})] for p in range(20, 20 + 6 * extra_reading, 6)]
    paraphrase = "\n".join(f"**{c}** (справочник, стр. {p}): «{q[40:120]}…»" for c, p, q in dec)
    verbatim = "\n\n".join(f"**{c}** (справочник, стр. {p}):\n\n> «{q}»" for c, p, q in dec)
    plan += [
        [tc("write_report_section", {"section_id": s, "title": t, "body": b}) for s, t, b in (
            ("approaches", "Подходы", "Гибридный поиск, шардирование (справочник, стр. 4, 261)."),
            ("limitations", "Ограничения", "Кэш не всегда снижает p95 (инциденты, стр. 17)."),
            ("disputed", "Спорные утверждения", f"Спорные утверждения справочника, стр. {dp[0]} и {dp[1]}: за и против."),
            ("summary", "Итоги", "Итог по трём документам."),
            ("decisions", "Решения", paraphrase))],
        [tc("complete_task", {"note": "решения приведены"})],          # rejected: [C5] paraphrase is caught by code
        [tc("write_report_section", {"section_id": "decisions", "title": "Решения архитектурного совета",
                                     "body": verbatim})],
        [tc("complete_task", {"note": "решения АС-01..03 дословно с номерами страниц"})],
        "Готово: обзор собран, критерии проверены harness.",
    ]
    return plan


def _fixture_quote(slug: str) -> str:
    html = (LAB2 / "fixtures" / "web" / "pages" / f"{slug}.html").read_text(encoding="utf-8")
    return re.findall(r"<p>(.*?)</p>", html)[0][:200]


def web_crash_plan() -> list:
    """I1: two parallel fetches per step, evidence right after reading (crash points hit fetches and evidence)."""
    slugs = ["checkpoint-1", "idempotency-1", "durable-1", "checkpoint-2", "idempotency-2", "durable-2"]
    u = [f"{BASE}/p/{s}" for s in slugs]
    plan = [
        [tc("web_search", {"query": "чекпоинты графа агента thread_id"})],
        [tc("web_search", {"query": "идемпотентность ключ повтор durable execution"})],
        [tc("select_sources", {"items": [{"url": x, "decision": "queue", "reason": "по теме"} for x in u]})],
        [tc("web_fetch", {"url": u[0]}), tc("web_fetch", {"url": u[1]})],
        [_ev_web(u[0], slugs[0]), _ev_web(u[1], slugs[1])],
        [tc("web_fetch", {"url": u[2]}), tc("web_fetch", {"url": u[3]})],
        [_ev_web(u[2], slugs[2]), _ev_web(u[3], slugs[3])],
        [tc("web_fetch", {"url": u[4]})],
        [_ev_web(u[4], slugs[4])],
        [tc("write_report_section", {"section_id": s, "title": s, "body": "по evidence"}) for s in
         ("approaches", "limitations", "unverified")],
        [tc("complete_task", {"note": "готово"})],
        "Готово.",
    ]
    return plan


def _ev_web(url: str, slug: str):
    return tc("save_evidence", {"claim": f"утверждение со страницы {slug}", "kind": "fact", "source": url,
                                "quote": _fixture_quote(slug)})


def http_limit_plan() -> list:
    slugs = ["checkpoint-1", "checkpoint-2", "backends-1", "backends-2", "memory-1", "memory-2", "durable-1",
             "durable-2", "schema-1", "security-1"]
    u = [f"{BASE}/p/{s}" for s in slugs]
    plan = [[tc("web_search", {"query": q})] for q in
            ("чекпоинты графа агента", "бэкенды хранения состояния sqlite postgres", "долговременная память агента")]
    plan.append([tc("select_sources", {"items": [{"url": x, "decision": "queue", "reason": "по теме"} for x in u]})])
    for i in range(8):                      # one page per step until the harness reports the HTTP limit
        plan.append([tc("web_fetch", {"url": u[i]})])
        if i < 6:
            plan.append([_ev_web(u[i], slugs[i])])
    plan += [[tc("write_report_section", {"section_id": s, "title": s, "body": "по evidence"}) for s in
              ("approaches", "limitations", "comparison", "unverified")],
             [tc("stop_partial", {"reason": "HTTP-бюджет исчерпан", "unverified": u[6:]})], "Остановлено по HTTP-лимиту."]
    return plan


def guard2_plan() -> list:
    out = os.getenv("HARNESS_OUTSIDE_DIR", str(LAB2 / "outside_area")).replace("\\", "/")
    return [
        [tc("write", {"path": f"{out}/abs_write.txt", "content": "test"})], "write: outside_workspace",
        [tc("move", {"source": I, "destination": f"{out}/moved_abs.md"})], "move: outside_workspace",
        [tc("move", {"source": I, "destination": "data/link_out/moved_junction.md"})], "move: outside_workspace",
        [tc("move", {"source": "data/link_out/secret.txt", "destination": "data/secret_copy.txt"})], "move: outside_workspace",
        [tc("write", {"path": "data/notes/guard_check.md", "content": "проверка"})],
        [tc("move", {"source": "data/notes/guard_check.md", "destination": "data/notes/guard_check_moved.md"})],
        "разрешённые write и move выполнены",
        [tc("read_pages", {"path": H, "start_page": a, "end_page": a + 2}) for a in (10, 20, 30, 40)],
        "два результата выгружены: step total would exceed",
    ]


def build(task_id: str | None, limits):
    t = (task_id or "").upper()
    if t in ("A1", "D1"):
        plan, delay = local_plan(), 0.0
    elif t == "E1":
        plan, delay = local_plan(extra_reading=20), 1.5
    elif t == "DL1":
        plan, delay = local_plan(extra_reading=80), 1.0
    elif t == "I1":
        plan, delay = web_crash_plan(), 0.0
    elif t == "C2":
        plan, delay = http_limit_plan(), 0.0
    elif t == "G2":
        plan, delay = guard2_plan(), 0.0
    else:
        plan, delay = ["нет сценария для dry run"], 0.0
    return PolicyModel(plan=plan, delay=delay), SummaryModel()
