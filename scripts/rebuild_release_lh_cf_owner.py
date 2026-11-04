#!/usr/bin/env python3
"""Owner release LH+CF fill — unique process name so competing pkills miss us."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PI_PITCHERS_ONLY"] = "1"
os.environ["PI_RELEASE_FILL"] = "1"
os.environ.setdefault("PI_MAX_EVENTS_P", "1000")
os.environ.setdefault("PI_SKIP_TEAMS", "SAN")
os.environ["PYTHONUNBUFFERED"] = "1"
sys.argv = [str(ROOT / "scripts" / "build_catalog.py")]
runpy.run_path(str(ROOT / "scripts" / "build_catalog.py"), run_name="__main__")
