#!/usr/bin/env python3
"""Collect OpenHands Docker container-run reports into CSV and JSON files.

LLM 用量与费用（优先顺序）：

1. **``openhands-state/<task…>/<uuid>/base_state.json``**（OpenHands 持久化状态）中的
   累计字段：常见为 ``metrics.default``，新版本为 ``stats.usage_to_metrics.default``。
   其中 ``accumulated_token_usage`` 与 ``accumulated_cost`` 为累计值；
   费用为 OpenHands 累计的 **美元** 量级数值（与网关/LiteLLM 计费一致，非人民币）。
2. 若无可用 ``base_state`` 字段，再回退扫描 ``openhands-events/*.jsonl`` 内嵌的 usage 字典。

``context_mode`` 为 ``pipeline`` 时，数据通常在 ``openhands-state/pipeline/<uuid>/`` 下，
仅汇总 run 级列；各 ``task*_`` 用量/费用列留空。爬取目录中若包含副本 ``YatCC/``，解析时会跳过其下路径。

**Agent LLM 轮次**（``run_agent_llm_rounds`` / ``task*_agent_llm_rounds``）：

1. 若 ``openhands_report.json`` 的 ``tasks`` 中含 ``turns`` / ``agent_turns`` / ``llm_rounds`` 等字段，
   或 ``metrics.total_turns`` 存在，则优先采用（同一 ``task_id`` 多条记录会累加轮次，如含复活）。
2. 否则从 ``openhands-events/*.jsonl`` 统计：按去重后的 ``llm_response_id`` 计为一次模型调用；
   ``pipeline`` 模式下按用户消息中的 ``请开始完成 Task N。`` 分段归属到各 ``task*``。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = ROOT / "eval" / "container-runs"
DEFAULT_OUTPUT = ROOT / "eval" / "container-runs-summary"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _latest_task_entries(tasks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Keep the last entry for each task, so resurrected retries replace earlier failures."""
    latest: dict[int, dict[str, Any]] = {}
    for task in tasks:
        task_id = task.get("task_id")
        if isinstance(task_id, int):
            latest[task_id] = task
    return latest


def _model_from_run_id(run_id: str) -> str:
    match = re.search(r"-(.+)-tasks-", run_id)
    return match.group(1) if match else run_id


def _usage_like_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    """遍历 JSON 对象，产出疑似 LLM usage 的字典（OpenAI / 兼容网关常见字段）。"""
    if isinstance(obj, dict):
        if ("prompt_tokens" in obj or "input_tokens" in obj) and (
            "completion_tokens" in obj or "output_tokens" in obj
        ):
            yield obj
        for v in obj.values():
            yield from _usage_like_dicts(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _usage_like_dicts(x)


def _add_usage(acc: dict[str, int], u: dict[str, Any]) -> None:
    pt = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    ct = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
    tt = u.get("total_tokens")
    tot = int(tt) if tt is not None else pt + ct
    acc["prompt"] += pt
    acc["completion"] += ct
    acc["total"] += tot


def _task_id_from_openhands_subdir(subdir: str) -> int | None:
    """openhands-state 下第一层目录名，如 task0、task3-resurrect → 任务编号；pipeline → None。"""
    if subdir == "pipeline":
        return None
    m = re.match(r"^task(\d+)", subdir)
    if not m:
        return None
    tid = int(m.group(1))
    return tid if 0 <= tid <= 5 else None


def _usage_metrics_default_block(data: dict[str, Any]) -> dict[str, Any]:
    """OpenHands 不同版本：``metrics.default`` 或 ``stats.usage_to_metrics.default``。"""
    metrics_default = (data.get("metrics") or {}).get("default")
    if isinstance(metrics_default, dict) and metrics_default:
        return metrics_default
    utm = (data.get("stats") or {}).get("usage_to_metrics") or {}
    block = utm.get("default")
    return block if isinstance(block, dict) else {}


def _extract_base_state_metrics(path: Path) -> dict[str, int | float] | None:
    """读取单个 base_state.json 中的累计 token 与 cost；无有效数据则返回 None。"""
    data = _read_json(path)
    if "_error" in data:
        return None
    default = _usage_metrics_default_block(data)
    if not default:
        return None
    usage = default.get("accumulated_token_usage")
    pt = ct = rt = cr = cw = 0
    if isinstance(usage, dict):
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        rt = int(usage.get("reasoning_tokens") or 0)
        cr = int(usage.get("cache_read_tokens") or 0)
        cw = int(usage.get("cache_write_tokens") or 0)
    cost_raw = default.get("accumulated_cost")
    cost_f = _safe_float(cost_raw, 0.0) if cost_raw is not None else 0.0
    if pt == 0 and ct == 0 and rt == 0 and cr == 0 and cw == 0 and cost_f == 0.0:
        return None
    return {
        "prompt": pt,
        "completion": ct,
        "reasoning": rt,
        "cache_read": cr,
        "cache_write": cw,
        "cost": cost_f,
    }


def _zero_usage_acc() -> dict[str, int | float]:
    return {"prompt": 0, "completion": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}


def _add_snap(acc: dict[str, int | float], snap: dict[str, int | float]) -> None:
    acc["prompt"] += int(snap["prompt"])
    acc["completion"] += int(snap["completion"])
    acc["reasoning"] += int(snap["reasoning"])
    acc["cache_read"] += int(snap["cache_read"])
    acc["cache_write"] += int(snap["cache_write"])
    acc["cost"] += float(snap["cost"])


def _total_tokens_from_acc(acc: dict[str, int | float]) -> int:
    return int(
        acc["prompt"]
        + acc["completion"]
        + acc["reasoning"]
        + acc["cache_read"]
        + acc["cache_write"]
    )


def _collect_from_base_states(run_dir: Path, context_mode: str) -> dict[str, Any]:
    """汇总 openhands-state 下各会话 base_state.json 的累计用量与费用。"""
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

    run_acc = _zero_usage_acc()
    per_task: dict[int, dict[str, int | float]] = {i: _zero_usage_acc() for i in range(6)}
    base_state_hits = 0
    cm = (context_mode or "").strip().lower()

    for bs_path in sorted(state_root.rglob("base_state.json")):
        if "YatCC" in bs_path.parts:
            continue
        try:
            rel = bs_path.relative_to(state_root)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) < 2:
            continue
        subdir = parts[0]
        snap = _extract_base_state_metrics(bs_path)
        if snap is None:
            continue
        base_state_hits += 1
        _add_snap(run_acc, snap)
        tid = _task_id_from_openhands_subdir(subdir)
        if cm != "pipeline" and tid is not None:
            _add_snap(per_task[tid], snap)

    if base_state_hits == 0:
        return empty

    row: dict[str, Any] = {
        "llm_metrics_source": "base_state",
        "llm_tokens_logged": True,
        "run_llm_cost_usd": round(float(run_acc["cost"]), 6) if run_acc["cost"] != 0.0 else "",
        "run_llm_prompt_tokens": int(run_acc["prompt"]),
        "run_llm_completion_tokens": int(run_acc["completion"]),
        "run_llm_reasoning_tokens": int(run_acc["reasoning"]),
        "run_llm_cache_read_tokens": int(run_acc["cache_read"]),
        "run_llm_cache_write_tokens": int(run_acc["cache_write"]),
        "run_llm_total_tokens": _total_tokens_from_acc(run_acc),
    }
    for tid in range(6):
        if cm == "pipeline":
            row[f"task{tid}_llm_total_tokens"] = ""
            row[f"task{tid}_llm_cost_usd"] = ""
        else:
            ta = per_task[tid]
            if _total_tokens_from_acc(ta) == 0 and ta["cost"] == 0:
                row[f"task{tid}_llm_total_tokens"] = ""
                row[f"task{tid}_llm_cost_usd"] = ""
            else:
                row[f"task{tid}_llm_total_tokens"] = _total_tokens_from_acc(ta)
                row[f"task{tid}_llm_cost_usd"] = round(float(ta["cost"]), 6) if ta["cost"] != 0.0 else ""

    return row


def _scan_jsonl_for_tokens(path: Path) -> tuple[dict[str, int], bool]:
    """汇总单文件 JSONL 中出现的 usage；若 OpenHands / 模型未写入 usage 则为全 0 且 found=False。"""
    acc = {"prompt": 0, "completion": 0, "total": 0}
    if not path.is_file():
        return acc, False
    found = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return acc, False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        for u in _usage_like_dicts(obj):
            found = True
            _add_usage(acc, u)
    return acc, found


def _collect_llm_token_usage_from_jsonl(run_dir: Path, context_mode: str) -> dict[str, Any]:
    """从 openhands-events/*.jsonl 聚合 token（无费用列）；pipeline 时各 task 列留空。"""
    events_dir = run_dir / "openhands-events"
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

    if not events_dir.is_dir():
        return empty

    run_acc: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
    per_task: dict[int, dict[str, int]] = {
        i: {"prompt": 0, "completion": 0, "total": 0} for i in range(6)
    }
    any_found = False

    for jf in sorted(events_dir.glob("*.jsonl")):
        part, found = _scan_jsonl_for_tokens(jf)
        if found:
            any_found = True
        for k in ("prompt", "completion", "total"):
            run_acc[k] += part[k]
        m = re.match(r"^task(\d+)", jf.stem)
        if m:
            tid = int(m.group(1))
            if 0 <= tid <= 5:
                for k in ("prompt", "completion", "total"):
                    per_task[tid][k] += part[k]

    row: dict[str, Any] = dict(empty)
    row["llm_tokens_logged"] = any_found
    if not any_found:
        return row

    row["llm_metrics_source"] = "jsonl"
    row["run_llm_prompt_tokens"] = run_acc["prompt"]
    row["run_llm_completion_tokens"] = run_acc["completion"]
    row["run_llm_reasoning_tokens"] = 0
    row["run_llm_cache_read_tokens"] = 0
    row["run_llm_cache_write_tokens"] = 0
    row["run_llm_total_tokens"] = run_acc["total"]

    cm = (context_mode or "").strip().lower()
    for tid in range(6):
        if cm == "pipeline":
            row[f"task{tid}_llm_total_tokens"] = ""
        else:
            row[f"task{tid}_llm_total_tokens"] = per_task[tid]["total"]
        row[f"task{tid}_llm_cost_usd"] = ""

    return row


def _collect_llm_metrics(run_dir: Path, context_mode: str) -> dict[str, Any]:
    """优先 base_state（含费用），否则回退 jsonl 事件流。"""
    bs = _collect_from_base_states(run_dir, context_mode)
    if bs.get("llm_metrics_source") == "base_state":
        return bs
    return _collect_llm_token_usage_from_jsonl(run_dir, context_mode)


_TASK_USER_START_RE = re.compile(r"请开始完成 Task\s*(\d+)\s*[。.]")


def _event_user_text(obj: dict[str, Any]) -> str:
    """MessageEvent 中 user 消息的纯文本（用于 pipeline 分段）。"""
    if obj.get("event_type") != "MessageEvent" or obj.get("source") != "user":
        return ""
    lm = obj.get("llm_message")
    if not isinstance(lm, dict):
        return ""
    content = lm.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def _turns_int_from_task_dict(task: dict[str, Any]) -> int | None:
    for key in ("turns", "agent_turns", "llm_rounds", "agent_llm_rounds", "iterations", "agent_iterations"):
        if key not in task:
            continue
        v = _safe_int(task.get(key), None)
        if v is not None and v >= 0:
            return v
    return None


def _report_turns_per_task(tasks: list[dict[str, Any]]) -> dict[int, int]:
    """``tasks`` 列表中同一 ``task_id`` 多条（如复活）的轮次累加。"""
    acc: dict[int, int] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = task.get("task_id")
        if not isinstance(tid, int) or not (0 <= tid <= 5):
            continue
        n = _turns_int_from_task_dict(task)
        if n is None:
            continue
        acc[tid] = acc.get(tid, 0) + n
    return acc


def _jsonl_objects(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
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


def _llm_response_id_from_event(obj: dict[str, Any]) -> str | None:
    lid = obj.get("llm_response_id")
    if isinstance(lid, str):
        s = lid.strip()
        if s:
            return s
    return None


def _pipeline_task_turns_from_jsonl(path: Path) -> tuple[dict[int, int] | None, int]:
    """按用户消息中的 Task 起点分段，统计各 task 的去重 ``llm_response_id`` 数。

    :return: (per_task 或分段失败时为 None, run 级去重总数)
    """
    all_ids: set[str] = set()
    per_sets: dict[int, set[str]] = {i: set() for i in range(6)}
    current = 0
    matched_any = False
    for obj in _jsonl_objects(path):
        text = _event_user_text(obj)
        if text:
            m = _TASK_USER_START_RE.search(text)
            if m:
                tid = int(m.group(1))
                if 0 <= tid <= 5:
                    current = tid
                    matched_any = True
        lid = _llm_response_id_from_event(obj)
        if lid:
            all_ids.add(lid)
            if matched_any:
                per_sets[current].add(lid)
    run_total = len(all_ids)
    if not matched_any:
        return None, run_total
    per_task = {i: len(per_sets[i]) for i in range(6)}
    return per_task, run_total


def _per_task_jsonl_turns(run_dir: Path) -> dict[int, int]:
    """``openhands-events/task*.jsonl``（含 ``taskN-resurrect``）按文件名聚合同一 task 的轮次。"""
    events_dir = run_dir / "openhands-events"
    acc: dict[int, int] = {i: 0 for i in range(6)}
    if not events_dir.is_dir():
        return acc
    for jf in sorted(events_dir.glob("*.jsonl")):
        if jf.stem == "pipeline":
            continue
        m = re.match(r"^task(\d+)", jf.stem)
        if not m:
            continue
        tid = int(m.group(1))
        if not (0 <= tid <= 5):
            continue
        ids: set[str] = set()
        for obj in _jsonl_objects(jf):
            lid = _llm_response_id_from_event(obj)
            if lid:
                ids.add(lid)
        acc[tid] += len(ids)
    return acc


def _collect_agent_llm_rounds(run_dir: Path, context_mode: str, report: dict[str, Any]) -> dict[str, Any]:
    """汇总 Agent LLM 调用轮次：优先 ``openhands_report.json`` 中各 task / ``metrics.total_turns``，否则解析事件流。

    事件流中按去重后的 ``llm_response_id`` 计为一次模型调用；pipeline 的 ``pipeline.jsonl`` 按
    ``请开始完成 Task N`` 用户消息分段归属到各 task。
    """
    empty: dict[str, Any] = {"agent_llm_rounds_source": "", "run_agent_llm_rounds": ""}
    for tid in range(6):
        empty[f"task{tid}_agent_llm_rounds"] = ""

    raw_tasks = report.get("tasks")
    tasks_norm: list[dict[str, Any]] = (
        [t for t in raw_tasks if isinstance(t, dict)] if isinstance(raw_tasks, list) else []
    )
    report_by_task = _report_turns_per_task(tasks_norm)
    m_raw = report.get("metrics")
    metrics: dict[str, Any] = m_raw if isinstance(m_raw, dict) else {}
    report_run_total = _safe_int(metrics.get("total_turns"))

    cm = (context_mode or "").strip().lower()
    events_dir = run_dir / "openhands-events"
    pipe = events_dir / "pipeline.jsonl"

    jsonl_per: dict[int, int] = {i: 0 for i in range(6)}
    jsonl_run_total = 0
    pipeline_seg: dict[int, int] | None = None

    if events_dir.is_dir():
        if pipe.is_file():
            pipeline_seg, jsonl_run_total = _pipeline_task_turns_from_jsonl(pipe)
            if pipeline_seg is not None:
                jsonl_per = dict(pipeline_seg)
        else:
            jsonl_per = _per_task_jsonl_turns(run_dir)
            jsonl_run_total = sum(jsonl_per.values())

    used_jsonl = False
    out: dict[str, Any] = dict(empty)

    for tid in range(6):
        if tid in report_by_task:
            out[f"task{tid}_agent_llm_rounds"] = int(report_by_task[tid])
        elif cm == "pipeline" and pipe.is_file() and pipeline_seg is not None:
            out[f"task{tid}_agent_llm_rounds"] = int(jsonl_per.get(tid, 0))
            used_jsonl = used_jsonl or jsonl_run_total > 0
        elif cm != "pipeline" and jsonl_per.get(tid, 0) > 0:
            out[f"task{tid}_agent_llm_rounds"] = int(jsonl_per[tid])
            used_jsonl = True

    sum_tasks = 0
    any_task = False
    for tid in range(6):
        cell = out[f"task{tid}_agent_llm_rounds"]
        if cell != "":
            any_task = True
            sum_tasks += int(cell)

    if report_run_total is not None and report_run_total >= 0:
        out["run_agent_llm_rounds"] = int(report_run_total)
    elif any_task:
        out["run_agent_llm_rounds"] = int(sum_tasks)
    elif jsonl_run_total > 0:
        out["run_agent_llm_rounds"] = int(jsonl_run_total)
        used_jsonl = True

    if report_by_task or (report_run_total is not None and report_run_total >= 0):
        out["agent_llm_rounds_source"] = "report" if not used_jsonl else "report+jsonl"
    elif used_jsonl or out["run_agent_llm_rounds"] != "":
        out["agent_llm_rounds_source"] = "jsonl"
    return out


def collect_one(run_dir: Path, *, include_tokens: bool = True) -> dict[str, Any]:
    metadata = _read_json(run_dir / "metadata.json") if (run_dir / "metadata.json").exists() else {}
    report_path = run_dir / "openhands_report.json"
    report = _read_json(report_path) if report_path.exists() else {}
    exit_code = (run_dir / "exit_code").read_text(encoding="utf-8").strip() if (run_dir / "exit_code").exists() else ""

    run_id = metadata.get("run_id") or report.get("run_id") or run_dir.name
    model = metadata.get("model") or report.get("agent_model") or _model_from_run_id(run_id)
    raw_tasks = report.get("tasks")
    tasks: list[dict[str, Any]] = (
        [t for t in raw_tasks if isinstance(t, dict)] if isinstance(raw_tasks, list) else []
    )
    latest = _latest_task_entries(tasks)

    row: dict[str, Any] = {
        "run_id": run_id,
        "model": model,
        "tasks": metadata.get("tasks", ""),
        "context_mode": metadata.get("context_mode") or report.get("context_mode", ""),
        "exit_code": exit_code,
        "has_report": report_path.exists(),
        "resurrection_count": report.get("metrics", {}).get("resurrection_count", ""),
        "total_elapsed_seconds": round(_safe_float(report.get("total_elapsed_seconds")), 2),
        "error": report.get("_error", ""),
    }

    scores: list[float] = []
    for task_id in range(6):
        task = latest.get(task_id, {})
        score = _safe_float(task.get("score"))
        scores.append(score)
        row[f"task{task_id}"] = score
        row[f"task{task_id}_passed"] = task.get("passed", "")

    row["pipeline_score"] = round(sum(scores) / len(scores), 2)

    row.update(_collect_agent_llm_rounds(run_dir, str(row.get("context_mode", "")), report))

    if include_tokens:
        row.update(_collect_llm_metrics(run_dir, str(row.get("context_mode", ""))))
    else:
        row.update(
            {
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
        )
        for tid in range(6):
            row[f"task{tid}_llm_total_tokens"] = ""
            row[f"task{tid}_llm_cost_usd"] = ""

    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect eval/container-runs results.")
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR), help="container-runs directory")
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT), help="output path prefix")
    parser.add_argument("--glob", default="*", help="run directory glob, e.g. 'run-per-task-*'")
    parser.add_argument(
        "--no-tokens",
        action="store_true",
        help="不收集 LLM token/费用（不读取 openhands-state/**/base_state.json；"
        "仍会从 openhands-events 解析 Agent LLM 轮次）",
    )
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    output_prefix = Path(args.output_prefix)
    rows = [
        collect_one(path, include_tokens=not args.no_tokens)
        for path in sorted(runs_dir.glob(args.glob))
        if path.is_dir()
    ]
    rows.sort(key=lambda row: (str(row["model"]), str(row["run_id"])))

    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "model",
        "run_id",
        "tasks",
        "context_mode",
        "exit_code",
        "has_report",
        "pipeline_score",
        "task0",
        "task1",
        "task2",
        "task3",
        "task4",
        "task5",
        "resurrection_count",
        "total_elapsed_seconds",
        "agent_llm_rounds_source",
        "run_agent_llm_rounds",
        "task0_agent_llm_rounds",
        "task1_agent_llm_rounds",
        "task2_agent_llm_rounds",
        "task3_agent_llm_rounds",
        "task4_agent_llm_rounds",
        "task5_agent_llm_rounds",
        "llm_metrics_source",
        "llm_tokens_logged",
        "run_llm_cost_usd",
        "run_llm_prompt_tokens",
        "run_llm_completion_tokens",
        "run_llm_reasoning_tokens",
        "run_llm_cache_read_tokens",
        "run_llm_cache_write_tokens",
        "run_llm_total_tokens",
        "task0_llm_total_tokens",
        "task0_llm_cost_usd",
        "task1_llm_total_tokens",
        "task1_llm_cost_usd",
        "task2_llm_total_tokens",
        "task2_llm_cost_usd",
        "task3_llm_total_tokens",
        "task3_llm_cost_usd",
        "task4_llm_total_tokens",
        "task4_llm_cost_usd",
        "task5_llm_total_tokens",
        "task5_llm_cost_usd",
        "error",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Collected {len(rows)} runs")
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()
