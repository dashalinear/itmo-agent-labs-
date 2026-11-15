"""Lab 2 harness around the lab 1 ReAct file-organizer agent.

The lab 1 package (``organizer``, ``mcp_es``) is reused as is: it is put on
sys.path here, so lab 2 imports the same tools, catalog, skills and MCP client.
"""
from __future__ import annotations

import sys
from pathlib import Path

LAB2_ROOT = Path(__file__).resolve().parent.parent
LAB1_ROOT = LAB2_ROOT.parent / "lab1"
if str(LAB1_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB1_ROOT))

__version__ = "2.0.0"
