"""Agent Skills: metadata in the system prompt, full SKILL.md on demand.

Layout (Agent Skills spec): skills/<name>/SKILL.md with YAML front matter
containing at least ``name`` and ``description``, followed by the procedure.
Only name + description are put into the system prompt; ``load_skill``
returns the full body when the model decides the skill is relevant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .results import ToolError, run_tool

_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    m = _FM.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML front matter")
    meta: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^[A-Za-z_][\w-]*:", line):
            key, _, value = line.partition(":")
            key = key.strip()
            meta[key] = value.strip().strip('"').strip("'")
        elif key and line.startswith((" ", "\t")):
            meta[key] = (meta[key] + " " + line.strip()).strip()
    for required in ("name", "description"):
        if not meta.get(required):
            raise ValueError(f"{path}: front matter lacks '{required}'")
    if meta["name"] != path.parent.name:
        raise ValueError(f"{path}: name '{meta['name']}' must match folder '{path.parent.name}'")
    return Skill(meta["name"], meta["description"], path, m.group(2).strip())


class SkillRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.skills: dict[str, Skill] = {}
        for p in sorted(root.glob("*/SKILL.md")):
            s = parse_skill(p)
            self.skills[s.name] = s
        self.loaded: list[str] = []  # audit trail of on-demand loads

    def metadata_block(self) -> str:
        lines = [f"- {s.name}: {s.description}" for s in self.skills.values()]
        return "\n".join(lines) if lines else "(no skills installed)"

    def load(self, name: str) -> dict:
        s = self.skills.get(name)
        if not s:
            raise ToolError("unknown_skill", f"No skill '{name}'. Available: {', '.join(self.skills)}")
        self.loaded.append(name)
        return {"name": s.name, "description": s.description, "instructions": s.body}

    def as_tools(self) -> list[BaseTool]:
        class LoadSkillArgs(BaseModel):
            name: str = Field(description="Skill name from the skills catalog in the system prompt")

        return [StructuredTool.from_function(
            func=lambda name: run_tool("load_skill", self.load, name),
            name="load_skill", args_schema=LoadSkillArgs,
            description="Load the full procedure (SKILL.md) of ONE skill when the current request matches "
                        "its description. Load only what you need; do not load all skills.")]
