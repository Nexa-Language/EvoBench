"""解析 Kimi Code CLI `--output-format=stream-json` 的 JSONL 输出。

官方说明：每行一条 JSON；模型每轮产出为 `role: "assistant"` 的消息（可含 `tool_calls`），
随后可有 `role: "tool"`。统计 assistant 行数 ≈ 模型采样轮数（与 EvoBench `--max-iterations` 对齐）。
参见: https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SESSION_RE = re.compile(r"kimi\s+-r\s+([0-9a-f-]{36})", re.IGNORECASE)


def kimi_stream_json_assistant_lines(stdout: str) -> list[str]:
    lines_out: list[str] = []
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("role") == "assistant":
            lines_out.append(line)
    return lines_out


def kimi_stream_json_assistant_step_count(stdout: str) -> int:
    return len(kimi_stream_json_assistant_lines(stdout))


def kimi_session_id(text: str) -> str | None:
    match = _SESSION_RE.search(text or "")
    return match.group(1) if match else None


def kimi_wire_path(kimi_home: Path, session_id: str | None = None) -> Path | None:
    sessions = kimi_home / "sessions"
    if not sessions.is_dir():
        return None
    if session_id:
        found = list(sessions.glob(f"*/{session_id}/wire.jsonl"))
        return found[0] if found else None
    files = [p for p in sessions.glob("*/**/wire.jsonl") if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def kimi_wire_usage_total(wire_path: Path) -> dict[str, int | float] | None:
    """累加 wire 中 StatusUpdate.payload.token_usage。"""
    acc = {"prompt": 0, "completion": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    hits = 0
    try:
        lines = wire_path.read_text(encoding="utf-8", errors="replace").splitlines()
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
        msg = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(msg, dict) or msg.get("type") != "StatusUpdate":
            continue
        payload = msg.get("payload")
        usage = payload.get("token_usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            continue
        inp = int(usage.get("input_other") or 0)
        out_t = int(usage.get("output") or 0)
        cache_read = int(usage.get("input_cache_read") or 0)
        cache_write = int(usage.get("input_cache_creation") or 0)
        if not (inp or out_t or cache_read or cache_write):
            continue
        acc["prompt"] += inp
        acc["completion"] += out_t
        acc["cache_read"] += cache_read
        acc["cache_write"] += cache_write
        hits += 1
    return acc if hits else None
