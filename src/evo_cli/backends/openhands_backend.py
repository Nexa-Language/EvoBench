"""OpenHands SDK 后端 — 真正的 Agent 框架集成。

使用 OpenHands SDK 的 LLM + Agent + Conversation API，
提供完整的 Agent 能力：文件读写、命令执行、Skill、MCP、无限自循环迭代。

OpenHands SDK 底层用 LiteLLM，天然支持任何 OpenAI-compatible API。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .base import AgentBackend, TaskContext, TaskResult


class OpenHandsBackend(AgentBackend):
    """OpenHands SDK 后端 — 真正的 Agent 框架。

    特性：
    - 无限自循环迭代（conversation.run() 直到任务完成）
    - 内置 TerminalTool + FileEditorTool + TaskTrackerTool
    - 支持 Skill、MCP 扩展
    - 通过 LiteLLM 支持任何 LLM provider
    """

    name = "openhands"

    def __init__(
        self,
        model: str = "",
        api_base: str = "",
        api_key: str = "",
        max_iterations: int = 50,
        **kwargs,
    ) -> None:
        super().__init__(model=model)
        self.api_base = api_base
        self.api_key = api_key
        self.max_iterations = max_iterations

    def is_available(self) -> bool:
        try:
            from openhands.sdk import LLM, Agent, Conversation, Tool  # noqa: F401
            from openhands.tools.terminal import TerminalTool  # noqa: F401
            from openhands.tools.file_editor import FileEditorTool  # noqa: F401
            return True
        except ImportError:
            return False

    def get_description(self) -> str:
        return f"OpenHands SDK v1.x ({self.model})"

    def solve_task(
        self,
        task_id: int,
        workspace: Path,
        context: TaskContext,
        max_turns: int = 50,
    ) -> TaskResult:
        """让 OpenHands Agent 解决一个 Task。

        Agent 在 workspace 中自由操作，使用内置工具：
        - TerminalTool: 执行 shell 命令
        - FileEditorTool: 读写文件
        - TaskTrackerTool: 任务追踪

        conversation.run() 会自动循环直到 Agent 认为任务完成。
        """
        result = TaskResult(task_id=task_id)
        t_start = time.time()

        try:
            from openhands.sdk import LLM, Agent, Conversation, Tool
            from openhands.tools.terminal import TerminalTool
            from openhands.tools.file_editor import FileEditorTool
            from openhands.tools.task_tracker import TaskTrackerTool

            # 配置 LLM
            # OpenHands SDK 通过 LiteLLM 支持任何 provider
            # 对于 OpenAI-compatible API，使用 openai/ 前缀
            model_name = self.model
            if self.api_base and not model_name.startswith("openai/"):
                model_name = f"openai/{model_name}"

            llm = LLM(
                model=model_name,
                api_key=self.api_key,
                base_url=self.api_base if self.api_base else None,
                temperature=0.2,
                timeout=300,
            )

            # 创建 Agent（内置工具）
            agent = Agent(
                llm=llm,
                tools=[
                    Tool(name=TerminalTool.name),
                    Tool(name=FileEditorTool.name),
                    Tool(name=TaskTrackerTool.name),
                ],
            )

            # 创建 Conversation（绑定工作区）
            conversation = Conversation(
                agent=agent,
                workspace=str(workspace),
            )

            # 组装任务提示
            prompt = context.to_prompt()
            prompt += f"\n\n请完成 Task {task_id}。你可以使用终端命令来编译和测试代码。"

            # 发送消息并运行（无限自循环）
            conversation.send_message(prompt)
            conversation.run(max_iterations=max_turns)

            result.success = True
            result.turns = max_turns  # OpenHands 不直接暴露轮次，用 max_turns 估算

        except ImportError as e:
            result.error = f"OpenHands SDK 未安装: {e}"
        except Exception as e:
            result.error = f"OpenHands 执行失败: {type(e).__name__}: {e}"

        result.elapsed_seconds = time.time() - t_start
        return result
