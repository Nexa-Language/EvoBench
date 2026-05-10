"""Stage 4: 自动化复活机制 — 强串行防误差级联。

当 Agent 在 Task N 失败时，自动修改 config.cmake 开启复活，
重新构建项目，使后续 Task 拿到标准答案的输入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ResurrectionState:
    """复活状态记录。"""
    task_id: int
    was_resurrected: bool
    revive_flags_before: dict[int, bool]
    revive_flags_after: dict[int, bool]


class ResurrectionEngine:
    """复活引擎。

    负责读取和修改 YatCC 的 config.cmake 中的 REVIVE 标志，
    控制 Agent 在失败后是否注入标准答案。
    """

    # 支持复活的 Task 范围
    REVIVABLE_TASKS = range(2, 6)  # Task 2-5

    def __init__(self, yatcc_root: Path) -> None:
        """初始化复活引擎。

        :param yatcc_root: YatCC 源码根目录
        """
        self.yatcc_root = yatcc_root
        self.config_path = yatcc_root / "config.cmake"
        self._history: list[ResurrectionState] = []

    def read_revive_flags(self) -> dict[int, bool]:
        """读取当前所有复活标志。

        :return: {task_id: is_revive_on}
        """
        if not self.config_path.exists():
            return {}

        content = self.config_path.read_text()
        flags: dict[int, bool] = {}

        for task_id in self.REVIVABLE_TASKS:
            m = re.search(rf'set\(TASK{task_id}_REVIVE\s+(\w+)\)', content)
            if m:
                flags[task_id] = m.group(1) == "ON"

        return flags

    def set_revive_flag(self, task_id: int, enabled: bool) -> bool:
        """设置单个 Task 的复活标志。

        :param task_id: Task ID (2-5)
        :param enabled: True=开启复活, False=关闭
        :return: 是否成功修改
        """
        if task_id not in self.REVIVABLE_TASKS:
            return False

        if not self.config_path.exists():
            return False

        content = self.config_path.read_text()
        new_value = "ON" if enabled else "OFF"
        new_content = re.sub(
            rf'set\(TASK{task_id}_REVIVE\s+\w+\)',
            f'set(TASK{task_id}_REVIVE {new_value})',
            content,
        )

        if new_content == content:
            return False  # 没有变化

        self.config_path.write_text(new_content)
        return True

    def trigger_resurrection(self, failed_task: int) -> ResurrectionState:
        """触发复活：将 failed_task 及之后所有 Task 的复活标志设为 ON。

        这是 EvoBench 最核心的机制：当 Agent 在 Task N 失败时，
        自动注入标准答案，使 Agent 能继续挑战 Task N+1。

        :param failed_task: 失败的 Task ID
        :return: 复活状态记录
        """
        before = self.read_revive_flags()

        for task_id in range(failed_task, 6):
            if task_id in self.REVIVABLE_TASKS:
                self.set_revive_flag(task_id, True)

        after = self.read_revive_flags()

        state = ResurrectionState(
            task_id=failed_task,
            was_resurrected=True,
            revive_flags_before=before,
            revive_flags_after=after,
        )
        self._history.append(state)
        return state

    def reset_all_revive(self) -> None:
        """重置所有复活标志为 OFF（用于新一轮评测）。"""
        for task_id in self.REVIVABLE_TASKS:
            self.set_revive_flag(task_id, False)

    def get_resurrection_count(self) -> int:
        """获取历史复活次数。"""
        return len(self._history)

    def get_resurrection_history(self) -> list[ResurrectionState]:
        """获取复活历史记录。"""
        return list(self._history)

    def inject_golden_answer(self, task_id: int) -> Path:
        """获取指定 Task 的标准答案路径。

        标准答案由 answer.py 生成，存放在 build/test/task{N}/ 下。

        :param task_id: Task ID
        :return: 标准答案目录路径
        """
        answer_dirs = {
            0: "build/test/task0",
            1: "build/test/task1",
            2: "build/test/task2",
            3: "build/test/task3",
            5: "build/test/task5",
        }
        rel = answer_dirs.get(task_id, f"build/test/task{task_id}")
        return self.yatcc_root / rel

    def is_resurrection_needed(
        self, score: float, max_score: float, threshold: float = 60.0
    ) -> bool:
        """判断是否需要触发复活。

        :param score: 当前得分
        :param max_score: 满分
        :param threshold: 及格线百分比
        :return: 是否需要复活
        """
        if max_score == 0:
            return True
        return (score / max_score * 100) < threshold