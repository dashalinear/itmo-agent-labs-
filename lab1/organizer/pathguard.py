"""Workspace containment: every file tool resolves paths through this module.

Rules:
* Paths are interpreted relative to the workspace root; absolute paths are
  accepted only if they resolve inside the workspace.
* The path is normalized and symlinks are resolved (Path.resolve) BEFORE the
  containment check, so both ``../`` and symlink escapes are rejected.
* The service catalog (catalog.json) is protected from generic write/move.
"""
from __future__ import annotations

import os
from pathlib import Path

from .results import ToolError

CATALOG_NAME = "catalog.json"


def _plain(p: Path) -> Path:
    """Drop the Windows extended-length prefix. Path.resolve() on Windows can return '\\\\?\\D:\\...' for a path
    whose parent directory is being created concurrently (parallel moves into a new folder); without this the
    containment check would wrongly report the path as outside the workspace."""
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + s[8:])
    if s.startswith("\\\\?\\"):
        return Path(s[4:])
    return p


class Workspace:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)
        self.input = self.root / "input"
        self.data = self.root / "data"
        self.catalog_path = self.root / CATALOG_NAME
        self.input.mkdir(exist_ok=True)
        self.data.mkdir(exist_ok=True)

    # -- resolution -------------------------------------------------------
    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        if path is None or str(path).strip() == "":
            raise ToolError("invalid_path", "Path must be a non-empty string")
        raw = str(path).strip().replace("\\", "/")
        if "\x00" in raw:
            raise ToolError("invalid_path", "Path contains a NUL byte")
        candidate = Path(raw)
        if not candidate.is_absolute():
            # tolerate a leading "workspace/" prefix used in prompts
            parts = candidate.parts
            if parts and parts[0] == "workspace":
                candidate = Path(*parts[1:]) if len(parts) > 1 else Path(".")
            candidate = self.root / candidate
        # strict=False: allow non-existing targets, but resolve every existing
        # component including symlinks.
        resolved = _plain(candidate.resolve(strict=False))
        if not self.contains(resolved):
            raise ToolError(
                "outside_workspace",
                f"Access outside the workspace is forbidden: {path}",
                path=str(path),
            )
        if must_exist and not resolved.exists():
            raise ToolError("not_found", f"No such file or directory: {path}", path=str(path))
        return resolved

    def lexical(self, path: str) -> Path:
        """Normalized path WITHOUT resolving symlinks (to inspect the link itself)."""
        raw = str(path).strip().replace("\\", "/")
        candidate = Path(raw)
        if not candidate.is_absolute():
            parts = candidate.parts
            if parts and parts[0] == "workspace":
                candidate = Path(*parts[1:]) if len(parts) > 1 else Path(".")
            candidate = self.root / candidate
        return Path(os.path.normpath(candidate))

    def contains(self, resolved: Path) -> bool:
        try:
            _plain(resolved).relative_to(self.root)
            return True
        except ValueError:
            return False

    def rel(self, p: Path) -> str:
        """Workspace-relative POSIX path ("." for the root)."""
        try:
            r = _plain(p.resolve(strict=False)).relative_to(self.root).as_posix()
        except ValueError:  # link pointing outside: report its lexical location
            r = Path(os.path.normpath(p)).relative_to(self.root).as_posix()
        return r or "."

    # -- classification ---------------------------------------------------
    def is_under(self, p: Path, area: Path) -> bool:
        try:
            p.relative_to(area)
            return p != area
        except ValueError:
            return False

    def is_catalog(self, p: Path) -> bool:
        return p == self.catalog_path

    @staticmethod
    def is_link(p: Path) -> bool:
        """Symlink or (Windows) directory junction."""
        return os.path.islink(p) or bool(getattr(os.path, "isjunction", lambda _: False)(p))

    def is_symlink_escape(self, p: Path) -> bool:
        """True for a symlink/junction whose target lies outside the workspace."""
        if not self.is_link(p):
            return False
        return not self.contains(Path(os.path.realpath(p)))
