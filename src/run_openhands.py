#!/usr/bin/env python3
"""使用 OpenHands SDK 运行 EvoBench 评测。

此脚本在 .venv-openhands (Python 3.12+) 虚拟环境中运行，
使用真正的 Agent 框架（无限自循环、文件读写、命令执行）。

用法:
    . .venv-openhands/bin/activate
    python run_openhands.py --model mimo-v2.5-pro --tasks 0-5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# 加载环境变量（优先项目根目录 .env，与 README 一致）
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env")
load_dotenv(_root / "src" / ".env")  # 可选：本地覆盖

# OpenHands Terminal 在 Windows 上会探测 powershell.exe；部分环境的 PATH 过窄会导致误判
if sys.platform == "win32":
    _win = os.environ.get("SystemRoot", r"C:\Windows")
    _prefix = os.pathsep.join(
        [
            os.path.join(_win, "System32", "WindowsPowerShell", "v1.0"),
            os.path.join(_win, "System32"),
        ]
    )
    _path = os.environ.get("PATH", "")
    if _prefix not in _path:
        os.environ["PATH"] = _prefix + os.pathsep + _path

YATCC_ROOT = _root / "data" / "YatCC"
if not YATCC_ROOT.exists():
    YATCC_ROOT = _root / "YatCC"  # fallback
OUTPUT_DIR = _root / "eval" / "results"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── OpenHands SDK ──────────────────────────────────────────────────────

os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool


def create_agent(
    model: str,
    api_base: str,
    api_key: str,
    *,
    caching_prompt: bool = True,
    prompt_cache_retention: str | None = None,
) -> Agent:
    """创建 OpenHands Agent。"""
    model_name = model
    if api_base and not model_name.startswith("openai/"):
        model_name = f"openai/{model}"

    litellm_extra: dict[str, Any] | None = None
    raw_extra = os.getenv("OPENHANDS_LITELLM_EXTRA_BODY", "").strip()
    if raw_extra:
        try:
            parsed = json.loads(raw_extra)
            if isinstance(parsed, dict):
                litellm_extra = parsed
            else:
                print("[警告] OPENHANDS_LITELLM_EXTRA_BODY 须为 JSON 对象，已忽略。")
        except json.JSONDecodeError:
            print("[警告] OPENHANDS_LITELLM_EXTRA_BODY 不是合法 JSON，已忽略。")

    llm_kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        "base_url": api_base if api_base else None,
        "temperature": 0.2,
        "timeout": 300,
        "caching_prompt": caching_prompt,
        "prompt_cache_retention": prompt_cache_retention if caching_prompt else None,
    }
    if litellm_extra:
        llm_kwargs["litellm_extra_body"] = litellm_extra

    llm = LLM(**llm_kwargs)

    return Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
    )


# 任务指南（data/task_guides.md）：全文、全局前言、各 Task 小节正文
TASK_GUIDE_FULL = ""
TASK_GUIDE_PREAMBLE = ""
TASK_GUIDES: dict[int, str] = {}


def _load_task_guides() -> None:
    """加载 data/task_guides.md：全文、首个 ## Task 之前的前言、各 Task 小节。"""
    global TASK_GUIDE_FULL, TASK_GUIDE_PREAMBLE, TASK_GUIDES
    guide_paths = [
        Path(__file__).parent.parent / "data" / "task_guides.md",
        Path(__file__).parent / ".." / "data" / "task_guides.md",
        Path("data/task_guides.md"),
    ]
    guide_content = ""
    for p in guide_paths:
        if p.exists():
            guide_content = p.read_text(encoding="utf-8")
            break

    if not guide_content:
        return

    TASK_GUIDE_FULL = guide_content
    sections = re.split(r"^## Task (\d+):", guide_content, flags=re.MULTILINE)
    TASK_GUIDE_PREAMBLE = sections[0].strip() if sections else ""
    for i in range(1, len(sections), 2):
        tid = int(sections[i])
        # re.split 会去掉匹配到的 `## Task N:`，补回前缀以保留标题行（与文档一致）
        body = sections[i + 1].lstrip()
        TASK_GUIDES[tid] = f"## Task {tid}: {body}".strip()


_load_task_guides()


def _yatcc_path_note() -> str:
    """文档与 task_guides 使用 /YatCC 作为仓库根；实际 OpenHands workspace 可能为其它路径。"""
    root = str(YATCC_ROOT).replace("\\", "/")
    return (
        f"说明：下文与 `data/task_guides.md` 中的路径以仓库根 **/YatCC** 为基准；"
        f"当前工作区根目录为 `{root}`（与 **/YatCC** 等价，例如 `task/3/README.md` 即 `{root}/task/3/README.md`）。\n"
    )


def build_task_prompt(
    task_id: int,
    context_mode: str,
    *,
    pipeline_stage: str | None = None,
) -> str:
    """组装发给 Agent 的提示词。

    context_mode:
      - per-task: data/task_guides.md 前言 + 当前 Task 对应小节 + 当前 task/README.md + 文件列表 + 流程
      - pipeline + pipeline_stage=first: 全文 task_guides + README 目录说明 + 当前 Task 的 README 与流程
      - pipeline + pipeline_stage=continue: 仅当前 Task 的 README + 文件列表 + 流程（不再重复全文指南）
      - pipeline_stage=resurrect: 与 per-task 相同结构（复活重试）
    """
    readme_path = YATCC_ROOT / "task" / str(task_id) / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    task_dir = YATCC_ROOT / "task" / str(task_id)
    files: list[str] = []
    if task_dir.exists():
        for f in sorted(task_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                files.append(str(f.relative_to(task_dir)))

    build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
    score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score",
                     3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}
    bt = build_targets.get(task_id, f"task{task_id}")
    st = score_targets.get(task_id, f"task{task_id}-score")

    file_block = "\n".join(f"- {f}" for f in files) if files else "- （无）"

    workflow = f"""## 工作流程
{_yatcc_path_note()}
1. 阅读本消息中的指南与 `/YatCC/task/{task_id}/README.md` 要求，理解输入/输出与评分标准
2. 阅读 `task/{task_id}/` 下需要修改的源文件
3. 编写/修改代码实现功能
4. 编译: `cmake --build build -t {bt}`
5. 评测: `cmake --build build -t {st}`
6. 查看结果: `cat build/test/task{task_id}/score.txt`（若存在）
7. 若失败，分析错误并修复，重复步骤 4–6
8. 评测通过（得分 >= 60%）即视为本 Task 完成

请开始完成 Task {task_id}。
"""

    guide_body = TASK_GUIDES.get(task_id, "")

    if pipeline_stage == "resurrect" or context_mode == "per-task":
        preamble = TASK_GUIDE_PREAMBLE or ""
        guide_section = guide_body or ""
        return f"""# Task {task_id}: 编译原理实验

## `@data/task_guides.md` — 全局说明（文档对应行 1–首个 `## Task` 之前）
{preamble}

## `@data/task_guides.md` — Task {task_id} 小节
{guide_section if guide_section else "（该 Task 在 task_guides.md 中无独立小节）"}

## `@data/YatCC/task/{task_id}/README.md` — 实验说明
{readme}

## 当前 Task 代码文件（相对 `task/{task_id}/`）
{file_block}

{workflow}
"""

    if context_mode == "pipeline" and pipeline_stage == "first":
        full = TASK_GUIDE_FULL or ""
        readme_hint = (
            "此外，你会在每个任务的代码目录中找到单独的 `README.md` "
            "（相对路径为 `task/<n>/README.md`，以 **/YatCC** 为仓库根）。\n"
        )
        return f"""# Pipeline 模式：连续完成多个 Task

## `@data/task_guides.md`（全文，对应文档第 1–84 行）
{full}

{readme_hint}
---

# Task {task_id}: 编译原理实验

## `@data/YatCC/task/{task_id}/README.md` — 实验说明
{readme}

## 当前 Task 代码文件（相对 `task/{task_id}/`）
{file_block}

{workflow}
"""

    if context_mode == "pipeline" and pipeline_stage == "continue":
        return f"""# Task {task_id}: 编译原理实验（Pipeline 延续）

全局指南已在对话开头提供完整 `data/task_guides.md`；本阶段请重点阅读当前 Task 的 `README.md`。

## `@data/YatCC/task/{task_id}/README.md` — 实验说明
{readme}

## 当前 Task 代码文件（相对 `task/{task_id}/`）
{file_block}

{workflow}
"""

    # 兜底：与 per-task 相同结构（避免递归到 resurrect）
    return build_task_prompt(task_id, "per-task")


def parse_score(task_id: int) -> dict:
    """解析 score.json。"""
    # Task 4/5 的文件名是 score-classic.json，其他是 score.json
    score_json = YATCC_ROOT / f"build/test/task{task_id}/score.json"
    if not score_json.exists():
        score_json = YATCC_ROOT / f"build/test/task{task_id}/score-classic.json"
    if not score_json.exists():
        return {"task_id": task_id, "score": 0, "max_score": 100, "passed": False}

    with open(score_json) as f:
        data = json.load(f)

    tests = data.get("tests", [])
    leaderboard = data.get("leaderboard", [])

    total = 0.0
    for lb in leaderboard:
        if lb.get("name") == "总分":
            total = float(lb["value"])
            break

    # 兼容无 leaderboard 的情况（如 task0）
    if total == 0.0 and tests:
        total = sum(t.get("score", 0) for t in tests) / len(tests)

    test_count = len(tests)
    passed_count = sum(1 for t in tests if t.get("score", 0) >= t.get("max_score", 100))

    return {
        "task_id": task_id,
        "score": total,
        "max_score": 100.0,
        "passed": total >= 60.0,
        "test_count": test_count,
        "passed_count": passed_count,
    }


def build_and_score(task_id: int) -> dict:
    """构建并评测。"""
    build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
    score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score",
                     3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}

    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", build_targets.get(task_id, f"task{task_id}")],
        cwd=str(YATCC_ROOT), capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    if rc.returncode != 0:
        err = (rc.stderr or "") or (rc.stdout or "")
        return {"task_id": task_id, "score": 0, "max_score": 100, "passed": False,
                "error": err[-500:]}

    subprocess.run(
        ["cmake", "--build", "build", "-t", score_targets.get(task_id, f"task{task_id}-score")],
        cwd=str(YATCC_ROOT), capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace",
    )

    return parse_score(task_id)


def trigger_resurrection(failed_task: int) -> None:
    """触发复活。"""
    config_path = YATCC_ROOT / "config.cmake"
    if config_path.exists():
        content = config_path.read_text()
        for i in range(failed_task, 6):
            content = re.sub(
                rf'set\(TASK{i}_REVIVE\s+\w+\)',
                f'set(TASK{i}_REVIVE ON)',
                content,
            )
        config_path.write_text(content)

    subprocess.run(
        ["cmake", "-S", ".", "-B", "build", "-GNinja"],
        cwd=str(YATCC_ROOT), capture_output=True, timeout=60,
    )

    answer_targets = {2: "task2-answer", 3: "task3-answer", 4: "task4-answer", 5: "task5-answer"}
    target = answer_targets.get(failed_task)
    if target:
        subprocess.run(["cmake", "--build", "build", "-t", target],
                       cwd=str(YATCC_ROOT), capture_output=True, timeout=300)


def _safe_name(value: str) -> str:
    """把模型名转换为可用作文件/目录名的字符串。"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "openhands"


def _event_logger(path: Path):
    """创建 OpenHands 事件 JSONL 记录器。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(event) -> None:
        try:
            if hasattr(event, "model_dump"):
                payload = event.model_dump(mode="json")
            else:
                payload = {"repr": repr(event)}
            payload.setdefault("event_type", type(event).__name__)
        except Exception as exc:
            payload = {
                "event_type": type(event).__name__,
                "repr": repr(event),
                "serialization_error": repr(exc),
            }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return log_event


# ─── 主流程 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OpenHands Agent EvoBench Runner")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro"))
    parser.add_argument("--tasks", default="0-5", help="任务范围 (如 0-5 或 0,1,2,3)")
    parser.add_argument("--max-iterations", type=int, default=50, help="每个 Task 最大迭代轮次")
    parser.add_argument("--resurrect", action="store_true", default=True, help="启用复活")
    parser.add_argument("--no-resurrect", action="store_false", dest="resurrect")
    parser.add_argument("--workspace", default=None, help="自定义工作区路径（用于并行运行）")
    parser.add_argument("--run-id", default=None, help="本次运行 ID（用于并行输出隔离）")
    parser.add_argument("--output-dir", default=None, help="报告和 OpenHands message/event 输出目录")
    parser.add_argument(
        "--context-mode",
        choices=["per-task", "pipeline"],
        default="per-task",
        help="上下文模式：per-task=每个 Task 新建 Conversation；pipeline=复用同一个 Conversation 连续完成所有 Task",
    )
    parser.add_argument(
        "--no-caching-prompt",
        action="store_true",
        help="关闭 OpenHands LLM 提示词缓存（否则默认开启；也可用环境变量 OPENHANDS_CACHING_PROMPT=0）",
    )
    parser.add_argument(
        "--prompt-cache-retention",
        default=os.getenv("OPENHANDS_PROMPT_CACHE_RETENTION") or "24h",
        metavar="STR",
        help="提示词缓存 TTL（OpenHands 透传；Anthropic 系常见为 5m / 1h）。关闭缓存时忽略。默认环境变量或 24h",
    )
    args = parser.parse_args()

    if args.no_caching_prompt:
        caching_prompt = False
    else:
        caching_prompt = os.getenv("OPENHANDS_CACHING_PROMPT", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
    pcr = (args.prompt_cache_retention or "").strip()
    prompt_cache_retention: str | None = pcr if caching_prompt and pcr else None
    run_id = args.run_id or f"{_safe_name(args.model)}-{time.strftime('%Y%m%d-%H%M%S')}"
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    events_dir = output_dir / "openhands-events"
    persistence_root = output_dir / "openhands-state"

    # 如果指定了自定义工作区，复制 YatCC 到该目录（用于并行运行）
    global YATCC_ROOT
    if args.workspace:
        ws = Path(args.workspace)
        if not ws.exists():
            import shutil
            print(f"[工作区] 创建独立工作区: {ws}")
            shutil.copytree(YATCC_ROOT, ws, symlinks=True)
        YATCC_ROOT = ws
        print(f"[工作区] 使用: {YATCC_ROOT}")

    # 解析任务范围
    tasks = []
    for part in args.tasks.split(","):
        if "-" in part:
            s, e = part.split("-", 1)
            tasks.extend(range(int(s), int(e) + 1))
        else:
            tasks.append(int(part))
    tasks = sorted(set(tasks))

    api_base = os.getenv("OPENAI_API_BASE", "")
    api_key = os.getenv("OPENAI_API_KEY", "")

    print(f"\n{'='*70}")
    print(f"  OpenHands Agent EvoBench Runner")
    print(f"  模型: {args.model}")
    print(f"  API: {api_base}")
    print(f"  任务: {tasks}")
    print(f"  上下文模式: {args.context_mode}")
    print(f"  最大迭代: {args.max_iterations}")
    print(f"  复活: {'是' if args.resurrect else '否'}")
    print(f"  LLM 提示词缓存: {'是' if caching_prompt else '否'}" + (f" (retention={prompt_cache_retention})" if prompt_cache_retention else ""))
    if caching_prompt:
        print(
            "  提示: 若 base_state 里 cache_read_tokens / cached 类字段始终为 0，多为当前 "
            "OPENAI_API_BASE 上的模型未实现服务端 prompt cache（OpenAI 兼容网关上的 Qwen 等很常见），"
            "与脚本是否传参无关。可设 LITELLM_LOG_LEVEL=DEBUG 或容器内环境 OPENHANDS_LITELLM_EXTRA_BODY（JSON）做网关级排查。"
        )
    print(f"  Run ID: {run_id}")
    print(f"  输出目录: {output_dir}")
    print(f"{'='*70}\n")

    # 创建 Agent
    agent = create_agent(
        args.model,
        api_base,
        api_key,
        caching_prompt=caching_prompt,
        prompt_cache_retention=prompt_cache_retention,
    )

    # CMake 配置
    print("[初始化] CMake 配置...")
    subprocess.run(
        ["cmake", "-S", ".", "-B", "build", "-GNinja",
         "-DSTUDENT_ID=EvoBench", "-DSTUDENT_NAME=Agent",
         "-DTASK1_WITH=antlr", "-DTASK2_WITH=antlr",
         "-DTASK2_REVIVE=OFF", "-DTASK3_REVIVE=OFF",
         "-DTASK4_REVIVE=OFF", "-DTASK5_REVIVE=OFF"],
        cwd=str(YATCC_ROOT), capture_output=True, timeout=60,
    )

    # 生成标准答案
    print("[初始化] 生成标准答案...")
    for ans in ["task0-answer", "task1-answer", "task2-answer", "task3-answer", "task5-answer"]:
        subprocess.run(["cmake", "--build", "build", "-t", ans],
                       cwd=str(YATCC_ROOT), capture_output=True, timeout=300)

    # 逐 Task 评测
    results = []
    t_start = time.time()
    pipeline_conversation = None

    def create_conversation(task_id: int, suffix: str = "") -> Conversation:
        event_name = f"task{task_id}{suffix}.jsonl"
        state_name = f"task{task_id}{suffix}"
        tags = {"runid": run_id, "taskid": str(task_id), "model": args.model}
        if suffix:
            tags["suffix"] = suffix.lstrip("-")

        return Conversation(
            agent=agent,
            workspace=str(YATCC_ROOT),
            max_iteration_per_run=args.max_iterations,
            callbacks=[_event_logger(events_dir / event_name)],
            persistence_dir=persistence_root / state_name,
            tags=tags,
        )

    if args.context_mode == "pipeline":
        pipeline_conversation = Conversation(
            agent=agent,
            workspace=str(YATCC_ROOT),
            max_iteration_per_run=args.max_iterations,
            callbacks=[_event_logger(events_dir / "pipeline.jsonl")],
            persistence_dir=persistence_root / "pipeline",
            tags={"runid": run_id, "model": args.model, "contextmode": "pipeline"},
        )

    first_pipeline_task = True
    for task_id in tasks:
        print(f"\n{'='*60}")
        print(f"  Task {task_id} 开始 (OpenHands Agent)")
        print(f"{'='*60}")

        task_start = time.time()

        if pipeline_conversation is not None:
            conversation = pipeline_conversation
            if first_pipeline_task:
                prompt = build_task_prompt(task_id, args.context_mode, pipeline_stage="first")
                first_pipeline_task = False
            else:
                prompt = build_task_prompt(task_id, args.context_mode, pipeline_stage="continue")
        else:
            # per-task 模式下每个任务从空 Conversation 开始，但共享同一个 YatCC 工作区。
            conversation = create_conversation(task_id)
            prompt = build_task_prompt(task_id, args.context_mode)

        # 发送任务
        conversation.send_message(prompt)

        # 运行（无限自循环，直到 Agent 调用 finish 工具或达到 max_iteration_per_run）
        print(f"  [Agent] 开始迭代 (max={args.max_iterations})...")
        try:
            conversation.run(max_iterations=args.max_iterations)
            print(f"  [Agent] 完成")
        except Exception as e:
            print(f"  [Agent] 异常: {type(e).__name__}: {e}")

        # 构建 + 评测
        print(f"  [评测] 构建并评测 Task {task_id}...")
        score = build_and_score(task_id)
        elapsed = time.time() - task_start

        entry = {
            "task_id": task_id,
            "score": score.get("score", 0),
            "max_score": score.get("max_score", 100),
            "passed": score.get("passed", False),
            "test_count": score.get("test_count", 0),
            "passed_count": score.get("passed_count", 0),
            "elapsed_seconds": elapsed,
            "backend": "openhands",
        }
        results.append(entry)

        status = "✅" if entry["passed"] else "❌"
        print(f"\n  Task {task_id}: {status} {entry['score']:.1f}/100 "
              f"[{entry['passed_count']}/{entry['test_count']}] ({elapsed:.1f}s)")

        # 复活机制
        if not entry["passed"] and args.resurrect and task_id >= 2:
            print(f"\n  [复活] Task {task_id} 未通过，触发复活...")
            trigger_resurrection(task_id)

            # pipeline 模式下复活也延续同一个上下文；per-task 模式下使用新的复活上下文。
            if pipeline_conversation is not None:
                conversation2 = pipeline_conversation
            else:
                conversation2 = create_conversation(task_id, "-resurrect")
            conversation2.send_message(
                build_task_prompt(
                    task_id,
                    args.context_mode,
                    pipeline_stage="resurrect" if pipeline_conversation is not None else None,
                )
            )
            try:
                conversation2.run(max_iterations=args.max_iterations)
            except Exception as e:
                print(f"  [Agent] 复活异常: {e}")

            score2 = build_and_score(task_id)
            entry2 = {
                "task_id": task_id,
                "score": score2.get("score", 0),
                "max_score": 100,
                "passed": score2.get("passed", False),
                "resurrected": True,
                "elapsed_seconds": time.time() - task_start,
                "backend": "openhands",
            }
            results.append(entry2)
            status2 = "✅" if entry2["passed"] else "❌"
            print(f"  复活后 Task {task_id}: {status2} {entry2['score']:.1f}/100")

    total_elapsed = time.time() - t_start

    # 生成报告
    report = {
        "benchmark": "EvoBench-v2",
        "agent_backend": "openhands",
        "agent_model": args.model,
        "context_mode": args.context_mode,
        "run_id": run_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_elapsed_seconds": total_elapsed,
        "tasks": results,
        "metrics": {
            "zero_shot_pass": all(r["passed"] for r in results if not r.get("resurrected")),
            "resurrection_count": sum(1 for r in results if r.get("resurrected")),
            "total_iterations": len(results) * args.max_iterations,
        },
    }

    report_path = output_dir / "openhands_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印摘要
    print(f"\n{'='*70}")
    print(f"  OpenHands Agent EvoBench 完成 — {args.model}")
    print(f"{'='*70}")
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        res = " [复活]" if r.get("resurrected") else ""
        print(f"  Task {r['task_id']}: {icon} {r['score']:.1f}/100 "
              f"[{r.get('passed_count', '?')}/{r.get('test_count', '?')}]{res}")
    print(f"  复活次数: {report['metrics']['resurrection_count']}")
    print(f"  总耗时: {total_elapsed:.1f}s")
    print(f"  报告: {report_path}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
