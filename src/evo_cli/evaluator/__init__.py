"""评测器 — CMake 构建 + score.py 评分 + score.json 解析。"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ScoreResult:
    """单个 Task 的评分结果。"""
    task_id: int
    score: float = 0.0
    max_score: float = 100.0
    passed: bool = False
    pass_rate: float = 0.0
    test_count: int = 0
    passed_count: int = 0
    error: str = ""
    raw_data: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "score": self.score,
            "max_score": self.max_score,
            "normalized_score": self.normalized_score,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "test_count": self.test_count,
            "passed_count": self.passed_count,
            "error": self.error,
        }

    @property
    def normalized_score(self) -> float:
        if self.max_score == 0:
            return 0.0
        return (self.score / self.max_score) * 100.0


# Task 构建/评分目标映射
BUILD_TARGETS = {
    0: "task0", 1: "task1", 2: "task2",
    3: "task3", 4: "task4", 5: "task5-classic",
}
SCORE_TARGETS = {
    0: "task0-score", 1: "task1-score", 2: "task2-score",
    3: "task3-score", 4: "task4-score", 5: "task5-classic-score",
}
ANSWER_TARGETS = {
    0: "task0-answer", 1: "task1-answer", 2: "task2-answer",
    3: "task3-answer", 5: "task5-answer",
}


class Evaluator:
    """评测器 — 负责 CMake 构建和评分。"""

    def __init__(self, workspace: Path, pass_threshold: float = 60.0) -> None:
        self.workspace = workspace
        self.pass_threshold = pass_threshold

    def build(self, task_id: int, timeout: int = 300) -> tuple[bool, str]:
        """构建指定 Task。

        :return: (success, error_message)
        """
        target = BUILD_TARGETS.get(task_id, f"task{task_id}")
        try:
            rc = subprocess.run(
                ["cmake", "--build", "build", "-t", target],
                cwd=str(self.workspace), capture_output=True, text=True, timeout=timeout,
            )
            if rc.returncode != 0:
                return False, rc.stderr[-1000:]
            return True, ""
        except subprocess.TimeoutExpired:
            return False, f"构建超时 ({timeout}s)"

    def score(self, task_id: int, timeout: int = 300) -> ScoreResult:
        """运行评分并解析结果。

        :return: ScoreResult
        """
        target = SCORE_TARGETS.get(task_id, f"task{task_id}-score")
        try:
            subprocess.run(
                ["cmake", "--build", "build", "-t", target],
                cwd=str(self.workspace), capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ScoreResult(task_id=task_id, error=f"评分超时 ({timeout}s)")

        return self.parse_score(task_id)

    def parse_score(self, task_id: int) -> ScoreResult:
        """解析 score.json，修复 task0 无 leaderboard 的 bug。"""
        score_json = self.workspace / f"build/test/task{task_id}/score.json"
        score_txt = self.workspace / f"build/test/task{task_id}/score.txt"

        # 优先尝试 JSON
        if score_json.exists():
            return self._parse_json(task_id, score_json)

        # fallback 到 TXT
        if score_txt.exists():
            return self._parse_txt(task_id, score_txt)

        return ScoreResult(task_id=task_id, error="score 文件不存在")

    def _parse_json(self, task_id: int, path: Path) -> ScoreResult:
        """解析 score.json，兼容无 leaderboard 的情况。"""
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            return ScoreResult(task_id=task_id, error=f"解析失败: {e}")

        tests = data.get("tests", [])
        leaderboard = data.get("leaderboard", [])

        # 从 leaderboard 提取总分
        total = 0.0
        for lb in leaderboard:
            if lb.get("name") == "总分":
                total = float(lb["value"])
                break

        # 如果 leaderboard 没有总分（如 task0），从 tests 计算
        if total == 0.0 and tests:
            total = sum(t.get("score", 0) for t in tests) / len(tests)

        test_count = len(tests)
        passed_count = sum(1 for t in tests if t.get("score", 0) >= t.get("max_score", 100))
        pass_rate = passed_count / test_count if test_count > 0 else 0.0

        return ScoreResult(
            task_id=task_id,
            score=total,
            max_score=100.0,
            passed=total >= self.pass_threshold,
            pass_rate=pass_rate,
            test_count=test_count,
            passed_count=passed_count,
            raw_data=data,
        )

    def _parse_txt(self, task_id: int, path: Path) -> ScoreResult:
        """解析 score.txt。"""
        try:
            content = path.read_text(encoding="utf-8")
        except IOError:
            return ScoreResult(task_id=task_id, error="score.txt 读取失败")

        m = re.search(r"总分（加权）：([\d.]+)/([\d.]+)", content)
        if m:
            total = float(m.group(1))
            max_s = float(m.group(2))
            return ScoreResult(
                task_id=task_id, score=total, max_score=max_s,
                passed=(total / max_s * 100) >= self.pass_threshold if max_s > 0 else False,
            )

        return ScoreResult(task_id=task_id, error="无法解析 score.txt")

    def generate_answer(self, task_id: int, timeout: int = 300) -> bool:
        """生成指定 Task 的标准答案。"""
        target = ANSWER_TARGETS.get(task_id)
        if not target:
            return True  # task4 没有独立的 answer target
        try:
            rc = subprocess.run(
                ["cmake", "--build", "build", "-t", target],
                cwd=str(self.workspace), capture_output=True, text=True, timeout=timeout,
            )
            return rc.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def cmake_configure(self, flags: list[str], generator: str = "Ninja") -> bool:
        """执行 CMake 配置。"""
        cmd = ["cmake", "-S", ".", "-B", "build", f"-G{generator}"] + flags
        try:
            rc = subprocess.run(
                cmd, cwd=str(self.workspace), capture_output=True, text=True, timeout=120,
            )
            return rc.returncode == 0
        except subprocess.TimeoutExpired:
            return False
