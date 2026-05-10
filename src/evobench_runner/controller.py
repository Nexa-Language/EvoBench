"""Stage 3+8: 评测控制器 — EvoBench 核心调度器。

协调沙盒、工作流引擎、Agent 接口、上下文管理器和复活机制，
完成完整的串行评测流水线。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .sandbox import Sandbox, LocalSandbox
from .workflow import WorkflowEngine, BuildConfig, PipelineResult
from .resurrection import ResurrectionEngine
from .agent_tools import AGENT_TOOLS, ToolExecutor
from .agent_interface import AgentInterface, AgentSession
from .context_manager import ContextManager
from .metrics import MetricsCollector, EvoBenchMetrics
from .report import ReportGenerator


class EvoBenchController:
    """EvoBench 评测控制器。

    作为"上帝视角"调度整个评测流程：
    1. 初始化沙盒环境
    2. 对每个 Task，组装上下文 -> Agent 编码 -> 构建评测 -> 判断复活
    3. 采集指标并生成报告
    """

    def __init__(
        self,
        yatcc_root: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        use_docker: bool = False,
        pass_threshold: float = 60.0,
        max_retries: int = 5,
        max_turns: int = 20,
    ) -> None:
        """初始化控制器。

        :param yatcc_root: YatCC 源码根目录
        :param output_dir: 报告输出目录
        :param use_docker: 是否使用 Docker 沙盒
        :param pass_threshold: 及格线（百分比）
        :param max_retries: 每个 Task 最大重试次数
        :param max_turns: Agent 最大交互轮次
        """
        # 加载环境变量
        load_dotenv(Path(__file__).parent.parent / ".env")

        self.yatcc_root = yatcc_root or (Path(__file__).parent.parent / "YatCC")
        self.output_dir = output_dir or (Path(__file__).parent.parent / "evobench_output")
        self.pass_threshold = pass_threshold
        self.max_retries = max_retries
        self.max_turns = max_turns

        # 沙盒
        if use_docker:
            self.sandbox = Sandbox(
                image=os.getenv("EVOBENCH_DOCKER_IMAGE", "evobench:latest"),
                yatcc_root=self.yatcc_root,
            )
        else:
            self.sandbox = LocalSandbox(yatcc_root=self.yatcc_root)

        # 复活引擎
        self.resurrection = ResurrectionEngine(self.yatcc_root)

        # 上下文管理器
        self.context_manager = ContextManager(self.yatcc_root)

        # Agent 接口
        self.agent = AgentInterface(
            api_base=os.getenv("OPENAI_API_BASE", "https://aihub.arcsysu.cn/v1"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro"),
            max_turns=self.max_turns,
        )

        # 指标采集器
        self.metrics_collector = MetricsCollector(pass_threshold=self.pass_threshold)

        # 报告生成器
        self.reporter = ReportGenerator(self.output_dir)

    def run(self) -> EvoBenchMetrics:
        """执行完整的 EvoBench 评测流水线。

        流程：
        1. 重置复活标志
        2. 环境准备（ANTLR + LLVM）
        3. CMake 配置 + 生成标准答案
        4. 对每个 Task 0-5：
           a. 组装 System Prompt
           b. Agent 编码（Tool Calling 循环）
           c. 构建 + 评测
           d. 判断是否需要复活
        5. 采集指标 + 生成报告

        :return: EvoBenchMetrics
        """
        print("=" * 70)
        print("  EvoBench 评测流水线启动")
        print(f"  YatCC 路径: {self.yatcc_root}")
        print(f"  Agent 模型: {self.agent.model}")
        print(f"  沙盒模式: {'Docker' if isinstance(self.sandbox, Sandbox) and not isinstance(self.sandbox, LocalSandbox) else '本地'}")
        print("=" * 70)

        # Step 0: 重置复活标志
        self.resurrection.reset_all_revive()

        # Step 1: 启动沙盒
        container_id = self.sandbox.launch(self.yatcc_root)
        print(f"\n[沙盒] 容器已启动: {container_id}")

        # Step 2: 环境准备
        print("\n[环境] 准备 ANTLR + LLVM + Task5 依赖...")
        self._setup_environment(container_id)

        # Step 3: CMake 配置 + 生成标准答案
        print("\n[构建] CMake 配置 + 生成标准答案...")
        config = BuildConfig()
        self._cmake_configure(container_id, config)
        self._build_target(container_id, "test-rtlib", "运行时库")
        self._generate_all_answers(container_id)

        # Step 4: 逐 Task 评测
        pipeline_result = PipelineResult(config=config)
        agent_sessions: list[AgentSession] = []
        tool_executor = ToolExecutor(self.sandbox, container_id, self.yatcc_root)

        for task_id in range(6):
            print(f"\n{'='*70}")
            print(f"  Task {task_id} 开始")
            print(f"{'='*70}")

            # 4a. 组装 System Prompt
            system_prompt = self.context_manager.build_system_prompt(
                task_id,
                include_history=(task_id > 0),
                include_errors=(task_id > 0),
            )

            # 4b. Agent 编码
            print(f"\n[Agent] 开始解决 Task {task_id}...")
            session = self.agent.solve_task(
                task_id=task_id,
                system_prompt=system_prompt,
                tools=AGENT_TOOLS,
                tool_executor=tool_executor,
            )
            agent_sessions.append(session)
            print(f"[Agent] Task {task_id} 完成: {len(session.turns)} 轮交互, "
                  f"{session.total_tokens} tokens, {session.total_elapsed:.1f}s")

            # 4c. 构建 + 评测
            print(f"\n[评测] 构建并评测 Task {task_id}...")
            attempt = self._build_and_score(container_id, task_id)
            pipeline_result.attempts.append(attempt)

            if attempt.score:
                status = "✅ 通过" if attempt.score.is_passing else "❌ 未通过"
                print(f"[评测] Task {task_id}: {status} "
                      f"({attempt.score.normalized_score:.1f}%)")
                self.context_manager.set_task_result(
                    task_id,
                    f"{status} ({attempt.score.normalized_score:.1f}%)",
                )
            else:
                print(f"[评测] Task {task_id}: ❌ 构建/评测失败")
                self.context_manager.set_task_result(task_id, "构建/评测失败")

            # 4d. 判断是否需要复活
            if attempt.score and not attempt.score.is_passing and task_id >= 2:
                print(f"\n[复活] Task {task_id} 未通过，触发复活机制...")
                self.resurrection.trigger_resurrection(task_id)

                # 更新 config 并重新配置 CMake
                config.revive_task2 = True
                config.revive_task3 = True
                config.revive_task4 = True
                config.revive_task5 = True
                self._cmake_configure(container_id, config)

                # 重新生成当前 Task 的标准答案
                answer_targets = {2: "task2-answer", 3: "task3-answer",
                                  4: "task4-answer", 5: "task5-answer"}
                target = answer_targets.get(task_id)
                if target:
                    self._build_target(container_id, target, f"Task {task_id} 标准答案(复活)")

                # 复活后重试一次
                print(f"[复活] 重试 Task {task_id}...")
                retry_attempt = self._build_and_score(container_id, task_id)
                retry_attempt.resurrected = True
                pipeline_result.attempts.append(retry_attempt)

                if retry_attempt.score:
                    status = "✅ 通过" if retry_attempt.score.is_passing else "❌ 未通过"
                    print(f"[复活] Task {task_id} 重试: {status} "
                          f"({retry_attempt.score.normalized_score:.1f}%)")

        # Step 5: 采集指标 + 生成报告
        print(f"\n{'='*70}")
        print(f"  采集指标 & 生成报告")
        print(f"{'='*70}")

        metrics = self.metrics_collector.collect(
            pipeline_result=pipeline_result,
            agent_sessions=agent_sessions,
            agent_model=self.agent.model,
        )

        # 生成报告
        report_paths = self.reporter.generate_all(metrics)
        for fmt, path in report_paths.items():
            print(f"[报告] {fmt}: {path}")

        # 终端摘要
        self.reporter.print_summary(metrics)

        # 清理
        self.sandbox.destroy(container_id)

        return metrics

    def _setup_environment(self, container_id: str) -> None:
        """准备 ANTLR、LLVM 和 Task5 环境。"""
        steps = [
            ("ANTLR", "bash antlr/setup.sh", 300),
            ("LLVM", "bash llvm/setup.sh", 600),
            ("Task5", "bash task5_setup.sh", 300),
        ]
        for name, cmd, timeout in steps:
            print(f"  [环境] 准备 {name}...")
            rc, stdout, stderr = self.sandbox.exec(
                container_id, f"cd /workspace/YatCC && {cmd}", timeout=timeout,
            )
            if rc != 0:
                print(f"  [环境] {name} 准备失败 (rc={rc}), 继续...")

    def _cmake_configure(self, container_id: str, config: BuildConfig) -> None:
        """执行 CMake 配置。"""
        flags = " ".join(config.to_cmake_flags())
        cmd = (
            f"cd /workspace/YatCC && "
            f"cmake -S . -B build -G{config.generator} {flags}"
        )
        rc, stdout, stderr = self.sandbox.exec(container_id, cmd, timeout=120)
        if rc != 0:
            print(f"  [CMake] 配置警告:\n{stderr[-500:]}")
        else:
            print(f"  [CMake] 配置完成")

    def _build_target(
        self, container_id: str, target: str, description: str = ""
    ) -> bool:
        """构建指定 CMake 目标。"""
        cmd = f"cd /workspace/YatCC && cmake --build build -t {target}"
        rc, stdout, stderr = self.sandbox.exec(container_id, cmd, timeout=300)
        if rc != 0:
            print(f"  [构建] {description or target} 失败:\n{stderr[-300:]}")
            return False
        print(f"  [构建] {description or target} 完成")
        return True

    def _generate_all_answers(self, container_id: str) -> None:
        """生成所有 Task 的标准答案。"""
        answer_targets = [
            "task0-answer", "task1-answer", "task2-answer",
            "task3-answer", "task5-answer",
        ]
        for target in answer_targets:
            self._build_target(container_id, target, target)

    def _build_and_score(
        self, container_id: str, task_id: int
    ) -> "TaskAttempt":
        """构建并评测单个 Task。

        :param container_id: 容器 ID
        :param task_id: Task ID
        :return: TaskAttempt
        """
        from .workflow import TaskAttempt

        attempt = TaskAttempt(task_id=task_id, attempt_number=1)
        t_start = time.time()

        # 构建 Task
        build_targets = {
            0: "task0", 1: "task1", 2: "task2",
            3: "task3", 4: "task4", 5: "task5-classic",
        }
        build_target = build_targets.get(task_id, f"task{task_id}")
        rc, stdout, stderr = self.sandbox.exec(
            container_id,
            f"cd /workspace/YatCC && cmake --build build -t {build_target}",
            timeout=300,
        )
        attempt.build_stdout = stdout
        attempt.build_stderr = stderr

        if rc != 0:
            attempt.error_message = f"构建失败:\n{stderr[-1000:]}"
            self.context_manager.set_build_errors(stdout, stderr)
            attempt.elapsed_seconds = time.time() - t_start
            return attempt

        # 运行评分
        score_targets = {
            0: "task0-score", 1: "task1-score", 2: "task2-score",
            3: "task3-score", 4: "task4-score", 5: "task5-classic-score",
        }
        score_target = score_targets.get(task_id, f"task{task_id}-score")
        rc2, stdout2, stderr2 = self.sandbox.exec(
            container_id,
            f"cd /workspace/YatCC && cmake --build build -t {score_target}",
            timeout=300,
        )

        # 解析评分结果
        score_paths = {
            0: "build/test/task0/score.json",
            1: "build/test/task1/score.json",
            2: "build/test/task2/score.json",
            3: "build/test/task3/score.json",
            4: "build/test/task4/score.json",
            5: "build/test/task5/score.json",
        }
        score_rel = score_paths.get(task_id)
        if score_rel:
            score_path = self.yatcc_root / score_rel
            if score_path.exists():
                from .score_parser import parse_score
                try:
                    attempt.score = parse_score(score_path)
                except Exception as e:
                    attempt.error_message = f"解析评分失败: {e}"

        self.context_manager.set_build_errors(stdout2, stderr2)
        attempt.elapsed_seconds = time.time() - t_start
        return attempt