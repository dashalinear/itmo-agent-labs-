"""Reset workspace/ from fixtures and (optionally) recreate the ES index.

    python scripts/prepare_workspace.py              # fresh workspace (input = fixtures/input)
    python scripts/prepare_workspace.py --reset-index  # also drop & recreate the teaching index
"""
import argparse
import shutil

import _common  # noqa: F401
from _common import ROOT

from organizer.config import load_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset-index", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    ws = s.workspace
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "data").mkdir(parents=True)
    shutil.copytree(ROOT / "fixtures" / "input", ws / "input")
    files = sorted(p.relative_to(ws).as_posix() for p in (ws / "input").rglob("*") if p.is_file())
    print(f"workspace reset: {ws}\n{len(files)} files in input:")
    for f in files:
        print("  ", f)
    print("data/ is empty; catalog.json will be created on the first move.")
    if args.reset_index:
        from mcp_es.store import ExcerptStore
        st = ExcerptStore(s.es_url, s.es_index)
        st.drop_index()
        st.ensure_index()
        print(f"Elasticsearch index '{s.es_index}' recreated at {s.es_url}")


if __name__ == "__main__":
    main()
