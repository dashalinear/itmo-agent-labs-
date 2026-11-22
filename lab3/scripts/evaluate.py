"""Technical results of the lab 3 run (no quality benchmark).

    python scripts/evaluate.py [--main M1] [--exhaustive-cli EXP1]

Reads the task root state (state.db, L0/L1/L2, coverage) and results/<task>/ exports; writes
results/evaluation.json and results/evaluation.md:
  * documents / fragments per level, Elasticsearch counts, validation;
  * ingestion tasks: runs, model/tool calls, tokens, compactions, restarts;
  * search calls per (kind, level) by agent (main + deep), i.e. the six combinations seen in traces;
  * sub-tasks (deep / exhaustive): status, evidence, usage, parent;
  * exhaustive: coverage registry vs the area and the expected mentions from corpus/manifest.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

LAB3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB3))

import navsearch  # noqa: E402,F401

TRACES = Path(__file__).resolve().parents[1] / "traces"
from navsearch.agents import Lab3  # noqa: E402
from navsearch.config import load  # noqa: E402
from navsearch.embed import HashEmbedder  # noqa: E402
from navsearch.index import CorpusIndex  # noqa: E402
from navsearch.levels import Levels  # noqa: E402
from harness.budget import Budget  # noqa: E402
from harness.config import Limits  # noqa: E402
from harness.store import TaskStore  # noqa: E402
from harness.workspace import HarnessWorkspace  # noqa: E402
from organizer.catalog import Catalog  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="M1")
    ap.add_argument("--exhaustive-cli", default="EXP1")
    args = ap.parse_args()
    cfg = load()
    man = json.loads((LAB3 / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    ws = HarnessWorkspace(cfg.root)
    levels = Levels(ws, Catalog(ws))
    store = TaskStore(cfg.state_dir / "state.db")
    idx = CorpusIndex()
    l0 = levels.rebuild_l0()
    out = {"corpus": {"documents_L0": l0["totals"]["documents"], "with_L1": l0["totals"]["with_l1"],
                      "fragments_L2": l0["totals"]["fragments"], "folders": l0["folders"]}}
    try:
        idx.refresh()
        out["corpus"]["es"] = {"L1": idx.count("L1"), "L2": idx.count("L2")}
        out["corpus"]["validation"] = {k: v for k, v in levels.validate(idx.counts_by_document()).items() if k != "problems"}
    except Exception as e:  # noqa: BLE001
        out["corpus"]["es"] = f"unavailable: {e}"

    tasks = store.list_tasks()
    rows = []
    for t in tasks:
        if t["kind"] == "probe":            # deterministic search probe, not an agent task
            continue
        if t["runs"] and not any(TRACES.glob(f"{t['task_id']}_*.jsonl")):
            # a superseded attempt: run_all_demos.ps1 -Part final2 removes its traces and keeps only the last one;
            # the task is still in the local state.db but does not belong to the results
            continue
        task = store.get_task(t["task_id"])
        b = Budget(store, t["task_id"], Limits.from_dict(task["limits"])).snapshot()
        ev = store.evidence(t["task_id"])
        searches = Counter(f"{e['data']['kind']}:{e['data']['level']}" for e in store.events(t["task_id"], "corpus_search"))
        status = t["status"]
        if status == "queued" and t["runs"] == 0 and t["task_id"] == "CLI":
            status = "parent of CLI sub-agents"
        rows.append({"task_id": t["task_id"], "kind": t["kind"], "status": status, "reason": (t["status_reason"] or "")[:90],
                     "parent": task["spec"].get("parent_task_id"), "runs": t["runs"], "model_calls": b["model_calls"]["used"],
                     "tool_calls": b["tool_calls"]["used"], "tokens": b["tokens"]["spent"], "compactions": b["compaction_calls"],
                     "peak_input": b["peak_input_tokens"], "evidence": len(ev), "searches": dict(searches),
                     "l1_written": len(store.events(t["task_id"], "l1_written")),
                     "l2_indexed": len(store.events(t["task_id"], "l2_indexed")),
                     "crashes": len(store.events(t["task_id"], "simulated_crash"))})
    out["tasks"] = rows

    combos = Counter()
    for r in rows:
        if r["kind"] in ("main", "deep"):
            combos.update(r["searches"])
    out["search_combinations"] = {k: combos.get(k, 0) for k in
                                  ("fulltext:L1", "grep:L1", "vector:L1", "fulltext:L2", "grep:L2", "vector:L2")}

    expected = {m["code"] for m in man["exhaustive_area"]["mentions"]}
    app = Lab3(cfg, idx, HashEmbedder())
    app.runner = type("R", (), {"store": store})()
    ex = []
    for r in rows:
        if r["kind"] != "exhaustive":
            continue
        try:
            cov = app.coverage_of(r["task_id"])
        except Exception as e:  # noqa: BLE001
            ex.append({"task_id": r["task_id"], "coverage": f"no area: {e}"})
            continue
        text = " ".join(e["quote"] + " " + e["claim"] for e in store.evidence(r["task_id"]))
        found = set(re.findall(r"L-ST-\d\d", text))
        ex.append({"task_id": r["task_id"], "status": r["status"], "runs": r["runs"],
                   "fragments_read": cov["fragments_read"], "fragments_total": cov["fragments_total"],
                   "documents_done": cov["documents_done"], "documents_total": cov["documents_total"],
                   "mentions_recorded": cov["mentions_recorded"], "expected_codes": len(expected),
                   "found_codes": len(found & expected), "missing_codes": sorted(expected - found),
                   "found_without_keyword": len(found & {m["code"] for m in man["exhaustive_area"]["mentions"] if not m["has_word"]})})
        (cfg.results_dir / r["task_id"]).mkdir(parents=True, exist_ok=True)
        (cfg.results_dir / r["task_id"] / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    out["exhaustive"] = ex
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "evaluation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    c = out["corpus"]
    md = ["# Технические результаты (scripts/evaluate.py)", "",
          f"Корпус: L0 — {c['documents_L0']} документов, L1 — {c['with_L1']}, L2 — {c['fragments_L2']} фрагментов; "
          f"Elasticsearch: {c.get('es')}; проверка: {c.get('validation')}", "",
          "| папка | документов | фрагментов L2 |", "|---|---|---|"]
    md += [f"| {f['folder']} | {f['documents']} | {f['fragments']} |" for f in c["folders"]]
    md += ["", "## Задачи", "", "| задача | вид | статус | родитель | запусков | вызовы модели | tools | токены | компакт. | пик входа | evidence | L1 / L2 | сбоев |",
           "|" + "---|" * 13]
    for r in rows:
        md.append(f"| {r['task_id']} | {r['kind']} | {r['status']} | {r['parent'] or '—'} | {r['runs']} | {r['model_calls']} | "
                  f"{r['tool_calls']} | {r['tokens']} | {r['compactions']} | {r['peak_input']} | {r['evidence']} | "
                  f"{r['l1_written']} / {r['l2_indexed']} | {r['crashes']} |")
    md += ["", "## Шесть поисковых сочетаний (вызовы основного и deep-агента)", "", "| поиск | L1 | L2 |", "|---|---|---|"]
    for k in ("fulltext", "grep", "vector"):
        md.append(f"| {k} | {out['search_combinations'][k + ':L1']} | {out['search_combinations'][k + ':L2']} |")
    md += ["", "## Exhaustive: покрытие и контрольные упоминания", "",
           "| задача | статус | запусков | фрагменты прочитано/всего | документы | упоминаний записано | контрольные коды найдено/ожидалось | из них без слова «Ограничение» | пропущено |",
           "|---|---|---|---|---|---|---|---|---|"]
    for e in ex:
        if isinstance(e.get("coverage"), str):
            md.append(f"| {e['task_id']} | {e['coverage']} |||||||")
            continue
        md.append(f"| {e['task_id']} | {e['status']} | {e['runs']} | {e['fragments_read']}/{e['fragments_total']} | "
                  f"{e['documents_done']}/{e['documents_total']} | {e['mentions_recorded']} | {e['found_codes']}/{e['expected_codes']} | "
                  f"{e['found_without_keyword']}/{man['exhaustive_area']['without_word_ogranichenie']} | {', '.join(e['missing_codes']) or '—'} |")
    (cfg.results_dir / "evaluation.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    store.close()


if __name__ == "__main__":
    main()
