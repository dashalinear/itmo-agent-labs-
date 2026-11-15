"""Helpers for UTF-8 text, line handling and content hashing."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def read_text(path: Path) -> str:
    """Read UTF-8 text (BOM tolerated). Raises UnicodeDecodeError for binary."""
    return path.read_bytes().decode("utf-8-sig")


def split_lines(text: str) -> list[str]:
    """Split into lines without terminators; '' -> []."""
    return text.splitlines()


def lines_range(path: Path, start: int, end: int) -> str:
    """Exact text of inclusive 1-based line range, joined with '\n'."""
    lines = split_lines(read_text(path))
    return "\n".join(lines[start - 1 : end])


def clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True
