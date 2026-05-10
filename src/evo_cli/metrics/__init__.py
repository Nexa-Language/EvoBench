"""指标采集与报告生成。"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..evaluator import ScoreResult


@dataclass
class EvoBenchResult:
    """完整评测结果。"""
    benchmark_version: str = "EvoBench-v2"
    agent_backend: str = ""
    agent_model: str = ""
    timestamp: str = ""
    total_elapsed: float = 0.0

    task_scores: list[ScoreResult] = field(default_factory=list)
    agent_results: list[dict] = field(default_factory=list)

    # 核心指标
    zero_shot_pass: bool = False
    pipeline_score: float = 0.0
    resurrection_count: int = 0
    total_tokens: int = 0
    total_turns: int = 0

    def compute_metrics(self) -> None:
        """从 task_scores 计算汇总指标。"""
        if not self.task_scores:
            return
        self.pipeline_score = sum(s.normalized_score for s in self.task_scores) / len(self.task_scores)
        self.zero_shot_pass = all(s.passed for s in self.task_scores)

    def to_dict(self) -> dict:
        return {
            "benchmark": self.benchmark_version,
            "agent_backend": self.agent_backend,
            "agent_model": self.agent_model,
            "timestamp": self.timestamp,
            "total_elapsed_seconds": self.total_elapsed,
            "tasks": [s.to_dict() for s in self.task_scores],
            "agent_details": self.agent_results,
            "metrics": {
                "zero_shot_pass": self.zero_shot_pass,
                "pipeline_score": round(self.pipeline_score, 2),
                "resurrection_count": self.resurrection_count,
                "total_tokens": self.total_tokens,
                "total_turns": self.total_turns,
            },
        }

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def to_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["task_id", "score", "max_score", "normalized_score", "passed", "test_count", "passed_count"])
            for s in self.task_scores:
                w.writerow([s.task_id, s.score, s.max_score, round(s.normalized_score, 2),
                            s.passed, s.test_count, s.passed_count])
            w.writerow([])
            w.writerow(["metric", "value"])
            w.writerow(["zero_shot_pass", self.zero_shot_pass])
            w.writerow(["pipeline_score", round(self.pipeline_score, 2)])
            w.writerow(["resurrection_count", self.resurrection_count])
            w.writerow(["total_tokens", self.total_tokens])

    def to_markdown(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# EvoBench 评测报告",
            "",
            f"- **后端**: {self.agent_backend}",
            f"- **模型**: {self.agent_model}",
            f"- **时间**: {self.timestamp}",
            f"- **总耗时**: {self.total_elapsed:.1f}s",
            "",
            "## 核心指标",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| 独立通关 | {'✅' if self.zero_shot_pass else '❌'} |",
            f"| 流水线总分 | {self.pipeline_score:.1f}/100 |",
            f"| 复活次数 | {self.resurrection_count} |",
            f"| 总 Token | {self.total_tokens} |",
            "",
            "## 逐 Task 详情",
            "",
            "| Task | 得分 | 归一化 | 通过 | 测例 |",
            "|------|------|--------|------|------|",
        ]
        for s in self.task_scores:
            icon = "✅" if s.passed else "❌"
            lines.append(f"| Task {s.task_id} | {s.score:.1f}/{s.max_score:.0f} | {s.normalized_score:.1f}% | {icon} | {s.passed_count}/{s.test_count} |")
        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

    def print_summary(self) -> None:
        print()
        print("=" * 70)
        print(f"  EvoBench 跑分完成 — {self.agent_backend} / {self.agent_model}")
        print("=" * 70)
        for s in self.task_scores:
            icon = "✅" if s.passed else "❌"
            print(f"  Task {s.task_id}: {icon} {s.normalized_score:.1f}% ({s.score:.1f}/{s.max_score:.0f}) [{s.passed_count}/{s.test_count}]")
        print("-" * 70)
        print(f"  独立通关: {'✅ YES' if self.zero_shot_pass else '❌ NO'}")
        print(f"  流水线总分: {self.pipeline_score:.1f}/100")
        print(f"  复活次数: {self.resurrection_count}")
        print(f"  总 Token: {self.total_tokens}")
        print(f"  总耗时: {self.total_elapsed:.1f}s")
        print("=" * 70)
        print()
