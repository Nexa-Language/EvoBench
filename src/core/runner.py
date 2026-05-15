from __future__ import annotations

import argparse
import inspect
import json
import os
import signal
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from core._prompt import TaskGuides, build_task_prompt
from core._report import build_report, write_report
from core._score import build_and_score, build_answers, configure_workspace
from lib._paths import ROOT
from lib._run_budget import format_wall_remaining_display, remaining_iterations_budget, remaining_wall_seconds
from lib._specs import parse_tasks, safe_name


def _load_env() -> None:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "src" / ".env")


def create_agent(model: str, api_base: str, api_key: str, *, caching_prompt: bool, prompt_cache_retention: str | None):
    os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"
    from openhands.sdk import Agent, LLM, Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    model_name = model if not api_base or model.startswith("openai/") else f"openai/{model}"
    extra_body = None
    raw_extra = os.getenv("OPENHANDS_LITELLM_EXTRA_BODY", "").strip()
    if raw_extra:
        try:
            parsed = json.loads(raw_extra)
            if isinstance(parsed, dict):
                extra_body = parsed
        except json.JSONDecodeError:
            print("[警告] OPENHANDS_LITELLM_EXTRA_BODY 不是合法 JSON，已忽略。")
    llm_kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        "base_url": api_base or None,
        "temperature": 0.2,
        "timeout": 300,
        "caching_prompt": caching_prompt,
        "prompt_cache_retention": prompt_cache_retention if caching_prompt else None,
    }
    if extra_body:
        llm_kwargs["litellm_extra_body"] = extra_body
    return Agent(
        llm=LLM(**llm_kwargs),
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
    )


def event_logger(path: Path) -> Callable[[Any], None]:
    path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(event: Any) -> None:
        try:
            payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else {"repr": repr(event)}
            payload.setdefault("event_type", type(event).__name__)
        except Exception as exc:
            payload = {"event_type": type(event).__name__, "repr": repr(event), "serialization_error": repr(exc)}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return log_event


def run_conversation(conversation: Any, max_iterations: int) -> None:
    """Run a Conversation across OpenHands SDK versions.

    Some SDK versions take the iteration cap only from the Conversation constructor
    (`max_iteration_per_run`) and expose `run()` without keyword arguments.
    """
    try:
        signature = inspect.signature(conversation.run)
    except (TypeError, ValueError):
        conversation.run()
        return
    if "max_iterations" in signature.parameters:
        conversation.run(max_iterations=max_iterations)
    else:
        conversation.run()


def prepare_workspace(workspace: Path | None) -> Path:
    default_workspace = ROOT / "data" / "YatCC"
    if not default_workspace.exists():
        default_workspace = ROOT / "YatCC"
    if workspace is None:
        return default_workspace
    if not workspace.exists():
        print(f"[工作区] 创建独立工作区: {workspace}")
        shutil.copytree(default_workspace, workspace, symlinks=True)
    return workspace


def _caching_enabled(no_caching_prompt: bool) -> bool:
    if no_caching_prompt:
        return False
    return os.getenv("OPENHANDS_CACHING_PROMPT", "1").strip().lower() not in {"0", "false", "no", "off"}


def run_openhands(args: argparse.Namespace) -> int:
    _load_env()
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "eval" / "results" / safe_name(args.model)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_dir = output_dir / "openhands-events"
    persistence_root = output_dir / "openhands-state"
    workspace = prepare_workspace(Path(args.workspace) if args.workspace else None)
    tasks = parse_tasks(args.tasks)
    run_id = args.run_id or f"{safe_name(args.model)}-{time.strftime('%Y%m%d-%H%M%S')}"
    caching_prompt = _caching_enabled(args.no_caching_prompt)
    retention = (args.prompt_cache_retention or "").strip()
    prompt_cache_retention = retention if caching_prompt and retention else None
    api_base = os.getenv("OPENAI_API_BASE", "")
    api_key = os.getenv("OPENAI_API_KEY", "")
    results: list[dict[str, Any]] = []
    start = time.time()
    error = ""
    report_path = output_dir / "openhands_report.json"

    def _handle_stop(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    old_int = signal.signal(signal.SIGINT, _handle_stop)
    old_term = signal.signal(signal.SIGTERM, _handle_stop)
    try:
        print("=" * 70)
        print("OpenHands Agent EvoBench Runner")
        print(f"模型: {args.model}")
        print(f"任务: {tasks}")
        print(f"上下文模式: {args.context_mode}")
        print(f"最大迭代: {args.max_iterations}")
        print(f"LLM 提示词缓存: {'是' if caching_prompt else '否'}")
        print(f"Run ID: {run_id}")
        print(f"输出目录: {output_dir}")
        print("=" * 70)
        agent = create_agent(
            args.model,
            api_base,
            api_key,
            caching_prompt=caching_prompt,
            prompt_cache_retention=prompt_cache_retention,
        )
        print("[初始化] CMake 配置（TASK*_REVIVE=ON）...")
        configure_workspace(workspace)
        print("[初始化] 生成标准答案...")
        build_answers(workspace)

        from openhands.sdk import Conversation

        guides = TaskGuides(ROOT)
        pipeline_conversation = None
        if args.context_mode == "pipeline":
            pipeline_conversation = Conversation(
                agent=agent,
                workspace=str(workspace),
                max_iteration_per_run=args.max_iterations,
                callbacks=[event_logger(events_dir / "pipeline.jsonl")],
                persistence_dir=persistence_root / "pipeline",
                tags={"runid": run_id, "model": args.model, "contextmode": "pipeline"},
            )

        first_pipeline_task = True
        for task_id in tasks:
            task_start = time.time()
            print(f"[Task {task_id}] 开始")
            if pipeline_conversation is not None:
                conversation = pipeline_conversation
                stage = "first" if first_pipeline_task else "continue"
                first_pipeline_task = False
            else:
                stage = None
                conversation = Conversation(
                    agent=agent,
                    workspace=str(workspace),
                    max_iteration_per_run=args.max_iterations,
                    callbacks=[event_logger(events_dir / f"task{task_id}.jsonl")],
                    persistence_dir=persistence_root / f"task{task_id}",
                    tags={"runid": run_id, "taskid": str(task_id), "model": args.model},
                )
            wall_sec = remaining_wall_seconds(output_dir)
            prompt = build_task_prompt(
                task_id=task_id,
                context_mode=args.context_mode,
                workspace=workspace,
                guides=guides,
                pipeline_stage=stage,
                budget_wall_display=format_wall_remaining_display(wall_sec),
                budget_remaining_iterations=remaining_iterations_budget(
                    output_dir, args.context_mode, args.max_iterations
                ),
            )
            conversation.send_message(prompt)
            try:
                run_conversation(conversation, args.max_iterations)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                print(f"[Task {task_id}] Agent 异常: {error}")
            score = build_and_score(workspace, task_id)
            entry = {
                "task_id": task_id,
                "score": score.get("score", 0),
                "max_score": score.get("max_score", 100),
                "passed": score.get("passed", False),
                "test_count": score.get("test_count", 0),
                "passed_count": score.get("passed_count", 0),
                "elapsed_seconds": time.time() - task_start,
                "backend": "openhands",
            }
            if score.get("error"):
                entry["error"] = score["error"]
            results.append(entry)
            print(f"[Task {task_id}] score={entry['score']:.1f}/100 passed={entry['passed']}")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        report = build_report(
            model=args.model,
            context_mode=args.context_mode,
            run_id=run_id,
            results=results,
            total_elapsed=time.time() - start,
            error=error,
        )
        report["metrics"]["total_iterations"] = len(tasks) * args.max_iterations
        write_report(report_path, report)
        print(f"[报告] {report_path}")
    return 0


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("runner", help="容器内 OpenHands 执行入口")
    sub = parser.add_subparsers(dest="runner_cmd", required=True)
    openhands = sub.add_parser("openhands", help="运行 OpenHands 评测")
    openhands.add_argument("--model", default=os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro"))
    openhands.add_argument("--tasks", default="0-5")
    openhands.add_argument("--max-iterations", type=int, default=50)
    openhands.add_argument("--workspace", default=None)
    openhands.add_argument("--run-id", default=None)
    openhands.add_argument("--output-dir", default=None)
    openhands.add_argument("--context-mode", choices=["per-task", "pipeline"], default="per-task")
    openhands.add_argument("--no-caching-prompt", action="store_true")
    openhands.add_argument("--prompt-cache-retention", default=os.getenv("OPENHANDS_PROMPT_CACHE_RETENTION") or "24h")
    openhands.set_defaults(func=run_openhands)
    score = sub.add_parser("score", help="构建并评分单个 task")
    score.add_argument("--workspace", required=True)
    score.add_argument("--task", type=int, required=True)
    score.set_defaults(func=cmd_score)
    report = sub.add_parser("emit-report", help="写一个空 partial report")
    report.add_argument("--output-dir", required=True)
    report.add_argument("--model", default="")
    report.add_argument("--run-id", default="")
    report.add_argument("--context-mode", default="per-task")
    report.set_defaults(func=cmd_emit_report)


def cmd_score(args: argparse.Namespace) -> int:
    result = build_and_score(Path(args.workspace), args.task)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


def cmd_emit_report(args: argparse.Namespace) -> int:
    report = build_report(
        model=args.model,
        context_mode=args.context_mode,
        run_id=args.run_id,
        results=[],
        total_elapsed=0.0,
        error="partial report emitted manually",
    )
    write_report(Path(args.output_dir) / "openhands_report.json", report)
    return 0
