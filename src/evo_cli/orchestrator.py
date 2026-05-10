"""BenchmarkOrchestrator — 串联 backend + evaluator + resurrection。

EvoBench 的核心调度器，控制整个评测流水线。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from .backends import get_backend
from .backends.base import AgentBackend, TaskResult
from .config import EvoConfig
from .context import ContextManager
from .evaluator import Evaluator, ScoreResult
from .metrics import EvoBenchResult


class BenchmarkOrchestrator:
    """EvoBench 评测编排器。

    流程：
    1. 初始化环境（CMake 配置 + 标准答案）
    2. 对每个 Task：
       a. 组装上下文
       b. Agent 编码
       c. 构建 + 评测
       d. 判断是否需要复活
    3. 采集指标 + 生成报告
    """

    def __init__(self, config: EvoConfig) -> None:
        self.config = config
        self.evaluator = Evaluator(config.workspace, config.pass_threshold)
        self.context = ContextManager(config.workspace)
        self.backend: Optional[AgentBackend] = None

    def run(self) -> EvoBenchResult:
        """执行完整评测流水线。"""
        # 初始化后端
        self.backend = get_backend(
            self.config.backend,
            model=self.config.model,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
        )

        print(f"\n{'='*70}")
        print(f"  EvoBench 评测启动")
        print(f"  后端: {self.backend.get_description()}")
        print(f"  模型: {self.config.model}")
        print(f"  任务: {self.config.tasks}")
        print(f"  复活: {'是' if self.config.resurrect else '否'}")
        print(f"{'='*70}\n")

        # 检查后端可用性
        if not self.backend.is_available():
            print(f"❌ 后端 {self.config.backend} 不可用！")
            return EvoBenchResult(
                agent_backend=self.config.backend,
                agent_model=self.config.model,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

        # Step 1: CMake 配置
        print("[初始化] CMake 配置...")
        flags = [
            f"-DSTUDENT_ID=EvoBench", f"-DSTUDENT_NAME=Agent",
            f"-DTASK1_WITH=flex", f"-DTASK2_WITH=bison",
            f"-DTASK2_REVIVE=OFF", f"-DTASK3_REVIVE=OFF",
            f"-DTASK4_REVIVE=OFF", f"-DTASK5_REVIVE=OFF",
        ]
        self.evaluator.cmake_configure(flags)

        # Step 2: 生成标准答案
        print("[初始化] 生成标准答案...")
        for task_id in [0, 1, 2, 3, 5]:
            if task_id in self.config.tasks or any(t > task_id for t in self.config.tasks):
                print(f"  生成 Task {task_id} 标准答案...")
                ok = self.evaluator.generate_answer(task_id)
                if not ok:
                    print(f"  ⚠️ Task {task_id} 标准答案生成失败（后续依赖它的任务可能受影响）")

        # Step 3: 逐 Task 评测
        result = EvoBenchResult(
            agent_backend=self.config.backend,
            agent_model=self.config.model,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        t_start = time.time()

        for task_id in self.config.tasks:
            score, agent_result = self._run_single_task(task_id)
            result.task_scores.append(score)
            result.agent_results.append(agent_result.to_dict())
            result.total_tokens += agent_result.total_tokens
            result.total_turns += agent_result.turns

            status = "✅" if score.passed else "❌"
            print(f"\n  Task {task_id}: {status} {score.normalized_score:.1f}% "
                  f"({score.score:.1f}/{score.max_score:.0f}) "
                  f"[{score.passed_count}/{score.test_count}] "
                  f"| {agent_result.turns}轮 {agent_result.total_tokens}tokens")

            # 复活机制
            if not score.passed and self.config.resurrect and task_id >= 2:
                print(f"\n  [复活] Task {task_id} 未通过，触发复活...")
                resurrected_score, resurrected_result = self._handle_resurrection(task_id)
                result.task_scores[-1] = resurrected_score  # 替换
                result.agent_results[-1] = resurrected_result.to_dict()
                result.resurrection_count += 1
                result.total_tokens += resurrected_result.total_tokens
                result.total_turns += resurrected_result.turns

                status2 = "✅" if resurrected_score.passed else "❌"
                print(f"  复活后 Task {task_id}: {status2} {resurrected_score.normalized_score:.1f}%")

        result.total_elapsed = time.time() - t_start
        result.compute_metrics()

        # Step 4: 生成报告
        self._generate_reports(result)

        return result

    def _run_single_task(self, task_id: int) -> tuple[ScoreResult, TaskResult]:
        """执行单个 Task：Agent 编码 -> 构建 -> 评测。"""
        # 组装上下文
        ctx = self.context.build_context(task_id)

        # Agent 编码
        print(f"\n  [Agent] 解决 Task {task_id}...")
        agent_result = self.backend.solve_task(
            task_id=task_id,
            workspace=self.config.workspace,
            context=ctx,
            max_turns=self.config.max_turns,
        )
        print(f"  [Agent] 完成: {agent_result.turns}轮, {agent_result.total_tokens}tokens, {agent_result.elapsed_seconds:.1f}s")

        # 构建
        print(f"  [构建] Task {task_id}...")
        build_ok, build_err = self.evaluator.build(task_id)
        if not build_ok:
            self.context.set_build_errors(build_err)
            score = ScoreResult(task_id=task_id, error=f"构建失败: {build_err[:200]}")
            return score, agent_result

        # 评测
        print(f"  [评测] Task {task_id}...")
        score = self.evaluator.score(task_id)
        self.context.set_task_result(task_id, score)

        return score, agent_result

    def _handle_resurrection(self, failed_task: int) -> tuple[ScoreResult, TaskResult]:
        """触发复活：修改 config.cmake，重新配置，注入标准答案。"""
        config_path = self.config.workspace / "config.cmake"
        if config_path.exists():
            content = config_path.read_text()
            for i in range(failed_task, 6):
                content = re.sub(
                    rf'set\(TASK{i}_REVIVE\s+\w+\)',
                    f'set(TASK{i}_REVIVE ON)',
                    content,
                )
            config_path.write_text(content)

        # 重新 CMake 配置
        flags = [
            f"-DSTUDENT_ID=EvoBench", f"-DSTUDENT_NAME=Agent",
            f"-DTASK1_WITH=flex", f"-DTASK2_WITH=bison",
        ]
        # 读取当前配置的 REVIVE 状态
        if config_path.exists():
            content = config_path.read_text()
            for i in range(2, 6):
                m = re.search(rf'set\(TASK{i}_REVIVE\s+(\w+)\)', content)
                flags.append(f"-DTASK{i}_REVIVE={m.group(1) if m else 'ON'}")

        self.evaluator.cmake_configure(flags)

        # 重新生成标准答案
        self.evaluator.generate_answer(failed_task)

        # 重新组装上下文（包含错误信息）
        self.context.set_build_errors("")

        # 重试
        return self._run_single_task(failed_task)

    def _generate_reports(self, result: EvoBenchResult) -> None:
        """生成报告。"""
        output = self.config.output_dir
        output.mkdir(parents=True, exist_ok=True)

        json_path = output / "evobench_report.json"
        result.to_json(json_path)

        csv_path = output / "evobench_report.csv"
        result.to_csv(csv_path)

        md_path = output / "evobench_report.md"
        result.to_markdown(md_path)

        print(f"\n  报告已生成:")
        print(f"    JSON: {json_path}")
        print(f"    CSV:  {csv_path}")
        print(f"    MD:   {md_path}")

        result.print_summary()
