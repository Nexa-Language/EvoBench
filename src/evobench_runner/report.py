"""Stage 7: 报告生成器 — 生成详细的评测报告。

支持多种输出格式：
- JSON: 结构化数据，供程序消费
- CSV: 扁平化数据，供 Excel/Pandas 分析
- Markdown: 人类可读的评测报告
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .metrics import EvoBenchMetrics, TaskMetrics


class ReportGenerator:
    """评测报告生成器。"""

    def __init__(self, output_dir: Path) -> None:
        """初始化报告生成器。

        :param output_dir: 报告输出目录
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, metrics: EvoBenchMetrics) -> dict[str, Path]:
        """生成所有格式的报告。

        :param metrics: 评测指标
        :return: {格式: 文件路径}
        """
        paths: dict[str, Path] = {}

        json_path = self.output_dir / "evobench_report.json"
        metrics.to_json(json_path)
        paths["json"] = json_path

        csv_path = self.output_dir / "evobench_report.csv"
        metrics.to_csv(csv_path)
        paths["csv"] = csv_path

        md_path = self.output_dir / "evobench_report.md"
        self._generate_markdown(metrics, md_path)
        paths["markdown"] = md_path

        return paths

    def _generate_markdown(
        self, metrics: EvoBenchMetrics, path: Path
    ) -> None:
        """生成 Markdown 格式的评测报告。

        :param metrics: 评测指标
        :param path: 输出路径
        """
        lines: list[str] = []

        # 标题
        lines.append(f"# EvoBench 评测报告")
        lines.append("")
        lines.append(f"- **Benchmark 版本**: {metrics.benchmark_version}")
        lines.append(f"- **Agent 模型**: {metrics.agent_model}")
        lines.append(f"- **评测时间**: {metrics.timestamp}")
        lines.append(f"- **总耗时**: {metrics.total_elapsed_seconds:.1f}s")
        lines.append("")

        # 核心指标
        lines.append("## 核心指标")
        lines.append("")
        lines.append("| 指标 | 值 | 说明 |")
        lines.append("|------|-----|------|")
        lines.append(
            f"| 独立通关率 | {'✅ 通过' if metrics.zero_shot_pass else '❌ 未通过'} | "
            f"不依赖复活一口气通关 |"
        )
        lines.append(
            f"| 流水线总分 | {metrics.zero_shot_pipeline_score:.1f}/100 | "
            f"所有 Task 归一化得分均值 |"
        )
        lines.append(
            f"| 复活次数 | {metrics.resurrection_count} | "
            f"完成全部 Task 所需复活次数 |"
        )
        lines.append(
            f"| 平均复活/任务 | {metrics.avg_resurrection_per_task:.2f} | "
            f"每个 Task 平均复活次数 |"
        )
        if metrics.evolutionary_gain is not None:
            lines.append(
                f"| 进化增益率 | {metrics.evolutionary_gain:.2f} | "
                f"Agent 理解自己代码的能力 |"
            )
        else:
            lines.append(
                f"| 进化增益率 | N/A | "
                f"需要对照实验 |"
            )
        lines.append("")

        # 逐 Task 详情
        lines.append("## 逐 Task 详情")
        lines.append("")
        lines.append(
            "| Task | 得分 | 归一化 | 通过 | 复活 | 尝试次数 | 耗时 |"
        )
        lines.append(
            "|------|------|--------|------|------|----------|------|"
        )
        for tm in metrics.task_metrics:
            passed_icon = "✅" if tm.passed else "❌"
            res_icon = "🔄" if tm.resurrected else "➖"
            lines.append(
                f"| Task {tm.task_id} | {tm.score:.1f}/{tm.max_score:.0f} | "
                f"{tm.normalized_score:.1f}% | {passed_icon} | {res_icon} | "
                f"{tm.total_attempts} | {tm.elapsed_seconds:.1f}s |"
            )
        lines.append("")

        # 节点通过率
        lines.append("## 节点通过率")
        lines.append("")
        for task_id, rate in metrics.node_pass_rates.items():
            icon = "✅" if rate >= 1.0 else "❌"
            lines.append(f"- Task {task_id}: {icon} {rate:.0%}")
        lines.append("")

        # 测例详情
        lines.append("## 测例详情")
        lines.append("")
        for tm in metrics.task_metrics:
            if tm.test_results:
                lines.append(f"### Task {tm.task_id}")
                lines.append("")
                lines.append("| 测例 | 得分 | 通过 |")
                lines.append("|------|------|------|")
                for test in tm.test_results:
                    passed = "✅" if test["passed"] else "❌"
                    lines.append(
                        f"| {test['name']} | "
                        f"{test['score']:.0f}/{test['max_score']:.0f} | {passed} |"
                    )
                lines.append("")

        # 统计信息
        lines.append("## 统计信息")
        lines.append("")
        lines.append(f"- 总交互轮次: {metrics.total_turns}")
        lines.append(f"- 总 Token 消耗: {metrics.total_tokens}")
        lines.append(f"- 总构建尝试: {metrics.total_build_attempts}")
        lines.append("")

        # 进化增益说明
        if metrics.evolutionary_gain_detail:
            lines.append("## 进化增益率说明")
            lines.append("")
            lines.append(metrics.evolutionary_gain_detail)
            lines.append("")

        # 写入文件
        path.write_text("\n".join(lines), encoding="utf-8")

    def print_summary(self, metrics: EvoBenchMetrics) -> None:
        """在终端打印评测摘要。

        :param metrics: 评测指标
        """
        print()
        print("=" * 70)
        print(f"  EvoBench 评测完成 — {metrics.agent_model}")
        print("=" * 70)
        print(f"  独立通关: {'✅ YES' if metrics.zero_shot_pass else '❌ NO'}")
        print(f"  流水线总分: {metrics.zero_shot_pipeline_score:.1f}/100")
        print(f"  复活次数: {metrics.resurrection_count}")
        print(f"  总耗时: {metrics.total_elapsed_seconds:.1f}s")
        print("-" * 70)
        for tm in metrics.task_metrics:
            icon = "✅" if tm.passed else "❌"
            res = " [复活]" if tm.resurrected else ""
            print(
                f"  Task {tm.task_id}: {icon} "
                f"{tm.normalized_score:.1f}% "
                f"({tm.score:.1f}/{tm.max_score:.0f})"
                f"{res}"
            )
        print("=" * 70)
        print()