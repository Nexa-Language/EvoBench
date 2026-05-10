"""上下文管理器 — 为每个 Task 组装最优的上下文。"""

from __future__ import annotations

from pathlib import Path

from ..backends.base import TaskContext
from ..evaluator import ScoreResult


class ContextManager:
    """动态上下文管理器。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self._task_history: dict[int, ScoreResult] = {}
        self._last_build_errors: str = ""

    def set_task_result(self, task_id: int, score: ScoreResult) -> None:
        """记录 Task 完成结果。"""
        self._task_history[task_id] = score

    def set_build_errors(self, errors: str) -> None:
        """记录最近一次构建错误。"""
        self._last_build_errors = errors

    def build_context(self, task_id: int) -> TaskContext:
        """为指定 Task 组装上下文。"""
        # 读取 README
        readme_path = self.workspace / "task" / str(task_id) / "README.md"
        readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

        # 列出当前 Task 代码文件
        task_dir = self.workspace / "task" / str(task_id)
        code_files = []
        if task_dir.exists():
            for f in sorted(task_dir.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    code_files.append(str(f.relative_to(task_dir)))

        # 前置任务结果
        previous = []
        for tid in sorted(self._task_history.keys()):
            sr = self._task_history[tid]
            previous.append({
                "task_id": tid,
                "score": sr.score,
                "passed": sr.passed,
            })

        # 构建/评分命令
        build_targets = {0: "task0", 1: "task1", 2: "task2", 3: "task3", 4: "task4", 5: "task5-classic"}
        score_targets = {0: "task0-score", 1: "task1-score", 2: "task2-score", 3: "task3-score", 4: "task4-score", 5: "task5-classic-score"}

        build_cmd = f"cmake --build build -t {build_targets.get(task_id, f'task{task_id}')}"
        score_cmd = f"cmake --build build -t {score_targets.get(task_id, f'task{task_id}-score')}"

        # 工作流程指引
        instructions = f"""1. 用 read_task_readme({task_id}) 阅读实验要求
2. 用 read_file 阅读需要修改的源文件
3. 编写/修改代码
4. 编译: {build_cmd}
5. 评测: {score_cmd}
6. 如果失败，分析错误并修复
7. 完成后直接回复 "TASK {task_id} COMPLETE" """

        return TaskContext(
            task_id=task_id,
            readme=readme,
            code_files=code_files,
            build_command=build_cmd,
            score_command=score_cmd,
            previous_results=previous,
            build_errors=self._last_build_errors,
            instructions=instructions,
        )
