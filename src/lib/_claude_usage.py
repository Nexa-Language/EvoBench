"""Claude Code CLI：projects/*.jsonl 内 ``message.usage`` 与可选 ``--output-format json`` stdout。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def snap_from_claude_usage(usage: dict[str, Any], *, cost: float = 0.0) -> dict[str, int | float] | None:
    inp = int(usage.get("input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or usage.get("cache_read_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or usage.get("cache_write_tokens") or 0)
    if not (inp or out_t or cache_read or cache_write or cost):
        return None
    return {
        "prompt": max(inp - cache_read, 0),
        "completion": out_t,
        "reasoning": 0,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cost": cost,
    }


def _merge_usage(acc: dict[str, int | float], snap: dict[str, int | float]) -> None:
    for key in ("prompt", "completion", "reasoning", "cache_read", "cache_write"):
        acc[key] += int(snap[key])
    acc["cost"] += float(snap["cost"])


def claude_jsonl_usage_total(path: Path) -> dict[str, int | float] | None:
    """累加 ``projects/<dir>/<sessionId>.jsonl`` 中每条 assistant ``message.usage``（按 message.id 去重）。"""
    acc = {"prompt": 0, "completion": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    seen_msg_ids: set[str] = set()
    hits = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        msg_id = str(msg.get("id") or obj.get("uuid") or "")
        if msg_id and msg_id in seen_msg_ids:
            continue
        if msg_id:
            seen_msg_ids.add(msg_id)
        snap = snap_from_claude_usage(usage)
        if snap is None:
            continue
        _merge_usage(acc, snap)
        hits += 1
    return acc if hits else None


def claude_jsonl_last_text(path: Path) -> str:
    """最后一条带 text 的 assistant 消息，用于 llm_response 展示。"""
    last = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return last
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            last = content
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "".join(str(p) for p in parts if p)
            if text.strip():
                last = text
    return last[:12000]


def claude_result_from_stdout(stdout: str) -> tuple[dict[str, int | float] | None, str, str]:
    text = (stdout or "").strip()
    if not text.startswith("{"):
        return None, text, ""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None, text, ""
    if not isinstance(obj, dict):
        return None, text, ""
    usage = obj.get("usage")
    snap = snap_from_claude_usage(usage, cost=float(obj.get("total_cost_usd") or 0)) if isinstance(usage, dict) else None
    display = obj.get("result") or obj.get("output") or text
    if not isinstance(display, str):
        display = json.dumps(display, ensure_ascii=False)
    return snap, display[:12000], str(obj.get("session_id") or "")


def claude_usage_snapshot_path(claude_home: Path, task_id: int) -> Path:
    return claude_home / f"usage-task{task_id}.json"


def write_claude_usage_snapshot(
    claude_home: Path,
    task_id: int,
    usage: dict[str, int | float],
    session_id: str = "",
    *,
    source_jsonl: Path | None = None,
) -> None:
    """写入宿主机可读的用量快照（projects/ 常为 root:700）。"""
    claude_home.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"usage_normalized": usage, "task_id": task_id}
    if session_id:
        payload["claude_session_id"] = session_id
    if source_jsonl is not None:
        payload["source_jsonl"] = str(source_jsonl.name)
    path = claude_usage_snapshot_path(claude_home, task_id)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o644)


def read_claude_usage_snapshot(claude_home: Path, task_id: int) -> dict[str, int | float] | None:
    path = claude_usage_snapshot_path(claude_home, task_id)
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    snap = obj.get("usage_normalized")
    return snap if isinstance(snap, dict) else None


def claude_make_session_readable(jsonl: Path) -> None:
    """尽量放宽 projects jsonl 权限，便于宿主机 summarize 回读。"""
    try:
        os.chmod(jsonl, 0o644)
        for parent in (jsonl.parent, jsonl.parent.parent):
            if parent.is_dir():
                os.chmod(parent, 0o755)
    except OSError:
        pass


def claude_session_jsonls(claude_home: Path) -> list[Path]:
    if not claude_home.is_dir():
        return []
    out: list[Path] = []
    projects = claude_home / "projects"
    if projects.is_dir():
        try:
            for proj_dir in projects.iterdir():
                if not proj_dir.is_dir():
                    continue
                try:
                    for p in proj_dir.glob("*.jsonl"):
                        if p.is_file():
                            out.append(p)
                except OSError:
                    continue
        except OSError:
            pass
    if not out:
        try:
            out = [
                p
                for p in claude_home.rglob("*.jsonl")
                if p.is_file() and not p.name.startswith("usage-task")
            ]
        except OSError:
            pass
    return sorted(out, key=lambda p: p.stat().st_mtime)


def claude_jsonl_for_session(claude_home: Path, session_id: str) -> Path | None:
    if not session_id or not claude_home.is_dir():
        return None
    hits = list(claude_home.glob(f"projects/**/{session_id}.jsonl"))
    return hits[0] if hits else None


def claude_latest_session_jsonl(claude_home: Path) -> Path | None:
    files = claude_session_jsonls(claude_home)
    return files[-1] if files else None
