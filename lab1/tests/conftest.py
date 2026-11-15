import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from organizer.catalog import Catalog  # noqa: E402
from organizer.config import load_settings  # noqa: E402
from organizer.pathguard import Workspace  # noqa: E402
from organizer.tools_fs import FileTools  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "data").mkdir(parents=True)
    shutil.copytree(ROOT / "fixtures" / "input", ws / "input")
    s = load_settings()
    return replace(s, workspace=ws, traces_dir=tmp_path / "traces",
                   es_index="itmo_lab1_test_" + tmp_path.name.lower().replace("-", "_")[-20:])


@pytest.fixture
def ws(settings):
    return Workspace(settings.workspace)


@pytest.fixture
def catalog(ws):
    return Catalog(ws)


@pytest.fixture
def fs(ws, catalog):
    return FileTools(ws, catalog, max_chars=2000)


def can_symlink(tmp_path) -> bool:
    try:
        (tmp_path / "_l").symlink_to(tmp_path)
        (tmp_path / "_l").unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def es_available(url="http://localhost:9200") -> bool:
    import httpx
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except Exception:
        return False
