"""Stage 3: 评分解析器 — 从 score.py 输出中提取得分和通过状态。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TestEntry:
    """单个测例的评测结果。"""
    name: str
    score: float
    max_score: float
    output: str = ""
    output_path: str = ""
    weight: float = 1.0

    @property
    def passed(self) -> bool:
        """测例是否通过（得分 >= 满分）。"""
        return self.score >= self.max_score


@dataclass
class TaskScore:
    """单个 Task 的完整评测结果。"""
    task_id: int
    total_score: float
    max_score: float
    tests: list[TestEntry] = field(default_factory=list)
    leaderboard: list[dict] = field(default_factory=list)
    raw_json: Optional[dict] = None

    @property
    def pass_rate(self) -> float:
        """通过率（通过的测例数 / 总测例数）。"""
        if not self.tests:
            return 0.0
        passed = sum(1 for t in self.tests if t.passed)
        return passed / len(self.tests)

    @property
    def normalized_score(self) -> float:
        """归一化得分（0-100）。"""
        if self.max_score == 0:
            return 0.0
        return (self.total_score / self.max_score) * 100.0

    @property
    def is_passing(self) -> bool:
        """是否达到及格线（默认 60%）。"""
        return self.normalized_score >= 60.0


def parse_score_json(path: Path) -> TaskScore:
    """从 score.json 解析评测结果。

    :param path: score.json 文件路径
    :return: TaskScore 对象
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tests = []
    for entry in data.get("tests", []):
        tests.append(TestEntry(
            name=entry.get("name", ""),
            score=float(entry.get("score", 0)),
            max_score=float(entry.get("max_score", 100)),
            output=entry.get("output", ""),
            output_path=entry.get("output_path", ""),
        ))

    leaderboard = data.get("leaderboard", [])
    total_score = 0.0
    max_score = 0.0
    for lb in leaderboard:
        if lb.get("name") == "总分":
            total_score = float(lb.get("value", 0))
            max_score = 100.0  # leaderboard 不直接提供 max_score

    # 如果 leaderboard 没有总分，从 tests 计算
    if total_score == 0.0 and tests:
        total_score = sum(t.score * t.weight / len(tests) for t in tests)
        max_score = sum(t.max_score * t.weight / len(tests) for t in tests)

    return TaskScore(
        task_id=_infer_task_id(path),
        total_score=total_score,
        max_score=max_score,
        tests=tests,
        leaderboard=leaderboard,
        raw_json=data,
    )


def parse_score_txt(path: Path) -> TaskScore:
    """从 score.txt 解析评测结果（fallback）。

    :param path: score.txt 文件路径
    :return: TaskScore 对象
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    tests = []
    total_score = 0.0
    max_score = 0.0

    # 解析每行：name ... score/max_score ... output
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("task") or line.startswith("总分"):
            continue

        # 匹配格式: name ... score/max_score ... output
        m = re.match(
            r"^(.+?)\s*\.{2,}\s*([\d.]+)/([\d.]+)\s*(.*)$",
            line,
        )
        if m:
            name = m.group(1).strip()
            score = float(m.group(2))
            max_s = float(m.group(3))
            output = m.group(4).strip()
            tests.append(TestEntry(
                name=name, score=score, max_score=max_s, output=output,
            ))

    # 解析总分行
    total_m = re.search(r"总分（加权）：([\d.]+)/([\d.]+)", content)
    if total_m:
        total_score = float(total_m.group(1))
        max_score = float(total_m.group(2))

    return TaskScore(
        task_id=_infer_task_id(path),
        total_score=total_score,
        max_score=max_score,
        tests=tests,
    )


def parse_score(path: Path) -> TaskScore:
    """自动选择 JSON 或 TXT 解析器。

    :param path: score.json 或 score.txt 路径
    :return: TaskScore 对象
    """
    if path.suffix == ".json":
        return parse_score_json(path)
    elif path.suffix == ".txt":
        return parse_score_txt(path)
    else:
        # 尝试 JSON 优先
        json_path = path.with_suffix(".json")
        if json_path.exists():
            return parse_score_json(json_path)
        txt_path = path.with_suffix(".txt")
        if txt_path.exists():
            return parse_score_txt(txt_path)
        raise FileNotFoundError(f"找不到评分文件: {path}")


def _infer_task_id(path: Path) -> int:
    """从路径推断 task ID。"""
    m = re.search(r"task(\d+)", str(path))
    if m:
        return int(m.group(1))
    return -1