"""Stage 5: 被测 Agent 交互接口 — Agent 调用接口。

封装与 LLM API 的交互，支持 Tool Calling 循环。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI


@dataclass
class AgentTurn:
    """单轮 Agent 交互记录。"""
    turn_number: int
    role: str  # "assistant" | "tool"
    content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_results: Optional[list[dict]] = None
    elapsed_seconds: float = 0.0


@dataclass
class AgentSession:
    """一次完整的 Agent 会话记录。"""
    task_id: int
    turns: list[AgentTurn] = field(default_factory=list)
    total_tokens: int = 0
    total_elapsed: float = 0.0
    final_message: str = ""


class AgentInterface:
    """被测 Agent 的标准调用接口。

    封装 OpenAI-compatible API 调用，支持：
    - Tool Calling 循环（Agent 调用工具 -> 返回结果 -> Agent 继续）
    - 最大轮次限制
    - 会话记录
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "mimo-v2.5-pro",
        max_turns: int = 20,
        temperature: float = 0.2,
    ) -> None:
        """初始化 Agent 接口。

        :param api_base: API Base URL
        :param api_key: API Key
        :param model: 模型名称
        :param max_turns: 最大交互轮次
        :param temperature: 生成温度
        """
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.max_turns = max_turns
        self.temperature = temperature

    def solve_task(
        self,
        task_id: int,
        system_prompt: str,
        tools: list[dict[str, Any]],
        tool_executor,
        initial_message: Optional[str] = None,
    ) -> AgentSession:
        """让 Agent 解决一个 Task。

        Agent 在 Tool Calling 循环中：
        1. 收到 system prompt + 当前状态
        2. 决定调用哪些工具
        3. 工具执行后返回结果
        4. 重复直到 Agent 认为完成或达到最大轮次

        :param task_id: Task ID
        :param system_prompt: 系统提示词
        :param tools: 可用工具列表
        :param tool_executor: ToolExecutor 实例
        :param initial_message: 初始用户消息
        :return: AgentSession
        """
        session = AgentSession(task_id=task_id)
        t_start = time.time()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        if initial_message:
            messages.append({"role": "user", "content": initial_message})
        else:
            messages.append({
                "role": "user",
                "content": (
                    f"请完成 Task {task_id} 的实现。"
                    f"你可以使用提供的工具来读取文件、修改代码、"
                    f"运行编译和测试。请逐步完成，每次修改后验证编译是否通过。"
                ),
            })

        for turn_num in range(1, self.max_turns + 1):
            turn_start = time.time()

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=self.temperature,
                    timeout=300,
                )
            except Exception as e:
                turn = AgentTurn(
                    turn_number=turn_num,
                    role="assistant",
                    content=f"API 调用失败: {e}",
                    elapsed_seconds=time.time() - turn_start,
                )
                session.turns.append(turn)
                break

            choice = response.choices[0]
            message = choice.message

            # 记录 token 使用
            if response.usage:
                session.total_tokens += response.usage.total_tokens

            # 如果 Agent 完成了（没有 tool_calls）
            if not message.tool_calls:
                turn = AgentTurn(
                    turn_number=turn_num,
                    role="assistant",
                    content=message.content,
                    elapsed_seconds=time.time() - turn_start,
                )
                session.turns.append(turn)
                session.final_message = message.content or ""
                break

            # 处理 tool calls
            tool_call_dicts = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

            # 记录 assistant turn
            assistant_turn = AgentTurn(
                turn_number=turn_num,
                role="assistant",
                content=message.content,
                tool_calls=tool_call_dicts,
                elapsed_seconds=time.time() - turn_start,
            )
            session.turns.append(assistant_turn)

            # 添加 assistant 消息到对话
            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": tool_call_dicts,
            })

            # 执行每个 tool call
            tool_results = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                result = tool_executor.execute(tc.function.name, args)
                tool_results.append({
                    "tool_call_id": tc.id,
                    "role": "tool",
                    "content": result,
                })

                # 添加 tool 结果到对话
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # 记录 tool turn
            tool_turn = AgentTurn(
                turn_number=turn_num,
                role="tool",
                tool_results=tool_results,
                elapsed_seconds=time.time() - turn_start,
            )
            session.turns.append(tool_turn)

        session.total_elapsed = time.time() - t_start
        return session

    def quick_chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: Optional[float] = None,
    ) -> str:
        """快速单轮对话（不使用工具）。

        :param system_prompt: 系统提示词
        :param user_message: 用户消息
        :param temperature: 温度（默认使用实例配置）
        :return: 模型回复文本
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature or self.temperature,
                timeout=120,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"API 调用失败: {e}"