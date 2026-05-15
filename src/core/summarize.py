from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from lib import _docker
from lib._metrics import compute_mean_reward, compute_pass_score, prior_non_full_flags, prior_non_full_score_count
from lib._openhands_events import (
    base_state_execution_status,
    cache_hit_info,
    collect_agent_llm_rounds,
    collect_error_events,
    collect_error_events_from_jsonl,
    collect_llm_metrics,
    read_json,
    safe_float,
    successful_llm_call_count,
)
from lib._paths import DEFAULT_RUNS_DIR, DEFAULT_SUMMARY_PREFIX, output_prefix_for_context
from lib._specs import parse_tasks
from lib._status import classify_termination


FIELDNAMES = [
    "model",
    "run_id",
    "tasks",
    "context_mode",
    "backend",
    "image",
    "container",
    "api_base",
    "started_at",
    "exit_code",
    "container_status",
    "container_exists",
    "output_dir_exists",
    "has_report",
    "has_openhands_state",
    "has_openhands_events",
    "termination_status",
    "termination_reason",
    "termination_detail",
    "openhands_execution_status",
    "last_error_code",
    "last_error_kind",
    "last_error_detail",
    "interrupted_by_user",
    "max_iterations",
    "successful_llm_calls",
    "failed_llm_calls",
    "remaining_iterations",
    "max_agent_hours",
    "elapsed_seconds",
    "remaining_wall_seconds",
    "agent_llm_rounds_source",
    "run_agent_llm_rounds",
    "llm_metrics_source",
    "llm_tokens_logged",
    "run_llm_cost_usd",
    "run_llm_prompt_tokens",
    "run_llm_completion_tokens",
    "run_llm_reasoning_tokens",
    "run_llm_cache_read_tokens",
    "run_llm_cache_write_tokens",
    "run_llm_total_tokens",
    "cache_prompt_enabled",
    "prompt_cache_retention",
    "cache_hit",
    "cache_hit_tokens",
    "cache_write_tokens",
    "pipeline_score",
    "mean_reward",
    "pass_score",
    "prior_non_full_score_count",
    "zero_shot_pass",
    "missing_outputs",
    "report_task_count",
    "expected_task_count",
    "error",
]
for _tid in range(6):
    FIELDNAMES.extend(
        [
            f"task{_tid}",
            f"task{_tid}_passed",
            f"task{_tid}_agent_llm_rounds",
            f"task{_tid}_llm_total_tokens",
            f"task{_tid}_llm_cost_usd",
        ]
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _latest_task_entries(tasks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for task in tasks:
        tid = task.get("task_id")
        if isinstance(tid, int):
            latest[tid] = task
    return latest


def _model_from_run_id(run_id: str) -> str:
    if "-tasks-" in run_id:
        left = run_id.split("-tasks-", 1)[0]
        return left.split("-", 1)[1] if "-" in left else left
    return run_id


def collect_one(run_dir: Path, *, include_tokens: bool = True) -> dict[str, Any]:
    metadata = read_json(run_dir / "metadata.json") if (run_dir / "metadata.json").is_file() else {}
    report_path = run_dir / "openhands_report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    run_id = str(metadata.get("run_id") or report.get("run_id") or run_dir.name)
    container = str(metadata.get("container") or _docker.container_name(run_id))
    container_data = _docker.inspect_container(container)
    container_status = _docker.container_status(container) if container_data else ""
    exit_code = _read_text(run_dir / "exit_code")
    context_mode = str(metadata.get("context_mode") or report.get("context_mode") or "")
    tasks_spec = str(metadata.get("tasks") or "")
    raw_tasks = report.get("tasks")
    tasks = [task for task in raw_tasks if isinstance(task, dict)] if isinstance(raw_tasks, list) else []
    latest = _latest_task_entries(tasks)
    scores: list[float] = []
    row: dict[str, Any] = {
        "model": metadata.get("model") or report.get("agent_model") or _model_from_run_id(run_id),
        "run_id": run_id,
        "tasks": tasks_spec,
        "context_mode": context_mode,
        "backend": report.get("agent_backend", "openhands"),
        "image": metadata.get("image", ""),
        "container": container,
        "api_base": metadata.get("api_base", ""),
        "started_at": metadata.get("started_at", ""),
        "exit_code": exit_code,
        "container_status": container_status,
        "container_exists": container_data is not None,
        "output_dir_exists": run_dir.is_dir(),
        "has_report": report_path.is_file(),
        "has_openhands_state": (run_dir / "openhands-state").is_dir(),
        "has_openhands_events": (run_dir / "openhands-events").is_dir(),
        "max_iterations": metadata.get("max_iterations", ""),
        "max_agent_hours": metadata.get("max_agent_hours", ""),
        "elapsed_seconds": round(safe_float(report.get("total_elapsed_seconds")), 2),
        "cache_prompt_enabled": metadata.get("llm_cache_prompt", ""),
        "prompt_cache_retention": metadata.get("prompt_cache_retention", ""),
        "report_task_count": len(tasks),
        "expected_task_count": len(parse_tasks(tasks_spec)) if tasks_spec else "",
        "error": report.get("_error", ""),
    }
    for task_id in range(6):
        task = latest.get(task_id, {})
        score = safe_float(task.get("score"))
        scores.append(score)
        row[f"task{task_id}"] = score
        row[f"task{task_id}_passed"] = task.get("passed", "")
    affected = prior_non_full_flags(scores)
    row["pipeline_score"] = round(sum(scores) / 6, 2)
    row["mean_reward"] = round(compute_mean_reward(scores, affected), 2)
    row["pass_score"] = round(compute_pass_score(scores, affected), 2)
    row["prior_non_full_score_count"] = prior_non_full_score_count(scores)
    row["zero_shot_pass"] = all(score >= 60.0 for score in scores)

    row.update(collect_agent_llm_rounds(run_dir, context_mode, report))
    if include_tokens:
        row.update(collect_llm_metrics(run_dir, context_mode))
        row.update(cache_hit_info(run_dir))
    errors = collect_error_events(run_dir)
    last_error = errors[-1] if errors else {}
    successful = successful_llm_call_count(run_dir)
    failed = len([err for err in errors if err.get("code")])
    max_iterations = int(row["max_iterations"] or 0) if str(row["max_iterations"]).isdigit() else 0
    row["successful_llm_calls"] = successful
    row["failed_llm_calls"] = failed
    row["remaining_iterations"] = max(max_iterations - successful, 0) if max_iterations else ""
    max_hours = safe_float(row.get("max_agent_hours"))
    row["remaining_wall_seconds"] = max(round(max_hours * 3600 - safe_float(row.get("elapsed_seconds")), 2), 0) if max_hours else ""
    row["openhands_execution_status"] = base_state_execution_status(run_dir)
    row["last_error_code"] = last_error.get("code", "")
    row["last_error_kind"] = last_error.get("kind", "")
    row["last_error_detail"] = str(last_error.get("detail", ""))[:1000]
    if row["error"] and not row["last_error_detail"]:
        row["last_error_code"] = "RunnerError"
        row["last_error_kind"] = "ReportError"
        row["last_error_detail"] = str(row["error"])[:1000]
    row["interrupted_by_user"] = exit_code in {"130", "-2"}
    missing = []
    for name in ("metadata.json", "exit_code", "openhands_report.json"):
        if not (run_dir / name).exists():
            missing.append(name)
    row["missing_outputs"] = ",".join(missing)
    term = classify_termination(
        exit_code=exit_code,
        openhands_status=str(row["openhands_execution_status"]),
        last_error_code=str(row["last_error_code"]),
        last_error_detail=str(row["last_error_detail"]),
        has_report=bool(row["has_report"]),
        missing_outputs=missing,
    )
    row["termination_status"] = term.status
    row["termination_reason"] = term.reason
    row["termination_detail"] = term.detail
    return row


def collect_launch_status_row(run_dir: Path) -> dict[str, Any]:
    """供 launch 实时行使用：不跑 docker inspect、不扫 base_state 用量，降低卡顿。"""
    metadata = read_json(run_dir / "metadata.json") if (run_dir / "metadata.json").is_file() else {}
    report_path = run_dir / "openhands_report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    raw_tasks = report.get("tasks")
    tasks = [task for task in raw_tasks if isinstance(task, dict)] if isinstance(raw_tasks, list) else []
    latest = _latest_task_entries(tasks)
    row: dict[str, Any] = {
        "max_iterations": metadata.get("max_iterations", ""),
        "successful_llm_calls": successful_llm_call_count(run_dir),
        "failed_llm_calls": 0,
    }
    for task_id in range(6):
        task = latest.get(task_id, {})
        row[f"task{task_id}"] = safe_float(task.get("score"))
    errors = collect_error_events_from_jsonl(run_dir)
    row["failed_llm_calls"] = len([e for e in errors if e.get("code")])
    return row


def aggregate_runs(
    runs_dir: Path = DEFAULT_RUNS_DIR,
    *,
    glob_pattern: str = "*",
    output_prefix: Path | None = None,
    include_tokens: bool = True,
) -> tuple[list[dict[str, Any]], Path, Path]:
    rows = [collect_one(path, include_tokens=include_tokens) for path in sorted(runs_dir.glob(glob_pattern)) if path.is_dir()]
    rows.sort(key=lambda row: (str(row.get("model", "")), str(row.get("run_id", ""))))
    if output_prefix is None:
        contexts = {str(row.get("context_mode", "")) for row in rows}
        output_prefix = output_prefix_for_context("pipeline" if contexts == {"pipeline"} else "per-task")
        if not rows:
            output_prefix = DEFAULT_SUMMARY_PREFIX
    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows, csv_path, json_path


def inspect_run(run_id: str, runs_dir: Path = DEFAULT_RUNS_DIR) -> dict[str, Any]:
    return collect_one(runs_dir / run_id)


def cmd_summarize_router(args: argparse.Namespace) -> int:
    """默认行为为 aggregate；仅 ``inspect`` 为子命令。"""
    if getattr(args, "summarize_cmd", None) == "inspect":
        return cmd_inspect(args)
    return cmd_aggregate(args)


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("summarize", help="汇总或诊断 container-runs 输出（默认生成汇总 JSON/CSV）")
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR), help="container-runs 根目录")
    parser.add_argument("--glob", default="*", help="匹配 run 子目录名的 glob，如 run-pipeline-*")
    parser.add_argument("--output-prefix", default="", help="汇总文件路径前缀，省略则按 context 选默认前缀")
    parser.add_argument("--no-tokens", action="store_true", help="不读取 base_state 中的 token/费用")
    sub = parser.add_subparsers(dest="summarize_cmd", required=False)
    inspect = sub.add_parser("inspect", help="诊断单个 run")
    inspect.add_argument("run_id")
    inspect.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    inspect.set_defaults(func=cmd_summarize_router, summarize_cmd="inspect")
    parser.set_defaults(func=cmd_summarize_router, summarize_cmd=None)


def cmd_aggregate(args: argparse.Namespace) -> int:
    prefix = Path(args.output_prefix) if args.output_prefix else None
    rows, csv_path, json_path = aggregate_runs(
        Path(args.runs_dir),
        glob_pattern=args.glob,
        output_prefix=prefix,
        include_tokens=not args.no_tokens,
    )
    print(f"Collected {len(rows)} runs")
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    row = inspect_run(args.run_id, Path(args.runs_dir))
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0
