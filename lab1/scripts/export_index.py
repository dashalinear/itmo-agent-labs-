"""Admin: export all excerpt records + snapshot of data/ and catalog.json into results/.

    python scripts/export_index.py
Produces results/index_export.jsonl, results/catalog.json, results/data_tree.txt.
This admin script reads Elasticsearch directly; the agent goes only through MCP.
"""
import json
import shutil

import _common  # noqa: F401
from _common import ROOT

from mcp_es.store import ExcerptStore
from organizer.config import load_settings

s = load_settings()
out = ROOT / "results"
out.mkdir(exist_ok=True)
rows = ExcerptStore(s.es_url, s.es_index).export_all()
with open(out / "index_export.jsonl", "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
if (s.workspace / "catalog.json").exists():
    shutil.copy2(s.workspace / "catalog.json", out / "catalog.json")
tree = sorted(p.relative_to(s.workspace).as_posix() + ("/" if p.is_dir() else f"  ({p.stat().st_size} B)")
              for p in s.workspace.rglob("*") if not p.name.endswith(".tmp"))
(out / "data_tree.txt").write_text("\n".join(tree) + "\n", encoding="utf-8")
snap = out / "workspace_snapshot"
if snap.exists():
    shutil.rmtree(snap)
shutil.copytree(s.workspace, snap)
print(f"{len(rows)} excerpts -> {out / 'index_export.jsonl'}; catalog, data tree and workspace snapshot saved")
