"""Manual user action during an open CLI session: drop the two prepared files into input/.

Run in a SECOND terminal while `python -m organizer.cli` is running
(or copy fixtures/late/* to workspace/input/ in Explorer).
"""
import shutil

import _common  # noqa: F401
from _common import ROOT

from organizer.config import load_settings

s = load_settings()
for f in sorted((ROOT / "fixtures" / "late").glob("*")):
    dst = s.workspace / "input" / f.name
    if dst.exists():
        print("already present:", dst.name)
    else:
        shutil.copy2(f, dst)
        print("added:", f"input/{f.name}")
