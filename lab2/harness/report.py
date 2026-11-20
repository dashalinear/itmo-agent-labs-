"""Deterministic report rendering: the agent's sections + appendices from the state DB.

Appendices are produced by code (not by the model): evidence with sources and
positions, search queries, found / selected / read / skipped sources with
reasons, errors, unverified items, budget and status. A partial result is
labelled as such.
"""
from __future__ import annotations

import json

from .store import TaskStore


def _cell(s, n: int = 160) -> str:
    s = "" if s is None else str(s)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_report(store: TaskStore, task_id: str, budget: dict) -> str:
    task = store.get_task(task_id)
    comp = task.get("completion") or {}
    status = task["status"]
    label = {"completed": "Итоговый отчёт", "paused": "Частичный результат (задача не завершена)",
             "cancelled": "Частичный результат (задача отменена)", "failed": "Частичный результат (ошибка)"}.get(
        status, "Промежуточный отчёт")
    out = [f"# {label}: {task_id}", "",
           f"- Статус: **{status}**" + (f" — {task['status_reason']}" if task.get("status_reason") else ""),
           f"- Вид задачи: {task['kind']}", "", "## Запрос", "", task["request"].strip(), ""]
    cons = task["spec"].get("constraints", [])
    if cons:
        out += ["## Ограничения пользователя", ""] + [f"- {c}" for c in cons] + [""]
    sections = store.sections(task_id)
    if sections:
        for s in sections:
            out += [f"## {s['title']}", "", s["body"].strip(), ""]
    else:
        out += ["## Обзор", "", "_Разделы обзора ещё не написаны._", ""]

    ev = store.evidence(task_id)
    out += [f"## Приложение A. Подтверждения (evidence): {len(ev)}", ""]
    if ev:
        out += ["| id | тип | вывод | источник | стр. | строки | цитата |", "|---|---|---|---|---|---|---|"]
        for e in ev:
            pages = (f"{e['page_start']}" + (f"–{e['page_end']}" if e["page_end"] and e["page_end"] != e["page_start"] else "")
                     if e["page_start"] else "—")
            out.append(f"| {e['evidence_id']} | {e['kind']} | {_cell(e['claim'], 120)} | {_cell(e['source'], 80)} | {pages} | "
                       f"{e['line_start']}–{e['line_end']} | {_cell(e['quote'], 200)} |")
    out.append("")

    searches = store.searches(task_id)
    if searches or task["kind"] in ("web", "mixed"):
        out += [f"## Приложение B. Поисковые запросы: {len(searches)}", ""]
        for s in searches:
            out.append(f"- `{s['query']}` — {s['results']} результатов, новых URL: {s['new_urls']} ({s['provider']})")
        q = store.url_counts(task_id)
        out += ["", "## Приложение C. Источники", "",
                f"Найдено: {q['found_total']}, отобрано: {q['selected_total']}, прочитано: {q['read_total']}, "
                f"ошибок: {q['error']}, отклонено: {q['skipped']}, не разобрано (found): {q['found']}, "
                f"в очереди (pending): {q['pending']}.", "",
                "| статус | URL | обнаружен | причина / ошибка |", "|---|---|---|---|"]
        for u in store.urls(task_id):
            if u["status"] == "found":
                continue
            out.append(f"| {u['status']} | {_cell(u['final_url'] or u['url'], 90)} | {_cell(u['discovered_by'], 60)} | "
                       f"{_cell(u['error'] or u['reason'], 120)} |")
        unread = [u for u in store.urls(task_id) if u["status"] in ("found", "pending")]
        if unread:
            out += ["", f"Непроверенные источники (не прочитаны): {len(unread)}"]
            out += [f"- {u['url']} ({u['status']})" for u in unread[:40]]
        out.append("")

    docs = store.documents(task_id)
    if docs:
        out += ["## Приложение D. Покрытие локальных документов", "", "| документ | страниц | прочитано стр. | поиск |",
                "|---|---|---|---|"]
        for d in docs:
            read = set()
            for r in store.ranges(task_id, d["path"], d["content_hash"]):
                if r["unit"] in ("page", "page_part"):        # read_pages or read_lines
                    read.update(range(r["start_no"], r["end_no"] + 1))
            out.append(f"| {d['path']} | {d['pages']} | {len(read)} | {'да' if d['searched'] else 'нет'} |")
        out.append("")

    if comp.get("unverified"):
        out += ["## Непроверенное", ""] + [f"- {x}" for x in comp["unverified"]] + [""]
    if comp.get("criteria"):
        out += ["## Критерии завершения", ""]
        out += [f"- {'OK' if v['ok'] else 'НЕТ'} {k}: "
                f"{v['detail'] if isinstance(v['detail'], str) else _cell(json.dumps(v['detail'], ensure_ascii=False), 600)}"
                for k, v in comp["criteria"].items()] + [""]
    out += ["## Расход", "", "```json", json.dumps(budget, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(out)
