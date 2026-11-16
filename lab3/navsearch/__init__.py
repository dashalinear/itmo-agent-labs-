"""Lab 3: hybrid navigational search over L0/L1/L2 built on the lab 1 organizer and the lab 2 harness.

lab 1 (organizer, catalog, skills, MCP) and lab 2 (harness) are reused from the sibling folders.
"""
from __future__ import annotations

import sys
from pathlib import Path

LAB3_ROOT = Path(__file__).resolve().parent.parent
LAB2_ROOT = LAB3_ROOT.parent / "lab2"
LAB1_ROOT = LAB3_ROOT.parent / "lab1"
for p in (LAB2_ROOT, LAB1_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import harness  # noqa: E402,F401  (lab 2; also registers lab 1)

__version__ = "3.0.0"
