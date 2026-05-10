#!/usr/bin/env python3
"""EvoBench 端到端跑分脚本。

使用 mimo-v2.5-pro 模型，逐个 Task 让 Agent 编码，
然后构建+评测，记录分数并生成报告。
"""

from __future__ import annotations

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

YATCC_ROOT = Path(__file__).parent / "YatCC"
OUTPUT_DIR = Path(__file__).parent / "evobench_output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── Agent 调用 ──────────────────────────────────────────────────────────

from openai import OpenAI

client = OpenAI(
    base_url=os.getenv("OPENAI_API_BASE"),
    api_key=os.getenv("OPENAI_API_KEY"),
)
MODEL = os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro")

# ─── Tool 定义 ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容（相对 YatCC 根目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件（相对 YatCC 根目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行 shell 命令（cwd=YatCC 根目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_task_readme",
            "description": "读取 Task 的 README.md",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        },
    },
]


def exec_tool(name: str, args: dict) -> str:
    """执行 Tool 调用。"""
    if name == "read_file":
        p = YATCC_ROOT / args["path"]
        if not p.exists():
            return f"文件不存在: {args['path']}"
        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        offset = args.get("offset", 1)
        limit = args.get("limit", 200)
        subset = lines[offset - 1 : offset - 1 + limit]
        return f"[{args['path']} 行 {offset}-{min(offset+limit-1, len(lines))}]\n" + "\n".join(subset)

    elif name == "write_file":
        p = YATCC_ROOT / args["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        line_count = len(args["content"].split("\n"))
        return f"写入成功: {args['path']} ({line_count} 行)"

    elif name == "run_command":
        try:
            result = subprocess.run(
                ["bash", "-c", args["command"]],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(YATCC_ROOT),
            )
            out = result.stdout[-3000:] if result.stdout else ""
            err = result.stderr[-3000:] if result.stderr else ""
            return f"[rc={result.returncode}]\n{out}\n{err}"
        except subprocess.TimeoutExpired:
            return "命令超时 (120s)"

    elif name == "read_task_readme":
        p = YATCC_ROOT / "task" / str(args["task_id"]) / "README.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
        return "README 不存在"

    return f"未知工具: {name}"


# ─── 评测 ──────────────────────────────────────────────────────────────

def parse_score(task_id: int) -> dict:
    """解析 score.json。"""
    score_path = YATCC_ROOT / f"build/test/task{task_id}/score.json"
    if not score_path.exists():
        return {"task_id": task_id, "score": 0, "max_score": 100, "passed": False, "error": "score.json not found"}
    with open(score_path) as f:
        data = json.load(f)
    total = 0.0
    for lb in data.get("leaderboard", []):
        if lb.get("name") == "总分":
            total = float(lb["value"])
    return {
        "task_id": task_id,
        "score": total,
        "max_score": 100.0,
        "passed": total >= 60.0,
        "tests": data.get("tests", []),
    }


def build_and_score(task_id: int) -> dict:
    """构建并评测单个 Task。"""
    build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
    score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score", 3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}

    # 构建
    rc = subprocess.run(
        ["cmake", "--build", "build", "-t", build_targets[task_id]],
        cwd=str(YATCC_ROOT), capture_output=True, text=True, timeout=300,
    )
    if rc.returncode != 0:
        return {"task_id": task_id, "score": 0, "max_score": 100, "passed": False, "error": rc.stderr[-500:]}

    # 评分
    subprocess.run(
        ["cmake", "--build", "build", "-t", score_targets[task_id]],
        cwd=str(YATCC_ROOT), capture_output=True, text=True, timeout=300,
    )

    return parse_score(task_id)


# ─── Agent 解题 ──────────────────────────────────────────────────────────

def solve_task(task_id: int, max_turns: int = 20) -> dict:
    """让 Agent 解决一个 Task。"""
    print(f"\n{'='*60}")
    print(f"  Agent 开始解决 Task {task_id}")
    print(f"{'='*60}")

    # 读取 README
    readme_path = YATCC_ROOT / "task" / str(task_id) / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    # 列出当前 Task 文件
    task_dir = YATCC_ROOT / "task" / str(task_id)
    files = []
    if task_dir.exists():
        for f in sorted(task_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                files.append(str(f.relative_to(task_dir)))

    system_prompt = f"""你是编译原理 AI 工程师。请完成 YatCC Task {task_id}。

## 当前任务说明
{readme}

## 当前 Task 文件
{chr(10).join('- ' + f for f in files)}

## 工作流程
1. 先用 read_file 阅读需要修改的源文件
2. 编写/修改代码
3. 用 run_command 编译: cmake --build build -t {'task' + str(task_id)}
4. 编译通过后评测: cmake --build build -t {'task' + str(task_id) + '-score'}
5. 如果失败，分析错误并修复
6. 当你认为完成时，直接回复 "TASK {task_id} COMPLETE" """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请完成 Task {task_id}。"},
    ]

    total_tokens = 0
    turns = 0

    for turn in range(1, max_turns + 1):
        turns = turn
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                temperature=0.2,
                timeout=300,
            )
        except Exception as e:
            print(f"  [Agent] API 失败 turn {turn}: {e}")
            break

        if resp.usage:
            total_tokens += resp.usage.total_tokens

        msg = resp.choices[0].message

        if not msg.tool_calls:
            print(f"  [Agent] 完成于第 {turn} 轮: {msg.content[:100]}")
            break

        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": tool_calls})

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = exec_tool(tc.function.name, args)
            print(f"  [Tool] {tc.function.name}({str(args)[:60]})")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return {"turns": turns, "total_tokens": total_tokens}


# ─── 主流程 ──────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  EvoBench 端到端跑分")
    print(f"  模型: {MODEL}")
    print(f"  API: {os.getenv('OPENAI_API_BASE')}")
    print("=" * 70)

    results = []
    t_start = time.time()

    for task_id in range(4):  # Task 0-3（Task 4-5 需要 RISC-V 修复）
        task_start = time.time()

        # Agent 解题
        agent_result = solve_task(task_id, max_turns=15)

        # 构建+评测
        score = build_and_score(task_id)

        elapsed = time.time() - task_start
        entry = {
            "task_id": task_id,
            "score": score.get("score", 0),
            "max_score": score.get("max_score", 100),
            "normalized_score": score.get("score", 0),
            "passed": score.get("passed", False),
            "turns": agent_result["turns"],
            "tokens": agent_result["total_tokens"],
            "elapsed_seconds": elapsed,
            "error": score.get("error", ""),
        }
        results.append(entry)

        status = "✅ PASS" if entry["passed"] else "❌ FAIL"
        print(f"\n  Task {task_id}: {status} ({entry['score']:.1f}/100)")
        print(f"  轮次: {entry['turns']}, Token: {entry['tokens']}, 耗时: {elapsed:.1f}s")

        # 检查是否需要复活
        if not entry["passed"] and task_id >= 2:
            print(f"\n  [复活] Task {task_id} 未通过，开启复活...")
            config = (YATCC_ROOT / "config.cmake").read_text()
            for i in range(task_id, 6):
                config = re.sub(rf'set\(TASK{i}_REVIVE\s+\w+\)', f'set(TASK{i}_REVIVE ON)', config)
            (YATCC_ROOT / "config.cmake").write_text(config)
            subprocess.run(
                ["cmake", "-S", ".", "-B", "build", "-GNinja"],
                cwd=str(YATCC_ROOT), capture_output=True, timeout=60,
            )
            # 重新生成标准答案
            answer_targets = {2: "task2-answer", 3: "task3-answer"}
            target = answer_targets.get(task_id)
            if target:
                subprocess.run(["cmake", "--build", "build", "-t", target], cwd=str(YATCC_ROOT), capture_output=True, timeout=300)

            # 复活后重试
            agent_result2 = solve_task(task_id, max_turns=10)
            score2 = build_and_score(task_id)
            elapsed2 = time.time() - task_start

            entry2 = {
                "task_id": task_id,
                "score": score2.get("score", 0),
                "max_score": 100,
                "normalized_score": score2.get("score", 0),
                "passed": score2.get("passed", False),
                "resurrected": True,
                "turns": agent_result2["turns"],
                "tokens": agent_result2["total_tokens"],
                "elapsed_seconds": elapsed2,
            }
            results.append(entry2)
            status2 = "✅ PASS" if entry2["passed"] else "❌ FAIL"
            print(f"  复活后 Task {task_id}: {status2} ({entry2['score']:.1f}/100)")

    total_elapsed = time.time() - t_start

    # 生成报告
    report = {
        "benchmark": "EvoBench-v1",
        "agent_model": MODEL,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_elapsed_seconds": total_elapsed,
        "tasks": results,
        "metrics": {
            "zero_shot_pass": all(r["passed"] for r in results if not r.get("resurrected")),
            "resurrection_count": sum(1 for r in results if r.get("resurrected")),
            "total_tokens": sum(r["tokens"] for r in results),
            "total_turns": sum(r["turns"] for r in results),
        },
    }

    report_path = OUTPUT_DIR / "evobench_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 打印摘要
    print(f"\n{'='*70}")
    print(f"  EvoBench 跑分完成 — {MODEL}")
    print(f"{'='*70}")
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        res = " [复活]" if r.get("resurrected") else ""
        print(f"  Task {r['task_id']}: {icon} {r['score']:.1f}/100{res}")
    print(f"  复活次数: {report['metrics']['resurrection_count']}")
    print(f"  总 Token: {report['metrics']['total_tokens']}")
    print(f"  总耗时: {total_elapsed:.1f}s")
    print(f"  报告: {report_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
