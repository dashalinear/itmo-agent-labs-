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
        crash_ev = next((r for rows in tr for r in rows if r["event"] == "harness:simulated_crash"), {})
        ev_all = jload(task, "evidence.json", []) or []
        dups = len(ev_all) - len({(e["source"], e["quote"]) for e in ev_all})
        resumed_rows = next((rows for rows in tr if rows[0].get("label") == "resume"), [])
        again = [r.get("data", {}).get("status") for n, _a, r in tool_pairs(resumed_rows) if n == "save_evidence"]
        out.append(row(task, "сбой после сохранённых evidence; после resume они восстановлены, дублей нет",
                       f"evidence на момент сбоя {crash_ev.get('evidence_at_crash')}, повторённые save_evidence после resume: "
                       f"{again[:6]}; в конце {len(ev_all)}, дублей {dups}",
                       (crash_ev.get("evidence_at_crash") or 0) >= 1 and dups == 0))
        pairs = [p for rows in tr for p in tool_pairs(rows)]

        def _hits(res):
            d = res.get("data") if isinstance(res.get("data"), dict) else res
            return len(d.get("hits") or []) if isinstance(d, dict) else 0
        ups = [r for n, _a, r in pairs if n == "upsert_excerpts"]
        srch = [r for n, _a, r in pairs if n == "search_excerpts"]
        es_ok = crit_ok(st, "indexed_in_es")
        out.append(row(task, "MCP Elasticsearch лабы 1: upsert_excerpts записал выдержки, search_excerpts их находит",
                       f"upsert ok: {sum(1 for r in ups if r.get('ok'))}/{len(ups)}, поиск с хитами: "
                       f"{sum(1 for r in srch if r.get('ok') and _hits(r))}/{len(srch)}, критерий indexed_in_es: {es_ok}",
                       any(r.get("ok") for r in ups) and any(r.get("ok") and _hits(r) for r in srch) and bool(es_ok)))
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


def check_p1(out: list) -> None:
    st = jload("P1", "status.json")
    if not st:
        return
    tr = traces("P1")
    first = tr[0] if tr else []
    end1 = next((r for r in first if r["event"] == "session_end"), {})
    stop1 = next((r for r in first if r["event"] == "harness:task_stopped"), {})
    out.append(row("P1", "CLI pause из другого процесса: задача paused на границе шага",
                   f"{end1.get('status')} ({(stop1.get('reason') or '')[:60]})",
                   end1.get("status") == "paused" and "pause requested" in (stop1.get("reason") or "")))
    resumed = [rows for rows in tr if rows[0].get("label") == "resume"]
    ub = (resumed[0][0].get("usage_before") or {}) if resumed else {}
    out.append(row("P1", "resume в новом процессе продолжил ту же задачу, расход не обнулён",
                   f"запусков {st['task']['runs']}, вызовов модели до resume {(ub.get('model_calls') or {}).get('used')}, "
                   f"всего {st['budget']['model_calls']['used']}; статус {st['task']['status']}",
                   bool(resumed) and (ub.get("model_calls") or {}).get("used", 0) > 0
                   and st["budget"]["model_calls"]["used"] > (ub.get("model_calls") or {}).get("used", 0)))


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
    out.append(row("DL1", "частичный отчёт сохранён (evidence: сколько успело сохраниться до дедлайна)",
                   f"evidence {len(jload('DL1', 'evidence.json', []) or [])}, отчёт: {'Частичный результат' in rep}",
                   "Частичный результат" in rep))
    blocked = [e["data"] for e in events("DL1", "guardrail") if (e["data"] or {}).get("guard") == "deadline"]
    out.append(row("DL1", "после дедлайна новые действия не выполняются (инструмент на границе)",
                   ", ".join(sorted({b.get("tool") or "model" for b in blocked})) or "-", bool(blocked)))


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
    jw = err_of(lambda n, a: n == "write" and "link_out" in str(a.get("path", "")))
    jdst = err_of(lambda n, a: n == "move" and "link_out" in str(a.get("destination", "")))
    jsrc = err_of(lambda n, a: n == "move" and "link_out" in str(a.get("source", "")))
    allowed = err_of(lambda n, a: n in ("write", "move") and "guard_check" in json.dumps(a, ensure_ascii=False))
    for prop, res in (("write по абсолютному пути наружу -> outside_workspace", absw),
                      ("move по абсолютному пути наружу -> outside_workspace", absm),
                      ("write в junction (data/link_out -> вне root) -> outside_workspace", jw),
                      ("move в junction (data/link_out -> вне root) -> outside_workspace", jdst),
                      ("move из junction -> outside_workspace", jsrc)):
        out.append(row("G2", prop, res or "вызова не было", bool(res) and all(x == "outside_workspace" for x in res)))
    out.append(row("G2", "разрешённые write и move внутри root работают", allowed or "вызова не было",
                   bool(allowed) and all(x == "ok" for x in allowed)))
    step = [r for rows in tr for r in rows if r["event"] == "harness:tool_output_offloaded"
            and "step total would exceed" in (r.get("reason") or "")]
    out.append(row("G2", "лимит суммарного вывода шага: step total would exceed", len(step), bool(step)))
    # the step total itself: all results of that AIMessage together (stubs included) stay within the limit
    worst = None
    for rows in tr:
        calls, sizes = {}, {}
        for r in rows:
            if r["event"] != "graph_step":
                continue
            for m in r.get("messages", []):
                if m.get("type") == "AIMessage":
                    for tc in m.get("tool_calls", []):
                        calls[tc["id"]] = m.get("id")
                elif m.get("type") == "ToolMessage" and m.get("tool_call_id") in calls:
                    k = calls[m["tool_call_id"]]
                    sizes[k] = sizes.get(k, 0) + len(m.get("content") or "")
        limit = next((r.get("step_limit") for r in rows if r["event"] == "harness:tool_output_offloaded"), None) or 12000
        for k, v in sizes.items():
            if worst is None or v > worst[0]:
                worst = (v, limit)
    if worst:
        out.append(row("G2", "сумма результатов одного шага не больше max_step_result_chars", f"максимум {worst[0]} из {worst[1]}",
                       worst[0] <= worst[1]))


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
    out.append(row("B3", "не помечен completed без выполненных критериев", f"{st['task']['status']} ({(st['task']['status_reason'] or '')[:60]})",
                   st["task"]["status"] in ("completed", "paused")))
    # computed from the recorded state (the same rules as complete_task), also when complete_task was never called
    crit = st.get("criteria") or {}
    after = [e["data"]["query"] for e in events("B3", "search_round") if (e["data"] or {}).get("pages_read_before")]
    followed = {(e["data"] or {}).get("url_key") for e in events("B3", "link_followed")}
    via_links = [u["url"] for u in done if (u.get("discovered_by") or "").startswith("page:") or u["url_key"] in followed]
    top = max(hosts.items(), key=lambda kv: kv[1]) if hosts else ("-", 0)
    share = top[1] / len(done) if done else 0.0
    out.append(row("B3", "второй раунд: новые запросы после чтения страниц", f"{len(after)} запросов после чтения",
                   len(after) >= crit.get("min_queries_after_reading", 1)))
    out.append(row("B3", "доп. критерий (сверх задания): прочитана страница по ссылке с прочитанной страницы (found_on)",
                   f"{len(via_links)}; link_followed событий: {len(followed)}",
                   len(via_links) >= crit.get("min_page_links_read", 1)))
    out.append(row("B3", f"доп. критерий (сверх задания): не больше {int(crit.get('max_domain_share', 0.6) * 100)} % прочитанных страниц с одного домена",
                   f"{top[0]}: {top[1]}/{len(done)} = {share:.0%}", bool(done) and share <= crit.get("max_domain_share", 0.6)))
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


def _all_traces() -> list[list[dict]]:
    out = []
    for f in sorted(TR.glob("*.jsonl")):
        try:
            first = json.loads(f.open(encoding="utf-8").readline())
        except ValueError:
            continue
        out.append((f.name, first))
    return out


def fixed_prompt_md() -> str | None:
    starts = [(n, r) for n, r in _all_traces() if r.get("event") == "session_start" and r.get("fixed_prompt_tokens")]
    default = [r["fixed_prompt_tokens"] for _n, r in starts if "cpt=2.60" in (r.get("token_count_method") or "")]
    if not default:
        return None
    a1 = [(n, r) for n, r in starts if n.startswith("A1_")]
    a1_run = next((n for n, _r in a1 if "_run" in n), None)
    first_call = None
    if a1_run:
        for line in (TR / a1_run).open(encoding="utf-8"):
            r = json.loads(line)
            if r["event"] == "harness:model_call_done":
                first_call = r
                break
    a1_resume = next((r for n, r in a1 if "_resume" in n), None)
    txt = (f"по стартовой эвристике (2,6 символа на токен плюс 10 %) — **{min(default)}–{max(default)} токенов** во всех "
           f"трассах в `traces/`, начатых с этой эвристикой (поле `fixed_prompt_tokens` в `session_start`)")
    if first_call:
        txt += (f". Эвристика с запасом: первый вызов A1 оценён в {first_call['estimated_input']} токенов, а сервер посчитал "
                f"{first_call['actual_input']} (`usage.input_tokens`); после этого `chars_per_token` калибруется до "
                f"{first_call['calibrated_cpt']} (`calibrated_cpt` в `model_call_done`)")
    if a1_resume and "cpt=2.60" not in (a1_resume.get("token_count_method") or ""):
        txt += (f". Калибровка хранится в state.db (`meta`, ключ `token_calibration:<task>`), поэтому resume A1 начал с "
                f"`{a1_resume.get('token_count_method')}`, а не с 2,6")
    return txt + "."


def artifacts_md() -> str:
    lines = []
    for task in ("A1", "G2", "G1"):
        d = jload(task, "artifact_after_restart.json")
        if not d or not d.get("example"):
            continue
        e = d["example"]
        manifest = json.loads((LAB2 / "corpus" / "manifest.json").read_text(encoding="utf-8"))["control_facts"]
        seen = (e.get("read_artifact_text") or "") + " " + (e.get("search_artifact_hit") or "")
        fact = e.get("control_fact") or next((f["id"] for f in manifest if all(k in seen for k in f["keywords"])), None)
        keyword = any(e["query"] == f["keywords"][0] for f in manifest)
        what = (f"контрольный факт **{fact}**" if fact else
                "строка с ключевым словом контрольного факта, но не сам факт" if keyword
                else "последний хит выгруженного результата (не контрольный факт)")
        lines.append(f"- `results/{task}/artifact_after_restart.json` — `{e['tool']}` ({e['total_chars']} символов, модели "
                     f"выдано {e['chars_given_to_model']}): {what}; запрос «{e['query'][:70]}», смещение "
                     f"{e['char_offset_in_artifact']} (дальше выданной части), прочитано через `{e['found_via']}`"
                     + (f", подтверждено текстом документа (стр. {e['verified_in_document']['page']})"
                        if (e.get("verified_in_document") or {}).get("snippet_on_page") else "") + ";")
    return "\n".join(lines)


def b3_md() -> str | None:
    import sys
    sys.path.insert(0, str(LAB2))
    import harness  # noqa: F401
    from harness.web import canonical_url
    st, q = jload("B3", "status.json"), jload("B3", "queue.json", {}) or {}
    if not st:
        return None
    urls = q.get("urls", [])
    done = [u for u in urls if u["status"] == "done"]
    origin = {}
    for u in done:
        o = (u.get("discovered_by") or "").split(":")[0] or "?"
        origin[o] = origin.get(o, 0) + 1
    papers = {canonical_url(u.get("final_url") or u["url"]) for u in done}
    hosts = {}
    for u in done:
        h = re.sub(r"^https?://([^/]+).*$", r"\1", u.get("final_url") or u["url"])
        hosts[h] = hosts.get(h, 0) + 1
    rounds = [e["data"] for e in events("B3", "search_round")]
    after = sum(1 for r in rounds if r.get("pages_read_before"))
    tr = traces("B3")
    pairs = [p for rows in tr for p in tool_pairs(rows)]
    fetches = {}
    for n, a, r in pairs:
        if n == "web_fetch":
            fetches[a.get("url")] = fetches.get(a.get("url"), 0) + 1
    repeats = {u: c for u, c in fetches.items() if c > 1}
    cache = sum(1 for n, _a, r in pairs if n == "web_fetch" and (r.get("data") or {}).get("from_cache"))
    failed_again = sum(1 for n, _a, r in pairs if n == "web_fetch" and (r.get("error") or {}).get("type") == "already_failed")
    rep_guard = sum(1 for n, _a, r in pairs if (r.get("error") or {}).get("type") in ("repeated_call", "no_progress"))
    errs = [u for u in urls if u["status"] == "error"]
    b = st["budget"]
    out = [f"- Трассы: {', '.join('`traces/' + rows[0]['session_id'] + '.jsonl`' for rows in tr)}; статус **{st['task']['status']}** "
           f"({st['task']['status_reason']}); запусков {st['task']['runs']}.",
           f"- Поиск: {len(rounds)} запросов, из них {len(rounds) - after} до чтения и **{after} после чтения** (`search_round`).",
           f"- Прочитано {len(done)} URL: " + ", ".join(f"{k} — {v}" for k, v in sorted(origin.items()))
           + f" (`discovered_by`; `agent` — URL, добавленный агентом напрямую); различных источников после канонизации "
             f"arXiv abs/html/vN — {len(papers)}. Домены: " + ", ".join(f"{h} — {c}" for h, c in hosts.items()) + ".",
           f"- Ошибки: " + ("; ".join(f"`{u['url']}` — `{(u['error'] or '').split(':')[0]}`, HTTP-попыток {u['attempts']}"
                                     for u in errs) if errs else "нет") + ".",
           f"- Повторные `web_fetch` одного URL: " + (", ".join(f"{u} ×{c}" for u, c in repeats.items()) if repeats else "нет")
           + f"; из них cache hit {cache}, сразу возвращённая сохранённая ошибка (`already_failed`) {failed_again}; "
             f"срабатываний `repeated_call`/`no_progress` {rep_guard}.",
           f"- Evidence {len(jload('B3', 'evidence.json', []) or [])}; вызовов модели {b['model_calls']['used']} из "
           f"{b['model_calls']['limit']}; токенов {b['tokens']['spent']} из {b['tokens']['limit']}; HTTP {b['http_requests']['used']}."]
    return "\n".join(out)


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
    check_p1(out)
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
    n_extra = sum(1 for r in out if not r["ok"] and r["property"].startswith("доп. критерий"))
    md.append("")
    md.append(f"Итого: {len(out) - n_bad} OK, {n_bad} НЕТ (scripts/final_checks.py по results/ и traces/)"
              + (f"; из них {n_extra} — дополнительные критерии сверх задания, по обязательным требованиям НЕТ: "
                 f"{n_bad - n_extra}." if n_extra else "."))
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
        for block, fn in (("FIXED_PROMPT", fixed_prompt_md), ("ARTIFACTS", artifacts_md), ("B3_SUMMARY", b3_md)):
            body = fn()
            if body:
                text = replace_block(text, block, body)
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
