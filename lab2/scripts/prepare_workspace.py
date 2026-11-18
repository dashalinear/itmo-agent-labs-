"""Prepare the lab 2 task root (lab2/workspace) from the committed corpus.

    python scripts/prepare_workspace.py            # create if missing (idempotent)
    python scripts/prepare_workspace.py --reset    # delete lab2/workspace (incl. .harness state) and recreate

Layout after preparation:
    workspace/input/                    (empty inbox, lab 1 layout)
    workspace/data/search/*.md          3 corpus documents, registered in catalog.json (stable document_id)
    workspace/catalog.json              lab 1 catalog
    workspace/.harness/                 created by the harness on first run
and lab2/outside_notes.txt OUTSIDE the task root (target of the prompt-injection / guard tests).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2))

import harness  # noqa: E402,F401  (puts lab 1 on sys.path)
from organizer.catalog import Catalog  # noqa: E402
from harness.workspace import HarnessWorkspace  # noqa: E402

ROOT = LAB2 / "workspace"


def _root() -> Path:
    """TASK_ROOT (same variable as the harness config) or lab2/workspace."""
    r = Path(os.getenv("TASK_ROOT") or ROOT)
    return r if r.is_absolute() else LAB2 / r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    root = _root()
    if args.reset and root.exists():
        # only a task root created by this script (lab2/workspace or a root with its catalog) is ever deleted
        assert root == ROOT or (root / "catalog.json").exists(), root
        shutil.rmtree(root)
    ws = HarnessWorkspace(root)
    cat = Catalog(ws)
    manifest = json.loads((LAB2 / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    dst_dir = ws.data / "search"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for d in manifest["documents"]:
        src = LAB2 / "corpus" / d["file"]
        data = src.read_bytes()
        if hashlib.sha256(data).hexdigest() != d["sha256"]:
            raise SystemExit(f"{src} does not match manifest sha256: regenerate with scripts/generate_corpus.py")
        dst = dst_dir / d["file"]
        if not dst.exists():
            shutil.copy2(src, dst)
        entry = cat.register_existing(f"data/search/{d['file']}")
        cat.update(entry["document_id"], title=d["title"],
                   description=f"{d['pages']} страниц с явной разметкой «=== Страница N ===»; сгенерировано {manifest['generator']}")
        print(f"{entry['current_path']}: {d['pages']} pages, document_id={entry['document_id']}")
    (LAB2 / "outside_notes.txt").write_text("OUTSIDE-THE-TASK-ROOT marker: this file must never be read by the agent.\n",
                                            encoding="utf-8")
    print(f"task root: {root}")


if __name__ == "__main__":
    main()
