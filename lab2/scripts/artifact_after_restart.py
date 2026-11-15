"""Show that an offloaded tool output stays reachable in a NEW process (after the task run ended).

    python scripts/artifact_after_restart.py --task-id A1 [--query АС-01]

Takes the offloaded outputs of the task from the state DB, opens each artifact
with a fresh ArtifactStore, searches the query and reads the matching lines.
Writes results/<task_id>/artifact_after_restart.json.
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
from harness.workspace import HarnessWorkspace  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--query", default="АС-01")
    args = ap.parse_args()
    cfg = load_config()
    store = TaskStore(cfg.state_dir / "state.db")
    arts = ArtifactStore(HarnessWorkspace(cfg.root), store, args.task_id)
    offloaded = [e["data"] for e in store.events(args.task_id, "tool_output_offloaded")]
    found = None
    for ev in offloaded:
        hits = arts.search(ev["artifact_id"], args.query, max_hits=3)
        if hits["hits"]:
            line = hits["hits"][0]["line"]
            found = {"artifact_id": ev["artifact_id"], "tool": ev["tool"], "total_chars": ev["total_chars"],
                     "chars_given_to_model": ev["passed_chars"], "query": args.query, "hit_line": line,
                     "text": arts.read(ev["artifact_id"], max(line - 1, 1), 3)["text"]}
            break
    res = {"task_id": args.task_id, "pid": os.getpid(), "offloaded_outputs": len(offloaded), "example": found}
    out = cfg.results_dir / args.task_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "artifact_after_restart.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    store.close()


if __name__ == "__main__":
    main()
