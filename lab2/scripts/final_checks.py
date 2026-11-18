"""Checks of the scenarios recorded by run_final_lab2.ps1, computed from results/ and traces/ (no LLM).

    python scripts/final_checks.py [--readme]

Writes results/final_checks.json and results/final_checks.md: for every scenario the expected property, the
actual value from the recorded state/trace and OK / НЕТ. With --readme the generated tables replace the blocks
between the markers <!-- FINAL_CHECKS:BEGIN --> ... END and <!-- COMPARISON:BEGIN --> ... END in README.md,
so the README always shows the numbers of the committed run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
RES = Path(os.getenv("RESULTS_DIR") or LAB2 / "results")
TR = Path(os.getenv("TRACES_DIR") or LAB2 / "traces")


def jload(task: str, name: str, default=None):
    p = RES / task / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else p.read_text(encoding="utf-8")


def traces(task: str) -> list[list[dict]]:
    out = []
    for p in TR.glob(f"{task}_*.jsonl"):
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        if rows:
            out.append(rows)
    return sorted(out, key=lambda r: r[0]["ts"])


def events(task: str, kind: str) -> list[dict]:
    return [e for e in (jload(task, "events.json", []) or []) if e["kind"] == kind]


def tool_pairs(rows: list[dict]) -> list[tuple[str, dict, dict]]:
    """(tool name, args, parsed result) of every tool call in a trace."""
    calls, out = {}, []
    for r in rows:
        if r["event"] != "graph_step":
            continue
        for m in r.get("messages", []):
            if m.get("type") == "AIMessage":
                for tc in m.get("tool_calls", []):
                    calls[tc["id"]] = (tc["name"], tc["args"])
            elif m.get("type") == "ToolMessage":
                name, args = calls.get(m.get("tool_call_id"), (m.get("name"), {}))
                try:
                    res = json.loads(m.get("content") or "")
                except ValueError:
                    res = {"raw": (m.get("content") or "")[:300]}
                out.append((name, args, res))
    return out


def row(task, prop, actual, ok) -> dict:
    return {"task": task, "property": prop, "actual": actual, "ok": bool(ok)}


def crit_ok(st: dict, key: str):
    c = ((st.get("task") or {}).get("completion") or {}).get("criteria") or {}
    return (c.get(key) or {}).get("ok")


def check_local(task: str, out: list) -> None:
    st = jload(task, "status.json")
    if not st:
        out.append(row(task, "есть результаты", "нет results/" + task, False))
        return
    t = st["task"]
    tr = traces(task)
    out.append(row(task, "статус completed только по критериям", f"{t['status']} ({(t['status_reason'] or '')[:80]})",
                   t["status"] == "completed"))
    v = crit_ok(st, "verbatim_quotes")
    out.append(row(task, "[C5] решения АС-01..03 дословно (проверка complete_task)", "ok" if v else str(v), v))
    rejected = sum(1 for rows in tr for n, _a, r in tool_pairs(rows) if n == "complete_task" and not r.get("ok"))
    out.append(row(task, "отказы complete_task до выполнения критериев (информативно)", rejected, True))
    if task == "A1":
        crash = any(r["event"] == "harness:simulated_crash" for rows in tr for r in rows)
        resumed = any(rows[0].get("label") == "resume" for rows in tr)
        out.append(row(task, "сбой после checkpoint и resume в новом процессе", f"crash={crash}, resume={resumed}, "
                       f"запусков {t['runs']}", crash and resumed))
        per_run = max([sum(1 for r in rows if r["event"] == "harness:compaction") for rows in tr] or [0])
        out.append(row(task, ">= 2 компактификаций в одном запуске без реплик пользователя", per_run, per_run >= 2))
        calls = [c for c in st.get("model_calls", []) if c["kind"] == "agent"]
        over = sum(1 for c in calls if c["estimated_input"] + c["reserve"] > c["working_limit"])
        mx = max([c["estimated_input"] + c["reserve"] for c in calls] or [0])
        out.append(row(task, "нет вызова сверх рабочего лимита", f"макс. оценка+резерв {mx} / {calls[0]['working_limit'] if calls else '-'}; "
                       f"сверх лимита: {over}", calls and over == 0))
        aar = jload(task, "artifact_after_restart.json", {}) or {}
        ex = aar.get("example") or {}
        out.append(row(task, "факт за пределами выданного фрагмента найден в артефакте в новом процессе",
                       f"{ex.get('query')!r}: смещение {ex.get('char_offset_in_artifact')} > выдано {ex.get('chars_given_to_model')}, "
                       f"через {ex.get('found_via')}" if ex else "нет примера", aar.get("ok")))
    if task == "D1":
        first = tr[0] if tr else []
        end1 = next((r for r in first if r["event"] == "session_end"), {})
        out.append(row(task, "первый запуск: paused по бюджету + частичный отчёт", end1.get("status"),
                       end1.get("status") == "paused"))
        lc = events(task, "limits_changed")
        out.append(row(task, "лимит поднят пользователем (limits_changed)", len(lc), bool(lc)))
        if len(tr) > 1:
            ub = tr[-1][0].get("usage_before") or {}
            out.append(row(task, "после resume счётчики не обнулены", f"токены до resume: {ub.get('tokens', {}).get('spent')}",
                           (ub.get("tokens", {}).get("spent") or 0) > 0))


def check_e1(out: list) -> None:
    st = jload("E1", "status.json")
    if not st:
        out.append(row("E1", "есть результаты", "нет", False))
        return
    b = st["budget"]
    ev = jload("E1", "evidence.json", []) or []
    rep = jload("E1", "report.md", "") or ""
    out.append(row("E1", "статус cancelled (не completed)", st["task"]["status"], st["task"]["status"] == "cancelled"))
    out.append(row("E1", "отмена после сохранения 2-3 evidence", len(ev), len(ev) >= 2))
    out.append(row("E1", "резерв токенов освобождён после отмены", f"reserved={b['tokens']['reserved']}, "
                   f"interrupted_call_tokens={b['tokens']['of_which']['interrupted_call_tokens']}",
                   b["tokens"]["reserved"] == 0))
    out.append(row("E1", "частичный отчёт сохранён", "Частичный результат" in rep, "Частичный результат" in rep))


def check_dl1(out: list) -> None:
    st = jload("DL1", "status.json")
    if not st:
        out.append(row("DL1", "есть результаты", "нет", False))
        return
    t = st["task"]
    g = [e for e in events("DL1", "guardrail") if (e["data"] or {}).get("guard") == "deadline"]
    rep = jload("DL1", "report.md", "") or ""
    out.append(row("DL1", "paused по дедлайну задачи (не completed)", f"{t['status']} ({(t['status_reason'] or '')[:70]})",
                   t["status"] == "paused" and "deadline" in (t["status_reason"] or "")))
    out.append(row("DL1", "trace: harness:guardrail guard=deadline", len(g), bool(g)))
    out.append(row("DL1", "частичный результат и evidence сохранены",
                   f"evidence {len(jload('DL1', 'evidence.json', []) or [])}, отчёт: {'Частичный результат' in rep}",
                   "Частичный результат" in rep))


def check_c2(out: list) -> None:
    st = jload("C2", "status.json")
    if not st:
        out.append(row("C2", "есть результаты", "нет", False))
        return
    b, q = st["budget"], st["queue"]
    g = [e["data"] for e in events("C2", "guardrail") if (e["data"] or {}).get("guard") == "http_budget"]
    out.append(row("C2", "HTTP-лимит достигнут в агентном прогоне", f"{b['http_requests']['used']}/{b['http_requests']['limit']}",
                   b["http_requests"]["used"] >= b["http_requests"]["limit"]))
    out.append(row("C2", "trace: harness:guardrail guard=http_budget (http_budget_exhausted)", len(g), bool(g)))
    out.append(row("C2", "необработанные URL сохранены в очереди", f"pending {q['pending']}, found {q['found']}",
                   q["pending"] + q["found"] > 0))
    out.append(row("C2", "задача не помечена completed", st["task"]["status"], st["task"]["status"] != "completed"))


def check_i1(out: list) -> None:
    d = jload("I1", "idempotency.json")
    if not d:
        out.append(row("I1", "есть results/I1/idempotency.json", "нет", False))
        return
    c = d["checks"]
    out.append(row("I1", "сбои процесса и resume в новых процессах", f"сбоев {c['crashes']}, resume {c['resumes_in_new_process']}",
                   c["crashes"] >= 1 and c["resumes_in_new_process"] >= 1))
    out.append(row("I1", "URL, оставшиеся running, возвращены в очередь", c["urls_requeued_after_crash"],
                   c["urls_requeued_after_crash"] >= 1))
    out.append(row("I1", "повторный шаг: web_fetch = cache hit", c["cache_hit_in_re_executed_step"], c["cache_hit_in_re_executed_step"]))
    out.append(row("I1", "страница, прочитанная из хранилища после сбоя, повторно не скачана (HTTP-попытки URL не выросли)",
                   c["cached_pages_not_downloaded_again"], c["cached_pages_not_downloaded_again"]))
    out.append(row("I1", "повторный шаг: save_evidence = already_saved", c["already_saved_in_re_executed_step"],
                   c["already_saved_in_re_executed_step"]))
    out.append(row("I1", "нет дублей evidence и URL", f"{c['no_duplicate_evidence']}, {c['no_duplicate_queue_entries']}",
                   c["no_duplicate_evidence"] and c["no_duplicate_queue_entries"]))
    out.append(row("I1", "повтор тех же ключей в новом процессе: already_saved / cache hit без HTTP",
                   f"{c['replay_all_already_saved']}, {c['replay_all_cache_hits_no_http']}",
                   c["replay_all_already_saved"] and c["replay_all_cache_hits_no_http"]))
    out.append(row("I1", "итоговый статус (информативно)", c["final_status"], True))


def check_g2(out: list) -> None:
    tr = traces("G2")
    if not tr:
        out.append(row("G2", "есть трассы G2", "нет", False))
        return
    pairs = [p for rows in tr for p in tool_pairs(rows)]

    def err_of(pred):
        hits = [r for n, a, r in pairs if pred(n, a)]
        return [((r.get("error") or {}).get("type") if not r.get("ok") else "ok") for r in hits]
    absw = err_of(lambda n, a: n == "write" and re.match(r"^[A-Za-z]:[/\\]", str(a.get("path", ""))))
    absm = err_of(lambda n, a: n == "move" and re.match(r"^[A-Za-z]:[/\\]", str(a.get("destination", ""))))
    jdst = err_of(lambda n, a: n == "move" and "link_out" in str(a.get("destination", "")))
    jsrc = err_of(lambda n, a: n == "move" and "link_out" in str(a.get("source", "")))
    allowed = err_of(lambda n, a: n in ("write", "move") and "guard_check" in json.dumps(a, ensure_ascii=False))
    for prop, res in (("write по абсолютному пути наружу -> outside_workspace", absw),
                      ("move по абсолютному пути наружу -> outside_workspace", absm),
                      ("move в junction (data/link_out -> вне root) -> outside_workspace", jdst),
                      ("move из junction -> outside_workspace", jsrc)):
        out.append(row("G2", prop, res or "вызова не было", bool(res) and all(x == "outside_workspace" for x in res)))
    out.append(row("G2", "разрешённые write и move внутри root работают", allowed or "вызова не было",
                   bool(allowed) and all(x == "ok" for x in allowed)))
    step = [r for rows in tr for r in rows if r["event"] == "harness:tool_output_offloaded"
            and "step total would exceed" in (r.get("reason") or "")]
    out.append(row("G2", "лимит суммарного вывода шага: step total would exceed", len(step), bool(step)))


def check_b3(out: list) -> None:
    st = jload("B3", "status.json")
    if not st:
        return
    q = jload("B3", "queue.json", {}) or {}
    done = [u for u in q.get("urls", []) if u["status"] == "done"]
    hosts = {}
    for u in done:
        h = re.sub(r"^https?://([^/]+).*$", r"\1", u.get("final_url") or u["url"])
        hosts[h] = hosts.get(h, 0) + 1
    out.append(row("B3", "статус (completed только по критериям)", f"{st['task']['status']} ({(st['task']['status_reason'] or '')[:60]})",
                   st["task"]["status"] in ("completed", "paused")))
    for key, prop in (("min_queries_after_reading", "второй раунд запросов после чтения"),
                      ("min_page_links_read", "прочитана страница по ссылке с прочитанной страницы"),
                      ("max_domain_share", "не больше 60 % страниц с одного домена")):
        out.append(row("B3", prop, crit_ok(st, key), crit_ok(st, key)))
    out.append(row("B3", "домены прочитанных страниц (информативно)", hosts, True))


def comparison_md() -> str | None:
    ev = RES / "evaluation.json"
    if not ev.exists():
        return None
    e = json.loads(ev.read_text(encoding="utf-8")).get("comparison") or {}
    if not e:
        return None
    h, b = e["harness"], e["baseline"]
    lim = ((jload("A1", "status.json", {}) or {}).get("task") or {}).get("limits") or {}
    return "\n".join([
        "| Показатель (задача A, qwen3.8-27b) | Исходный агент (лаба 1) | Harness (A1) |", "|---|---|---|",
        f"| Лимит вызовов модели (разный!) | **40** (`baseline --max-model-calls 40`), использовано {b['model_calls']} | "
        f"**{lim.get('max_model_calls', '?')}** (`max_model_calls` задачи A), использовано {h['model_calls']} |",
        f"| Успешность | нет: лимит вызовов исчерпан, итогового ответа нет | {'да' if h['success'] else 'нет'} ({h['status']}) |",
        f"| Пик контекста (вход, токены) | {b['peak_input_tokens']} | {h['peak_input_tokens']} (рабочий лимит {h['working_limit']}) |",
        f"| Расход токенов | {b['tokens_total']} | {h['tokens_total']} (в т. ч. компактификация {h['compaction_tokens']}) |",
        f"| Вызовы модели | {b['model_calls']} | {h['model_calls']} + {h['compaction_calls']} компактификаций |",
        f"| Вызовы tools | {b['tool_calls']} | {h['tool_calls']} |",
        f"| Время, с | {b['duration_s']} | {h['active_seconds']} (активное время всех запусков) |",
        f"| Контрольные факты в итоговом ответе | {len(b['facts_in_answer'])} из 5 (итогового ответа нет) | "
        f"{len(h['facts_confirmed'])} из 5 ({', '.join(h['facts_confirmed']) or '-'}), с документом и страницей |"])


def replace_block(text: str, name: str, body: str) -> str:
    pat = re.compile(rf"(<!-- {name}:BEGIN -->\n)(.*?)(<!-- {name}:END -->)", re.S)
    return pat.sub(lambda m: m.group(1) + body.rstrip() + "\n" + m.group(3), text) if pat.search(text) else text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", action="store_true", help="update the generated blocks of README.md")
    args = ap.parse_args()
    out: list[dict] = []
    for t in ("A1", "D1"):
        check_local(t, out)
    check_e1(out)
    check_dl1(out)
    check_c2(out)
    check_i1(out)
    check_g2(out)
    check_b3(out)
    (RES / "final_checks.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["| задача | ожидаемое свойство | фактически | итог |", "|---|---|---|---|"]
    for r in out:
        a = r["actual"] if isinstance(r["actual"], str) else json.dumps(r["actual"], ensure_ascii=False)
        md.append(f"| {r['task']} | {r['property']} | {a.replace('|', '/')[:220]} | {'OK' if r['ok'] else 'НЕТ'} |")
    n_bad = sum(1 for r in out if not r["ok"])
    md.append("")
    md.append(f"Итого: {len(out) - n_bad} OK, {n_bad} НЕТ (scripts/final_checks.py по results/ и traces/).")
    table = "\n".join(md)
    (RES / "final_checks.md").write_text("# Проверки сценариев (scripts/final_checks.py)\n\n" + table + "\n", encoding="utf-8")
    print(table)
    if args.readme:
        readme = LAB2 / "README.md"
        text = readme.read_text(encoding="utf-8")
        text = replace_block(text, "FINAL_CHECKS", table)
        comp = comparison_md()
        if comp:
            text = replace_block(text, "COMPARISON", comp)
        ev_md = RES / "evaluation.md"
        if ev_md.exists():
            parts = re.split(r"(?m)^## ", ev_md.read_text(encoding="utf-8"))
            for block, head in (("COUNTERS", "Технические счётчики задач"), ("FACTS", "Контрольные факты и ограничения: A1")):
                part = next((p for p in parts if p.startswith(head)), None)
                if part:
                    title, _, body = part.partition("\n")
                    text = replace_block(text, block, f"_{title.strip()} (results/evaluation.md)_\n\n{body.strip()}\n")
        readme.write_text(text, encoding="utf-8", newline="\n")
        print("README.md: generated blocks updated")


if __name__ == "__main__":
    main()
