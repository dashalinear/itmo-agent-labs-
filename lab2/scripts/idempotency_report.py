"""Evidence of crash recovery without repeated effects for one task (scenario I1 of run_final_lab2.ps1).

    python scripts/idempotency_report.py --task-id I1

Reads the traces of every process of the task (traces/<task>_*.jsonl) and the state DB, and writes
results/<task>/idempotency.json with:
  * processes: label, resume_kind, the simulated crash (point, counters at the crash, URLs left 'running'),
    what the next process recovered (URLs re-queued, in-flight reservations charged);
  * re_executed_steps: tool results of the interrupted step that LangGraph re-executed in the next process
    (from the trace, before its first model call): web_fetch -> from_cache with the HTTP counter unchanged,
    save_evidence -> already_saved;
  * invariants over the final state: one evidence per (source, quote), one queue entry per canonical URL,
    one stored copy per fetched page, HTTP requests per URL;
  * replay: in THIS new process, on a copy of the state, every saved evidence and every fetched page is
    requested again with the same key -> already_saved / cache hit, HTTP counter unchanged (no network used).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2))

import httpx  # noqa: E402

import harness  # noqa: E402,F401
from harness.artifacts import ArtifactStore  # noqa: E402
from harness.budget import Budget  # noqa: E402
from harness.config import Limits, load_config  # noqa: E402
from harness.store import TaskStore  # noqa: E402
from harness.tools_research import ResearchTools  # noqa: E402
from harness.tools_task import TaskTools  # noqa: E402
from harness.web import FixtureProvider, HttpFetcher  # noqa: E402
from harness.workspace import HarnessWorkspace  # noqa: E402
from organizer.catalog import Catalog  # noqa: E402


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _tool_results(rows: list[dict], seen: set[str]) -> list[dict]:
    """Tool results of the interrupted step at the start of a resumed process (before its first model call).
    A result whose tool_call_id was already emitted by an earlier process is a pending write restored from the
    checkpoint (LangGraph does not execute that call again): it is marked restored_from_checkpoint."""
    out = []
    for r in rows:
        if r["event"] == "harness:model_call_plan":
            break
        if r["event"] != "graph_step" or r.get("node") != "tools":
            continue
        for m in r.get("messages", []):
            if m.get("type") != "ToolMessage":
                continue
            try:
                env = json.loads(m.get("content") or "")
            except ValueError:
                env = {}
            d = env.get("data") or {}
            item = {"tool": m.get("name"), "ok": env.get("ok"), "tool_call_id": m.get("tool_call_id"),
                    "restored_from_checkpoint": m.get("tool_call_id") in seen}
            if m.get("name") == "web_fetch":
                item.update(url=d.get("url"), from_cache=d.get("from_cache", False), artifact_id=d.get("artifact_id"))
            elif m.get("name") == "save_evidence":
                item.update(evidence_id=d.get("evidence_id"), status=d.get("status"))
            elif not env.get("ok"):
                item["error"] = (env.get("error") or {}).get("type")
            out.append(item)
    return out


def processes(cfg, task_id: str) -> list[dict]:
    traces = sorted(cfg.traces_dir.glob(f"{task_id}_*.jsonl"), key=lambda p: _load(p)[0]["ts"])
    procs, seen = [], set()
    for t in traces:
        rows = _load(t)
        start = rows[0]
        crash = next((r for r in rows if r["event"] == "harness:simulated_crash"), None)
        cache_hits = [r for r in rows if r["event"] == "harness:fetch_cache_hit"]
        end = next((r for r in rows if r["event"] == "session_end"), None)
        p = {"trace": t.name, "label": start.get("label"), "resume_kind": start.get("resume_kind"),
             "dry_run": bool(start.get("dry_run_scripted_model")), "recovered": start.get("recovered"),
             "usage_before": {k: (start.get("usage_before") or {}).get(k) for k in ("http_requests", "cache_hits")},
             "crash": ({k: crash.get(k) for k in ("at", "phase", "tool", "count", "usage_at_crash", "evidence_at_crash",
                                                   "urls_running")} if crash else None),
             "status_at_end": end.get("status") if end else "process killed (no session_end)"}
        if start.get("resume_kind") == "continue_interrupted_step":
            p["re_executed_step"] = _tool_results(rows, seen)
        if cache_hits:
            http_before = (start.get("usage_before") or {}).get("http_requests")
            p["cache_hits"] = [{"url": c.get("url"), "url_key": c.get("url_key"), "http_requests_at_hit": c.get("http_requests"),
                                "url_http_attempts_at_hit": c.get("url_http_attempts"),
                                "http_requests_at_process_start": http_before} for c in cache_hits]
        seen |= {m.get("tool_call_id") for r in rows if r["event"] == "graph_step"
                 for m in r.get("messages", []) if m.get("type") == "ToolMessage"}
        procs.append(p)
    return procs


def invariants(store: TaskStore, task_id: str) -> dict:
    ev = store.evidence(task_id)
    pairs = [(e["source"], e["quote"]) for e in ev]
    urls = store.urls(task_id)
    canon = [u["canonical"] for u in urls]
    done = [u for u in urls if u["status"] == "done"]
    arts = [a for a in store.artifacts(task_id) if a["kind"] == "web_page"]
    return {"evidence_total": len(ev), "evidence_duplicates": len(pairs) - len(set(pairs)),
            "queue_entries": len(urls), "queue_duplicates": len(canon) - len(set(canon)),
            "pages_done": len(done), "page_artifacts": len({a["artifact_id"] for a in arts}),
            "http_attempts_per_done_url": {u["url"]: u["attempts"] for u in done},
            "usage": {k: store.usage(task_id).get(k) for k in ("http_requests", "cache_hits", "tool_calls", "model_calls")}}


async def replay(cfg, task_id: str) -> dict:
    """Same keys again in a new process, on a COPY of the state (the recorded task is not modified)."""
    tmp = Path(tempfile.mkdtemp(prefix="lab2_idem_"))
    try:
        root = tmp / "root"
        (root / ".harness" / "artifacts").mkdir(parents=True)
        src = sqlite3.connect(str(cfg.state_dir / "state.db"))
        dst = sqlite3.connect(str(root / ".harness" / "state.db"))
        src.backup(dst)
        src.close()
        dst.close()
        store = TaskStore(root / ".harness" / "state.db")
        for a in store.artifacts(task_id):
            f = cfg.state_dir / "artifacts" / f"{a['artifact_id']}.txt"
            if f.exists():
                shutil.copy2(f, root / ".harness" / "artifacts" / f.name)
        ws = HarnessWorkspace(root)
        limits = Limits.from_dict(store.get_task(task_id)["limits"])
        budget = Budget(store, task_id, limits)
        arts = ArtifactStore(ws, store, task_id)

        def no_network(request):        # any real request would be a bug of the replay: make it visible
            raise httpx.ConnectError("replay must not use the network")
        rt = ResearchTools(store, task_id, budget, limits, HttpFetcher(budget, limits, httpx.MockTransport(no_network)),
                           FixtureProvider(cfg.serp_path, cfg.fixture_base_url), arts)
        tt = TaskTools(ws, Catalog(ws), store, task_id, arts, budget)
        http0 = store.usage(task_id)["http_requests"]
        n_ev0, n_art0 = len(store.evidence(task_id)), len(list((root / ".harness" / "artifacts").glob("*.txt")))
        fetches = []
        for u in store.urls(task_id, "done"):
            r = json.loads(await rt.web_fetch(u["url"]))
            fetches.append({"url": u["url"], "from_cache": (r.get("data") or {}).get("from_cache", False), "ok": r["ok"]})
        evid = []
        for e in store.evidence(task_id):
            if e["source_type"] != "web":
                continue
            r = tt.save_evidence(e["claim"], e["kind"], e["source"], e["quote"])
            evid.append({"evidence_id": r["evidence_id"], "status": r["status"], "same_id": r["evidence_id"] == e["evidence_id"]})
        res = {"pages_requested_again": len(fetches), "all_cache_hits": all(f["from_cache"] for f in fetches),
               "http_requests_before": http0, "http_requests_after": store.usage(task_id)["http_requests"],
               "evidence_saved_again": len(evid), "all_already_saved": all(x["status"] == "already_saved" and x["same_id"]
                                                                           for x in evid),
               "evidence_count_before": n_ev0, "evidence_count_after": len(store.evidence(task_id)),
               "artifact_files_before": n_art0,
               "artifact_files_after": len(list((root / ".harness" / "artifacts").glob("*.txt"))),
               "fetches": fetches, "evidence": evid}
        store.close()
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    args = ap.parse_args()
    cfg = load_config()
    store = TaskStore(cfg.state_dir / "state.db")
    try:
        procs = processes(cfg, args.task_id)
        inv = invariants(store, args.task_id)
        status = store.get_task(args.task_id)
    finally:
        store.close()
    rep = asyncio.run(replay(cfg, args.task_id))
    re_exec = [x for p in procs for x in p.get("re_executed_step", []) if not x["restored_from_checkpoint"]]
    # a page re-read from the store after a crash is not downloaded again: its HTTP attempts at the cache hit equal
    # its attempts at the end of the task (the global counter may still grow because of OTHER pages of the same step)
    final_attempts = {u: n for u, n in inv["http_attempts_per_done_url"].items()}
    unchanged = []
    for prev, p in zip(procs, procs[1:]):
        if not prev["crash"]:
            continue
        for hit in p.get("cache_hits", []):
            hit["http_requests_at_previous_crash"] = (prev["crash"].get("usage_at_crash") or {}).get("http_requests")
            hit["url_http_attempts_final"] = final_attempts.get(hit["url"])
            unchanged.append(hit["url_http_attempts_at_hit"] == hit["url_http_attempts_final"])
    checks = {
        "crashes": sum(1 for p in procs if p["crash"]),
        "resumes_in_new_process": sum(1 for p in procs if p["label"] == "resume"),
        "urls_requeued_after_crash": sum((p.get("recovered") or {}).get("urls_requeued", 0) for p in procs),
        "cache_hit_in_re_executed_step": any(x["tool"] == "web_fetch" and x.get("from_cache") for x in re_exec),
        "already_saved_in_re_executed_step": any(x["tool"] == "save_evidence" and x.get("status") == "already_saved"
                                                 for x in re_exec),
        "cached_pages_not_downloaded_again": bool(unchanged) and all(unchanged),
        "no_duplicate_evidence": inv["evidence_duplicates"] == 0,
        "no_duplicate_queue_entries": inv["queue_duplicates"] == 0,
        "replay_all_already_saved": rep["all_already_saved"] and rep["evidence_count_after"] == rep["evidence_count_before"],
        "replay_all_cache_hits_no_http": rep["all_cache_hits"] and rep["http_requests_after"] == rep["http_requests_before"],
        "final_status": status["status"],
    }
    out = {"task_id": args.task_id, "checks": checks, "processes": procs, "final_state": inv, "replay_new_process": rep,
           "note": ("Recovery point = last checkpoint; the interrupted tools step is executed again. Effects are not "
                    "repeated because of operation keys (evidence_id = hash(task, source, version, quote), page = "
                    "canonical URL + stored artifact). A page whose download finished but was not stored yet is "
                    "fetched again after resume (counted in the HTTP budget): exactly-once is not claimed.")}
    dst = cfg.results_dir / args.task_id
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "idempotency.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
