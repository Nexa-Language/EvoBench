"""Agent 后端抽象基类与数据结构。

所有 Agent 框架的统一接口。核心理念：
Agent 在 YatCC 工作区中自由操作，EvoBench 只负责准备上下文和收集结果。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class TaskContext:
    """交给 Agent 的任务上下文。"""

    task_id: int
    readme: str = ""
    code_files: list[str] = field(default_factory=list)
    build_command: str = ""
    score_command: str = ""
    previous_results: list[dict] = field(default_factory=list)
    build_errors: str = ""
    instructions: str = ""

    def to_prompt(self) -> str:
        """组装为自然语言提示词。"""
        parts = [f"# Task {self.task_id}"]

        if self.readme:
            parts.append(f"\n## 实验说明\n{self.readme}")

        if self.code_files:
            parts.append("\n## 代码文件")
            for f in self.code_files:
                parts.append(f"- {f}")

        if self.previous_results:
            parts.append("\n## 前置任务结果")
            for r in self.previous_results:
                status = "✅" if r.get("passed") else "❌"
                parts.append(f"- Task {r['task_id']}: {status} {r.get('score', 0):.1f}/100")

        if self.build_errors:
            parts.append(f"\n## 上次构建错误\n```\n{self.build_errors[-2000:]}\n```")

        if self.instructions:
            parts.append(f"\n## 工作流程\n{self.instructions}")

        return "\n".join(parts)


@dataclass
class TaskResult:
    """Agent 解题结果。"""

    task_id: int
    success: bool = False
    turns: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    trajectory: list[dict] = field(default_factory=list)
    final_message: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "turns": self.turns,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "final_message": self.final_message[:500],
            "error": self.error,
        }


class AgentBackend(ABC):
    """所有 Agent 后端的统一接口。

    子类只需实现 solve_task() 方法。
    Agent 在工作区中自由读写文件、执行命令。
    """

    name: str = "base"

    def __init__(self, model: str = "", **kwargs) -> None:
        self.model = model

    @abstractmethod
    def solve_task(
        self,
        task_id: int,
        workspace: Path,
        context: TaskContext,
        max_turns: int = 20,
    ) -> TaskResult:
        """让 Agent 解决一个 Task。

        :param task_id: Task ID (0-5)
        :param workspace: YatCC 工作区路径
        :param context: 任务上下文
        :param max_turns: 最大交互轮次
        :return: TaskResult
        """
        ...

    def is_available(self) -> bool:
        """检查后端是否可用（依赖是否安装）。"""
        return True

    def get_description(self) -> str:
        """返回后端描述。"""
        return f"{self.name} backend"
