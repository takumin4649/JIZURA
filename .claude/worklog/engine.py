#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable Claude Code work-log lifecycle.

Uses RECORDING.md section 3, session snapshots, safe Git staging, and
Stop decision=block only when the active session left unlogged changes.
Does not call an AI API. The current Claude turn writes qualitative records.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HEADINGS = (
    "作業内容",
    "使った指示・道具",
    "試したこと（失敗→修正）",
    "前後比較",
    "決定事項",
    "数字",
    "素材（あとで記事・教材にできそうなもの）",
    "変更ファイル",
    "未解決事項",
    "次のアクション",
)
META = ("date", "project", "実行モデル", "provider", "session", "transcript")
TEXT_EXT = {
    ".md", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".sh", ".ps1",
    ".bat", ".html", ".css", ".sql", ".xml", ".gitignore", ".gitmodules",
    ".mjs", ".cjs", ".go", ".rs", ".java", ".c", ".h", ".cpp",
}
BLOCK_PARTS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__",
               "secrets", "credentials", ".ssh", ".aws", ".codex"}
BLOCK_TERMS = ("secret", "password", "credential", "api_key", "apikey", ".env",
               "private", "id_rsa", "id_ed25519", ".bak", ".log", ".lock",
               ".pem", ".key", "cookie", "session", "token")
SENSITIVE = (
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    rb"github_pat_[A-Za-z0-9_]{20,}",
    rb"gh[pousr]_[A-Za-z0-9_]{30,}",
    rb"sk-(?:ant|proj|live)-[A-Za-z0-9_-]{16,}",
    rb"AKIA[A-Z0-9]{16}",
    rb"(?i)(?:password|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9_./+=-]{16,}",
)
MAX_FILES = 40
MAX_BYTES_PER_FILE = 800_000
MAX_BYTES_TOTAL = 3_000_000
STATE_TTL = 12 * 3600

def command(repo: Path, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout)

def stdout(repo: Path, *args: str) -> str:
    proc = command(repo, *args)
    if proc.returncode:
        raise RuntimeError("git " + " ".join(args) + ": " + proc.stderr.decode("utf-8", "replace")[:150])
    return proc.stdout.decode("utf-8", "replace").strip()

def repo_for(cwd: str | None) -> Path | None:
    if not cwd:
        return None
    folder = Path(cwd).resolve()
    proc = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
                          capture_output=True)
    if proc.returncode:
        return None
    result = Path(proc.stdout.decode("utf-8", "replace").strip()).resolve()
    return result if (result / ".git").exists() else None

def status(repo: Path) -> dict[str, str]:
    proc = command(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if proc.returncode:
        raise RuntimeError("Cannot read Git status")
    raw = proc.stdout.split(b"\0")
    result = {}
    i = 0
    while i < len(raw):
        cell = raw[i]
        i += 1
        if not cell:
            continue
        code = cell[:2].decode("ascii", "replace")
        path = cell[3:].decode("utf-8", "replace").replace("\\", "/")
        if "R" in code or "C" in code:
            if i < len(raw):
                i += 1
            result[path] = code + ":renamed"
        else:
            result[path] = code
    return result

def digest(repo: Path, path: str, code: str) -> str:
    target = repo / path
    if "D" in code or not target.exists():
        return code + ":missing"
    if target.is_symlink():
        return code + ":symlink"
    try:
        st = target.stat()
        if not target.is_file():
            return code + ":notfile"
        return "%s:%s:%s" % (code, st.st_size, st.st_mtime_ns)
    except OSError:
        return code + ":unknown"

def fingerprint(repo: Path) -> dict[str, str]:
    return {p: digest(repo, p, code) for p, code in status(repo).items()}

def state_folder(repo: Path) -> Path:
    # No repository modifications for session state.
    key = hashlib.sha256(str(repo).lower().encode("utf-8")).hexdigest()[:20]
    folder = Path.home() / ".claude" / "worklog-auto" / key
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def state_path(repo: Path, sid: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", sid)[:90]
    return state_folder(repo) / (safe_id + ".json")

def read_state(repo: Path, sid: str) -> dict | None:
    p = state_path(repo, sid)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

def write_state(repo: Path, sid: str, payload: dict):
    path = state_path(repo, sid)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def note(repo: Path, message: str):
    try:
        p = state_folder(repo) / "events.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message[:500] + "\n")
    except OSError:
        pass

def active_others(repo: Path, sid: str) -> bool:
    now = time.time()
    for path in state_folder(repo).glob("*.json"):
        if path.name == state_path(repo, sid).name:
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if item.get("active") and now - float(item.get("time", 0)) < 3600:
            return True
    return False

def delta(before: dict, after: dict) -> list[str]:
    return sorted(p for p in after if before.get(p) != after[p])

def is_log(path: str) -> bool:
    return bool(re.fullmatch(r"work-log/\d{4}-\d{2}-\d{2}_[^/]+\.md", path))

def validate_log(path: Path, project: str, sid: str) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ["work-logをUTF-8で読み取れません"]
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not match:
        return ["メタデータ形式がありません"]
    header = match.group(1)
    problems = []
    for name in META:
        if not re.search(r"(?m)^" + re.escape(name) + r":\s*(.*)$", header):
            problems.append(name + "の欄がありません")
    date = re.search(r"(?m)^date:\s*(\d{4}-\d{2}-\d{2})\s*$", header)
    if not date or not path.name.startswith(date.group(1) + "_"):
        problems.append("dateとファイル名が不一致")
    proj = re.search(r"(?m)^project:\s*(.+)$", header)
    if not proj or not proj.group(1).strip():
        problems.append("projectが空")
    ses = re.search(r"(?m)^session:\s*(.*)$", header)
    if sid and ses and ses.group(1).strip() not in (sid, sid[:8]):
        problems.append("別セッションのwork-logです")
    positions = []
    for name in HEADINGS:
        pattern = r"(?ms)^## " + re.escape(name) + r"\s*\n(.*?)(?=^## |\Z)"
        group = re.search(pattern, text)
        if group is None or not group.group(1).strip():
            problems.append("内容なし: " + name)
        else:
            positions.append(group.start())
    if positions != sorted(positions):
        problems.append("見出しの順番が違います")
    work_section = re.search(r"(?ms)^## 作業内容\s*\n(.*?)(?=^## |\Z)", text)
    if not work_section or not re.search(r"(?m)^\s*-\s*進捗[：:]", work_section.group(1)):
        problems.append("作業内容の進捗欄がありません")
    comp = re.search(r"(?ms)^## 前後比較\s*\n(.*?)(?=^## |\Z)", text)
    if comp:
        value = comp.group(1)
        if not (re.search(r"\bBefore[：:]", value) and re.search(r"\bAfter[：:]", value)) and not re.search(r"比較不要[：:]\s*\S+", value):
            problems.append("前後比較の実測または比較不要の理由が必要")
    if project == "dmm-fanza" and not re.search(r"(?m)^段階:\s*\S+", header):
        problems.append("dmm-fanzaの段階がありません")
    return problems

def safe_path(repo: Path, path: str, code: str) -> tuple[bool, int]:
    parts = path.split("/")
    name = parts[-1].lower()
    if ".." in parts or any(p.lower() in BLOCK_PARTS for p in parts):
        return False, 0
    if any(term in name for term in BLOCK_TERMS):
        return False, 0
    if name.startswith(".") and name not in (".gitignore", ".gitmodules"):
        return False, 0
    if Path(name).suffix not in TEXT_EXT and name not in (".gitignore", ".gitmodules"):
        return False, 0
    if code not in (" M", "??"):
        return False, 0
    path_obj = repo / path
    try:
        if path_obj.is_symlink() or not path_obj.is_file():
            return False, 0
        actual = path_obj.resolve()
        if repo not in actual.parents or actual.stat().st_size > MAX_BYTES_PER_FILE:
            return False, 0
        data = actual.read_bytes()
    except OSError:
        return False, 0
    if b"\x00" in data or any(re.search(pattern, data) for pattern in SENSITIVE):
        return False, 0
    return True, len(data)

def instruct(repo: Path, sid: str, reason: str, data: dict):
    source = Path.home() / "ai-rules" / "RECORDING.md"
    portable = repo / ".claude" / "worklog" / "RECORDING.md"
    canonical = source if source.is_file() else portable
    fallback = repo / "AGENTS.md"
    rule = str(canonical if canonical.is_file() else fallback)
    transcript = data.get("transcript_path") or "未取込"
    provider = os.environ.get("WORKLOG_PROVIDER", "claude-code")
    return (
        "ワークログを自動完成させてから返答してください。理由: " + reason + "。"
        "必ず正本 " + rule + " の第3章と各リポジトリのAGENTS.md/CLAUDE.mdを確認。"
        "今の作業に対応するwork-log/YYYY-MM-DD_プロジェクト名_題.mdを新規作成・追記し、"
        "既定のメタ情報と10見出しを正しい順番で全て記入。"
        "作業内容に「- 進捗：前→後」を記載。目的、実行した操作、試行錯誤、Before/Afterの実データ、"
        "採用・不採用の理由、数字と取得元、素材、変更ファイル、残課題、次の手を記録。"
        "ユーザー指示の原文は改変しない。原文は会話の全文を正本とする。"
        "証拠不足は未確認と明記し、成功を創作しない。"
        "provider: " + provider + "、session: " + sid + "、transcript: " + str(transcript) + "。"
        "実行モデルは現在のモデル名。FANZAでは独自の段階も記録。"
        "ワークログを書いたら同じ作業に属する安全な変更だけをコミット・pushする。"
        "別のセッションの変更や未確認ファイルは含めない。"
    )

def github_remote(repo: Path) -> bool:
    try:
        url = stdout(repo, "remote", "get-url", "origin")
    except RuntimeError:
        return False
    return bool(re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)[\w.-]+/[\w.-]+(?:\.git)?", url))

def sync(repo: Path, changed: list[str], baseline: dict, sid: str) -> str:
    """Push only the safely attributable work-tree edits on an existing tracked branch."""
    if os.environ.get("WORKLOG_AUTO_PUSH", "1") == "0":
        return "自動pushは無効設定"
    if not github_remote(repo):
        return "originが対象外"
    if not changed:
        return "差分なし"
    current = status(repo)
    if len(changed) > MAX_FILES:
        return "変更ファイル多数のため保留"
    if command(repo, "diff", "--cached", "--quiet").returncode != 0:
        return "すでにステージされた別変更があるため保留"
    # Avoid accidentally including another session's dirty files in the same commit.
    total = 0
    for path in changed:
        safe, n = safe_path(repo, path, current.get(path, ""))
        if not safe:
            return "送信を保留（安全性未確認: " + path + "）"
        total += n
    if total > MAX_BYTES_TOTAL:
        return "合計サイズ上限で保留"
    branch = stdout(repo, "branch", "--show-current")
    if not branch or branch == "HEAD":
        return "detached HEADのため保留"
    upstream = command(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    if upstream.returncode or upstream.stdout.decode("utf-8", "replace").strip() != "origin/" + branch:
        return "追跡するoriginブランチなし"
    fetch = command(repo, "fetch", "-q", "origin", branch, timeout=25)
    if fetch.returncode:
        return "fetch失敗"
    if stdout(repo, "rev-parse", "HEAD") != stdout(repo, "rev-parse", "refs/remotes/origin/" + branch):
        return "リモートと履歴が異なるため保留"
    lock = Path(stdout(repo, "rev-parse", "--absolute-git-dir")) / "worklog-auto.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return "他の自動同期と競合"
    try:
        # A second status check before staging minimizes cross-session races.
        for path in changed:
            if path not in status(repo):
                return "同期前に作業状態が変わったため保留"
        adding = command(repo, "add", "--", *changed)
        if adding.returncode:
            return "git add失敗"
        staged = command(repo, "diff", "--cached", "--name-only", "-z")
        names = {s.decode("utf-8", "replace") for s in staged.stdout.split(b"\0") if s}
        if names != set(changed):
            return "意図しないステージ差分があるため保留"
        provider = os.environ.get("WORKLOG_PROVIDER", "claude-code")
        result = command(repo, "commit", "-m", "work-log: " + provider + "の作業記録と変更を保存", timeout=25)
        if result.returncode:
            return "commit失敗"
        result = command(repo, "push", "-q", "origin", "HEAD:refs/heads/" + branch, timeout=30)
        if result.returncode:
            return "push失敗（コミットはローカルに保存）"
        return "保存成功: " + stdout(repo, "rev-parse", "--short", "HEAD")
    finally:
        try:
            lock.unlink()
        except OSError:
            pass

def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    event = data.get("hook_event_name")
    sid = str(data.get("session_id") or "")
    repo = repo_for(data.get("cwd"))
    if not sid or repo is None:
        return
    if event == "SessionStart":
        if not read_state(repo, sid):
            write_state(repo, sid, {
                "base": fingerprint(repo),
                "head": stdout(repo, "rev-parse", "HEAD"),
                "time": time.time(), "active": True,
            })
        return
    if event == "SessionEnd":
        state = read_state(repo, sid)
        if state:
            state["active"] = False
            write_state(repo, sid, state)
        return
    if event != "Stop":
        return
    state = read_state(repo, sid)
    if not state:
        # Existing sessions which predate installation: establish a baseline
        # without claiming others' pre-existing work as this session's work.
        write_state(repo, sid, {
            "base": fingerprint(repo),
            "head": stdout(repo, "rev-parse", "HEAD"),
            "time": time.time(), "active": True,
        })
        return
    current = fingerprint(repo)
    paths = delta(state["base"], current)
    old_head = state.get("head")
    head = stdout(repo, "rev-parse", "HEAD")
    if not paths:
        if old_head != head:
            diff = command(repo, "diff", "--name-only", old_head, head, "--", "work-log")
            if not diff.stdout.strip():
                note(repo, "HEAD変化にwork-logが見つからない（コミット済みのため変更不可）")
        return
    substantive = [p for p in paths if not p.startswith("work-log/")]
    log_paths = [p for p in paths if is_log(p)]
    if not substantive and not log_paths:
        return
    if active_others(repo, sid):
        note(repo, "同時セッション検出。自動push保留: " + sid)
        # The second session may be operating on the same files; avoid associating
        # its work with the current session.
        return
    if not log_paths:
        reason = "このセッションの変更に対応するwork-logがありません"
    else:
        errors = []
        for path in log_paths:
            errors += [path + ": " + s for s in validate_log(repo / path, repo.name, sid)]
        reason = "正本の形式に不足があります: " + "; ".join(errors[:6]) if errors else ""
    if reason:
        if data.get("stop_hook_active"):
            print(json.dumps({"systemMessage": "ワークログ未完成。自動pushは保留: " + reason}, ensure_ascii=False))
        else:
            print(json.dumps({"decision": "block", "reason": instruct(repo, sid, reason, data)}, ensure_ascii=False))
        return
    if not substantive:
        # Work-log changed but not other files; it may be a valid record of research.
        pass
    if old_head != head:
        note(repo, "セッション中にHEAD変化。手動コミットとの混在を避け自動push保留")
        return
    result = sync(repo, paths, state["base"], sid)
    note(repo, result)
    if result.startswith("保存成功"):
        # baseline refreshed so the next task in same session starts fresh
        state["base"] = fingerprint(repo)
        state["head"] = stdout(repo, "rev-parse", "HEAD")
        write_state(repo, sid, state)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"systemMessage": "work-log自動処理は安全停止: " + str(exc)[:140]}, ensure_ascii=False))
