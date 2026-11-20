"""Replace check-mark symbols that came from downloaded web pages with plain words in the committed copies.

    python scripts/plain_text_marks.py --task-id B3

Web pages (e.g. comparison tables) may contain check-mark symbols; the repository keeps plain text only. In
results/<task>/ (page copies, history, report, events) and traces/<task>_* the symbols are replaced by "да" / "нет".
The change is recorded in results/<task>/plain_text_marks.json (files and counts). Evidence quotes that contain
such a symbol are listed there too (their verbatim check was done against the original page before the replacement).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
RES = Path(os.getenv("RESULTS_DIR") or LAB2 / "results")
TR = Path(os.getenv("TRACES_DIR") or LAB2 / "traces")
YES = ("\u2713", "\u2714", "\u2705")   # check marks
NO = ("\u2717", "\u2718", "\u274c")     # crosses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    args = ap.parse_args()
    files = [p for p in (RES / args.task_id).rglob("*") if p.is_file() and p.suffix in (".txt", ".md", ".json", ".jsonl")]
    files += [p for p in TR.glob(f"{args.task_id}_*") if p.is_file()]
    changed = {}
    evidence_hits = []
    ev_path = RES / args.task_id / "evidence.json"
    if ev_path.exists():
        for e in json.loads(ev_path.read_text(encoding="utf-8")):
            if any(c in e.get("quote", "") for c in YES + NO):
                evidence_hits.append(e["evidence_id"])
    for p in files:
        if p.name == "plain_text_marks.json":
            continue
        t = p.read_text(encoding="utf-8")
        n = sum(t.count(c) for c in YES + NO)
        if not n:
            continue
        for c in YES:
            t = t.replace(c, "да")
        for c in NO:
            t = t.replace(c, "нет")
        p.write_text(t, encoding="utf-8", newline="\n")
        changed[str(p.relative_to(LAB2) if LAB2 in p.parents else p)] = n
    out = {"task_id": args.task_id, "replaced": changed, "evidence_with_marks": evidence_hits}
    if changed or evidence_hits:
        (RES / args.task_id).mkdir(parents=True, exist_ok=True)
        (RES / args.task_id / "plain_text_marks.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                                                 encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
