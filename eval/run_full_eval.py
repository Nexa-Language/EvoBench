#!/usr/bin/env python3
"""EvoBench 完整评测脚本。

每个模型使用独立工作区，避免 cmake 构建冲突。
支持断点续跑、自动清理、结果收集。

用法:
    python run_full_eval.py --models all --tasks 0-5
    python run_full_eval.py --models deepseek-v4-pro,glm-5 --tasks 4-5
    python run_full_eval.py --models all --tasks 0-5 --max-parallel 3
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).parent.parent
YATCC_SRC = ROOT_DIR / "data" / "YatCC"
WS_BASE = Path("/home/agent/workspace")
LOG_DIR = ROOT_DIR / "eval" / "logs"
RESULT_DIR = ROOT_DIR / "eval" / "results" / "openhands"
VENV_PYTHON = ROOT_DIR / ".venv-openhands" / "bin" / "python"

# 所有可评测模型
ALL_MODELS = [
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-chat",
    "deepseek-reasoner",
    "glm-5",
    "glm-4.7",
    "glm-5.1",
    "kimi-k2-thinking",
    "minimax-m2.5",
    "minimax-m2.7",
    "qwen3.6-max-preview",
    "qwen3.6-flash",
    "qwen3.6-plus",
    "qwen3-coder-flash",
    "qwen3.5-plus-2026-02-15",
    "qwen-omni-turbo",
    "mimo-v2.5-pro",
]

TASK_IDS = list(range(6))  # 0-5


@dataclass
class EvalResult:
    model: str
    task_scores: dict = field(default_factory=dict)  # {task_id: score}
    task_passed: dict = field(default_factory=dict)   # {task_id: bool}
    resurrections: int = 0
    elapsed: float = 0.0
    error: str = ""
    workspace: str = ""


def prepare_workspace(model: str) -> Path:
    """为模型创建工作区副本。"""
    ws = WS_BASE / f"evobench-{model}"
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(YATCC_SRC, ws, symlinks=True)
    return ws


def setup_workspace(ws: Path) -> bool:
    """在工作区中初始化 CMake 构建。"""
    try:
        # CMake 配置
        subprocess.run(
            ["cmake", "-S", ".", "-B", "build", "-GNinja",
             "-DSTUDENT_ID=EvoBench", "-DSTUDENT_NAME=Agent",
             "-DTASK1_WITH=flex", "-DTASK2_WITH=bison",
             "-DTASK2_REVIVE=OFF", "-DTASK3_REVIVE=OFF",
             "-DTASK4_REVIVE=OFF", "-DTASK5_REVIVE=OFF"],
            cwd=str(ws), capture_output=True, timeout=120
        )
        # 构建 test-rtlib
        subprocess.run(
            ["cmake", "--build", "build", "-t", "test-rtlib"],
            cwd=str(ws), capture_output=True, timeout=300
        )
        return True
    except Exception as e:
        print(f"  [错误] 工作区初始化失败: {e}")
        return False


def generate_answers(ws: Path) -> bool:
    """生成标准答案。"""
    try:
        for target in ["task0-answer", "task1-answer", "task2-answer", "task3-answer", "task5-answer"]:
            subprocess.run(
                ["cmake", "--build", "build", "-t", target],
                cwd=str(ws), capture_output=True, timeout=300
            )
        return True
    except Exception as e:
        print(f"  [错误] 标准答案生成失败: {e}")
        return False


def read_score(ws: Path, task_id: int) -> tuple[float, bool]:
    """读取指定 Task 的分数。"""
    for suffix in ["", "-classic", "-llm"]:
        score_file = ws / f"build/test/task{task_id}/score{suffix}.json"
        if score_file.exists():
            try:
                data = json.loads(score_file.read_text())
                tests = data.get("tests", [])
                lb = data.get("leaderboard", [])
                if lb:
                    score = float(lb[0]["value"])
                elif tests:
                    score = sum(t.get("score", 0) for t in tests) / len(tests)
                else:
                    score = 0.0
                return score, score >= 60.0
            except:
                pass
    return 0.0, False


def build_and_score(ws: Path, task_id: int) -> tuple[float, bool, str]:
    """构建并评测单个 Task。"""
    build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
    score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score",
                     3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}
    
    # 构建
    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", build_targets.get(task_id, f"task{task_id}")],
        cwd=str(ws), capture_output=True, text=True, timeout=300
    )
    if rc.returncode != 0:
        return 0.0, False, rc.stderr[-500:]
    
    # 评分
    subprocess.run(
        ["cmake", "--build", "build", "-t", score_targets.get(task_id, f"task{task_id}-score")],
        cwd=str(ws), capture_output=True, text=True, timeout=300
    )
    
    score, passed = read_score(ws, task_id)
    return score, passed, ""


def trigger_resurrection(ws: Path, failed_task: int) -> None:
    """触发复活。"""
    config_path = ws / "config.cmake"
    if config_path.exists():
        content = config_path.read_text()
        for i in range(failed_task, 6):
            content = re.sub(rf'set\(TASK{i}_REVIVE\s+\w+\)', f'set(TASK{i}_REVIVE ON)', content)
        config_path.write_text(content)
    
    subprocess.run(
        ["cmake", "-S", ".", "-B", "build"],
        cwd=str(ws), capture_output=True, timeout=60
    )
    
    answer_targets = {2: "task2-answer", 3: "task3-answer", 4: "task4-answer", 5: "task5-answer"}
    target = answer_targets.get(failed_task)
    if target:
        subprocess.run(
            ["cmake", "--build", "build", "-t", target],
            cwd=str(ws), capture_output=True, timeout=300
        )


def run_openhands_eval(model: str, tasks: list[int], max_iterations: int = 100) -> EvalResult:
    """运行单个模型的 OpenHands 评测。"""
    result = EvalResult(model=model)
    t_start = time.time()
    
    # 准备工作区
    print(f"  [{model}] 准备工作区...")
    ws = prepare_workspace(model)
    result.workspace = str(ws)
    
    if not setup_workspace(ws):
        result.error = "工作区初始化失败"
        result.elapsed = time.time() - t_start
        return result
    
    if not generate_answers(ws):
        result.error = "标准答案生成失败"
        result.elapsed = time.time() - t_start
        return result
    
    # 运行评测
    print(f"  [{model}] 开始评测...")
    log_file = LOG_DIR / f"openhands-{model}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    env = os.environ.copy()
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    env["OPENAI_API_BASE"] = os.getenv("OPENAI_API_BASE", "https://aihub.arcsysu.cn/v1")
    env["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
    env["OPENAI_MODEL_NAME"] = model
    
    # 使用独立的评测脚本
    eval_script = f"""
import sys
sys.path.insert(0, '{ROOT_DIR / "src"}')
from run_openhands import run_openhands_eval
result = run_openhands_eval(
    model='{model}',
    workspace='{ws}',
    tasks={tasks},
    max_iterations={max_iterations}
)
print(f'RESULT: {{result}}')
"""
    
    try:
        proc = subprocess.run(
            [str(VENV_PYTHON), "-c", eval_script],
            capture_output=True, text=True,
            timeout=max_iterations * 60,  # 每轮最多1分钟
            cwd=str(ROOT_DIR),
            env=env
        )
        
        # 从日志中提取结果
        log_content = proc.stdout + proc.stderr
        
        # 解析 Task 分数
        import re
        for task_id in tasks:
            # 查找最终分数
            pattern = rf"Task {task_id}:.*?(\d+\.?\d*)/100"
            matches = re.findall(pattern, log_content)
            if matches:
                score = float(matches[-1])  # 取最后一次
                result.task_scores[task_id] = score
                result.task_passed[task_id] = score >= 60.0
        
        # 统计复活次数
        result.resurrections = log_content.count("触发复活")
        
        # 保存日志
        log_file.write_text(log_content, encoding="utf-8")
        
    except subprocess.TimeoutExpired:
        result.error = "评测超时"
    except Exception as e:
        result.error = str(e)
    
    result.elapsed = time.time() - t_start
    
    # 清理工作区
    try:
        shutil.rmtree(ws)
        print(f"  [{model}] 工作区已清理")
    except:
        print(f"  [{model}] 工作区清理失败")
    
    return result


def save_result(result: EvalResult) -> None:
    """保存评测结果。"""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    result_file = RESULT_DIR / f"{result.model}.json"
    
    data = {
        "model": result.model,
        "backend": "openhands",
        "task_scores": result.task_scores,
        "task_passed": result.task_passed,
        "resurrections": result.resurrections,
        "elapsed": result.elapsed,
        "error": result.error,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pipeline_score": sum(result.task_scores.values()) / len(result.task_scores) if result.task_scores else 0
    }
    
    result_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  [{result.model}] 结果已保存: {result_file}")


def update_leaderboard(results: list[EvalResult]) -> None:
    """更新 Leaderboard。"""
    lb_file = ROOT_DIR / "site" / "assets" / "data" / "leaderboard.json"
    
    # 读取现有数据
    existing = {}
    if lb_file.exists():
        try:
            existing_data = json.loads(lb_file.read_text())
            existing = {e["model"]: e for e in existing_data}
        except:
            pass
    
    # 更新数据
    for result in results:
        if result.error:
            continue
        
        pipeline_score = sum(result.task_scores.values()) / len(result.task_scores) if result.task_scores else 0
        
        existing[result.model] = {
            "model": result.model,
            "backend": "openhands",
            "task0": result.task_scores.get(0, 0),
            "task1": result.task_scores.get(1, 0),
            "task2": result.task_scores.get(2, 0),
            "task3": result.task_scores.get(3, 0),
            "task4": result.task_scores.get(4, 0),
            "task5": result.task_scores.get(5, 0),
            "resurrections": result.resurrections,
            "date": time.strftime("%Y-%m-%d"),
            "pipeline_score": round(pipeline_score, 2)
        }
    
    # 排序
    lb_data = sorted(existing.values(), key=lambda x: x.get("pipeline_score", 0), reverse=True)
    for i, entry in enumerate(lb_data):
        entry["rank"] = i + 1
    
    # 保存
    lb_file.write_text(json.dumps(lb_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ Leaderboard 已更新: {lb_file}")
    print(f"   共 {len(lb_data)} 个模型")


def main():
    parser = argparse.ArgumentParser(description="EvoBench 完整评测")
    parser.add_argument("--models", default="all", help="模型列表 (逗号分隔或 'all')")
    parser.add_argument("--tasks", default="0-5", help="任务范围 (如 0-5 或 0,1,2,3)")
    parser.add_argument("--max-iterations", type=int, default=100, help="每个 Task 最大迭代轮次")
    parser.add_argument("--max-parallel", type=int, default=3, help="最大并行数")
    parser.add_argument("--no-cleanup", action="store_true", help="不清理工作区")
    args = parser.parse_args()
    
    # 解析模型列表
    if args.models == "all":
        models = ALL_MODELS
    else:
        models = [m.strip() for m in args.models.split(",")]
    
    # 解析任务范围
    if "-" in args.tasks:
        start, end = args.tasks.split("-")
        tasks = list(range(int(start), int(end) + 1))
    else:
        tasks = [int(t.strip()) for t in args.tasks.split(",")]
    
    print(f"\n{'='*60}")
    print(f"  EvoBench 完整评测")
    print(f"  模型: {', '.join(models)}")
    print(f"  任务: {tasks}")
    print(f"  并行: {args.max_parallel}")
    print(f"{'='*60}\n")
    
    # 检查环境
    if not YATCC_SRC.exists():
        print(f"❌ YatCC 源码不存在: {YATCC_SRC}")
        return
    
    if not VENV_PYTHON.exists():
        print(f"❌ OpenHands 虚拟环境不存在: {VENV_PYTHON}")
        print("   请运行: uv venv .venv-openhands --python 3.12 && .venv-openhands/bin/pip install openhands-sdk openhands-tools")
        return
    
    # 确保日志目录存在
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 运行评测
    results = []
    
    if args.max_parallel <= 1:
        # 串行执行
        for model in models:
            print(f"\n{'='*40}")
            print(f"  评测: {model}")
            print(f"{'='*40}")
            
            result = run_openhands_eval(model, tasks, args.max_iterations)
            results.append(result)
            save_result(result)
            
            # 实时更新 Leaderboard
            update_leaderboard(results)
    else:
        # 并行执行
        print(f"\n  启动 {len(models)} 个评测任务，最大并行 {args.max_parallel}...")
        
        with ProcessPoolExecutor(max_workers=args.max_parallel) as executor:
            futures = {
                executor.submit(run_openhands_eval, model, tasks, args.max_iterations): model
                for model in models
            }
            
            for future in as_completed(futures):
                model = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    save_result(result)
                    print(f"\n  ✅ {model} 完成: {result.task_scores}")
                except Exception as e:
                    print(f"\n  ❌ {model} 失败: {e}")
        
        # 最终更新 Leaderboard
        update_leaderboard(results)
    
    # 打印总结
    print(f"\n{'='*60}")
    print(f"  评测完成! 共 {len(results)} 个模型")
    print(f"{'='*60}")
    
    for result in results:
        pipeline = sum(result.task_scores.values()) / len(result.task_scores) if result.task_scores else 0
        status = "✅" if not result.error else "❌"
        print(f"  {status} {result.model:<35s} Pipeline: {pipeline:.1f}  R={result.resurrections}")
    
    print(f"\n  结果目录: {RESULT_DIR}")
    print(f"  Leaderboard: {ROOT_DIR / 'site' / 'assets' / 'data' / 'leaderboard.json'}")


if __name__ == "__main__":
    main()
