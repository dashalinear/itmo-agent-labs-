"""Admin: recreate the teaching index from results/index_export.jsonl (reproducible input for lab 3).

    python scripts/restore_index.py [--file results/index_export.jsonl]
"""
import argparse
import json

import _common  # noqa: F401
from _common import ROOT

from mcp_es.schema import Excerpt
from mcp_es.store import ExcerptStore
from organizer.config import load_settings

ap = argparse.ArgumentParser()
ap.add_argument("--file", default=str(ROOT / "results" / "index_export.jsonl"))
a = ap.parse_args()
s = load_settings()
st = ExcerptStore(s.es_url, s.es_index)
st.drop_index()
st.ensure_index()
with open(a.file, encoding="utf-8") as f:
    rows = [Excerpt(**json.loads(line)) for line in f if line.strip()]
res = st.upsert(rows, mode="merge")
print(f"index '{s.es_index}' recreated: created={res['created']} updated={res['updated']} total={st.count()}")
