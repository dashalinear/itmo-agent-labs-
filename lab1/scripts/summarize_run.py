"""Build results/RUN_SUMMARY.md from the traces of one demo run (written by run_all_demos.ps1).

    python scripts/summarize_run.py --manifest <run_manifest.json>

The manifest lists the sessions of the chain in order:
    {"started_at": "...", "model": "...", "sessions": [{"label": "main", "dialog": "demo/dialog_ru.txt",
     "trace": "traces/session_....jsonl"}, ...]}
The summary is generated from the traces themselves (per turn: user text, tool calls, tool errors,
execution-limit stops, history check), plus verify reports, the catalog and the index export, so the
README never has to hard-code model-dependent numbers. Scenario checks that fail are printed as WARN.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import _common  # noqa: F401
from _common import ROOT

RESULTS = ROOT / "results"


def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tool_error_type(content: str) -> str | None:
    try:
        env = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(env, dict) and env.get("ok") is False:
        return (env.get("error") or {}).get("type") or "error"
    return None


def tool_ok(content: str) -> bool:
    try:
        env = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(env, dict) and env.get("ok") is True


def summarize_session(events: list[dict]) -> dict:
    start = next((e for e in events if e["event"] == "session_start"), {})
    turns, current, actions, commands, end = [], None, [], [], {}
    for e in events:
        kind = e["event"]
        if kind == "user_turn":
            current = {"n": len(turns) + 1, "text": e["text"], "run_id": e["run_id"], "calls": Counter(),
                       "errors": Counter(), "ok_results": Counter(), "model_calls": 0}
            turns.append(current)
        elif kind == "graph_step" and current and e["run_id"] == current["run_id"]:
            if e["node"] == "model":
                current["model_calls"] += 1
            for m in e["messages"]:
                if m["type"] == "AIMessage" and str(m.get("content", "")).startswith("Model call limits exceeded"):
                    current["limit_message"] = True
                for tc in m.get("tool_calls") or []:
                    current["calls"][tc["name"]] += 1
                if m["type"] == "ToolMessage":
                    t = tool_error_type(m["content"])
                    if t:
                        current["errors"][f"{m.get('name')}:{t}"] += 1
                    elif tool_ok(m.get("content", "")):
                        current["ok_results"][m.get("name")] += 1
        elif kind == "turn_end" and current:
            current.update(stopped_by=e.get("stopped_by") or ("model_call_limit" if current.get("limit_message") else None),
                           error=e.get("error"),
                           history_ok=not e.get("history_problems"), duration_s=e.get("duration_s", 0))
        elif kind == "user_action":
            actions.append({"after_turn": len(turns), **{k: e[k] for k in e if k not in ("ts", "session_id", "event")}})
        elif kind == "cli_command":
            commands.append({"after_turn": len(turns), "command": e.get("command"), "ok": e.get("ok", True),
                             "result": e.get("result")})
        elif kind == "session_end":
            end = {k: e.get(k) for k in ("ended_by", "user_turns")}
    return {"start": start, "turns": turns, "actions": actions, "commands": commands, "end": end}


def fmt_counter(c: Counter) -> str:
    return ", ".join(f"{k}×{v}" if v > 1 else k for k, v in sorted(c.items())) or "—"


def cell(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit] + "…"


def scenario_checks(sessions: list[dict]) -> list[tuple[bool, str]]:
    by = {s["label"]: s for s in sessions}
    checks = []

    def all_errors(label):
        return Counter(k.split(":", 1)[1] for t in by.get(label, {}).get("summary", {}).get("turns", [])
                       for k in t["errors"].elements())

    def all_calls(label):
        c = Counter()
        for t in by.get(label, {}).get("summary", {}).get("turns", []):
            c.update(t["calls"])
        return c

    if "main" in by:
        s = by["main"]["summary"]
        ext = [a for a in s["actions"] if a.get("action") == "external_files_added"]
        checks.append((bool(ext) and len(ext[0].get("files", [])) == 2 and not ext[0].get("timed_out"),
                       "main: 2 late files appeared in input/ from outside the CLI (external_files_added)"))
        after = [t for t in s["turns"] if ext and t["n"] == ext[0]["after_turn"] + 1]
        checks.append((bool(after) and after[0]["calls"].get("ls", 0) > 0 and after[0]["calls"].get("move", 0) >= 2,
                       "main: the turn after the pause calls ls again and moves the new files"))
        c = all_calls("main")
        checks.append((c.get("load_skill", 0) >= 2, "main: load_skill called (organize_files + index_excerpts)"))
        checks.append((c.get("upsert_excerpts", 0) >= 1 and c.get("search_excerpts", 0) >= 1,
                       "main: MCP upsert_excerpts and search_excerpts called"))
        checks.append((c.get("update_document_path", 0) >= 1, "main: update_document_path after moving an indexed file"))
    if "limit" in by:
        stops = [t for t in by["limit"]["summary"]["turns"] if t.get("stopped_by")]
        checks.append((bool(stops), "limit: the looping turn was stopped by an execution limit"
                       + (f" ({stops[0]['stopped_by']}, {stops[0]['model_calls']} model calls)" if stops else "")))
    if "guard" in by:
        e = all_errors("guard")
        for t in ("invalid_argument", "outside_workspace", "name_conflict"):
            checks.append((e.get(t, 0) > 0, f"guard: a tool returned {t}"))
        okr = Counter()
        for t in by["guard"]["summary"]["turns"]:
            okr.update(t["ok_results"])
        checks.append((okr.get("web_fetch", 0) > 0, "guard: web_fetch succeeded on the local fixture server"))
        for t in ("head", "tail", "write"):
            checks.append((okr.get(t, 0) > 0, f"guard: a successful {t} call"))
        we = Counter(k for t in by["guard"]["summary"]["turns"] for k in t["errors"].elements() if k.startswith("web_fetch:"))
        for t in ("http_error", "timeout", "network_error"):
            checks.append((we.get(f"web_fetch:{t}", 0) > 0, f"guard: web_fetch returned {t}"))
    if "errors" in by:
        e = all_errors("errors")
        checks.append((e.get("es_unavailable", 0) + e.get("es_error", 0) > 0,
                       "errors: MCP tools reported unavailable Elasticsearch"))
    if "mcp_down" in by:
        s = by["mcp_down"]["summary"]
        e = all_errors("mcp_down")
        checks.append((str(s["start"].get("mcp", "")).startswith("MCP unavailable"),
                       "mcp_down: the CLI could not start the MCP server (session_start.mcp)"))
        checks.append((e.get("mcp_unavailable", 0) > 0, "mcp_down: an index tool returned mcp_unavailable"))
        cat = RESULTS / "catalog_after_mcp_down.json"
        if cat.exists():
            docs = json.loads(cat.read_text(encoding="utf-8"))["documents"]
            checks.append((any(d.get("indexing_status") == "error" for d in docs),
                           "mcp_down: the document whose indexing failed has status error in the catalog"))
        moved = sum(t["ok_results"].get("move", 0) for t in s["turns"])
        checks.append((moved > 0, "mcp_down: file organization still works (a successful move)"))
    for s in sessions:
        bad = [t["n"] for t in s["summary"]["turns"] if not t.get("history_ok", True)]
        checks.append((not bad, f"{s['label']}: history check OK in every turn" + (f" (bad: {bad})" if bad else "")))
        end = s["summary"].get("end") or {}
        checks.append((str(end.get("ended_by", "")).startswith("exit_command"),
                       f"{s['label']}: session ended by the exit command (session_end.ended_by={end.get('ended_by')})"))
    return checks


def results_block() -> list[str]:
    out = []
    sym = RESULTS / "symlink_test.txt"
    if sym.exists():
        status = next((l for l in sym.read_text(encoding="utf-8-sig").splitlines() if l.startswith("status:")), "")
        out.append(f"- `results/symlink_test.txt` (выход через ссылки): {status[len('status:'):].strip()}")
    http = RESULTS / "http_fixture_requests.log"
    if http.exists():
        reqs = [l for l in http.read_text(encoding="utf-8", errors="replace").splitlines() if " /" in l and "/health" not in l]
        out.append(f"- `results/http_fixture_requests.log`: локальный HTTP-сервер получил {len(reqs)} запросов web_fetch: "
                   + ", ".join(cell(l.split(" ", 1)[1], 30) for l in reqs))
    for name in ("verify_report_main.json", "verify_report.json"):
        p = RESULTS / name
        if p.exists():
            rep = json.loads(p.read_text(encoding="utf-8"))
            checks = ", ".join(f"{k} {'OK' if v else 'FAIL'}" for k, v in rep.get("checks", {}).items())
            out.append(f"- `results/{name}`: {checks}")
            if rep.get("problems"):
                out.append("  - problems: " + "; ".join(cell(x, 200) for x in rep["problems"]))
    cat_p, exp_p = RESULTS / "catalog.json", RESULTS / "index_export.jsonl"
    if cat_p.exists():
        docs = json.loads(cat_p.read_text(encoding="utf-8"))["documents"]
        status = Counter(d["indexing_status"] for d in docs)
        out.append(f"- каталог: {len(docs)} документов, статусы: {dict(status)}")
        rows = [json.loads(l) for l in exp_p.read_text(encoding="utf-8").splitlines() if l.strip()] if exp_p.exists() else []
        by_doc = {d["document_id"]: d for d in docs}
        out.append(f"- выгрузка индекса: {len(rows)} выдержек, {len({r['document_id'] for r in rows})} документов")
        drift = [r for r in rows if r["document_id"] in by_doc and
                 (r["current_path"] != by_doc[r["document_id"]]["current_path"] or
                  r.get("category") != by_doc[r["document_id"]]["category"])]
        out.append(f"- выдержки, у которых путь или категория расходятся с каталогом: {len(drift)}")
        out.append("")
        out.append("| Документ (current_path) | category | статус | выдержек в индексе |")
        out.append("|---|---|---|---|")
        per = Counter(r["document_id"] for r in rows)
        for d in docs:
            out.append(f"| `{d['current_path']}` | {d['category']} | {d['indexing_status']} | {per.get(d['document_id'], 0)} |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=RESULTS / "RUN_SUMMARY.md")
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    sessions = []
    for s in manifest["sessions"]:
        trace = ROOT / s["trace"]
        sessions.append({**s, "summary": summarize_session(load_events(trace))})

    L = ["# Сводка демо-прогона лабораторной 1", "",
         "Файл создан `scripts/summarize_run.py` по трассам этого прогона (`scripts/run_all_demos.ps1`).",
         f"Начало прогона: {manifest.get('started_at', '?')}. Модель: `{manifest.get('model', '?')}`.", "",
         "## Цепочка сессий (в порядке запуска, все из чистого workspace и пустого индекса)", "",
         "| # | Сценарий | Диалог | Трасса | Ходов | Вызовов модели | Длительность, с |", "|---|---|---|---|---|---|---|"]
    for i, s in enumerate(sessions, 1):
        t = s["summary"]["turns"]
        L.append(f"| {i} | {s['label']}: {s.get('title', '')} | `{s['dialog']}` | `{Path(s['trace']).stem}` | {len(t)} | "
                 f"{sum(x['model_calls'] for x in t)} | {round(sum(x.get('duration_s', 0) for x in t))} |")
    late = RESULTS / "late_files_added.json"
    if late.exists():
        info = json.loads(late.read_text(encoding="utf-8-sig"))
        L += ["", f"Поздние файлы скопировал отдельный процесс PowerShell (PID {info.get('copier_pid')}, CLI PID "
                  f"{info.get('cli_pid', '?')}) в {info.get('copied_at')}: {', '.join(info.get('files', []))}."]
    for s in sessions:
        sm = s["summary"]
        lim = sm["start"].get("limits", {})
        L += ["", f"## {s['label']}: {s.get('title', '')}", "",
              f"Трасса `traces/{Path(s['trace']).name}` (и `.md`), MCP: {cell(sm['start'].get('mcp', '?'), 60)}, "
              f"лимиты: model_calls_per_turn={lim.get('model_calls_per_turn')}, recursion_limit={lim.get('recursion_limit')}.", "",
              "| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |",
              "|---|---|---|---|---|---|"]
        acts = {a["after_turn"]: a for a in sm["actions"]}
        if 0 in acts:
            L.append(f"| — | *действие: {acts[0]['action']} {', '.join(acts[0].get('files', []))}* | | | | |")
        for t in sm["turns"]:
            stop = t.get("stopped_by") or ("ошибка: " + cell(t["error"], 40) if t.get("error") else "—")
            L.append(f"| {t['n']} | {cell(t['text'])} | {fmt_counter(t['calls'])} | {fmt_counter(t['errors'])} | "
                     f"{stop} | {'OK' if t.get('history_ok', True) else 'FAIL'} |")
            if t["n"] in acts:
                a = acts[t["n"]]
                L.append(f"| — | *действие: {a['action']} {', '.join(a.get('files', []))} "
                         f"({a.get('added_by', '')})* | | | | |")
        if sm.get("commands"):
            L += ["", "Команды CLI (события `cli_command`):", ""]
            for c in sm["commands"]:
                res = c["result"]
                if isinstance(res, dict) and "history_ok" in res:
                    res = f"history_ok={res['history_ok']}, messages={res.get('messages')}"
                L.append(f"- после хода {c['after_turn']}: `{c['command']}` -> "
                         f"{'ok' if c['ok'] else 'FAILED'}, {cell(json.dumps(res, ensure_ascii=False, default=str), 120)}")
        end = sm.get("end") or {}
        L += ["", f"Завершение сессии (`session_end.ended_by`): `{end.get('ended_by', 'не записано')}`."]
    checks = scenario_checks(sessions)
    L += ["", "## Проверки сценариев по трассам", ""]
    L += [f"- [{'x' if ok else ' '}] {text}" for ok, text in checks]
    L += ["", "## Итоговое состояние (results/)", ""] + results_block()
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    for ok, text in checks:
        print(("OK   " if ok else "WARN ") + text)
    print(f"summary: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
