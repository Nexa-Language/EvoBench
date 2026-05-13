#!/usr/bin/env python3
"""通用 CLI Agent Runner — 支持 Claude Code / Codex CLI / Kimi CLI。

每个 backend 使用独立的 YatCC workspace 副本，避免 CMake 构建冲突。

用法:
    python run_cli_agent.py --backend claude --tasks 0-5
    python run_cli_agent.py --backend codex --tasks 0-5
    python run_cli_agent.py --backend kimi --tasks 0-5
    python run_cli_agent.py --backend kimi --tasks 0-5 --workspace /path/to/ws
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
YATCC_ROOT = PROJECT_ROOT / "data" / "YatCC"
OUTPUT_DIR = PROJECT_ROOT / "eval" / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_BASE = Path("/home/agent/workspace")

# 加载任务指南
TASK_GUIDES: dict[int, str] = {}
_guide_path = PROJECT_ROOT / "data" / "task_guides.md"
if _guide_path.exists():
    _content = _guide_path.read_text(encoding="utf-8")
    sections = re.split(r'^## Task (\d+):', _content, flags=re.MULTILINE)
    for i in range(1, len(sections), 2):
        TASK_GUIDES[int(sections[i])] = sections[i + 1].strip()

# Task 4/5 使用 task5-classic 构建目标
BUILD_TARGETS = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
SCORE_TARGETS = {0: "task0-score", 1: "task1-score", 2: "task2-score",
                 3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}


# ──────────────────────── Workspace 管理 ────────────────────────

def create_workspace(backend: str) -> Path:
    """为 backend 创建独立的 YatCC workspace 副本。

    大型只读目录（llvm/, antlr/）使用符号链接节省磁盘空间。
    """
    ws_name = f"cli-{backend}-{int(time.time())}"
    ws = WORKSPACE_BASE / ws_name
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    print(f"  [WS] 创建独立 workspace: {ws}")

    # 大型只读目录 → 符号链接
    SYMLINK_DIRS = {"llvm", "antlr", "pybind11", "Testing"}
    # 排除的目录
    SKIP_DIRS = {"build", "build_test", ".git"}

    src = YATCC_ROOT
    for item in src.iterdir():
        if item.name in SKIP_DIRS:
            continue
        dst = ws / item.name
        if item.name in SYMLINK_DIRS:
            # 符号链接节省空间
            dst.symlink_to(item.resolve())
        elif item.is_dir():
            shutil.copytree(item, dst, symlinks=True)
        else:
            shutil.copy2(item, dst)
    print(f"  [WS] 创建完成 (llvm → symlink)")
    return ws


def setup_workspace(ws: Path) -> bool:
    """配置 workspace 的 CMake 并生成标准答案。"""
    print(f"  [WS] 配置 CMake...")
    rc = subprocess.run(
        ["cmake", "-S", ".", "-B", "build", "-GNinja",
         "-DSTUDENT_ID=EvoBench", "-DSTUDENT_NAME=Agent",
         "-DTASK1_WITH=flex", "-DTASK2_WITH=bison",
         "-DTASK2_REVIVE=OFF", "-DTASK3_REVIVE=OFF",
         "-DTASK4_REVIVE=OFF", "-DTASK5_REVIVE=OFF"],
        cwd=str(ws), capture_output=True, text=True, timeout=120,
    )
    if rc.returncode != 0:
        print(f"  [WS] ❌ CMake 配置失败: {rc.stderr[:300]}")
        return False

    print(f"  [WS] 生成标准答案...")
    for ans in ["task0-answer", "task1-answer", "task2-answer", "task3-answer", "task5-answer"]:
        subprocess.run(["cmake", "--build", "build", "-t", ans],
                       cwd=str(ws), capture_output=True, text=True, timeout=300)

    # 验证 task0
    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", "task0"],
        cwd=str(ws), capture_output=True, text=True, timeout=60,
    )
    if rc.returncode != 0:
        print(f"  [WS] ❌ task0 构建失败")
        return False

    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", "task0-score"],
        cwd=str(ws), capture_output=True, text=True, timeout=60,
    )
    print(f"  [WS] ✅ workspace 就绪")
    return True


def cleanup_workspace(ws: Path) -> None:
    """清理 workspace 释放磁盘空间。"""
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
        print(f"  [WS] 已清理 {ws}")


# ──────────────────────── Prompt 构建 ────────────────────────

def build_prompt(task_id: int) -> str:
    """为 CLI Agent 构建任务提示。"""
    readme_path = YATCC_ROOT / "task" / str(task_id) / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    guide = TASK_GUIDES.get(task_id, "")

    return f"""请完成 YatCC 编译原理实验 Task {task_id}。

## 实验说明
{readme}

{f'## 任务指南{chr(10)}{guide}' if guide else ''}

## 工作流程
1. 阅读 task/{task_id}/README.md 和上面的任务指南
2. 阅读 task/{task_id}/ 下的源文件
3. 编写/修改代码
4. 编译: cmake --build build -t {BUILD_TARGETS.get(task_id, f'task{task_id}')}
5. 评测: cmake --build build -t {SCORE_TARGETS.get(task_id, f'task{task_id}-score')}
6. 查看结果: cat build/test/task{task_id}/score.txt
7. 如果失败，分析错误并修复，重复 4-6
8. 得分 >= 60% 时任务完成
"""


# ──────────────────────── CLI Agent 运行 ────────────────────────

def run_cli_agent(backend: str, task_id: int, workspace: Path, timeout_sec: int = 1800, model: str = "") -> dict:
    """运行 CLI Agent 解决一个 Task。"""
    prompt = build_prompt(task_id)
    t_start = time.time()

    env = os.environ.copy()
    env.setdefault("OPENAI_API_BASE", "https://aihub.arcsysu.cn/v1")
    # 让子进程使用 UTF-8
    env["PYTHONIOENCODING"] = "utf-8"
    env["LANG"] = "en_US.UTF-8"

    if backend == "claude":
        # Claude Code: 使用 agent 用户运行（root 不允许 --dangerously-skip-permissions）
        cmd = [
            "su", "-", "agent", "-c",
            f"cd {workspace} && claude -p {repr(prompt)} --dangerously-skip-permissions --max-turns 200"
        ]
    elif backend == "codex":
        cmd = ["codex", "exec", prompt]
    elif backend == "kimi":
        model_flag = model if model else "kimi-k2.6"
        cmd = ["kimi", "--model", model_flag, "--yes", "-p", prompt]
    else:
        return {"task_id": task_id, "error": f"Unknown backend: {backend}"}

    log_file = OUTPUT_DIR.parent / "logs" / f"{backend}-task{task_id}-{int(time.time())}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"  [Agent] 启动 {backend} (timeout={timeout_sec}s)...")
    print(f"  [Agent] 日志: {log_file}")

    try:
        with open(log_file, "w") as lf:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(workspace),
                env=env,
            )
            # 实时写日志
            assert proc.stdout is not None
            for line in proc.stdout:
                lf.write(line)
                lf.flush()
            proc.wait(timeout=timeout_sec)

        elapsed = time.time() - t_start
        return {
            "task_id": task_id, "backend": backend,
            "exit_code": proc.returncode,
            "elapsed_seconds": elapsed,
            "success": proc.returncode == 0,
            "log_file": str(log_file),
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        elapsed = time.time() - t_start
        print(f"  [Agent] ⏰ 超时 ({elapsed:.0f}s)")
        return {"task_id": task_id, "backend": backend, "error": f"Timeout ({timeout_sec}s)",
                "elapsed_seconds": elapsed}
    except FileNotFoundError:
        return {"task_id": task_id, "backend": backend, "error": f"Command not found: {backend}"}


# ──────────────────────── 评分 ────────────────────────

def parse_score(task_id: int, workspace: Path) -> dict:
    """解析 score.json / score-classic.json（Task 4/5 用 classic）。"""
    score_json = workspace / f"build/test/task{task_id}/score.json"
    if not score_json.exists():
        score_json = workspace / f"build/test/task{task_id}/score-classic.json"
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


def build_and_score(task_id: int, workspace: Path) -> dict:
    """构建并评测。"""
    build_target = BUILD_TARGETS.get(task_id, f"task{task_id}")
    score_target = SCORE_TARGETS.get(task_id, f"task{task_id}-score")

    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", build_target],
        cwd=str(workspace), capture_output=True, text=True, timeout=300,
    )
    if rc.returncode != 0:
        return {"task_id": task_id, "score": 0, "max_score": 100, "passed": False,
                "build_error": rc.stderr[-500:] if rc.stderr else rc.stdout[-500:]}

    subprocess.run(
        ["cmake", "--build", "build", "-t", score_target],
        cwd=str(workspace), capture_output=True, text=True, timeout=300,
    )
    return parse_score(task_id, workspace)


def trigger_resurrection(failed_task: int, workspace: Path) -> None:
    """触发复活：注入 REVIVE 标志。"""
    config_path = workspace / "config.cmake"
    if config_path.exists():
        content = config_path.read_text()
        for i in range(failed_task, 6):
            content = re.sub(rf'set\(TASK{i}_REVIVE\s+\w+\)', f'set(TASK{i}_REVIVE ON)', content)
        config_path.write_text(content)
    subprocess.run(["cmake", "-S", ".", "-B", "build", "-GNinja"],
                   cwd=str(workspace), capture_output=True, text=True, timeout=60)
    answer_targets = {2: "task2-answer", 3: "task3-answer", 4: "task4-answer", 5: "task5-answer"}
    target = answer_targets.get(failed_task)
    if target:
        subprocess.run(["cmake", "--build", "build", "-t", target],
                       cwd=str(workspace), capture_output=True, text=True, timeout=300)


# ──────────────────────── 指标计算 ────────────────────────

WEIGHTS = [0.05, 0.20, 0.20, 0.15, 0.30, 0.10]  # Task 0-5 权重 (5%, 20%, 20%, 15%, 30%, 10%)


def compute_metrics(task_scores: dict[int, float], resurrections: dict[int, bool]) -> dict:
    """计算 mean_reward, pass_score, pipeline_score。"""
    weighted_sum = 0.0
    weight_total = 0.0
    for i in range(6):
        score = task_scores.get(i, 0)
        w = WEIGHTS[i]
        bonus = 1.0 if resurrections.get(i, False) else 1.2
        weighted_sum += score * w * bonus
        weight_total += w * bonus
    mean_reward = weighted_sum / weight_total if weight_total > 0 else 0.0

    passed = sum(1 for s in task_scores.values() if s >= 60.0)
    pass_score = passed / 6.0 * 100.0

    # Pipeline score: 串行通过的最高 Task
    pipeline = 0
    for i in range(6):
        if task_scores.get(i, 0) >= 60.0:
            pipeline = i + 1
        else:
            break
    pipeline_score = pipeline / 6.0 * 100.0

    return {
        "mean_reward": round(mean_reward, 2),
        "pass_score": round(pass_score, 2),
        "pipeline_score": round(pipeline_score, 2),
    }


# ──────────────────────── 主入口 ────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CLI Agent EvoBench Runner")
    parser.add_argument("--backend", required=True, choices=["claude", "codex", "kimi"])
    parser.add_argument("--tasks", default="0-5",
                        help="Task 范围，如 0-5 或 1,2,3 或 4-5")
    parser.add_argument("--model", default=None,
                        help="模型名（用于 leaderboard 显示）")
    parser.add_argument("--workspace", default=None,
                        help="指定 workspace 路径（跳过创建）")
    parser.add_argument("--resurrect", action="store_true", default=True)
    parser.add_argument("--no-resurrect", action="store_false", dest="resurrect")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="每个 Task 的超时时间（秒）")
    parser.add_argument("--keep-workspace", action="store_true",
                        help="评测完成后不清理 workspace")
    args = parser.parse_args()

    tasks = []
    for part in args.tasks.split(","):
        if "-" in part:
            s, e = part.split("-", 1)
            tasks.extend(range(int(s), int(e) + 1))
        else:
            tasks.append(int(part))
    tasks = sorted(set(tasks))

    model_name = args.model or f"{args.backend}-unknown"

    print(f"\n{'=' * 70}")
    print(f"  CLI Agent EvoBench — {args.backend} ({model_name})")
    print(f"  任务: {tasks}")
    print(f"{'=' * 70}\n")

    # 创建或使用指定 workspace
    if args.workspace:
        ws = Path(args.workspace)
        print(f"  [WS] 使用指定 workspace: {ws}")
    else:
        ws = create_workspace(args.backend)

    if not setup_workspace(ws):
        print("  ❌ workspace 配置失败，终止")
        cleanup_workspace(ws)
        return

    results = []
    t_start = time.time()

    for task_id in tasks:
        print(f"\n{'=' * 60}")
        print(f"  Task {task_id} ({args.backend})")
        print(f"{'=' * 60}")

        # Agent 编码
        agent_result = run_cli_agent(args.backend, task_id, ws, timeout_sec=args.timeout, model=model_name)
        elapsed = agent_result.get("elapsed_seconds", 0)
        exit_code = agent_result.get("exit_code", -1)
        print(f"  Agent 完成: {elapsed:.0f}s, exit={exit_code}")

        # 构建 + 评测
        score = build_and_score(task_id, ws)
        status = "✅" if score.get("passed") else "❌"
        print(f"  Task {task_id}: {status} {score.get('score', 0):.1f}/100")

        entry = {
            "task_id": task_id,
            "backend": args.backend,
            "model": model_name,
            "score": score.get("score", 0),
            "max_score": 100,
            "passed": score.get("passed", False),
            "elapsed_seconds": elapsed,
            "agent_exit_code": exit_code,
            "resurrected": False,
        }
        results.append(entry)

        # 复活
        if not entry["passed"] and args.resurrect and task_id >= 2:
            print(f"  [复活] Task {task_id} 失败，触发复活...")
            trigger_resurrection(task_id, ws)
            agent_result2 = run_cli_agent(args.backend, task_id, ws, timeout_sec=args.timeout, model=model_name)
            score2 = build_and_score(task_id, ws)
            entry2 = {
                "task_id": task_id,
                "backend": args.backend,
                "model": model_name,
                "score": score2.get("score", 0),
                "max_score": 100,
                "passed": score2.get("passed", False),
                "resurrected": True,
                "elapsed_seconds": agent_result2.get("elapsed_seconds", 0),
            }
            results.append(entry2)
            print(f"  复活后: {'✅' if entry2['passed'] else '❌'} {entry2['score']:.1f}/100")

    total_elapsed = time.time() - t_start

    # 计算最终分数（取每个 Task 的最高分）
    task_scores: dict[int, float] = {}
    resurrections: dict[int, bool] = {}
    for r in results:
        tid = r["task_id"]
        if tid not in task_scores or r["score"] > task_scores[tid]:
            task_scores[tid] = r["score"]
            resurrections[tid] = r.get("resurrected", False)

    metrics = compute_metrics(task_scores, resurrections)

    # 保存报告
    report = {
        "benchmark": "EvoBench-v3",
        "agent_backend": args.backend,
        "model": model_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_elapsed_seconds": total_elapsed,
        "task_scores": {str(k): v for k, v in task_scores.items()},
        "resurrections": {str(k): v for k, v in resurrections.items()},
        **metrics,
        "tasks": results,
    }
    report_path = OUTPUT_DIR / f"cli_{args.backend}_{model_name.replace('/', '_')}_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"\n{'=' * 70}")
    print(f"  {args.backend} ({model_name}) 完成 | 总耗时: {total_elapsed:.0f}s")
    print(f"  Mean Reward: {metrics['mean_reward']:.2f}")
    print(f"  Pass Score:  {metrics['pass_score']:.2f}")
    print(f"  Pipeline:    {metrics['pipeline_score']:.2f}")
    for tid in sorted(task_scores.keys()):
        s = task_scores[tid]
        icon = "✅" if s >= 60 else "❌"
        res = " [复活]" if resurrections.get(tid) else ""
        print(f"  Task {tid}: {icon} {s:.1f}/100{res}")
    print(f"  报告: {report_path}")
    print(f"{'=' * 70}\n")

    # 清理 workspace
    if not args.keep_workspace:
        cleanup_workspace(ws)

    # 更新 leaderboard.json
    update_leaderboard(model_name, args.backend, task_scores, resurrections, metrics)


def update_leaderboard(model: str, backend: str, task_scores: dict[int, float],
                       resurrections: dict[int, bool], metrics: dict) -> None:
    """更新 site/assets/data/leaderboard.json。"""
    lb_path = PROJECT_ROOT / "site" / "assets" / "data" / "leaderboard.json"
    if not lb_path.exists():
        lb_data: list[dict] = []
    else:
        with open(lb_path) as f:
            lb_data = json.load(f)

    # 查找已有条目
    existing = None
    for entry in lb_data:
        if entry.get("model") == model and entry.get("backend") == backend:
            existing = entry
            break

    new_entry = {
        "model": model,
        "backend": backend,
        "task0": task_scores.get(0, 0),
        "task1": task_scores.get(1, 0),
        "task2": task_scores.get(2, 0),
        "task3": task_scores.get(3, 0),
        "task4": task_scores.get(4, 0),
        "task5": task_scores.get(5, 0),
        "mean_reward": metrics["mean_reward"],
        "pass_score": metrics["pass_score"],
        "pipeline_score": metrics["pipeline_score"],
        "resurrections": sum(1 for v in resurrections.values() if v),
        "zero_shot_pass": all(s >= 60 for s in task_scores.values()) and not any(resurrections.values()),
        "resurrected": [resurrections.get(i, False) for i in range(6)],
        "date": time.strftime("%Y-%m-%d"),
    }

    if existing:
        existing.update(new_entry)
    else:
        lb_data.append(new_entry)

    # 按 mean_reward 降序排序
    lb_data.sort(key=lambda x: x.get("mean_reward", 0), reverse=True)
    for i, entry in enumerate(lb_data):
        entry["rank"] = i + 1

    with open(lb_path, "w") as f:
        json.dump(lb_data, f, ensure_ascii=False, indent=2)
    print(f"  [LB] 已更新 leaderboard ({len(lb_data)} 条目)")


if __name__ == "__main__":
    main()
