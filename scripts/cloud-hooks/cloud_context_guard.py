#!/usr/bin/env python3
"""Cloud-only, zero-model-call context guard for Claude Code command hooks.

Source of truth: ~/ai-rules/tools/cloud_context_guard.py
Copies live in each repository's scripts/cloud-hooks/ directory.

`pre` rewrites eligible Bash commands and caps Grep results via updatedInput.
`post` replaces large Read and Bash outputs via updatedToolOutput.
No deny/retry, no added Claude model calls, and no additionalContext.

Safety:
* Only runs when CLAUDE_CODE_REMOTE=true. Never overlaps the local Mod.
* Small/explicit-range/binary reads, errors and unsupported outputs pass through.
* Uses Zaivern/ContextZip when available, otherwise a Python-only fallback.
* Changes only what Claude sees; actions already performed remain unchanged.
* Exact ranges can always be read with Read(offset, limit).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MIN_READ_CHARS = 6000
MIN_OUTPUT_CHARS = 16000
MIN_REDUCTION = 0.30
MAX_FOLDED_CHARS = 12000
GREP_CAP = 120
GREP_CONTEXT_CAP = 80
VERSION = "1.1.0"
PASSTHROUGH_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".pdf", ".ipynb", ".zip", ".gz", ".xz", ".7z", ".rar", ".exe",
    ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".otf",
    ".mp3", ".mp4", ".mov", ".wav", ".sqlite", ".db", ".xlsx",
    ".docx", ".pptx",
}
SIGNAL_RE = re.compile(
    r"^\s*(?:#|//|/\*|class\s|def\s|async\s+def\s|function\s|"
    r"export\s|import\s|from\s|interface\s|type\s|const\s|"
    r"@\w|describe\(|test\(|it\(|ERROR\b|WARN(?:ING)?\b|"
    r"FAIL\b|PASS\b|Traceback\b|\[.+\])",
    re.IGNORECASE,
)
ERROR_RE = re.compile(
    r"\b(?:error|failure|failed|warning|fatal|traceback|exception|"
    r"tests? passed|tests? failed|passed|summary)\b", re.IGNORECASE
)
ZAI_READ_RE = re.compile(r"\[context\]\s+read\s+.+?~(\d+)→~(\d+)\s+tok", re.I)
ZAI_TEXT_RE = re.compile(r"\[context\]\s+text\s+.+?~(\d+)→~(\d+)\s+tok", re.I)


def remote_enabled() -> bool:
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"


def find_binary(name: str) -> str | None:
    home_bin = Path.home() / ".local" / "bin" / name
    if home_bin.is_file():
        return str(home_bin)
    return shutil.which(name)


def state_path(session_id: str) -> Path:
    key = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / "claude-cloud-context-guard" / (key + ".json")


def was_read(session_id: str, file_path: str) -> bool:
    try:
        state = json.loads(state_path(session_id).read_text(encoding="utf-8"))
        return file_path in state.get("folded", [])
    except (OSError, ValueError, TypeError):
        return False


def record_read(session_id: str, file_path: str) -> None:
    try:
        p = state_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            obj = {}
        folded = obj.setdefault("folded", [])
        if file_path not in folded:
            folded.append(file_path)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def debug_event(kind: str, original: int, reduced: int) -> None:
    # A compact, model-token-free activity record. Only successful changes
    # are recorded; the status command can prove actual hook execution.
    try:
        p = Path(tempfile.gettempdir()) / "claude-cloud-context-guard" / "metrics.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 100_000:
            # Keep this local diagnostic bounded to a small file.
            p.write_text("", encoding="utf-8")
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "type": kind, "before_chars": original, "after_chars": reduced,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def activity_status() -> dict:
    p = Path(tempfile.gettempdir()) / "claude-cloud-context-guard" / "metrics.jsonl"
    result = {"successful_events": 0, "last_event": None,
              "before_chars": 0, "after_chars": 0, "recent": []}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            result["successful_events"] += 1
            result["last_event"] = event.get("at")
            result["before_chars"] += event.get("before_chars", 0)
            result["after_chars"] += event.get("after_chars", 0)
            result["recent"] = (result["recent"] + [event])[-5:]
    except (OSError, ValueError, TypeError):
        pass
    return result


def pre(data: dict) -> dict | None:
    tool = str(data.get("tool_name") or "")
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return None

    if tool == "Grep":
        requested = ti.get("head_limit")
        has_context = any(int(ti.get(k) or 0) > 0 for k in ("-A", "-B", "-C", "context"))
        cap = GREP_CONTEXT_CAP if has_context else GREP_CAP
        if isinstance(requested, int) and 0 < requested <= cap:
            return None
        updated = {**ti, "head_limit": cap}
        debug_event("Grep:cap", len(str(ti)), len(str(updated)))
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "updatedInput": updated}}

    if tool not in ("Bash", "PowerShell"):
        return None
    cmd = ti.get("command")
    if not isinstance(cmd, str) or not cmd.strip() or ti.get("run_in_background"):
        return None
    # Never reinterpret complex commands, explicit helpers, or intentional raw diffs.
    if re.search(r"(?:^|[\s;&|])(?:contextzip|zai)(?:\s|$)", cmd, re.I):
        return None
    if "--no-compact" in cmd or re.search(r"[;&|><`\n\r]", cmd):
        return None
    if tool == "PowerShell":
        return None  # PowerShell is not present in the default hosted Ubuntu VM.

    contextzip = find_binary("contextzip")
    if not contextzip:
        return None
    try:
        result = subprocess.run(
            [contextzip, "rewrite", cmd], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=2,
        )
        replacement = result.stdout.strip()
        if result.returncode != 0 or not replacement or replacement == cmd.strip():
            return None
        # ContextZip says this is supported; preserve all other input fields.
        updated = {**ti, "command": replacement}
        debug_event("Bash:rewrite", len(cmd), len(replacement))
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "updatedInput": updated}}
    except (OSError, subprocess.SubprocessError):
        return None


def fallback_read_outline(content: str, mode: str) -> str | None:
    lines = content.splitlines()
    if len(lines) < 55:
        return None
    if mode == "auto":
        indices = set(range(min(12, len(lines))))
        for i, line in enumerate(lines):
            if SIGNAL_RE.search(line):
                indices.add(i)
            if len(indices) >= 105:
                break
    else:
        indices = set(range(min(50, len(lines))))
        indices.update(range(max(0, len(lines) - 30), len(lines)))
        for i, line in enumerate(lines):
            if SIGNAL_RE.search(line):
                indices.add(i)
            if len(indices) >= 200:
                break
    result = [
        f"[Cloud Context Guard {VERSION}] {mode}: PARTIAL file view, {len(lines)} total lines. "
        "For exact body text use Read with offset/limit."
    ]
    prev = -1
    for i in sorted(indices):
        if i > prev + 1:
            result.append(f"... omitted source lines {prev + 2}-{i} ...")
        result.append(f"L{i + 1}: {lines[i][:300]}")
        prev = i
        if sum(map(len, result)) > MAX_FOLDED_CHARS - 200:
            result.append("... further outline entries omitted ...")
            break
    if prev < len(lines) - 1:
        result.append("... more source lines omitted ...")
    return "\n".join(result)


def zai_read(file_path: str, mode: str) -> str | None:
    zai = find_binary("zai")
    if not zai:
        return None
    try:
        result = subprocess.run(
            [zai, "context", "read", file_path, "--mode", mode],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=6,
        )
        if result.returncode == 0 and ZAI_READ_RE.search(result.stdout.splitlines()[0]):
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return None


def compact_read(data: dict) -> dict | None:
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict) or any(ti.get(key) is not None for key in ("offset", "limit", "pages")):
        return None
    original = data.get("tool_response")
    if not isinstance(original, dict) or original.get("type") != "text":
        return None
    f = original.get("file")
    if not isinstance(f, dict) or not isinstance(f.get("content"), str):
        return None
    text = f["content"]
    path = str(ti.get("file_path") or f.get("filePath") or "")
    if len(text) < MIN_READ_CHARS or not path or Path(path).suffix.lower() in PASSTHROUGH_EXT:
        return None
    if f.get("truncatedByTokenCap"):
        return None
    session = str(data.get("session_id") or "unknown")
    norm_path = str(Path(path).resolve())
    mode = "slim" if was_read(session, norm_path) else "auto"

    new_text = zai_read(norm_path, mode) or fallback_read_outline(text, mode)
    if not new_text or len(new_text) >= len(text) * (1 - MIN_REDUCTION):
        return None
    new_text = new_text[:MAX_FOLDED_CHARS]
    updated_file = {**f, "content": new_text,
                    "numLines": len(new_text.splitlines()),
                    "totalLines": f.get("totalLines") or len(text.splitlines())}
    # Preserve Read's structured shape; tell Claude this was a partial view.
    updated_file["truncatedByTokenCap"] = True
    update = {**original, "file": updated_file}
    record_read(session, norm_path)
    debug_event("Read:" + mode, len(text), len(new_text))
    return {"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "updatedToolOutput": update}}


def compact_lines(content: str) -> str | None:
    lines = content.splitlines()
    if len(lines) < 130:
        return None
    selected = set(range(min(28, len(lines))))
    selected.update(range(max(0, len(lines) - 50), len(lines)))
    signal = [i for i, line in enumerate(lines) if ERROR_RE.search(line)]
    selected.update(signal[:70])
    output = [f"[Cloud Context Guard {VERSION}] PARTIAL stdout, {len(lines)} original lines. "
              "Errors, head, and tail preserved. Rerun a narrower command for exact details."]
    last = -1
    for i in sorted(selected):
        if i > last + 1:
            output.append(f"... omitted output lines {last + 2}-{i} ...")
        output.append(lines[i])
        last = i
        if sum(map(len, output)) > MAX_FOLDED_CHARS - 200:
            output.append("... further stdout omitted ...")
            break
    return "\n".join(output)


def zai_text(content: str) -> str | None:
    zai = find_binary("zai")
    if not zai:
        return None
    try:
        r = subprocess.run([zai, "context", "text", "-", "--level", "normal"],
                           input=content, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=8)
        if r.returncode == 0 and r.stdout and ZAI_TEXT_RE.search(r.stdout.splitlines()[0]):
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return None


def compact_shell(data: dict) -> dict | None:
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        return None
    cmd = str(ti.get("command") or "")
    if "--no-compact" in cmd or re.match(r"^\s*(?:contextzip|zai)\b", cmd, re.I):
        return None  # This output has already gone through an explicit helper.
    resp = data.get("tool_response")
    if not isinstance(resp, dict):
        return None
    stdout = resp.get("stdout")
    if not isinstance(stdout, str) or len(stdout) < MIN_OUTPUT_CHARS:
        return None
    if resp.get("stderr") or resp.get("interrupted") or resp.get("isImage") or resp.get("backgroundTaskId"):
        return None
    # Do not silently truncate machine-readable JSON, CSV or one-line values.
    s = stdout.lstrip()
    if s.startswith("{") or s.startswith('"') or stdout.count("\n") < 130:
        return None
    if s.startswith("["):
        try:
            json.loads(s)
            return None
        except ValueError:
            pass
    compacted = zai_text(stdout) or compact_lines(stdout)
    if not compacted or len(compacted) >= len(stdout) * (1 - MIN_REDUCTION):
        return None
    compacted = compacted[:MAX_FOLDED_CHARS]
    debug_event("Bash", len(stdout), len(compacted))
    return {"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "updatedToolOutput": {**resp, "stdout": compacted}}}


def post(data: dict) -> dict | None:
    tool = str(data.get("tool_name") or "")
    if tool == "Read":
        return compact_read(data)
    if tool in ("Bash", "PowerShell"):
        return compact_shell(data)
    return None


def handle(action: str, data: dict) -> dict | None:
    if not remote_enabled():
        return None
    if action == "pre":
        return pre(data)
    if action == "post":
        return post(data)
    return None


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps({"version": VERSION, "remote": remote_enabled(),
                          "zai": bool(find_binary("zai")),
                          "contextzip": bool(find_binary("contextzip")),
                          "activity": activity_status()}))
        return
    if not remote_enabled() or len(sys.argv) < 2:
        return
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if not isinstance(event, dict):
            return
        result = handle(sys.argv[1], event)
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except Exception:
        # All errors fail open: a context-saving hook must not break the task.
        return


if __name__ == "__main__":
    main()
