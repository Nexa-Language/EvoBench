from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from core.job_manage import check_conflicts
from core.summarize import collect_launch_status_row
from lib import _docker
from lib._api_keys import ApiKeyEntry, load_api_keys, select_api_key
from lib._paths import DEFAULT_API_KEYS_FILE, DEFAULT_IMAGE, DEFAULT_MODELS_FILE, DEFAULT_RUNS_DIR, ROOT, load_api_base
from lib._specs import (
    DEFAULT_TASKS,
    ExperimentSpec,
    add_csv_models,
    load_models,
    parse_experiment_specs,
    read_experiment_file,
)

# 实时状态行刷新周期（秒）。仅唤醒 wait 以重绘；设为 5 可减轻对输出目录的扫描频率。
STATUS_REFRESH_SEC = 1.0


def default_cache_env(retention: str = "24h") -> dict[str, str]:
    return {"OPENHANDS_CACHING_PROMPT": "1", "OPENHANDS_PROMPT_CACHE_RETENTION": retention}


def write_metadata(
    *,
    output_dir: Path,
    spec: ExperimentSpec,
    image: str,
    api_base: str,
    key_entry: ApiKeyEntry,
    context_mode: str,
    max_iterations: int,
    prompt_cache_retention: str,
    litellm_log_level: str,
    max_agent_hours: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "run_id": spec.run_id,
        "model": spec.model,
        "tasks": spec.tasks,
        "context_mode": context_mode,
        "max_iterations": max_iterations,
        "image": image,
        "api_base": api_base,
        "api_key_name": key_entry.name,
        "llm_cache_prompt": 1,
        "prompt_cache_retention": prompt_cache_retention,
        "litellm_log_level": litellm_log_level,
        "max_agent_hours": max_agent_hours,
        "container": _docker.container_name(spec.run_id),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _inner_command(spec: ExperimentSpec, max_iterations: int, context_mode: str, max_agent_hours: int) -> str:
    cmd = [
        "python3",
        "src/main.py",
        "runner",
        "openhands",
        "--model",
        spec.model,
        "--tasks",
        spec.tasks,
        "--max-iterations",
        str(max_iterations),
        "--context-mode",
        context_mode,
        "--workspace",
        "/workspace/YatCC",
        "--run-id",
        spec.run_id,
        "--output-dir",
        "/workspace/output",
    ]
    inner = " ".join(shlex.quote(part) for part in cmd) + " > /workspace/output/console.log 2>&1"
    if max_agent_hours > 0:
        return f"timeout --signal=TERM --kill-after=120 {shlex.quote(str(max_agent_hours) + 'h')} bash -c {shlex.quote(inner)}"
    return inner


def launch_one(
    *,
    spec: ExperimentSpec,
    key_entry: ApiKeyEntry,
    output_root: Path,
    image: str,
    api_base: str,
    context_mode: str,
    max_iterations: int,
    prompt_cache_retention: str,
    litellm_log_level: str,
    max_agent_hours: int,
) -> tuple[subprocess.Popen[str], Path, str]:
    output_dir = output_root / spec.run_id
    container = _docker.container_name(spec.run_id)
    write_metadata(
        output_dir=output_dir,
        spec=spec,
        image=image,
        api_base=api_base,
        key_entry=key_entry,
        context_mode=context_mode,
        max_iterations=max_iterations,
        prompt_cache_retention=prompt_cache_retention,
        litellm_log_level=litellm_log_level,
        max_agent_hours=max_agent_hours,
    )
    _docker.remove_container(container)
    env_args: list[str] = []
    if (ROOT / ".env").is_file():
        env_args.extend(["--env-file", str(ROOT / ".env")])
    env = default_cache_env(prompt_cache_retention)
    docker_cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        container,
        *env_args,
        "-e",
        f"OPENAI_API_BASE={api_base}",
        "-e",
        f"OPENAI_API_KEY={key_entry.key}",
        "-e",
        f"OPENAI_MODEL_NAME={spec.model}",
        "-e",
        f"OPENHANDS_CACHING_PROMPT={env['OPENHANDS_CACHING_PROMPT']}",
        "-e",
        f"OPENHANDS_PROMPT_CACHE_RETENTION={env['OPENHANDS_PROMPT_CACHE_RETENTION']}",
    ]
    if litellm_log_level:
        docker_cmd.extend(["-e", f"LITELLM_LOG_LEVEL={litellm_log_level}"])
    docker_cmd.extend(
        [
            "-v",
            f"{ROOT}:/workspace/EvoBench:ro",
            "-v",
            f"{output_dir}:/workspace/output",
            "-w",
            "/workspace/EvoBench",
            image,
            "bash",
            "-lc",
            _inner_command(spec, max_iterations, context_mode, max_agent_hours),
        ]
    )
    print(f"[启动] {spec.run_id} | model={spec.model} | tasks={spec.tasks} | key={key_entry.name}")
    proc = subprocess.run(docker_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        (output_dir / "exit_code").write_text(str(proc.returncode), encoding="utf-8")
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return subprocess.Popen(["docker", "wait", container], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True), output_dir, container


def _write_exit_code(output_dir: Path, wait_proc: subprocess.Popen[str]) -> int:
    stdout, _stderr = wait_proc.communicate()
    try:
        code = int(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        code = wait_proc.returncode or 1
    (output_dir / "exit_code").write_text(f"{code}\n", encoding="utf-8")
    return code


def _metadata_started_and_max_hours(output_dir: Path) -> tuple[float | None, int | None]:
    """一次读取 metadata：started_at 的 Unix 时间戳、max_agent_hours。"""
    path = output_dir / "metadata.json"
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    s = str(data.get("started_at", "")).strip()
    ts: float | None = None
    if s:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(s).timestamp()
        except ValueError:
            ts = None
    max_h: int | None = None
    raw = data.get("max_agent_hours")
    if raw is not None:
        try:
            max_h = int(raw)
        except (TypeError, ValueError):
            max_h = None
    return ts, max_h


def _wall_used_rem_str(output_dir: Path) -> tuple[str, str]:
    """宿主机墙钟：已用秒数、剩余秒数（无 timeout 上限时剩余为 --）。"""
    ts, max_h = _metadata_started_and_max_hours(output_dir)
    if ts is None:
        return "?", "?"
    used = int(max(0.0, time.time() - ts))
    used_s = f"{used}s"
    if max_h is None or max_h <= 0:
        return used_s, "--"
    rem = int(max_h * 3600 - used)
    rem = max(rem, 0)
    return used_s, f"{rem}s"


def _status_segment(spec: ExperimentSpec, output_dir: Path) -> str:
    try:
        row = collect_launch_status_row(output_dir)
        scores = ",".join(str(row.get(f"task{i}", "")) for i in range(6))
        used_s, rem_s = _wall_used_rem_str(output_dir)
        return (
            f"{spec.run_id}: calls {row.get('successful_llm_calls', '?')}/{row.get('max_iterations', '?')} "
            f"fail {row.get('failed_llm_calls', '?')} used={used_s} rem={rem_s} scores [{scores}]"
        )
    except Exception:
        return f"{spec.run_id}: starting"


def _status_line(runs: list[tuple[ExperimentSpec, Path]], *, max_width: int | None) -> str:
    line = " | ".join(_status_segment(spec, output_dir) for spec, output_dir in runs)
    if max_width is not None and len(line) > max_width:
        return line[:max_width]
    return line


def run_batch(
    *,
    specs: list[ExperimentSpec],
    key_entries: list[ApiKeyEntry],
    output_root: Path,
    image: str,
    api_base: str,
    context_mode: str,
    max_iterations: int,
    prompt_cache_retention: str,
    litellm_log_level: str,
    max_agent_hours: int,
    parallel: int,
) -> int:
    output_root.mkdir(parents=True, exist_ok=True)
    running: dict[Future[int], tuple[ExperimentSpec, Path, str]] = {}
    active_runs: list[tuple[ExperimentSpec, Path]] = []
    containers: list[str] = []
    failures = 0
    interrupted = False

    def stop_all(_signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        print("\n收到中断信号，停止已启动容器...")
        for name in containers:
            _docker.stop_container(name)

    old_int = signal.signal(signal.SIGINT, stop_all)
    old_term = signal.signal(signal.SIGTERM, stop_all)
    try:
        with ThreadPoolExecutor(max_workers=max(parallel, 1)) as executor:
            pending = list(specs)
            while pending or running:
                while pending and len(running) < parallel and not interrupted:
                    spec = pending.pop(0)
                    key_entry = select_api_key(key_entries, spec.model)
                    wait_proc, output_dir, container = launch_one(
                        spec=spec,
                        key_entry=key_entry,
                        output_root=output_root,
                        image=image,
                        api_base=api_base,
                        context_mode=context_mode,
                        max_iterations=max_iterations,
                        prompt_cache_retention=prompt_cache_retention,
                        litellm_log_level=litellm_log_level,
                        max_agent_hours=max_agent_hours,
                    )
                    containers.append(container)
                    active_runs.append((spec, output_dir))
                    running[executor.submit(_write_exit_code, output_dir, wait_proc)] = (spec, output_dir, container)
                if running:
                    done, _ = wait(running, timeout=STATUS_REFRESH_SEC, return_when=FIRST_COMPLETED)
                    width = max(shutil.get_terminal_size((120, 20)).columns - 1, 20)
                    print("\r" + _status_line(active_runs, max_width=width), end="", flush=True)
                    for future in done:
                        spec, output_dir, _container = running.pop(future)
                        try:
                            code = future.result()
                        except Exception:
                            code = 1
                        if code != 0:
                            failures += 1
                        print(f"\n[结束] {spec.run_id} exit_code={code}")
                        print("[状态]", _status_line(active_runs, max_width=None))
                elif pending:
                    time.sleep(0.5)
        if active_runs:
            print("\n[本轮结束 · 最终状态]")
            print(_status_line(active_runs, max_width=None))
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        print("")
    return failures if not interrupted else max(failures, 1)


def build_specs_from_args(args: argparse.Namespace) -> list[ExperimentSpec]:
    selected: list[str] = []
    explicit: list[str] = list(args.experiment or [])
    if args.all_models:
        selected.extend(load_models(Path(args.models_file)))
    for model in args.model or []:
        selected.append(model)
    if args.models:
        add_csv_models(selected, args.models)
    if args.experiments_file:
        explicit.extend(read_experiment_file(Path(args.experiments_file)))
    return parse_experiment_specs(
        selected_models=selected,
        explicit_specs=explicit,
        default_tasks=args.tasks,
        run_prefix=args.run_prefix,
    )


def cmd_launch(args: argparse.Namespace) -> int:
    if getattr(args, "launch_action", None) == "test-cache":
        return cmd_test_cache(args)
    api_base = load_api_base(explicit=args.api_base)
    specs = build_specs_from_args(args)
    key_entries = load_api_keys(Path(args.api_keys))
    conflicts = check_conflicts([spec.run_id for spec in specs], Path(args.output_dir))
    if conflicts:
        print("存在 run_id 冲突，批次未启动：")
        for trace in conflicts:
            print(json.dumps(trace.__dict__, ensure_ascii=False))
        return 2
    print("==========================================")
    print("OpenHands Docker 并行评测计划")
    print(f"镜像: {args.image}")
    print(f"API Base: {api_base}")
    print(f"上下文模式: {args.context_mode}")
    print(f"并行数: {args.parallel}")
    print(f"输出目录: {args.output_dir}")
    print("==========================================")
    for index, spec in enumerate(specs, 1):
        key = select_api_key(key_entries, spec.model)
        print(f"[{index}/{len(specs)}] model={spec.model} tasks={spec.tasks} run_id={spec.run_id} key={key.name}")
    if args.dry_run:
        print("dry-run：未启动容器。")
        return 0
    return run_batch(
        specs=specs,
        key_entries=key_entries,
        output_root=Path(args.output_dir),
        image=args.image,
        api_base=api_base,
        context_mode=args.context_mode,
        max_iterations=args.max_iterations,
        prompt_cache_retention=args.prompt_cache_retention,
        litellm_log_level=args.litellm_log_level,
        max_agent_hours=args.max_agent_hours,
        parallel=args.parallel,
    )


def cmd_test_cache(args: argparse.Namespace) -> int:
    from core._cache import test_cache

    return test_cache(args)


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("launch", help="启动 OpenHands Docker 评测")
    parser.add_argument("launch_action", nargs="?", choices=["test-cache"])
    parser.add_argument("--all-models", action="store_true")
    parser.add_argument("--model", action="append")
    parser.add_argument("--models", default="")
    parser.add_argument("--experiment", action="append")
    parser.add_argument("--experiments-file", default="")
    parser.add_argument("--models-file", default=str(DEFAULT_MODELS_FILE))
    parser.add_argument("--api-keys", default=str(DEFAULT_API_KEYS_FILE))
    parser.add_argument("--api-base", default="")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--output-dir", default=str(DEFAULT_RUNS_DIR))
    parser.add_argument("--tasks", default=DEFAULT_TASKS)
    parser.add_argument("--max-iterations", type=int, default=200)
    parser.add_argument("--context-mode", choices=["per-task", "pipeline"], default="per-task")
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--run-prefix", default=f"run-{time.strftime('%Y%m%d-%H%M%S')}")
    parser.add_argument("--prompt-cache-retention", default="24h")
    parser.add_argument("--litellm-log-level", default="")
    parser.add_argument("--max-agent-hours", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id", default="", help="仅用于 launch test-cache")
    parser.set_defaults(func=cmd_launch)
