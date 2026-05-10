#!/usr/bin/env python3
"""通用 CLI Agent Runner — 支持 Claude Code / Codex CLI / Gemini CLI / Kimi CLI。

这些都是真正的 coding agent，有完整的文件编辑和命令执行能力。
关键是使用它们的自主模式（非 --print 单次对话模式）。

用法:
    python run_cli_agent.py --backend claude --tasks 0-5
    python run_cli_agent.py --backend codex --tasks 0-5
    python run_cli_agent.py --backend gemini --tasks 0-5
    python run_cli_agent.py --backend kimi --tasks 0-5
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

YATCC_ROOT = Path(__file__).parent.parent / "data" / "YatCC"
if not YATCC_ROOT.exists():
    YATCC_ROOT = Path(__file__).parent.parent / "YatCC"
OUTPUT_DIR = Path(__file__).parent.parent / "eval" / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 加载任务指南
TASK_GUIDES = {}
_guide_path = Path(__file__).parent.parent / "data" / "task_guides.md"
if _guide_path.exists():
    _content = _guide_path.read_text(encoding="utf-8")
    sections = re.split(r'^## Task (\d+):', _content, flags=re.MULTILINE)
    for i in range(1, len(sections), 2):
        TASK_GUIDES[int(sections[i])] = sections[i+1].strip()


def build_prompt(task_id: int) -> str:
    """为 CLI Agent 构建任务提示。"""
    readme_path = YATCC_ROOT / "task" / str(task_id) / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    guide = TASK_GUIDES.get(task_id, "")

    build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
    score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score",
                     3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}

    return f"""请完成 YatCC 编译原理实验 Task {task_id}。

## 实验说明
{readme}

{f'## 任务指南{chr(10)}{guide}' if guide else ''}

## 工作流程
1. 阅读 task/{task_id}/README.md 和上面的任务指南
2. 阅读 task/{task_id}/ 下的源文件
3. 编写/修改代码
4. 编译: cmake --build build -t {build_targets.get(task_id, f'task{task_id}')}
5. 评测: cmake --build build -t {score_targets.get(task_id, f'task{task_id}-score')}
6. 查看结果: cat build/test/task{task_id}/score.txt
7. 如果失败，分析错误并修复，重复 4-6
8. 得分 >= 60% 时任务完成
"""


def run_cli_agent(backend: str, task_id: int, workspace: Path) -> dict:
    """运行 CLI Agent 解决一个 Task。

    关键：使用各 CLI 的自主模式，而不是 --print 单次对话模式。
    - Claude Code: claude -p "prompt" (自主模式，自动编辑文件)
    - Codex CLI: codex --full-auto "prompt" (完全自主)
    - Gemini CLI: gemini -p "prompt" -d workspace -y (确认模式)
    - Kimi CLI: kimi chat "prompt" (自主模式)
    """
    prompt = build_prompt(task_id)
    t_start = time.time()

    env = os.environ.copy()
    # 确保使用正确的 API 配置
    env.setdefault("OPENAI_API_BASE", "https://aihub.arcsysu.cn/v1")

    if backend == "claude":
        # Claude Code 自主模式：-p 是 prompt，会自动读写文件和执行命令
        cmd = ["claude", "-p", prompt, "--max-turns", "100"]
    elif backend == "codex":
        # Codex CLI 完全自主模式
        cmd = ["codex", "--full-auto", prompt]
    elif backend == "gemini":
        # Gemini CLI 自主模式：-y 确认所有操作
        cmd = ["gemini", "-p", prompt, "-d", str(workspace), "-y"]
    elif backend == "kimi":
        # Kimi CLI 自主模式
        cmd = ["kimi", "chat", "--yes", prompt]
    else:
        return {"task_id": task_id, "error": f"Unknown backend: {backend}"}

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 分钟
            cwd=str(workspace),
            env=env,
        )
        elapsed = time.time() - t_start
        return {
            "task_id": task_id,
            "backend": backend,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-5000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
            "elapsed_seconds": elapsed,
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"task_id": task_id, "backend": backend, "error": "Timeout (1800s)",
                "elapsed_seconds": time.time() - t_start}
    except FileNotFoundError:
        return {"task_id": task_id, "backend": backend, "error": f"Command not found: {backend}"}


def parse_score(task_id: int) -> dict:
    """解析 score.json。"""
    score_json = YATCC_ROOT / f"build/test/task{task_id}/score.json"
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
    if total == 0.0 and tests:
        total = sum(t.get("score", 0) for t in tests) / len(tests)
    test_count = len(tests)
    passed_count = sum(1 for t in tests if t.get("score", 0) >= t.get("max_score", 100))
    return {"task_id": task_id, "score": total, "max_score": 100.0, "passed": total >= 60.0,
            "test_count": test_count, "passed_count": passed_count}


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
            content = re.sub(rf'set\(TASK{i}_REVIVE\s+\w+\)', f'set(TASK{i}_REVIVE ON)', content)
        config_path.write_text(content)
    subprocess.run(["cmake", "-S", ".", "-B", "build", "-GNinja"],
                   cwd=str(YATCC_ROOT), capture_output=True, timeout=60)
    answer_targets = {2: "task2-answer", 3: "task3-answer", 4: "task4-answer", 5: "task5-answer"}
    target = answer_targets.get(failed_task)
    if target:
        subprocess.run(["cmake", "--build", "build", "-t", target],
                       cwd=str(YATCC_ROOT), capture_output=True, timeout=300)


def verify_environment(workspace: Path) -> bool:
    """验证构建环境是否正常。"""
    print("  [环境] 验证构建环境...")
    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", "task0"],
        cwd=str(workspace), capture_output=True, text=True, timeout=60,
    )
    if rc.returncode != 0:
        print(f"  [环境] ❌ task0 构建失败: {rc.stderr[:200]}")
        return False
    print("  [环境] ✅ 构建环境正常")
    return True


def main():
    parser = argparse.ArgumentParser(description="CLI Agent EvoBench Runner")
    parser.add_argument("--backend", required=True, choices=["claude", "codex", "gemini", "kimi"])
    parser.add_argument("--tasks", default="0-5")
    parser.add_argument("--resurrect", action="store_true", default=True)
    parser.add_argument("--no-resurrect", action="store_false", dest="resurrect")
    args = parser.parse_args()

    tasks = []
    for part in args.tasks.split(","):
        if "-" in part:
            s, e = part.split("-", 1)
            tasks.extend(range(int(s), int(e) + 1))
        else:
            tasks.append(int(part))
    tasks = sorted(set(tasks))

    print(f"\n{'='*70}")
    print(f"  CLI Agent EvoBench — {args.backend}")
    print(f"  任务: {tasks}")
    print(f"{'='*70}\n")

    # CMake 配置
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

    # 验证环境
    if not verify_environment(YATCC_ROOT):
        print("  [环境] ❌ 环境验证失败，终止")
        return

    results = []
    t_start = time.time()

    for task_id in tasks:
        print(f"\n{'='*60}")
        print(f"  Task {task_id} ({args.backend})")
        print(f"{'='*60}")

        # Agent 编码
        agent_result = run_cli_agent(args.backend, task_id, YATCC_ROOT)
        print(f"  Agent 完成: {agent_result.get('elapsed_seconds', 0):.0f}s, exit={agent_result.get('exit_code', '?')}")

        # 构建+评测
        score = build_and_score(task_id)
        status = "✅" if score.get("passed") else "❌"
        print(f"  Task {task_id}: {status} {score.get('score', 0):.1f}/100")

        entry = {
            "task_id": task_id,
            "backend": args.backend,
            "score": score.get("score", 0),
            "max_score": 100,
            "passed": score.get("passed", False),
            "elapsed_seconds": agent_result.get("elapsed_seconds", 0),
            "agent_exit_code": agent_result.get("exit_code", -1),
        }
        results.append(entry)

        # 复活
        if not entry["passed"] and args.resurrect and task_id >= 2:
            print(f"  [复活] Task {task_id} 失败，触发复活...")
            trigger_resurrection(task_id)
            agent_result2 = run_cli_agent(args.backend, task_id, YATCC_ROOT)
            score2 = build_and_score(task_id)
            entry2 = {
                "task_id": task_id,
                "backend": args.backend,
                "score": score2.get("score", 0),
                "max_score": 100,
                "passed": score2.get("passed", False),
                "resurrected": True,
                "elapsed_seconds": agent_result2.get("elapsed_seconds", 0),
            }
            results.append(entry2)
            print(f"  复活后: {'✅' if entry2['passed'] else '❌'} {entry2['score']:.1f}/100")

    total_elapsed = time.time() - t_start

    # 保存报告
    report = {
        "benchmark": "EvoBench-v2",
        "agent_backend": args.backend,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_elapsed_seconds": total_elapsed,
        "tasks": results,
    }
    report_path = OUTPUT_DIR / f"cli_{args.backend}_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"\n{'='*70}")
    print(f"  {args.backend} 完成 | 总耗时: {total_elapsed:.0f}s")
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        res = " [复活]" if r.get("resurrected") else ""
        print(f"  Task {r['task_id']}: {icon} {r['score']:.1f}/100{res}")
    print(f"  报告: {report_path}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
