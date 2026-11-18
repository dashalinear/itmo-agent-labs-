"""Show that an offloaded tool output stays reachable in a NEW process (after the task run ended).

    python scripts/artifact_after_restart.py --task-id A1 [G1 ...] [--query АС-01 ...]

For every task: takes the offloaded outputs from the state DB (events tool_output_offloaded), opens each artifact
with a fresh ArtifactStore (this process never saw the run) and looks for a fact that lies BEYOND the part the model
received (character offset > passed_chars of the offload event):
  1. the control facts given with --query (default: АС-01, АС-02, АС-03, INC-2026-017, nDCG@10);
  2. if none of them is in the artifact: the last hit / last line of the artifact (e.g. the 40th hit of an offloaded
     search_text), which by construction is outside the preview.
The found place is read with read_artifact / search_artifact and, when it carries a document page, checked against
the document itself. Writes results/<task_id>/artifact_after_restart.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2))

import harness  # noqa: E402,F401
from harness.artifacts import ArtifactStore  # noqa: E402
from harness.config import load_config  # noqa: E402
from harness.store import TaskStore  # noqa: E402
from harness.tools_docs import load_doc  # noqa: E402
from harness.tools_task import _norm  # noqa: E402
from harness.workspace import HarnessWorkspace  # noqa: E402

CONTROL = ["АС-01", "АС-02", "АС-03", "INC-2026-017", "nDCG@10"]


def _last_hit(text: str) -> dict | None:
    """Last structured hit of an offloaded JSON result (search_text, search_excerpts...) or the last non-empty line."""
    try:
        env = json.loads(text)
        hits = (env.get("data") or {}).get("hits") or []
        if hits and isinstance(hits[-1], dict) and hits[-1].get("snippet"):
            return hits[-1]
    except (ValueError, AttributeError):
        pass
    lines = [l for l in text.split("\n") if l.strip()]
    return {"snippet": lines[-1][:120]} if lines else None


def _verify_in_document(ws: HarnessWorkspace, hit: dict) -> dict | None:
    if not hit.get("path") or not hit.get("page"):
        return None
    doc = load_doc(ws.resolve(hit["path"], must_exist=True), hit["path"])
    a, b = doc.page_bounds(int(hit["page"]))
    page_text = _norm("\n".join(doc.lines[a - 1: b]))
    return {"path": hit["path"], "page": hit["page"], "snippet_on_page": _norm(hit["snippet"])[:80] in page_text}


def check_task(cfg, store: TaskStore, task_id: str, queries: list[str]) -> dict:
    ws = HarnessWorkspace(cfg.root)
    arts = ArtifactStore(ws, store, task_id)
    offloaded = [e["data"] for e in store.events(task_id, "tool_output_offloaded")]
    found, tried = None, []
    for ev in offloaded:
        try:
            text = arts.text(ev["artifact_id"])
        except Exception as e:  # noqa: BLE001
            tried.append({"artifact_id": ev["artifact_id"], "error": str(e)})
            continue
        given = int(ev.get("passed_chars") or 0)
        single_line = "\n" not in text
        candidates = [(q, "control fact") for q in queries]
        last = _last_hit(text)
        if last:
            # an older artifact is one JSON line: the hit's own line number is a unique search key inside it
            key = f'"line": {last["line"]}' if single_line and last.get("line") else last["snippet"][:60]
            candidates.append((key, "last hit of the offloaded output"))
        for q, why in candidates:
            pos = text.find(q)
            if pos >= 0 and pos < given and not single_line:
                pos = text.find(q, given)          # multi-line artifact: the occurrence beyond the given part
            if pos < 0 or pos < given:
                continue
            line = text.count("\n", 0, pos) + 1
            hits = arts.search(ev["artifact_id"], q, max_hits=100)
            hit = next((h for h in hits["hits"] if h["line"] == line), None)
            if hit is None:
                continue
            part = arts.read(ev["artifact_id"], line, 1)
            found = {"artifact_id": ev["artifact_id"], "tool": ev["tool"], "total_chars": ev["total_chars"],
                     "chars_given_to_model": given, "query": q, "kind": why, "char_offset_in_artifact": pos,
                     "beyond_given_part": pos >= given, "artifact_layout": "one JSON line" if single_line else "lines",
                     "search_artifact_hit": hit["snippet"][:400], "artifact_line": line,
                     "read_artifact_text": part["text"][:400], "read_artifact_has_query": q in part["text"],
                     "verified_in_document": _verify_in_document(ws, last) if why.startswith("last") and last else None}
            found["found_via"] = "read_artifact" if found["read_artifact_has_query"] else "search_artifact"
            break
        tried.append({"artifact_id": ev["artifact_id"], "tool": ev["tool"], "passed_chars": given})
        if found:
            break
    return {"task_id": task_id, "pid": os.getpid(), "note": "new process: artifacts opened from disk after the run",
            "offloaded_outputs": len(offloaded), "artifacts_checked": tried, "example": found,
            "ok": bool(found and found["beyond_given_part"]
                       and (found["read_artifact_has_query"] or found["query"] in found["search_artifact_hit"]))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", nargs="+", required=True)
    ap.add_argument("--query", nargs="*", default=CONTROL)
    args = ap.parse_args()
    cfg = load_config()
    store = TaskStore(cfg.state_dir / "state.db")
    try:
        for task_id in args.task_id:
            res = check_task(cfg, store, task_id, args.query)
            out = cfg.results_dir / task_id
            out.mkdir(parents=True, exist_ok=True)
            (out / "artifact_after_restart.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(res, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
