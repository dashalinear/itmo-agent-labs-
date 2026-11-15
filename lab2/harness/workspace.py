"""Task root guard: lab 1 Workspace + a protected service area.

The task root is fixed per task (``tasks.spec.root``); every file tool of lab 1
and every harness tool resolves paths through ``HarnessWorkspace.resolve``:
normalization + symlink/junction resolution + containment check (lab 1), plus
``.harness/`` (state.db, checkpoints, artifacts, history archive) is closed for
generic read/write/move - it is reachable only through dedicated harness tools.
"""
from __future__ import annotations

from pathlib import Path

from organizer.catalog import Catalog
from organizer.pathguard import Workspace
from organizer.results import ToolError
from organizer.tools_fs import FileTools

SERVICE_DIR = ".harness"


class HarnessWorkspace(Workspace):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.service = self.root / SERVICE_DIR
        self.service.mkdir(exist_ok=True)

    def in_service(self, p: Path) -> bool:
        return p == self.service or self.is_under(p, self.service)

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        p = super().resolve(path, must_exist=False)
        if self.in_service(p):
            raise ToolError("protected", f"{SERVICE_DIR}/ is the harness service area (state, checkpoints, artifacts); "
                                         "use read_artifact / search_artifact / task_progress instead", path=str(path))
        if must_exist and not p.exists():
            raise ToolError("not_found", f"No such file or directory: {path}", path=str(path))
        return p

    def service_path(self, *parts: str) -> Path:
        """Internal (harness-only) path inside .harness/, verified to stay in the task root."""
        p = self.service.joinpath(*parts).resolve(strict=False)
        if not self.in_service(p):
            raise ToolError("outside_workspace", f"service path escapes the task root: {parts}")
        return p


class HarnessFileTools(FileTools):
    """Lab 1 file tools; results never include the service area."""

    def __init__(self, ws: HarnessWorkspace, catalog: Catalog, max_chars: int) -> None:
        super().__init__(ws, catalog, max_chars=max_chars)
        self.hws = ws

    def grep(self, *args, **kwargs) -> dict:
        res = super().grep(*args, **kwargs)
        prefix = SERVICE_DIR + "/"
        kept = [m for m in res["matches"] if not m["path"].startswith(prefix)]
        res["matches"], res["returned"] = kept, len(kept)
        res["skipped"] = [s for s in res["skipped"] if not str(s.get("path", "")).startswith(prefix)]
        return res

    def ls(self, path: str = ".") -> dict:
        res = super().ls(path)
        for e in res["entries"]:
            if e["name"] == SERVICE_DIR and res["path"] == ".":
                e["service"] = True
                e["note"] = "harness state; not accessible to file tools"
        return res
