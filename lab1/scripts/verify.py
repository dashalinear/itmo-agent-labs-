"""Deterministic post-demo checks of workspace, catalog and Elasticsearch (via MCP).

    python scripts/verify.py [--no-es]

Checks
 1. No file lost or duplicated: every fixture (input + late files that were
    added) exists exactly once (by content hash) in data/ or input/.
 2. >= 3 topic folders with meaningful documents in data/.
 3. Catalog: unique document_id, current_path exists, content_hash matches,
    category == first folder under data/, no unregistered data files.
 4. Elasticsearch through the MCP server: for every indexed document the
    stored excerpts point to the catalog current_path and category, their text equals the
    source lines and the hash matches; no duplicate (document, range) keys;
    count equals the catalog excerpt list. >= 3 indexed documents.
Writes results/verify_report.json.
"""
import argparse
import asyncio
import json
import sys
from collections import Counter

import _common  # noqa: F401
from _common import ROOT

from organizer.catalog import Catalog
from organizer.config import load_settings
from organizer.mcp_client import server_connection
from organizer.pathguard import Workspace
from organizer.textutil import file_hash, lines_range


def fixture_hashes(ws) -> dict[str, str]:
    out = {}
    for p in sorted((ROOT / "fixtures" / "input").rglob("*")):
        if p.is_file():
            out[f"input/{p.relative_to(ROOT / 'fixtures' / 'input').as_posix()}"] = file_hash(p)
    for p in sorted((ROOT / "fixtures" / "late").glob("*")):
        out[f"late/{p.name}"] = file_hash(p)
    return out


def workspace_hashes(ws: Workspace) -> Counter:
    c = Counter()
    for area in (ws.input, ws.data):
        for p in area.rglob("*"):
            if p.is_file() and not p.name.startswith("_about"):
                c[file_hash(p)] += 1
    return c


def check_document_excerpts(d: dict, exs: list[dict], ws_root) -> tuple[bool, list[str]]:
    """Compare the excerpts stored in ES for one catalog document with the catalog and the file.

    Path or category drift is acceptable only while the catalog records pending_sync
    (the move is done, the index update is still due); everything else is an error.
    """
    ok, problems = True, []
    keys = Counter((e["start_line"], e["end_line"]) for e in exs)
    if any(n > 1 for n in keys.values()):
        ok = False; problems.append(f"duplicate excerpts for {d['current_path']}")
    if len(exs) != len(d["excerpts"]):
        ok = False; problems.append(f"{d['current_path']}: ES has {len(exs)} excerpts, catalog {len(d['excerpts'])}")
    pending = d["indexing_status"] == "pending_sync"
    for e in exs:
        drift = []
        if e["current_path"] != d["current_path"]:
            drift.append(f"current_path {e['current_path']}")
        if e.get("category") != d.get("category"):
            drift.append(f"category {e.get('category')} (catalog {d.get('category')})")
        if drift:
            ok = ok and pending
            problems.append(f"{d['current_path']}:{e['start_line']}-{e['end_line']}: excerpt has {', '.join(drift)} "
                            f"({'pending_sync recorded' if pending else 'NOT recorded'})")
            if e["current_path"] != d["current_path"]:
                continue
        if e["content_hash"] != d["content_hash"] or \
                lines_range(ws_root / d["current_path"], e["start_line"], e["end_line"]) != e["text"]:
            ok = False; problems.append(f"{d['current_path']}:{e['start_line']}-{e['end_line']} text/hash mismatch")
    return ok, problems


async def mcp_call(settings, tool: str, args: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    conn = server_connection(settings)
    params = StdioServerParameters(command=conn["command"], args=conn["args"], env=conn["env"], cwd=conn["cwd"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = "".join(getattr(b, "text", "") for b in res.content)
            return json.loads(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-es", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    ws = Workspace(s.workspace)
    cat = Catalog(ws)
    report: dict = {"checks": {}, "problems": []}
    P = report["problems"]

    # 1. no loss / no copies
    fx = fixture_hashes(ws)
    present = workspace_hashes(ws)
    originals = {d["original_path"] for d in cat.load()["documents"]}
    lost, dup = [], []
    for name, h in fx.items():
        if name.startswith("late/"):
            fname = name.split("/", 1)[1]
            added = f"input/{fname}" in originals or (ws.input / fname).exists()
            if not added:
                report.setdefault("late_files_not_added", []).append(fname)
                continue
        if present[h] == 0:
            lost.append(name)
        elif present[h] > 1 and list(fx.values()).count(h) < present[h]:
            dup.append(name)
    report["checks"]["files_present_once"] = not lost and not dup
    if lost:
        P.append(f"lost files: {lost}")
    if dup:
        P.append(f"duplicated files: {dup}")

    # 2. topic folders
    docs = cat.load()["documents"]
    topics = Counter(d["category"] for d in docs if d["category"] and not d["is_empty"] and not d["category"].startswith("_"))
    report["checks"]["topic_folders>=3"] = len(topics) >= 3
    report["topics"] = dict(topics)
    if len(topics) < 3:
        P.append(f"only {len(topics)} topic folders: {dict(topics)}")

    # 3. catalog consistency
    ids = Counter(d["document_id"] for d in docs)
    cat_ok = all(n == 1 for n in ids.values())
    for d in docs:
        p = ws.root / d["current_path"]
        if not p.is_file():
            cat_ok = False; P.append(f"catalog path missing: {d['current_path']}")
            continue
        if file_hash(p) != d["content_hash"]:
            cat_ok = False; P.append(f"hash changed: {d['current_path']}")
        if d["current_path"].startswith("data/") and d["category"] != Catalog.category_of(d["current_path"]):
            cat_ok = False; P.append(f"category mismatch: {d['current_path']}")
    rec = cat.reconcile()
    if rec["unregistered_data_files"]:
        unreg = [f for f in rec["unregistered_data_files"] if not f.split("/")[-1].startswith("_about")]
        if unreg:
            cat_ok = False; P.append(f"unregistered data files: {unreg}")
    report["checks"]["catalog_consistent"] = cat_ok
    report["catalog"] = {"documents": len(docs), "status": dict(Counter(d["indexing_status"] for d in docs))}

    # 4. Elasticsearch via MCP
    if not args.no_es:
        es_ok = True
        indexed = [d for d in docs if d["indexing_status"] in ("indexed", "pending_sync")]
        per_doc = {}
        for d in indexed:
            r = asyncio.run(mcp_call(s, "get_document_excerpts", {"document_id": d["document_id"]}))
            if not r.get("ok"):
                es_ok = False; P.append(f"MCP error: {r.get('error')}"); break
            exs = r["excerpts"]
            doc_ok, doc_problems = check_document_excerpts(d, exs, ws.root)
            es_ok = es_ok and doc_ok
            P.extend(doc_problems)
            per_doc[d["current_path"]] = len(exs)
        report["checks"]["es_consistent"] = es_ok
        report["checks"]["indexed_docs>=3"] = len([v for v in per_doc.values() if v > 0]) >= 3
        report["es"] = {"excerpts_per_document": per_doc}

    out = ROOT / "results" / "verify_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in report["checks"].items():
        print(f"{'OK  ' if v else 'FAIL'} {k}")
    for p in P:
        print("  -", p)
    print(f"report: {out}")
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
