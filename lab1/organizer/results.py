"""Uniform success/error envelope returned by every local tool.

Every tool returns a JSON string:
  {"ok": true,  "tool": "<name>", "data": {...}}
  {"ok": false, "tool": "<name>", "error": {"type": "<code>", "message": "...", ...}}
Errors are observations for the model, never exceptions.
"""
from __future__ import annotations

import json
from typing import Any


class ToolError(Exception):
    """Raised inside tool implementations; converted to an error envelope."""

    def __init__(self, type_: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.type = type_
        self.message = message
        self.extra = extra


def ok(tool: str, **data: Any) -> str:
    return json.dumps({"ok": True, "tool": tool, "data": data}, ensure_ascii=False)


def err(tool: str, type_: str, message: str, **extra: Any) -> str:
    payload = {"type": type_, "message": message, **extra}
    return json.dumps({"ok": False, "tool": tool, "error": payload}, ensure_ascii=False)


def run_tool(tool: str, fn, *args, **kwargs) -> str:
    """Execute fn and wrap its dict result or ToolError into the envelope."""
    try:
        data = fn(*args, **kwargs)
        return ok(tool, **data)
    except ToolError as e:
        return err(tool, e.type, e.message, **e.extra)
    except UnicodeDecodeError:
        return err(tool, "not_utf8", "File is not valid UTF-8 text")
    except PermissionError as e:
        return err(tool, "permission_denied", str(e))
    except OSError as e:
        return err(tool, "os_error", f"{type(e).__name__}: {e}")


def parse(result: Any) -> dict | None:
    """Parse an envelope from a ToolMessage content (str or list of blocks)."""
    if isinstance(result, list):
        result = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in result
        )
    if not isinstance(result, str):
        return None
    try:
        value = json.loads(result)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
