#!/usr/bin/env python3
"""Portable Codex hook: rely on the user's global hook when installed."""
from __future__ import annotations
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

try:
    if (Path.home() / "ai-rules" / "tools" / "worklog_codex.py").is_file():
        sys.exit(0)  # PC has global Codex hook; avoid double processing.
    os.environ["WORKLOG_PROVIDER"] = "codex"
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, timeout=5)
    if p.returncode:
        sys.exit(0)
    repo = Path(p.stdout.decode("utf-8", "replace").strip()).resolve()
    engine = repo / ".claude" / "worklog" / "engine.py"
    if engine.is_file():
        runpy.run_path(str(engine), run_name="__main__")
except Exception as exc:
    print(json.dumps({"systemMessage": "Codex work-log hook skipped: " + str(exc)[:140]}, ensure_ascii=False))

