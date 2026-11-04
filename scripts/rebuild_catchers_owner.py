#!/usr/bin/env python3
"""Owner rebuild entrypoint — unique process name so competing pkills miss us."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
runpy.run_path(str(ROOT / "scripts" / "build_catalog.py"), run_name="__main__")
