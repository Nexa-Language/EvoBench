"""根据 run 输出目录与 metadata 估算「剩余墙钟」「剩余对话轮数」供任务提示使用。"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from lib._openhands_events import read_json, response_ids_from_single_jsonl


def _started_at_unix_ts(raw: Any) -> float | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def remaining_wall_seconds(output_dir: Path) -> int | None:
    """剩余墙钟秒数；无 `max_agent_hours` 或无法解析 `started_at` 时返回 None。"""
    path = output_dir / "metadata.json"
    if not path.is_file():
        return None
    meta = read_json(path)
    try:
        max_h = int(meta.get("max_agent_hours") or 0)
    except (TypeError, ValueError):
        max_h = 0
    if max_h <= 0:
        return None
    ts = _started_at_unix_ts(meta.get("started_at"))
    if ts is None:
        return None
    elapsed = time.time() - ts
    return max(int(max_h * 3600 - elapsed), 0)


def format_cn_wall_remain(seconds: int) -> str:
    """口语化剩余时长（正数秒）。"""
    if seconds <= 0:
        return "已用尽"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{h}小时")
    if m:
        parts.append(f"{m}分")
    if s or not parts:
        parts.append(f"{s}秒")
    return "约" + "".join(parts)


def format_wall_remaining_display(seconds: int | None) -> str:
    """嵌入提示句的「剩余时间」占位内容。"""
    if seconds is None:
        return "无明确墙钟上限（仅受对话最大迭代轮数约束）"
    if seconds <= 0:
        return "墙钟预算已用尽或仅剩极少时间"
    return format_cn_wall_remain(seconds)


def iterations_used_for_budget(output_dir: Path, context_mode: str) -> int:
    """当前对话已消耗的 LLM 响应数（用于估算剩余轮数）。"""
    if (context_mode or "").lower() != "pipeline":
        return 0
    return len(response_ids_from_single_jsonl(output_dir / "openhands-events" / "pipeline.jsonl"))


def remaining_iterations_budget(output_dir: Path, context_mode: str, max_iterations: int) -> int:
    """剩余可迭代轮数（与 OpenHands `max_iteration_per_run` 对齐的粗估）。"""
    used = iterations_used_for_budget(output_dir, context_mode)
    return max(0, int(max_iterations) - used)
