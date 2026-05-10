#!/usr/bin/env python3
"""EvoBench 主入口 — 串行 Agent 评测基准。

用法:
    # 使用本地沙盒运行完整评测
    python -m evobench_runner.main

    # 使用 Docker 沙盒
    python -m evobench_runner.main --docker

    # 指定 YatCC 路径和输出目录
    python -m evobench_runner.main --yatcc-path /path/to/YatCC --output /path/to/output

    # 仅运行 Baseline Agent 端到端测试
    python -m evobench_runner.main --baseline
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EvoBench — 串行 Agent 评测基准",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                           # 本地沙盒，完整评测
  %(prog)s --docker                  # Docker 沙盒
  %(prog)s --baseline                # Baseline Agent 端到端测试
  %(prog)s --task 3                  # 仅评测 Task 3
  %(prog)s --pass-threshold 80       # 设置及格线为 80%%
        """,
    )

    parser.add_argument(
        "--yatcc-path",
        type=Path,
        default=_project_root / "YatCC",
        help="YatCC 源码根目录 (默认: ./YatCC)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_project_root / "evobench_output",
        help="评测报告输出目录 (默认: ./evobench_output)",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="使用 Docker 沙盒 (默认: 本地沙盒)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="运行 Baseline Agent 端到端测试",
    )
    parser.add_argument(
        "--task",
        type=int,
        choices=range(6),
        help="仅评测指定 Task (0-5)",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=60.0,
        help="及格线百分比 (默认: 60)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="每个 Task 最大重试次数 (默认: 5)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=20,
        help="Agent 最大交互轮次 (默认: 20)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="覆盖 .env 中的模型名称",
    )

    args = parser.parse_args()

    # 验证 YatCC 路径
    if not args.yatcc_path.exists():
        print(f"错误: YatCC 路径不存在: {args.yatcc_path}")
        sys.exit(1)

    # 导入控制器
    from evobench_runner.controller import EvoBenchController

    controller = EvoBenchController(
        yatcc_root=args.yatcc_path,
        output_dir=args.output,
        use_docker=args.docker,
        pass_threshold=args.pass_threshold,
        max_retries=args.max_retries,
        max_turns=args.max_turns,
    )

    if args.model:
        controller.agent.model = args.model

    if args.baseline:
        print("运行 Baseline Agent 端到端测试...")
        from evobench_runner.baseline_agent import run_baseline_agent
        run_baseline_agent(args.yatcc_path, args.output)
    elif args.task is not None:
        print(f"仅评测 Task {args.task}...")
        _run_single_task(controller, args.task)
    else:
        print("运行完整 EvoBench 评测流水线...")
        controller.run()


def _run_single_task(controller, task_id: int) -> None:
    """仅运行单个 Task 的评测（调试用）。"""
    from evobench_runner.agent_tools import AGENT_TOOLS, ToolExecutor
    from evobench_runner.workflow import BuildConfig

    container_id = controller.sandbox.launch(controller.yatcc_root)
    print(f"沙盒已启动: {container_id}")

    try:
        # 环境准备
        controller._setup_environment(container_id)

        # CMake 配置
        config = BuildConfig()
        controller._cmake_configure(container_id, config)

        # 生成标准答案
        controller._build_target(container_id, "test-rtlib", "运行时库")
        controller._generate_all_answers(container_id)

        # 构建前置 Task（确保依赖满足）
        for tid in range(task_id):
            build_targets = {
                0: "task0", 1: "task1", 2: "task2",
                3: "task3", 4: "task4",
            }
            target = build_targets.get(tid, f"task{tid}")
            controller._build_target(container_id, target, f"Task {tid}")

        # 组装上下文
        system_prompt = controller.context_manager.build_system_prompt(task_id)
        tool_executor = ToolExecutor(controller.sandbox, container_id, controller.yatcc_root)

        # Agent 编码
        session = controller.agent.solve_task(
            task_id=task_id,
            system_prompt=system_prompt,
            tools=AGENT_TOOLS,
            tool_executor=tool_executor,
        )
        print(f"Agent 完成: {len(session.turns)} 轮, {session.total_tokens} tokens")

        # 构建 + 评测
        attempt = controller._build_and_score(container_id, task_id)
        if attempt.score:
            print(f"Task {task_id}: {attempt.score.normalized_score:.1f}%")
        else:
            print(f"Task {task_id}: 评测失败")

    finally:
        controller.sandbox.destroy(container_id)


if __name__ == "__main__":
    main()