"""Export the compiled graph of the prebuilt agent (Mermaid) to docs/graph.mmd.

The graph topology does not depend on the LLM, so a scripted model is used.
    python scripts/export_graph.py
"""
import tempfile
from dataclasses import replace
from pathlib import Path

import _common  # noqa: F401
from _common import ROOT

from organizer.agent import build_agent
from organizer.config import load_settings
from organizer.mcp_client import unavailable_tools
from organizer.testing import ScriptedChatModel

s = replace(load_settings(), workspace=Path(tempfile.mkdtemp()) / "ws")
b = build_agent(s, ScriptedChatModel(steps=[]), unavailable_tools("graph export"))
g = b.agent.get_graph()
mmd = g.draw_mermaid()
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "graph.mmd").write_text(mmd, encoding="utf-8")
print(mmd)
print("nodes:", list(g.nodes))
print("edges:", [(e.source, e.target, e.conditional) for e in g.edges])
