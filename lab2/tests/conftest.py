import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2))
sys.path.insert(0, str(LAB2 / "scripts"))

import harness  # noqa: E402,F401
from harness.artifacts import ArtifactStore  # noqa: E402
from harness.budget import Budget  # noqa: E402
from harness.config import Limits, load_config  # noqa: E402
from harness.store import TaskStore  # noqa: E402
from harness.workspace import HarnessWorkspace  # noqa: E402
from organizer.catalog import Catalog  # noqa: E402

HANDBOOK = "data/search/search_engineering_handbook.md"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "ws"
    (r / "data" / "search").mkdir(parents=True)
    for f in (LAB2 / "corpus").glob("*.md"):
        shutil.copy2(f, r / "data" / "search" / f.name)
    ws = HarnessWorkspace(r)
    cat = Catalog(ws)
    for f in sorted((r / "data" / "search").glob("*.md")):
        cat.register_existing(f"data/search/{f.name}")
    (tmp_path / "outside_notes.txt").write_text("OUTSIDE secret", encoding="utf-8")
    return r


@pytest.fixture
def cfg(root, tmp_path):
    c = load_config(root=root)
    return replace(c, traces_dir=tmp_path / "traces", results_dir=tmp_path / "results", context_window=32768,
                   token_count_mode="heuristic", crash_at="")


@pytest.fixture
def ws(root):
    return HarnessWorkspace(root)


@pytest.fixture
def store(ws):
    s = TaskStore(ws.service / "state.db")
    s.create_task("T", "local", "test request", {"root": str(ws.root), "scope": ["data/search"],
                                                 "constraints": ["[C1] test"]}, Limits().to_dict())
    yield s
    s.close()


@pytest.fixture
def limits():
    return Limits()


@pytest.fixture
def budget(store, limits):
    return Budget(store, "T", limits)


@pytest.fixture
def artifacts(ws, store):
    return ArtifactStore(ws, store, "T", max_chars=5000)


@pytest.fixture(scope="session")
def web():
    import fixture_web
    fixture_web.SLOW_SECONDS = 6.0
    srv, base = fixture_web.start(0)
    yield base, fixture_web.Handler
    srv.shutdown()


def can_symlink(tmp_path) -> bool:
    try:
        (tmp_path / "_l").symlink_to(tmp_path)
        (tmp_path / "_l").unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def make_junction(link: Path, target: Path) -> bool:
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return True
    except Exception:  # noqa: BLE001
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            return False


def es_available(url="http://localhost:9200") -> bool:
    import httpx
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False
