from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator


TASK_USER_START_RE = re.compile(r"请开始完成 Task\s*(\d+)\s*[。.]")
FAILED_LLM_CODES = ("LLMBadRequestError", "LLMRateLimitError", "LLMAuthenticationError", "LLMConnectionError")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"_value": data}
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
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


def iter_state_events(run_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    state_root = run_dir / "openhands-state"
    if not state_root.is_dir():
        return
    for path in sorted(state_root.rglob("events/event-*.json")):
        if "YatCC" in path.parts:
            continue
        yield path, read_json(path)


def usage_default_block(data: dict[str, Any]) -> dict[str, Any]:
    metrics_default = (data.get("metrics") or {}).get("default")
    if isinstance(metrics_default, dict) and metrics_default:
        return metrics_default
    usage_to_metrics = (data.get("stats") or {}).get("usage_to_metrics") or {}
    default = usage_to_metrics.get("default")
    return default if isinstance(default, dict) else {}


def base_state_execution_status(run_dir: Path) -> str:
    latest = ""
    for path in sorted((run_dir / "openhands-state").rglob("base_state.json")):
        if "YatCC" in path.parts:
            continue
        data = read_json(path)
        value = data.get("execution_status")
        if isinstance(value, str) and value:
            latest = value
    return latest


def _task_id_from_state_subdir(subdir: str) -> int | None:
    if subdir == "pipeline":
        return None
    match = re.match(r"^task(\d+)", subdir)
    if not match:
        return None
    task_id = int(match.group(1))
    return task_id if 0 <= task_id <= 5 else None


def zero_usage() -> dict[str, int | float]:
    return {"prompt": 0, "completion": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}


def add_usage(acc: dict[str, int | float], snap: dict[str, int | float]) -> None:
    for key in ("prompt", "completion", "reasoning", "cache_read", "cache_write"):
        acc[key] += int(snap[key])
    acc["cost"] += float(snap["cost"])


def total_tokens(acc: dict[str, int | float]) -> int:
    return int(acc["prompt"] + acc["completion"] + acc["reasoning"] + acc["cache_read"] + acc["cache_write"])


def extract_base_state_usage(path: Path) -> dict[str, int | float] | None:
    data = read_json(path)
    if "_error" in data:
        return None
    default = usage_default_block(data)
    usage = default.get("accumulated_token_usage")
    prompt = completion = reasoning = cache_read = cache_write = 0
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        reasoning = int(usage.get("reasoning_tokens") or 0)
        cache_read = int(usage.get("cache_read_tokens") or 0)
        cache_write = int(usage.get("cache_write_tokens") or 0)
    cost = safe_float(default.get("accumulated_cost"), 0.0)
    if prompt == completion == reasoning == cache_read == cache_write == 0 and cost == 0.0:
        return None
    return {
        "prompt": prompt,
        "completion": completion,
        "reasoning": reasoning,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cost": cost,
    }


def collect_llm_metrics(run_dir: Path, context_mode: str) -> dict[str, Any]:
    state_root = run_dir / "openhands-state"
    empty: dict[str, Any] = {
        "llm_metrics_source": "",
        "llm_tokens_logged": False,
        "run_llm_cost_usd": "",
        "run_llm_prompt_tokens": "",
        "run_llm_completion_tokens": "",
        "run_llm_reasoning_tokens": "",
        "run_llm_cache_read_tokens": "",
        "run_llm_cache_write_tokens": "",
        "run_llm_total_tokens": "",
    }
    for tid in range(6):
        empty[f"task{tid}_llm_total_tokens"] = ""
        empty[f"task{tid}_llm_cost_usd"] = ""
    if not state_root.is_dir():
        return empty

    run_acc = zero_usage()
    per_task = {i: zero_usage() for i in range(6)}
    hits = 0
    cm = (context_mode or "").lower()
    for path in sorted(state_root.rglob("base_state.json")):
        if "YatCC" in path.parts:
            continue
        rel = path.relative_to(state_root)
        if len(rel.parts) < 2:
            continue
        snap = extract_base_state_usage(path)
        if snap is None:
            continue
        hits += 1
        add_usage(run_acc, snap)
        tid = _task_id_from_state_subdir(rel.parts[0])
        if cm != "pipeline" and tid is not None:
            add_usage(per_task[tid], snap)
    if hits == 0:
        return empty
    row = dict(empty)
    row.update(
        {
            "llm_metrics_source": "base_state",
            "llm_tokens_logged": True,
            "run_llm_cost_usd": round(float(run_acc["cost"]), 6) if run_acc["cost"] else "",
            "run_llm_prompt_tokens": int(run_acc["prompt"]),
            "run_llm_completion_tokens": int(run_acc["completion"]),
            "run_llm_reasoning_tokens": int(run_acc["reasoning"]),
            "run_llm_cache_read_tokens": int(run_acc["cache_read"]),
            "run_llm_cache_write_tokens": int(run_acc["cache_write"]),
            "run_llm_total_tokens": total_tokens(run_acc),
        }
    )
    for tid in range(6):
        if cm == "pipeline":
            continue
        acc = per_task[tid]
        if total_tokens(acc) or acc["cost"]:
            row[f"task{tid}_llm_total_tokens"] = total_tokens(acc)
            row[f"task{tid}_llm_cost_usd"] = round(float(acc["cost"]), 6) if acc["cost"] else ""
    return row


def event_user_text(obj: dict[str, Any]) -> str:
    if obj.get("event_type") != "MessageEvent" or obj.get("source") != "user":
        return ""
    message = obj.get("llm_message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
    return ""


def llm_response_id(obj: dict[str, Any]) -> str | None:
    value = obj.get("llm_response_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def collect_agent_llm_rounds(run_dir: Path, context_mode: str, report: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"agent_llm_rounds_source": "", "run_agent_llm_rounds": ""}
    for tid in range(6):
        out[f"task{tid}_agent_llm_rounds"] = ""

    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    total = safe_int(metrics.get("total_turns"))
    if total is not None:
        out["run_agent_llm_rounds"] = total
        out["agent_llm_rounds_source"] = "report"
        return out

    events_dir = run_dir / "openhands-events"
    if not events_dir.is_dir():
        return out
    cm = (context_mode or "").lower()
    per_task = {i: set() for i in range(6)}
    all_ids: set[str] = set()
    if cm == "pipeline" and (events_dir / "pipeline.jsonl").is_file():
        current = 0
        matched = False
        for obj in iter_jsonl(events_dir / "pipeline.jsonl"):
            text = event_user_text(obj)
            match = TASK_USER_START_RE.search(text) if text else None
            if match:
                current = int(match.group(1))
                matched = True
            rid = llm_response_id(obj)
            if rid:
                all_ids.add(rid)
                if matched and 0 <= current <= 5:
                    per_task[current].add(rid)
    else:
        for path in sorted(events_dir.glob("*.jsonl")):
            match = re.match(r"^task(\d+)", path.stem)
            if not match:
                continue
            tid = int(match.group(1))
            for obj in iter_jsonl(path):
                rid = llm_response_id(obj)
                if rid:
                    all_ids.add(rid)
                    if 0 <= tid <= 5:
                        per_task[tid].add(rid)
    if all_ids:
        out["run_agent_llm_rounds"] = len(all_ids)
        out["agent_llm_rounds_source"] = "jsonl"
        for tid in range(6):
            if per_task[tid]:
                out[f"task{tid}_agent_llm_rounds"] = len(per_task[tid])
    return out


def is_failed_llm_call_event(obj: dict[str, Any]) -> bool:
    text = f"{obj.get('code', '')} {obj.get('detail', '')} {obj.get('kind', '')}"
    return obj.get("kind") == "ConversationErrorEvent" and (
        "litellm" in text.lower() or any(code in text for code in FAILED_LLM_CODES)
    )


def collect_error_events(run_dir: Path) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for path, obj in iter_state_events(run_dir):
        if obj.get("kind") == "ConversationErrorEvent" or is_failed_llm_call_event(obj):
            errors.append(
                {
                    "path": str(path),
                    "code": obj.get("code", ""),
                    "kind": obj.get("kind", ""),
                    "detail": obj.get("detail", ""),
                    "timestamp": obj.get("timestamp", ""),
                }
            )
    return errors


def collect_error_events_from_jsonl(run_dir: Path) -> list[dict[str, Any]]:
    """与 collect_error_events 语义相近，但只扫 openhands-events/*.jsonl（适合 launch 高频刷新）。"""
    errors: list[dict[str, Any]] = []
    root = run_dir / "openhands-events"
    if not root.is_dir():
        return errors
    for path in sorted(root.glob("*.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") != "ConversationErrorEvent" and not is_failed_llm_call_event(obj):
                        continue
                    errors.append(
                        {
                            "path": str(path),
                            "code": obj.get("code", ""),
                            "kind": obj.get("kind", ""),
                            "detail": str(obj.get("detail", "")),
                            "timestamp": str(obj.get("timestamp", "")),
                        }
                    )
        except OSError:
            continue
    return errors


def response_ids_from_base_state_token_usages(run_dir: Path) -> set[str]:
    """OpenHands 持久化进 base_state 的 token_usages（可能明显滞后于真实请求）。"""
    ids: set[str] = set()
    for path in sorted((run_dir / "openhands-state").rglob("base_state.json")):
        if "YatCC" in path.parts:
            continue
        default = usage_default_block(read_json(path))
        usages = default.get("token_usages")
        if isinstance(usages, list):
            for usage in usages:
                if isinstance(usage, dict):
                    rid = usage.get("response_id")
                    if isinstance(rid, str) and rid:
                        ids.add(rid)
    return ids


def response_ids_from_single_jsonl(path: Path) -> set[str]:
    """单条 jsonl 内去重 `llm_response_id`（流式读取）。"""
    ids: set[str] = set()
    if not path.is_file():
        return ids
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                rid = llm_response_id(obj)
                if rid:
                    ids.add(rid)
    except OSError:
        return ids
    return ids


def response_ids_from_openhands_event_jsonl(run_dir: Path) -> set[str]:
    """runner 回调写入的 openhands-events/*.jsonl，通常随每次 LLM 响应追加，适合实时进度。"""
    ids: set[str] = set()
    root = run_dir / "openhands-events"
    if not root.is_dir():
        return ids
    for path in sorted(root.glob("*.jsonl")):
        ids |= response_ids_from_single_jsonl(path)
    return ids


def context_mode_from_run_metadata(run_dir: Path) -> str:
    path = run_dir / "metadata.json"
    if not path.is_file():
        return ""
    data = read_json(path)
    return str(data.get("context_mode", "") or "")


def successful_llm_call_count(run_dir: Path) -> int:
    """成功 LLM 调用数：取 base_state 与事件日志两套去重 id 的较大值（避免 state 滞后导致终端卡在旧数字）。"""
    state_ids = response_ids_from_base_state_token_usages(run_dir)
    event_ids = response_ids_from_openhands_event_jsonl(run_dir)
    if state_ids or event_ids:
        return max(len(state_ids), len(event_ids))
    report = read_json(run_dir / "openhands_report.json") if (run_dir / "openhands_report.json").is_file() else {}
    cm = context_mode_from_run_metadata(run_dir) or str(report.get("context_mode", "") or "")
    rounds = collect_agent_llm_rounds(run_dir, cm, report)
    value = safe_int(rounds.get("run_agent_llm_rounds"), 0)
    return int(value or 0)


def cache_hit_info(run_dir: Path) -> dict[str, Any]:
    metrics = collect_llm_metrics(run_dir, "")
    read_tokens = int(metrics.get("run_llm_cache_read_tokens") or 0)
    write_tokens = int(metrics.get("run_llm_cache_write_tokens") or 0)
    return {
        "cache_hit": read_tokens > 0,
        "cache_hit_tokens": read_tokens,
        "cache_write_tokens": write_tokens,
    }
