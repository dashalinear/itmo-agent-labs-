import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

LAB3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB3))
sys.path.insert(0, str(LAB3.parent / "lab2" / "tests"))      # PolicyModel / SummaryModel stand-ins for the LLM
os.environ["ES_PREFIX"] = "itmo_lab3_test"

import navsearch  # noqa: E402,F401
from navsearch.config import load  # noqa: E402
from navsearch.embed import Embedder  # noqa: E402
from navsearch.index import CorpusIndex  # noqa: E402
from navsearch.levels import Levels  # noqa: E402
from navsearch.tools_corpus import CorpusEnv, CorpusTools  # noqa: E402
from harness.store import TaskStore  # noqa: E402
from harness.workspace import HarnessFileTools, HarnessWorkspace  # noqa: E402
from organizer.catalog import Catalog  # noqa: E402


def es_available() -> bool:
    import httpx
    try:
        return httpx.get("http://localhost:9200", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not es_available(), reason="Elasticsearch is not running")


def organize_and_index(root: Path, index: CorpusIndex, embedder) -> dict:
    """Deterministic stand-in for the ingestion agent (only for fixtures): moves by input subfolder,
    L1 from the headings, L2 indexing. The real ingestion is done by the agent."""
    ws = HarnessWorkspace(root)
    shutil.copytree(LAB3 / "corpus" / "input", ws.input, dirs_exist_ok=True)
    cat = Catalog(ws)
    fs = HarnessFileTools(ws, cat, 10 ** 6)
    for p in sorted(ws.input.rglob("*")):
        if p.is_file():
            topic = p.relative_to(ws.input).parts[0]
            topic = "lab1_notes" if topic == "inbox_lab1" else topic
            prefix = p.parent.name + "_" if p.parent.name in ("courses", "meetings") else ""
            fs.move(ws.rel(p), f"data/{topic}/{prefix}{p.name}")
    store = TaskStore(ws.service / "state.db")
    store.create_task("FIX", "ingest", "fixture", {"root": str(root)}, {})
    env = CorpusEnv(ws, cat, Levels(ws, cat), index, embedder, store, "FIX")
    ct = CorpusTools(env)
    for d in cat.load()["documents"]:
        o = ct.doc_outline(d["current_path"])
        if o["status"] == "empty":
            continue
        secs = o["suggested_sections"] or [{"title": "Текст", "start_line": 1, "end_line": o["lines"]}]
        ct.write_l1(d["document_id"], o["path"].split("/")[-1], "описание", "Документ " + o["path"], secs)
        ct.index_document(d["document_id"])
    index.refresh()
    store.close()
    return {"ws": ws}


@pytest.fixture(scope="session")
def embedder():
    return Embedder()


@pytest.fixture(scope="session")
def corpus(tmp_path_factory, embedder):
    root = tmp_path_factory.mktemp("lab3") / "workspace"
    idx = CorpusIndex(prefix="itmo_lab3_test")
    idx.drop()
    idx.ensure()
    organize_and_index(root, idx, embedder)
    yield {"root": root, "index": idx}
    idx.drop()


@pytest.fixture
def env(corpus, embedder):
    ws = HarnessWorkspace(corpus["root"])
    cat = Catalog(ws)
    store = TaskStore(ws.service / "state.db")
    import uuid
    tid = "T" + uuid.uuid4().hex[:6]
    store.create_task(tid, "main", "test", {"root": str(ws.root)}, {})
    e = CorpusEnv(ws, cat, Levels(ws, cat), corpus["index"], embedder, store, tid)
    yield e
    store.close()


@pytest.fixture
def cfg(corpus, tmp_path):
    c = load()
    return replace(c, root=corpus["root"], traces_dir=tmp_path / "traces", results_dir=tmp_path / "results",
                   context_window=262144, token_count_mode="heuristic", crash_at="")
