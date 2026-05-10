"""Kimi CLI 后端 — 封装 Kimi 命令行工具。"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .base import AgentBackend, TaskContext, TaskResult


class KimiCLIBackend(AgentBackend):
    """Kimi CLI 后端。"""

    name = "kimi-cli"

    def __init__(self, model: str = "", kimi_path: str = "kimi", **kwargs) -> None:
        super().__init__(model=model)
        self.kimi_path = kimi_path

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                [self.kimi_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def get_description(self) -> str:
        return f"Kimi CLI ({self.kimi_path})"

    def solve_task(
        self,
        task_id: int,
        workspace: Path,
        context: TaskContext,
        max_turns: int = 20,
    ) -> TaskResult:
        result = TaskResult(task_id=task_id)
        t_start = time.time()

        prompt = context.to_prompt()
        prompt += f"\n\n请完成 Task {task_id}。"

        cmd = [self.kimi_path, "chat", "--yes", prompt]
        if self.model:
            cmd.extend(["--model", self.model])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=600,
                cwd=str(workspace),
            )
            result.final_message = proc.stdout[:2000] if proc.stdout else ""
            result.success = proc.returncode == 0
            if proc.returncode != 0:
                result.error = proc.stderr[-500:] if proc.stderr else f"exit code {proc.returncode}"
        except subprocess.TimeoutExpired:
            result.error = "Kimi CLI 执行超时 (600s)"
        except FileNotFoundError:
            result.error = f"找不到 kimi 命令: {self.kimi_path}"

        result.elapsed_seconds = time.time() - t_start
        return result
