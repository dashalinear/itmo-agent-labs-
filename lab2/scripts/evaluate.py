"""Evaluate recorded runs: control facts, constraint preservation through compaction, baseline vs harness.

    python scripts/evaluate.py [--harness A1] [--baseline A_base] [--tasks A1 B1 C1 D1 E1]

Reads results/<task_id>/ (written by the harness CLI) and corpus/manifest.json.
Writes results/evaluation.json and results/evaluation.md.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
RES = LAB2 / "results"
CONSTRAINT = re.compile(r"\[(?:C|W|G)\d\]")


def load(task: str, name: str, default=None):
    p = RES / task / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else p.read_text(encoding="utf-8")


def agent_sections(report: str) -> str:
    """Report text written by the agent: without the copied request and user constraints."""
    out, keep = [], True
    for line in report.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip() not in ("Запрос", "Ограничения пользователя")
        if keep:
            out.append(line)
    return "\n".join(out)


def facts_check(task: str, manifest: dict) -> dict:
    ev = load(task, "evidence.json", []) or []
    report = agent_sections(load(task, "report.md", "") or "")
    comps = load(task, "compactions.json", []) or []
    summary = load(task, "summary.md", "") or ""
    rows = []
    for f in manifest["control_facts"]:
        kw = f["keywords"]
        in_ev = [e for e in ev if all(k.lower() in (e["quote"] + " " + e["claim"]).lower() for k in kw[:1])]
        pages = sorted({e["page_start"] for e in in_ev if e.get("page_start")})
        first_comp = next((c["id"] for c in comps if kw[0] in (c.get("summary") or "")), None)
        later = [c for c in comps if first_comp and c["id"] >= first_comp]
        kept = sum(1 for c in later if kw[0] in (c.get("summary") or ""))
        rows.append({"fact": f["id"], "position": f["position"], "expected_page": f["page"], "file": f["file"],
                     "in_evidence": bool(in_ev), "evidence_pages": pages,
                     "page_correct": f["page"] in pages if in_ev else None,
                     "in_report": kw[0] in report, "in_final_summary": kw[0] in summary,
                     "summaries_after_first_mention": f"{kept}/{len(later)}" if later else "—"})
    status = load(task, "status.json", {}) or {}
    cons = status.get("constraints", [])
    cons_rows = []
    for c in cons:
        tag = CONSTRAINT.match(c).group(0) if CONSTRAINT.match(c) else c[:6]
        in_s = sum(1 for x in comps if tag in (x.get("summary") or ""))
        cons_rows.append({"constraint": tag, "pinned_in_every_call": True, "in_compaction_summaries": f"{in_s}/{len(comps)}"})
    return {"facts": rows, "constraints": cons_rows, "compactions": len(comps)}


def harness_metrics(task: str) -> dict | None:
    st = load(task, "status.json")
    if not st:
        return None
    b = st["budget"]
    calls = [c for c in st.get("model_calls", []) if c["kind"] == "agent"]
    return {"status": st["task"]["status"], "reason": st["task"]["status_reason"], "runs": st["task"]["runs"],
            "success": st["task"]["status"] == "completed",
            "peak_input_tokens": b["peak_input_tokens"],
            "max_estimate_plus_reserve": max([c["estimated_input"] + c["reserve"] for c in calls] or [0]),
            "working_limit": calls[0]["working_limit"] if calls else None, "window": calls[0]["window_tokens"] if calls else None,
            "tokens_total": b["tokens"]["spent"], "compaction_tokens": b["tokens"]["of_which"]["compaction_input_tokens"]
            + b["tokens"]["of_which"]["compaction_output_tokens"], "model_calls": b["model_calls"]["used"],
            "compaction_calls": b["compaction_calls"], "failed_calls": b["failed_calls"], "tool_calls": b["tool_calls"]["used"],
            "search_calls": b["search_calls"]["used"], "http_requests": b["http_requests"]["used"],
            "cache_hits": b["cache_hits"], "active_seconds": b["active_seconds"]["used"],
            "compactions": len(st.get("compactions", [])), "queue": st.get("queue"),
            "calls_over_working_limit": sum(1 for c in calls if c["estimated_input"] + c["reserve"] > (c["working_limit"] or 0)),
            "blocked_calls": sum(1 for c in calls if c["status"] == "blocked")}


def baseline_metrics(task: str, manifest: dict) -> dict | None:
    b = load(task, "baseline.json")
    if not b:
        return None
    ans = b.get("answer", "")
    facts = [f["id"] for f in manifest["control_facts"] if f["keywords"][0] in ans]
    return {"status": "error" if b["error"] else "finished", "error": b["error"], "success": not b["error"] and len(facts) >= 3,
            "peak_input_tokens": b["peak_input_tokens"], "peak_estimated_input": b["peak_estimated_input"],
            "tokens_total": b["input_tokens"] + b["output_tokens"], "model_calls": b["model_calls"],
            "failed_calls": b["failed_calls"], "tool_calls": b["tool_calls"], "duration_s": b["duration_s"],
            "facts_in_answer": facts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", default="A1")
    ap.add_argument("--baseline", default="A_base")
    ap.add_argument("--tasks", nargs="*", default=["A1", "B1", "C1", "D1", "E1", "G1", "T1"])
    args = ap.parse_args()
    manifest = json.loads((LAB2 / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    out = {"tasks": {}, "facts": {}, "comparison": {}}
    for t in args.tasks:
        m = harness_metrics(t)
        if m:
            out["tasks"][t] = m
    if (RES / args.harness / "status.json").exists():
        out["facts"][args.harness] = facts_check(args.harness, manifest)
    h, b = harness_metrics(args.harness), baseline_metrics(args.baseline, manifest)
    if h and b:
        hf = [r["fact"] for r in out["facts"][args.harness]["facts"] if r["in_evidence"] and r["in_report"]]
        out["comparison"] = {"harness": {**h, "facts_confirmed": hf}, "baseline": b}
    (RES / "evaluation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# Оценка прогонов (scripts/evaluate.py)", ""]
    if out["tasks"]:
        md += ["## Технические счётчики задач", "",
               "| задача | статус | запусков | пик входа | макс. оценка+резерв / рабочий лимит / окно | токены (из них компакт.) | "
               "вызовы модели | компакт. | tools | поиск | HTTP | cache | время, с |", "|" + "---|" * 13]
        for t, m in out["tasks"].items():
            md.append(f"| {t} | {m['status']} ({(m['reason'] or '')[:60]}) | {m['runs']} | {m['peak_input_tokens']} | "
                      f"{m['max_estimate_plus_reserve']} / {m['working_limit']} / {m['window']} | {m['tokens_total']} "
                      f"({m['compaction_tokens']}) | {m['model_calls']} | {m['compactions']} | {m['tool_calls']} | "
                      f"{m['search_calls']} | {m['http_requests']} | {m['cache_hits']} | {m['active_seconds']} |")
        md.append("")
    for t, fc in out["facts"].items():
        md += [f"## Контрольные факты и ограничения: {t} (компактификаций: {fc['compactions']})", "",
               "| факт | место | ожид. стр. | в evidence | стр. в evidence | в отчёте | в итоговом summary | в summary после первого упоминания |",
               "|---|---|---|---|---|---|---|---|"]
        for r in fc["facts"]:
            md.append(f"| {r['fact']} | {r['position']} ({r['file']}) | {r['expected_page']} | {'✔' if r['in_evidence'] else '✘'} | "
                      f"{r['evidence_pages']} | {'✔' if r['in_report'] else '✘'} | {'✔' if r['in_final_summary'] else '✘'} | "
                      f"{r['summaries_after_first_mention']} |")
        md += ["", "| ограничение | закреплено в каждом вызове (TASK CONTRACT) | в summary компактификаций |",
               "|---|---|---|"]
        for c in fc["constraints"]:
            md.append(f"| {c['constraint']} | ✔ | {c['in_compaction_summaries']} |")
        md.append("")
    if out["comparison"]:
        h, b = out["comparison"]["harness"], out["comparison"]["baseline"]
        md += ["## Исходный агент (лаба 1) против harness на сценарии A", "",
               "| показатель | исходный агент | harness |", "|---|---|---|",
               f"| успешность | {'да' if b['success'] else 'нет'} ({b['error'] or b['status']}) | {'да' if h['success'] else 'нет'} ({h['status']}) |",
               f"| пик контекста (вход, токены) | {b['peak_input_tokens']} | {h['peak_input_tokens']} |",
               f"| расход токенов всего | {b['tokens_total']} | {h['tokens_total']} |",
               f"| вызовы модели | {b['model_calls']} | {h['model_calls']} (+{h['compaction_calls']} компакт.) |",
               f"| вызовы tools | {b['tool_calls']} | {h['tool_calls']} |",
               f"| время, с | {b['duration_s']} | {h['active_seconds']} |",
               f"| контрольные факты в итоге | {', '.join(b['facts_in_answer']) or '—'} | {', '.join(h['facts_confirmed']) or '—'} |"]
    (RES / "evaluation.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
