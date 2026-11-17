import asyncio
import os
import sys
from pathlib import Path

import pytest

LAB4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB4))
os.environ["LAB4_ES_PREFIX"] = "itmo_lab4_test"
os.environ["LAB4_ES_INDEX"] = "itmo_lab4_test_excerpts"

import topicflow  # noqa: E402,F401
from topicflow import config as lab4_config  # noqa: E402

from fake_llm import router  # noqa: E402  (deterministic stand-ins for the LLM)


def es_available() -> bool:
    import httpx
    try:
        return httpx.get("http://localhost:9200", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def temporal_available() -> bool:
    import socket
    try:
        with socket.create_connection(("localhost", 7233), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not es_available(), reason="Elasticsearch is not running")


@pytest.fixture
def lab4env(tmp_path):
    from dataclasses import replace
    from topicflow.activities import Env
    os.environ["LAB4_TASK_ROOT"] = str(tmp_path / "workspace")
    cfg = lab4_config.load()
    cfg = replace(cfg, traces_dir=tmp_path / "traces", results_dir=tmp_path / "results", context_window=262144,
                  token_count_mode="heuristic", crash_at="")
    model = router()
    env = Env(cfg, model=model, use_mcp=False, limit_overrides={"working_context_tokens": 120000})
    env.embedder.embed(["прогрев модели"])           # load the embedding model before tool timeouts apply
    env.index.drop()
    env.index.ensure()
    Env._inst = env
    (env.ws.root / "rejected").mkdir(parents=True, exist_ok=True)
    yield env
    env.index.drop()
    env.registry.close()
    Env._inst = None


def put(env, which, names=None, rename=None):
    src = LAB4 / "corpus" / which
    out = []
    for f in sorted(src.glob("*")):
        if names and f.name not in names:
            continue
        dst = env.ws.root / "input" / (rename(f.name) if rename else f.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(f.read_bytes())
        st = dst.stat()
        out.append({"input_path": "input/" + dst.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return out


def run(coro):
    return asyncio.run(coro)
