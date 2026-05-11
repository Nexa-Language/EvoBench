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

from dotenv import load_dotenv

# 加载环境变量
load_dotenv(Path(__file__).parent / ".env")

YATCC_ROOT = Path(__file__).parent.parent / "data" / "YatCC"
if not YATCC_ROOT.exists():
    YATCC_ROOT = Path(__file__).parent.parent / "YatCC"  # fallback
OUTPUT_DIR = Path(__file__).parent.parent / "eval" / "results"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── OpenHands SDK ──────────────────────────────────────────────────────

os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool


def create_agent(model: str, api_base: str, api_key: str) -> Agent:
    """创建 OpenHands Agent。"""
    model_name = model
    if api_base and not model_name.startswith("openai/"):
        model_name = f"openai/{model}"

    llm = LLM(
        model=model_name,
        api_key=api_key,
        base_url=api_base if api_base else None,
        temperature=0.2,
        timeout=300,
    )

    return Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
    )


# 任务指南（精简版，从 YatCC-docs 提取）
TASK_GUIDES = {}

def _load_task_guides():
    """加载精简版任务指南。"""
    global TASK_GUIDES
    # 尝试多个可能的路径
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

    # 按 ## Task N 分割
    import re
    sections = re.split(r'^## Task (\d+):', guide_content, flags=re.MULTILINE)
    for i in range(1, len(sections), 2):
        task_id = int(sections[i])
        TASK_GUIDES[task_id] = sections[i+1].strip()

_load_task_guides()


def build_task_prompt(task_id: int) -> str:
    """为指定 Task 组装提示词，包含精简版任务指南。"""
    readme_path = YATCC_ROOT / "task" / str(task_id) / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    task_dir = YATCC_ROOT / "task" / str(task_id)
    files = []
    if task_dir.exists():
        for f in sorted(task_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                files.append(str(f.relative_to(task_dir)))

    build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
    score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score",
                     3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}

    # 注入精简版任务指南
    guide = TASK_GUIDES.get(task_id, "")
    guide_section = f"\n## 任务指南（关键信息）\n{guide}\n" if guide else ""

    prompt = f"""# Task {task_id}: 编译原理实验

## 实验说明
{readme}
{guide_section}
## 当前 Task 代码文件
{chr(10).join('- ' + f for f in files)}

## 工作流程
1. 先阅读上面的任务指南，理解输入/输出格式和评分标准
2. 阅读 task/{task_id}/ 下的源文件，理解基础代码
3. 编写/修改代码实现功能
4. 编译: cmake --build build -t {build_targets.get(task_id, f'task{task_id}')}
5. 评测: cmake --build build -t {score_targets.get(task_id, f'task{task_id}-score')}
6. 查看评测结果: cat build/test/task{task_id}/score.txt
7. 如果失败，分析错误并修复代码，重复步骤 4-6
8. 当评测通过（得分 >= 60%）时，任务完成

请开始完成 Task {task_id}。
"""
    return prompt


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
    )
    if rc.returncode != 0:
        return {"task_id": task_id, "score": 0, "max_score": 100, "passed": False,
                "error": rc.stderr[-500:]}

    subprocess.run(
        ["cmake", "--build", "build", "-t", score_targets.get(task_id, f"task{task_id}-score")],
        cwd=str(YATCC_ROOT), capture_output=True, text=True, timeout=300,
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


# ─── 主流程 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OpenHands Agent EvoBench Runner")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro"))
    parser.add_argument("--tasks", default="0-5", help="任务范围 (如 0-5 或 0,1,2,3)")
    parser.add_argument("--max-iterations", type=int, default=50, help="每个 Task 最大迭代轮次")
    parser.add_argument("--resurrect", action="store_true", default=True, help="启用复活")
    parser.add_argument("--no-resurrect", action="store_false", dest="resurrect")
    parser.add_argument("--workspace", default=None, help="自定义工作区路径（用于并行运行）")
    args = parser.parse_args()

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
    print(f"  最大迭代: {args.max_iterations}")
    print(f"  复活: {'是' if args.resurrect else '否'}")
    print(f"{'='*70}\n")

    # 创建 Agent
    agent = create_agent(args.model, api_base, api_key)

    # CMake 配置
    print("[初始化] CMake 配置...")
    subprocess.run(
        ["cmake", "-S", ".", "-B", "build", "-GNinja",
         "-DSTUDENT_ID=EvoBench", "-DSTUDENT_NAME=Agent",
         "-DTASK1_WITH=flex", "-DTASK2_WITH=bison",
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

    for task_id in tasks:
        print(f"\n{'='*60}")
        print(f"  Task {task_id} 开始 (OpenHands Agent)")
        print(f"{'='*60}")

        task_start = time.time()

        # 创建 Conversation（max_iteration_per_run 在构造时设置）
        conversation = Conversation(
            agent=agent,
            workspace=str(YATCC_ROOT),
            max_iteration_per_run=args.max_iterations,
        )

        # 发送任务
        prompt = build_task_prompt(task_id)
        conversation.send_message(prompt)

        # 运行（无限自循环，直到 Agent 调用 finish 工具或达到 max_iteration_per_run）
        print(f"  [Agent] 开始迭代 (max={args.max_iterations})...")
        try:
            conversation.run()
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

            # 复活后重试
            conversation2 = Conversation(
                agent=agent,
                workspace=str(YATCC_ROOT),
                max_iteration_per_run=args.max_iterations,
            )
            conversation2.send_message(build_task_prompt(task_id))
            try:
                conversation2.run()
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
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_elapsed_seconds": total_elapsed,
        "tasks": results,
        "metrics": {
            "zero_shot_pass": all(r["passed"] for r in results if not r.get("resurrected")),
            "resurrection_count": sum(1 for r in results if r.get("resurrected")),
            "total_iterations": len(results) * args.max_iterations,
        },
    }

    report_path = OUTPUT_DIR / "openhands_report.json"
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
