#!/usr/bin/env python3
"""GitHub Actions work-log presence and format check for a single push/PR."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine

def git(*args):
    p = subprocess.run(["git", "-c", "core.quotepath=false", *args], text=True, capture_output=True, encoding="utf-8")
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()

def main():
    event = os.environ.get("GITHUB_EVENT_NAME")
    before = os.environ.get("GITHUB_EVENT_BEFORE", "")
    if event == "pull_request":
        base = os.environ.get("GITHUB_BASE_REF", "main")
        git("fetch", "-q", "origin", base)
        names = git("diff", "--name-only", "origin/" + base + "...HEAD")
    elif before and before != "0" * 40:
        names = git("diff", "--name-only", before, "HEAD")
    else:
        names = git("show", "--format=", "--name-only", "HEAD")
    changed = [n.strip().replace("\\", "/") for n in names.splitlines() if n.strip()]
    substantive = [n for n in changed if not n.startswith("work-log/")]
    logs = [n for n in changed if engine.is_log(n) and Path(n).is_file()]
    if substantive and not logs:
        print("ERROR: Changes have no work-log in this push/PR")
        return 1
    errors = []
    for name in logs:
        errors += [name + ": " + err for err in engine.validate_log(Path(name), Path.cwd().name, sid="")]
    if errors:
        print("ERROR: invalid work-log:\n" + "\n".join(errors[:15]))
        return 1
    print("PASS: %d changed paths and %d valid work-log(s)" % (len(substantive), len(logs)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
