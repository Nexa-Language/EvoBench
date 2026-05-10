"""Stage 8: Baseline Agent — 最简评测 Agent。

使用 OpenAI-compatible API 直接调用模型，不依赖任何 Agent 框架。
用于验证 EvoBench 评测流水线的端到端正确性。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from openai import OpenAI


class BaselineAgent:
    """最简 Baseline Agent。

    直接使用 OpenAI API 的 Tool Calling 能力，
    在 EvoBench 环境中完成编译原理实验。
    """

    def __init__(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_turns: int = 20,
        temperature: float = 0.2,
    ) -> None:
        """初始化 Baseline Agent。

        :param api_base: API Base URL
        :param api_key: API Key
        :param model: 模型名称
        :param max_turns: 最大交互轮次
        :param temperature: 生成温度
        """
        load_dotenv(Path(__file__).parent.parent / ".env")

        self.client = OpenAI(
            base_url=api_base or os.getenv("OPENAI_API_BASE"),
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
        )
        self.model = model or os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro")
        self.max_turns = max_turns
        self.temperature = temperature

    def solve(
        self,
        system_prompt: str,
        tools: list[dict[str, Any]],
        tool_executor,
        task_id: int,
    ) -> dict[str, Any]:
        """让 Agent 解决一个 Task。

        :param system_prompt: 系统提示词
        :param tools: 可用工具列表
        :param tool_executor: ToolExecutor 实例
        :param task_id: Task ID
        :return: 执行结果摘要
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"请完成 Task {task_id} 的实现。你可以使用提供的工具来"
                    f"读取文件、修改代码、运行编译和测试。"
                    f"请逐步完成，每次修改代码后验证编译是否通过。"
                    f"当你认为任务完成时，请直接回复而不调用工具。"
                ),
            },
        ]

        total_tokens = 0
        turns = 0

        for turn in range(1, self.max_turns + 1):
            turns = turn
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=self.temperature,
                    timeout=300,
                )
            except Exception as e:
                print(f"  [Agent] API 调用失败 (turn {turn}): {e}")
                break

            if response.usage:
                total_tokens += response.usage.total_tokens

            choice = response.choices[0]
            message = choice.message

            # Agent 完成（无 tool_calls）
            if not message.tool_calls:
                print(f"  [Agent] Task {task_id} 完成于第 {turn} 轮")
                break

            # 添加 assistant 消息
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
            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": tool_call_dicts,
            })

            # 执行 tool calls
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                tool_name = tc.function.name
                print(f"  [Tool] {tool_name}({str(args)[:80]}...)")

                result = tool_executor.execute(tool_name, args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return {
            "task_id": task_id,
            "turns": turns,
            "total_tokens": total_tokens,
            "completed": turns < self.max_turns,
        }


def run_baseline_agent(
    yatcc_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> None:
    """运行 Baseline Agent 的端到端测试。

    这是 EvoBench 的最简端到端验证：
    使用本地沙盒 + Baseline Agent 跑完整流水线。

    :param yatcc_root: YatCC 源码根目录
    :param output_dir: 输出目录
    """
    from .controller import EvoBenchController

    controller = EvoBenchController(
        yatcc_root=yatcc_root,
        output_dir=output_dir,
        use_docker=False,  # 本地运行
    )

    metrics = controller.run()

    # 保存指标
    metrics.to_json(controller.output_dir / "evobench_metrics.json")
    print(f"\n指标已保存: {controller.output_dir / 'evobench_metrics.json'}")

    return metrics


if __name__ == "__main__":
    run_baseline_agent()