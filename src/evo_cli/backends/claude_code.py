"""Claude Code CLI 后端 — 通过 claude 命令行工具驱动 Agent。

Claude Code 自带文件读写和命令执行能力，无需额外定义 tools。
通过 --prompt 注入任务上下文，通过 --cwd 指定工作区。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .base import AgentBackend, TaskContext, TaskResult


class ClaudeCodeBackend(AgentBackend):
    """Claude Code CLI 后端。"""

    name = "claude-code"

    def __init__(self, model: str = "", claude_path: str = "claude", **kwargs) -> None:
        super().__init__(model=model)
        self.claude_path = claude_path

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                [self.claude_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def get_description(self) -> str:
        return f"Claude Code CLI ({self.claude_path})"

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
        prompt += f"\n\n请完成 Task {task_id}。你可以读取和修改文件、执行命令。完成后回复 DONE。"

        cmd = [
            self.claude_path,
            "--print",
            "--output-format", "json",
            "--max-turns", str(max_turns),
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=600,
                cwd=str(workspace),
            )

            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    data = json.loads(proc.stdout)
                    result.final_message = data.get("result", proc.stdout[:1000])
                    result.turns = data.get("num_turns", 0)
                    result.total_tokens = data.get("usage", {}).get("total_tokens", 0)
                    result.success = True
                except json.JSONDecodeError:
                    result.final_message = proc.stdout[:2000]
                    result.success = True
            else:
                result.error = proc.stderr[-1000:] if proc.stderr else f"exit code {proc.returncode}"
                result.final_message = proc.stdout[:1000] if proc.stdout else ""

        except subprocess.TimeoutExpired:
            result.error = "Claude Code 执行超时 (600s)"
        except FileNotFoundError:
            result.error = f"找不到 claude 命令: {self.claude_path}"

        result.elapsed_seconds = time.time() - t_start
        return result
