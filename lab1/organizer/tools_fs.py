"""Seven typed file tools: read, head, tail, grep, ls, write, move.

All paths go through Workspace.resolve (normalization + symlink resolution +
containment check). All results use the envelope from results.py and are
bounded by ``max_chars``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .catalog import Catalog
from .pathguard import Workspace
from .results import ToolError, run_tool
from .textutil import clip, file_hash, read_text, split_lines

GREP_HARD_CAP = 500
LS_CAP = 500


# ---------------------------------------------------------------- schemas
class ReadArgs(BaseModel):
    path: str = Field(description="File path relative to the workspace, e.g. 'input/notes.md'")
    numbered: bool = Field(False, description="Prefix each line with its 1-based number (useful to choose excerpt line ranges)")


class HeadTailArgs(BaseModel):
    path: str = Field(description="File path relative to the workspace")
    n: int = Field(10, description="Number of lines; must be a positive integer")


class GrepArgs(BaseModel):
    pattern: str = Field(description="Literal substring to search for (regex only if regex=true)")
    path: str = Field(".", description="File or folder relative to the workspace")
    recursive: bool = Field(False, description="Descend into subfolders when path is a folder")
    max_matches: int = Field(50, description=f"Maximum matches to return (1..{GREP_HARD_CAP})")
    ignore_case: bool = Field(False, description="Case-insensitive matching")
    regex: bool = Field(False, description="Extension: treat pattern as a Python regular expression")


class LsArgs(BaseModel):
    path: str = Field(".", description="Folder relative to the workspace, e.g. 'input' or 'data/courses'")


class WriteArgs(BaseModel):
    path: str = Field(description="Target file relative to the workspace; parent folders are created")
    content: str = Field(description="UTF-8 text to write")
    mode: Literal["overwrite", "append"] = Field("overwrite", description="overwrite or append")


class MoveArgs(BaseModel):
    source: str = Field(description="File in input/ or data/, relative to the workspace")
    destination: str = Field(
        description="Target file path under data/ (e.g. 'data/courses/ir.md') or an existing "
                    "data folder / a path ending with '/' to keep the file name"
    )


# ---------------------------------------------------------------- tools
class FileTools:
    def __init__(self, ws: Workspace, catalog: Catalog, max_chars: int = 12000) -> None:
        self.ws = ws
        self.catalog = catalog
        self.max_chars = max_chars

    # helpers -------------------------------------------------------------
    def _file(self, path: str) -> Path:
        p = self.ws.resolve(path, must_exist=True)
        if p.is_dir():
            raise ToolError("is_directory", f"Expected a file but got a folder: {path}")
        return p

    @staticmethod
    def _check_n(n) -> int:
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise ToolError("invalid_argument", f"n must be a positive integer, got {n!r}")
        return n

    # read ----------------------------------------------------------------
    def read(self, path: str, numbered: bool = False) -> dict:
        p = self._file(path)
        text = read_text(p)
        lines = split_lines(text)
        body = "\n".join(f"{i}: {l}" for i, l in enumerate(lines, 1)) if numbered else text
        body, truncated = clip(body, self.max_chars)
        res = {"path": self.ws.rel(p), "text": body, "truncated": truncated,
               "total_chars": len(text), "total_lines": len(lines),
               "content_hash": file_hash(p)}
        if truncated:
            res["hint"] = "Output limit reached; use head/tail/grep to read the rest."
        doc = self.catalog.by_path(self.ws.rel(p))
        if doc:
            res["document_id"] = doc["document_id"]
        return res

    def _edge(self, path: str, n: int, from_end: bool) -> dict:
        n = self._check_n(n)
        p = self._file(path)
        lines = split_lines(read_text(p))
        if from_end:
            start = max(len(lines) - n, 0)
            chunk = lines[start:]
        else:
            start = 0
            chunk = lines[:n]
        numbered = [{"line": start + i + 1, "text": t} for i, t in enumerate(chunk)]
        out = "\n".join(chunk)
        out, truncated = clip(out, self.max_chars)
        return {"path": self.ws.rel(p), "n": n, "total_lines": len(lines),
                "returned_lines": len(chunk),
                "first_line": numbered[0]["line"] if numbered else None,
                "last_line": numbered[-1]["line"] if numbered else None,
                "text": out, "truncated": truncated}

    def head(self, path: str, n: int = 10) -> dict:
        return self._edge(path, n, from_end=False)

    def tail(self, path: str, n: int = 10) -> dict:
        return self._edge(path, n, from_end=True)

    # grep ----------------------------------------------------------------
    def grep(self, pattern: str, path: str = ".", recursive: bool = False, max_matches: int = 50,
             ignore_case: bool = False, regex: bool = False) -> dict:
        if not pattern:
            raise ToolError("invalid_argument", "pattern must be non-empty")
        if isinstance(max_matches, bool) or not isinstance(max_matches, int) or max_matches <= 0:
            raise ToolError("invalid_argument", f"max_matches must be a positive integer, got {max_matches!r}")
        max_matches = min(max_matches, GREP_HARD_CAP)
        if regex:
            try:
                rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
            except re.error as e:
                raise ToolError("invalid_regex", str(e))
            match = lambda line: rx.search(line) is not None  # noqa: E731
        else:
            needle = pattern.lower() if ignore_case else pattern
            match = (lambda line: needle in line.lower()) if ignore_case else (lambda line: needle in line)  # noqa: E731

        root = self.ws.resolve(path, must_exist=True)
        files: list[Path] = []
        skipped: list[dict] = []
        if root.is_file():
            files = [root]
        else:
            if recursive:
                for dirpath, dirs, names in os.walk(root, followlinks=False):
                    # os.walk does not follow symlinks but does descend into NTFS junctions: such a directory is
                    # reported as skipped by its own name, its content (outside the workspace) is never listed
                    for d in list(dirs):
                        if self.ws.is_symlink_escape(Path(dirpath) / d):
                            dirs.remove(d)
                            skipped.append({"path": self.ws.rel(Path(dirpath) / d), "reason": "link_outside_workspace"})
                    dirs.sort()
                    for name in sorted(names):
                        files.append(Path(dirpath) / name)
            else:
                files = sorted(p for p in root.iterdir() if p.is_file() or p.is_symlink())

        matches, total = [], 0
        for f in files:
            if self.ws.is_symlink_escape(f) or not self.ws.contains(f.resolve()):
                skipped.append({"path": self.ws.rel(f) if self.ws.contains(f) else str(f), "reason": "symlink_outside_workspace"})
                continue
            if f.resolve() == self.ws.catalog_path or f.suffix == ".tmp":
                continue
            try:
                lines = split_lines(read_text(f))
            except UnicodeDecodeError:
                skipped.append({"path": self.ws.rel(f), "reason": "not_utf8"})
                continue
            for i, line in enumerate(lines, 1):
                if match(line):
                    total += 1
                    if len(matches) < max_matches:
                        matches.append({"path": self.ws.rel(f), "line": i, "text": line[:500]})
        return {"pattern": pattern, "path": self.ws.rel(root), "recursive": recursive,
                "files_scanned": len(files) - len(skipped), "matches": matches,
                "returned": len(matches), "total_matches": total,
                "has_more": total > len(matches), "remaining": total - len(matches),
                "skipped": skipped}

    # ls ------------------------------------------------------------------
    def ls(self, path: str = ".") -> dict:
        d = self.ws.resolve(path, must_exist=True)
        if not d.is_dir():
            raise ToolError("not_a_directory", f"Expected a folder: {path}")
        entries = []
        for child in sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower(), c.name)):
            if child.name.endswith(".json.tmp"):
                continue
            item: dict = {"name": child.name, "path": self.ws.rel(child) if self.ws.contains(child.resolve()) else None}
            if self.ws.is_link(child):
                item["type"] = "symlink"
                if self.ws.is_symlink_escape(child):
                    item["target"] = "outside_workspace (inaccessible)"
                    item["path"] = None
            elif child.is_dir():
                item["type"] = "dir"
                item["items"] = sum(1 for _ in child.iterdir())
            else:
                item["type"] = "file"
                item["size"] = child.stat().st_size
                doc = self.catalog.by_path(self.ws.rel(child))
                if doc:
                    item.update(document_id=doc["document_id"], title=doc["title"],
                                description=doc.get("description", ""),
                                indexing_status=doc["indexing_status"])
            if child.resolve() == self.ws.catalog_path:
                item["service"] = True
            entries.append(item)
        truncated = len(entries) > LS_CAP
        return {"path": self.ws.rel(d), "entries": entries[:LS_CAP], "count": len(entries),
                "truncated": truncated}

    # write ---------------------------------------------------------------
    def write(self, path: str, content: str, mode: str = "overwrite") -> dict:
        if mode not in ("overwrite", "append"):
            raise ToolError("invalid_argument", "mode must be 'overwrite' or 'append'")
        p = self.ws.resolve(path)
        if p == self.ws.root or p.is_dir():
            raise ToolError("is_directory", f"Cannot write to a folder: {path}")
        if self.ws.is_catalog(p):
            raise ToolError("protected", "catalog.json is maintained by code; use catalog_update")
        rel = self.ws.rel(p)
        if p.exists() and self.catalog.by_path(rel):
            raise ToolError("protected", f"{rel} is a catalogued document; organization must not rewrite documents")
        if self.ws.is_under(p, self.ws.input):
            raise ToolError("protected", "input/ is the user's inbox; write new artifacts elsewhere (e.g. data/<folder>/_about.md)")
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        with open(p, "a" if mode == "append" else "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return {"path": rel, "mode": mode, "created": not existed,
                "bytes_written": len(content.encode("utf-8")), "size": p.stat().st_size}

    # move ----------------------------------------------------------------
    @staticmethod
    def _suggest(dst: Path) -> str:
        i = 2
        while True:
            cand = dst.with_name(f"{dst.stem}_{i}{dst.suffix}")
            if not cand.exists():
                return cand.name
            i += 1

    def move(self, source: str, destination: str) -> dict:
        lex = self.ws.lexical(source)
        if self.ws.is_link(lex):
            raise ToolError("symlink_not_allowed", "Symlinks are not moved; move the real file instead")
        src = self.ws.resolve(source, must_exist=True)
        if src.is_dir():
            raise ToolError("is_directory", "move works on single files; move files one by one")
        if not (self.ws.is_under(src, self.ws.input) or self.ws.is_under(src, self.ws.data)):
            raise ToolError("invalid_source", "Source must be inside input/ or data/")
        dst = self.ws.resolve(destination)
        looks_like_dir = (not dst.exists() and dst.suffix == "" and src.suffix != "")
        if destination.replace("\\", "/").endswith("/") or dst.is_dir() or looks_like_dir:
            dst = dst / src.name
        if not self.ws.is_under(dst, self.ws.data):
            raise ToolError("invalid_destination", "Destination must be inside data/")
        if dst.parent == self.ws.data:
            raise ToolError("invalid_destination",
                            "Put documents into a topic folder: data/<topic>/<file> (the folder is the category)")
        if dst == src:
            raise ToolError("same_path", "Source and destination are the same")
        if dst.exists():
            raise ToolError("name_conflict", f"Destination already exists: {self.ws.rel(dst)}; not overwriting",
                            existing=self.ws.rel(dst), suggested_name=self._suggest(dst))
        old_rel = self.ws.rel(src)
        hash_before = file_hash(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)          # fails atomically if dst appeared meanwhile
            os.unlink(src)
        except FileExistsError:
            raise ToolError("name_conflict", f"Destination already exists: {self.ws.rel(dst)}",
                            existing=self.ws.rel(dst), suggested_name=self._suggest(dst))
        except OSError:
            if dst.exists():
                raise ToolError("name_conflict", f"Destination already exists: {self.ws.rel(dst)}",
                                suggested_name=self._suggest(dst))
            os.rename(src, dst)        # no-overwrite on Windows; checked above on POSIX
        hash_after = file_hash(dst)
        verified = (not src.exists()) and dst.is_file() and hash_before == hash_after
        if not verified:
            raise ToolError("verification_failed", "Move could not be verified; check both paths")
        entry = self.catalog.record_move(old_rel, dst)
        self._prune_empty_input_dirs(src.parent)
        duplicates = [{"document_id": d["document_id"], "current_path": d["current_path"]}
                      for d in self.catalog.same_content(entry["content_hash"], exclude_id=entry["document_id"])]
        res = {"old_path": old_rel, "new_path": entry["current_path"],
               "document_id": entry["document_id"], "content_hash": entry["content_hash"],
               "category": entry["category"], "verified": verified,
               "indexing_status": entry["indexing_status"]}
        if duplicates:
            res["warning"] = "Another catalogued document has identical content"
            res["duplicate_of"] = duplicates
        if entry["indexing_status"] == "pending_sync":
            res["action_required"] = ("Document is indexed in Elasticsearch: call MCP update_document_path("
                                      f"document_id='{entry['document_id']}', new_path='{entry['current_path']}')")
        return res

    def _prune_empty_input_dirs(self, d: Path) -> None:
        # parallel moves may empty and remove the same folder concurrently: tolerate the race
        while self.ws.is_under(d, self.ws.input):
            try:
                if not d.is_dir() or any(d.iterdir()):
                    return
                d.rmdir()
            except OSError:
                return
            d = d.parent

    # LangChain wrappers ----------------------------------------------------
    def as_tools(self) -> list[BaseTool]:
        def mk(name, fn, schema, desc):
            return StructuredTool.from_function(
                func=lambda **kw: run_tool(name, fn, **kw),
                name=name, description=desc, args_schema=schema)

        return [
            mk("read", self.read, ReadArgs,
               "cat/read: return the UTF-8 text of a workspace file with path, truncation flag, "
               "total lines and content_hash. Use numbered=true to see line numbers."),
            mk("head", self.head, HeadTailArgs,
               "Return the first n lines of a file (n positive integer; short files return all lines)."),
            mk("tail", self.tail, HeadTailArgs,
               "Return the last n lines of a file in original order; empty file -> empty result."),
            mk("grep", self.grep, GrepArgs,
               "Find a literal substring in a file or folder; returns path, 1-based line and text, "
               "total_matches, has_more/remaining. regex=true is an extension."),
            mk("ls", self.ls, LsArgs,
               "List one folder level (name, type, size; catalog info for documents) in stable order. "
               "Always call again to see newly added files."),
            mk("write", self.write, WriteArgs,
               "Write UTF-8 text (overwrite|append) to a NEW artifact inside the workspace, e.g. folder "
               "descriptions data/<folder>/_about.md. Cannot modify catalogued documents, input/ or catalog.json."),
            mk("move", self.move, MoveArgs,
               "Move ONE file from input/ to data/ or between data/ folders, creating parent folders. "
               "Never overwrites: returns name_conflict with suggested_name. Registers/updates the "
               "document in the catalog (stable document_id) and reports if the ES index needs a path sync."),
        ]
