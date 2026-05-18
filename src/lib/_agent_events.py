from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator

from lib._claude_usage import (
    claude_jsonl_for_session,
    claude_jsonl_usage_total,
    claude_session_jsonls,
    read_claude_usage_snapshot,
)
from lib._kimi_stream_json import kimi_session_id, kimi_wire_path, kimi_wire_usage_total


def append_agent_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def iter_agent_events(run_dir: Path) -> Iterator[dict[str, Any]]:
    root = run_dir / "agent-events"
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.jsonl")):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def agent_successful_llm_call_count(run_dir: Path) -> int:
    ids: set[str] = set()
    fallback = 0
    for event in iter_agent_events(run_dir):
        if event.get("event_type") != "llm_response":
            continue
        rid = event.get("llm_response_id")
        if isinstance(rid, str) and rid:
            ids.add(rid)
        else:
            fallback += 1
    return len(ids) if ids else fallback


def agent_error_events(run_dir: Path) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for event in iter_agent_events(run_dir):
        if event.get("event_type") != "error":
            continue
        errors.append(
            {
                "code": event.get("code", ""),
                "kind": event.get("kind", ""),
                "detail": event.get("detail", ""),
                "timestamp": event.get("timestamp", ""),
            }
        )
    return errors


_TASK_JSONL_RE = re.compile(r"^task(\d+)\.jsonl$")


def _count_llm_responses_in_task_file(path: Path) -> int:
    """与 `agent_successful_llm_call_count` 相同：按 `llm_response_id` 去重，无 id 时按事件条数累计。"""
    ids: set[str] = set()
    fallback = 0
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("event_type") != "llm_response":
            continue
        rid = obj.get("llm_response_id")
        if isinstance(rid, str) and rid:
            ids.add(rid)
        else:
            fallback += 1
    return len(ids) if ids else fallback


def agent_events_per_task_llm_rounds(run_dir: Path) -> dict[str, Any]:
    """CLI backend 写入的 `agent-events/taskN.jsonl` 中每任务 LLM 轮次（OpenHands 事件路径不会填充）。"""
    out: dict[str, Any] = {f"task{tid}_agent_llm_rounds": "" for tid in range(6)}
    root = run_dir / "agent-events"
    if not root.is_dir():
        return out
    for path in sorted(root.glob("task*.jsonl")):
        match = _TASK_JSONL_RE.match(path.name)
        if not match:
            continue
        tid = int(match.group(1))
        if 0 <= tid <= 5:
            out[f"task{tid}_agent_llm_rounds"] = _count_llm_responses_in_task_file(path)
    return out


def _usage_rows_from_codex_json_message(message: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in message.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "turn.completed":
            continue
        usage = obj.get("usage")
        if isinstance(usage, dict):
            rows.append(usage)
    return rows


def _snap_from_codex_usage(usage: dict[str, Any]) -> dict[str, int | float] | None:
    """将 Codex ``exec --json`` 中 ``turn.completed.usage`` 对齐到 OpenHands base_state 口径。"""
    inp = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    reasoning = int(usage.get("reasoning_output_tokens") or 0)
    cost = float(usage.get("total_cost_usd") or usage.get("cost_usd") or 0.0)
    prompt = max(inp - cached, 0)
    if not (inp or cached or out_t or reasoning or cost):
        return None
    return {
        "prompt": prompt,
        "completion": out_t,
        "reasoning": reasoning,
        "cache_read": cached,
        "cache_write": 0,
        "cost": cost,
    }


def _add_usage(acc: dict[str, int | float], snap: dict[str, int | float]) -> None:
    for key in ("prompt", "completion", "reasoning", "cache_read", "cache_write"):
        acc[key] += int(snap[key])
    acc["cost"] += float(snap["cost"])


def _total_tokens(acc: dict[str, int | float]) -> int:
    return int(acc["prompt"] + acc["completion"] + acc["reasoning"] + acc["cache_read"] + acc["cache_write"])


def _zero_usage() -> dict[str, int | float]:
    return {"prompt": 0, "completion": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}


def _usage_row(
    run_acc: dict[str, int | float],
    per_task: dict[int, dict[str, int | float]],
    context_mode: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "llm_metrics_source": "agent-events",
        "llm_tokens_logged": True,
        "run_llm_cost_usd": round(float(run_acc["cost"]), 6) if run_acc["cost"] else "",
        "run_llm_prompt_tokens": int(run_acc["prompt"]),
        "run_llm_completion_tokens": int(run_acc["completion"]),
        "run_llm_reasoning_tokens": int(run_acc["reasoning"]),
        "run_llm_cache_read_tokens": int(run_acc["cache_read"]),
        "run_llm_cache_write_tokens": int(run_acc["cache_write"]),
        "run_llm_total_tokens": _total_tokens(run_acc),
    }
    for i in range(6):
        row[f"task{i}_llm_total_tokens"] = ""
        row[f"task{i}_llm_cost_usd"] = ""
    if (context_mode or "").lower() != "pipeline":
        for tid in range(6):
            acc = per_task[tid]
            if _total_tokens(acc) or acc["cost"]:
                row[f"task{tid}_llm_total_tokens"] = _total_tokens(acc)
                row[f"task{tid}_llm_cost_usd"] = round(float(acc["cost"]), 6) if acc["cost"] else ""
    return row


def collect_llm_metrics_from_agent_events(run_dir: Path, context_mode: str) -> dict[str, Any]:
    """从 ``agent-events`` 汇总 token：Kimi ``llm_usage``、Codex JSON，或无事件时回读 wire.jsonl。"""
    root = run_dir / "agent-events"
    if not root.is_dir():
        return {}
    kimi_home = run_dir / "agent-state" / "kimi"
    claude_home = run_dir / "agent-state" / "claude" / "claude"
    claude_jsonls = claude_session_jsonls(claude_home)
    claude_i = 0

    run_acc = _zero_usage()
    per_task = {i: _zero_usage() for i in range(6)}
    hits = 0

    for path in sorted(root.glob("task*.jsonl")):
        match = _TASK_JSONL_RE.match(path.name)
        if not match or not 0 <= int(match.group(1)) <= 5:
            continue
        tid = int(match.group(1))
        session_id: str | None = None
        claude_session_id: str | None = None
        got_usage = False
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            et = obj.get("event_type")
            if et == "llm_usage":
                snap = obj.get("usage_normalized")
                if isinstance(snap, dict):
                    hits += 1
                    got_usage = True
                    _add_usage(run_acc, snap)
                    _add_usage(per_task[tid], snap)
                sid = obj.get("claude_session_id")
                if isinstance(sid, str) and sid.strip():
                    claude_session_id = sid.strip()
                continue
            if et == "agent_stderr":
                session_id = kimi_session_id(str(obj.get("detail", ""))) or session_id
                continue
            if et != "llm_response":
                continue
            msg = obj.get("message")
            if not isinstance(msg, str) or not msg.strip():
                continue
            for usage in _usage_rows_from_codex_json_message(msg):
                snap = _snap_from_codex_usage(usage)
                if snap is None:
                    continue
                hits += 1
                _add_usage(run_acc, snap)
                _add_usage(per_task[tid], snap)
        if not got_usage and kimi_home.is_dir():
            wire = kimi_wire_path(kimi_home, session_id)
            snap = kimi_wire_usage_total(wire) if wire else None
            if snap:
                hits += 1
                _add_usage(run_acc, snap)
                _add_usage(per_task[tid], snap)
        if not got_usage and claude_home.is_dir():
            snap = read_claude_usage_snapshot(claude_home, tid)
            if snap is None:
                jsonl = (
                    claude_jsonl_for_session(claude_home, claude_session_id or "")
                    if claude_session_id
                    else (claude_jsonls[claude_i] if claude_i < len(claude_jsonls) else None)
                )
                if claude_session_id is None and claude_i < len(claude_jsonls):
                    claude_i += 1
                snap = claude_jsonl_usage_total(jsonl) if jsonl else None
            if snap:
                hits += 1
                _add_usage(run_acc, snap)
                _add_usage(per_task[tid], snap)

    return _usage_row(run_acc, per_task, context_mode) if hits else {}
