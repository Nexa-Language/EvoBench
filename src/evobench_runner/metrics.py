"""Stage 7: 数据采集与指标评测 — Metrics & Logging。

量化 Agent 的进化与学习能力，实现 idea.md 中定义的多维指标：
1. 独立通关率 (Zero-shot Pipeline Pass Rate)
2. 节点通过率 (Node Pass Rate)
3. 平均复活次数 (Average Resurrection Count)
4. 进化增益率 (Evolutionary Gain)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .score_parser import TaskScore
from .workflow import TaskAttempt, PipelineResult


@dataclass
class TaskMetrics:
    """单个 Task 的详细指标。"""
    task_id: int
    score: float = 0.0
    max_score: float = 100.0
    normalized_score: float = 0.0
    passed: bool = False
    resurrected: bool = False
    total_attempts: int = 0
    successful_attempt: int = 0
    build_errors: list[str] = field(default_factory=list)
    test_results: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass
class EvoBenchMetrics:
    """EvoBench 完整评测指标。

    包含 idea.md 中定义的全部 4 个核心指标。
    """

    # 元信息
    benchmark_version: str = "EvoBench-v1"
    agent_model: str = ""
    timestamp: str = ""
    total_elapsed_seconds: float = 0.0

    # 逐 Task 指标
    task_metrics: list[TaskMetrics] = field(default_factory=list)

    # ─── 核心指标 ───────────────────────────────────────────────

    # 1. 独立通关率：Agent 不依赖任何复活，一口气通关到 Task 5 的概率
    zero_shot_pass: bool = False
    zero_shot_pipeline_score: float = 0.0

    # 2. 节点通过率：在获得前置正确答案（复活）的前提下，单独通过某个 Task 的概率
    node_pass_rates: dict[int, float] = field(default_factory=dict)

    # 3. 平均复活次数：完成整个编译器所需要的复活次数
    resurrection_count: int = 0
    avg_resurrection_per_task: float = 0.0

    # 4. 进化增益率
    evolutionary_gain: Optional[float] = None
    evolutionary_gain_detail: str = ""

    # 额外统计
    total_turns: int = 0
    total_tokens: int = 0
    total_build_attempts: int = 0

    def to_dict(self) -> dict:
        """转换为可序列化的字典。"""
        return {
            "benchmark": self.benchmark_version,
            "agent_model": self.agent_model,
            "timestamp": self.timestamp,
            "total_elapsed_seconds": self.total_elapsed_seconds,
            "tasks": [
                {
                    "task_id": tm.task_id,
                    "score": tm.score,
                    "max_score": tm.max_score,
                    "normalized_score": tm.normalized_score,
                    "passed": tm.passed,
                    "resurrected": tm.resurrected,
                    "total_attempts": tm.total_attempts,
                    "elapsed_seconds": tm.elapsed_seconds,
                }
                for tm in self.task_metrics
            ],
            "metrics": {
                "zero_shot_pass": self.zero_shot_pass,
                "zero_shot_pipeline_score": self.zero_shot_pipeline_score,
                "node_pass_rates": self.node_pass_rates,
                "resurrection_count": self.resurrection_count,
                "avg_resurrection_per_task": self.avg_resurrection_per_task,
                "evolutionary_gain": self.evolutionary_gain,
                "evolutionary_gain_detail": self.evolutionary_gain_detail,
            },
            "stats": {
                "total_turns": self.total_turns,
                "total_tokens": self.total_tokens,
                "total_build_attempts": self.total_build_attempts,
            },
        }

    def to_json(self, path: Path) -> None:
        """导出为 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def to_csv(self, path: Path) -> None:
        """导出为 CSV 文件（扁平化）。"""
        import csv
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "task_id", "score", "max_score", "normalized_score",
                "passed", "resurrected", "total_attempts", "elapsed_seconds",
            ])
            for tm in self.task_metrics:
                writer.writerow([
                    tm.task_id, tm.score, tm.max_score, tm.normalized_score,
                    tm.passed, tm.resurrected, tm.total_attempts, tm.elapsed_seconds,
                ])
            # 汇总行
            writer.writerow([])
            writer.writerow(["metric", "value"])
            writer.writerow(["zero_shot_pass", self.zero_shot_pass])
            writer.writerow(["zero_shot_pipeline_score", self.zero_shot_pipeline_score])
            writer.writerow(["resurrection_count", self.resurrection_count])
            writer.writerow(["avg_resurrection_per_task", self.avg_resurrection_per_task])
            writer.writerow(["evolutionary_gain", self.evolutionary_gain or "N/A"])


class MetricsCollector:
    """指标采集器。

    从 PipelineResult 和 AgentSession 中提取并计算 EvoBench 指标。
    """

    def __init__(self, pass_threshold: float = 60.0) -> None:
        """初始化指标采集器。

        :param pass_threshold: 及格线（百分比）
        """
        self.pass_threshold = pass_threshold

    def collect(
        self,
        pipeline_result: PipelineResult,
        agent_sessions: Optional[list] = None,
        agent_model: str = "",
    ) -> EvoBenchMetrics:
        """从流水线结果中采集指标。

        :param pipeline_result: 流水线执行结果
        :param agent_sessions: Agent 会话记录列表
        :param agent_model: Agent 模型名称
        :return: EvoBenchMetrics
        """
        metrics = EvoBenchMetrics(
            agent_model=agent_model,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            total_elapsed_seconds=pipeline_result.total_elapsed,
        )

        # 逐 Task 指标
        task_attempts: dict[int, list[TaskAttempt]] = {}
        for attempt in pipeline_result.attempts:
            task_attempts.setdefault(attempt.task_id, []).append(attempt)

        for task_id in range(6):
            attempts = task_attempts.get(task_id, [])
            tm = self._compute_task_metrics(task_id, attempts)
            metrics.task_metrics.append(tm)

        # 1. 独立通关率
        metrics.zero_shot_pass = pipeline_result.zero_shot_pass
        metrics.zero_shot_pipeline_score = self._compute_pipeline_score(
            metrics.task_metrics
        )

        # 2. 节点通过率
        metrics.node_pass_rates = {
            tm.task_id: (1.0 if tm.passed else 0.0)
            for tm in metrics.task_metrics
        }

        # 3. 平均复活次数
        metrics.resurrection_count = pipeline_result.resurrection_count
        metrics.avg_resurrection_per_task = (
            metrics.resurrection_count / 6.0 if metrics.resurrection_count > 0 else 0.0
        )

        # 4. 进化增益率（需要对照实验，此处标记为待计算）
        metrics.evolutionary_gain = None
        metrics.evolutionary_gain_detail = (
            "进化增益率需要对照实验："
            "运行两次评测，一次 Agent 继承自己代码，一次 Agent 拿到标准答案。"
            "增益率 = (继承自己代码成功率) / (标准答案成功率)"
        )

        # Agent 会话统计
        if agent_sessions:
            metrics.total_turns = sum(len(s.turns) for s in agent_sessions)
            metrics.total_tokens = sum(s.total_tokens for s in agent_sessions)

        metrics.total_build_attempts = len(pipeline_result.attempts)

        return metrics

    def _compute_task_metrics(
        self, task_id: int, attempts: list[TaskAttempt]
    ) -> TaskMetrics:
        """计算单个 Task 的指标。

        :param task_id: Task ID
        :param attempts: 该 Task 的所有尝试记录
        :return: TaskMetrics
        """
        tm = TaskMetrics(task_id=task_id, total_attempts=len(attempts))

        for i, attempt in enumerate(attempts):
            if attempt.score:
                tm.score = attempt.score.total_score
                tm.max_score = attempt.score.max_score
                tm.normalized_score = attempt.score.normalized_score
                tm.passed = attempt.score.is_passing
                tm.test_results = [
                    {"name": t.name, "score": t.score, "max_score": t.max_score,
                     "passed": t.passed}
                    for t in attempt.score.tests
                ]

            if attempt.resurrected:
                tm.resurrected = True

            if attempt.build_stderr:
                tm.build_errors.append(attempt.build_stderr[-500:])

            tm.elapsed_seconds += attempt.elapsed_seconds

            if tm.passed:
                tm.successful_attempt = i + 1

        return tm

    @staticmethod
    def _compute_pipeline_score(task_metrics: list[TaskMetrics]) -> float:
        """计算流水线总分（所有 Task 归一化得分的加权平均）。"""
        if not task_metrics:
            return 0.0
        return sum(tm.normalized_score for tm in task_metrics) / len(task_metrics)

    def compute_evolutionary_gain(
        self,
        self_code_results: EvoBenchMetrics,
        golden_code_results: EvoBenchMetrics,
    ) -> float:
        """计算进化增益率。

        增益率 = (Agent 继承自己代码做后续任务的成功率) /
                 (Agent 拿到标准答案做后续任务的成功率)

        :param self_code_results: Agent 继承自己代码的评测结果
        :param golden_code_results: Agent 拿到标准答案的评测结果
        :return: 进化增益率
        """
        self_pass = sum(
            1 for tm in self_code_results.task_metrics if tm.passed
        )
        golden_pass = sum(
            1 for tm in golden_code_results.task_metrics if tm.passed
        )

        if golden_pass == 0:
            return 0.0

        return self_pass / golden_pass