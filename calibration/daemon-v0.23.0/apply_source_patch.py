#!/usr/bin/env python3
"""Use the shared strict source patcher; no build or deployment."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[1]/'common/apply_source_patch.py'),run_name='__main__')
