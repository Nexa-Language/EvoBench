"""OpenAI API 后端 — 使用 OpenAI Tool Calling 让 Agent 编码。

支持任何 OpenAI-compatible API（包括 aihub.arcsysu.cn）。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from .base import AgentBackend, TaskContext, TaskResult


# ─── Tool 定义 ──────────────────────────────────────────────────────────

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容（相对 YatCC 根目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "offset": {"type": "integer", "description": "起始行号"},
                    "limit": {"type": "integer", "description": "最大行数"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件（相对 YatCC 根目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行 shell 命令（cwd=YatCC 根目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "shell 命令"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_task_readme",
            "description": "读取 Task 的 README.md",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录下的文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"},
                },
                "required": ["path"],
            },
        },
    },
]


def exec_tool(name: str, args: dict, workspace: Path) -> str:
    """执行 Tool 调用。"""
    if name == "read_file":
        p = workspace / args["path"]
        if not p.exists():
            return f"文件不存在: {args['path']}"
        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        offset = args.get("offset", 1)
        limit = args.get("limit", 200)
        subset = lines[offset - 1 : offset - 1 + limit]
        return f"[{args['path']} 行 {offset}-{min(offset+limit-1, len(lines))}]\n" + "\n".join(subset)

    elif name == "write_file":
        p = workspace / args["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        line_count = len(args["content"].split("\n"))
        return f"写入成功: {args['path']} ({line_count} 行)"

    elif name == "run_command":
        try:
            result = subprocess.run(
                ["bash", "-c", args["command"]],
                capture_output=True, text=True, timeout=120, cwd=str(workspace),
            )
            out = result.stdout[-3000:] if result.stdout else ""
            err = result.stderr[-3000:] if result.stderr else ""
            return f"[rc={result.returncode}]\n{out}\n{err}"
        except subprocess.TimeoutExpired:
            return "命令超时 (120s)"

    elif name == "read_task_readme":
        p = workspace / "task" / str(args["task_id"]) / "README.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
        return "README 不存在"

    elif name == "list_files":
        p = workspace / args["path"]
        if not p.exists():
            return f"目录不存在: {args['path']}"
        entries = sorted(p.iterdir())
        return "\n".join(
            f"{'d' if e.is_dir() else 'f'} {e.name}" for e in entries[:100]
        )

    return f"未知工具: {name}"


class OpenAIBackend(AgentBackend):
    """OpenAI API 后端。"""

    name = "openai"

    def __init__(
        self,
        model: str = "",
        api_base: str = "",
        api_key: str = "",
        temperature: float = 0.2,
        **kwargs,
    ) -> None:
        super().__init__(model=model)
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=self.api_base, api_key=self.api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key and self.model)

    def get_description(self) -> str:
        return f"OpenAI API ({self.model} @ {self.api_base})"

    def solve_task(
        self,
        task_id: int,
        workspace: Path,
        context: TaskContext,
        max_turns: int = 20,
    ) -> TaskResult:
        result = TaskResult(task_id=task_id)
        t_start = time.time()

        system_prompt = context.to_prompt()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请完成 Task {task_id}。使用工具来读取文件、修改代码、编译和测试。"},
        ]

        for turn in range(1, max_turns + 1):
            result.turns = turn
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=OPENAI_TOOLS,
                    temperature=self.temperature,
                    timeout=300,
                )
            except Exception as e:
                result.error = f"API 调用失败 turn {turn}: {e}"
                break

            if resp.usage:
                result.total_tokens += resp.usage.total_tokens

            msg = resp.choices[0].message

            # Agent 完成
            if not msg.tool_calls:
                result.final_message = msg.content or ""
                result.success = True
                break

            # 处理 tool calls
            tool_calls = [
                {
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
            messages.append({"role": "assistant", "content": msg.content, "tool_calls": tool_calls})

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_result = exec_tool(tc.function.name, args, workspace)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
                result.trajectory.append({
                    "turn": turn, "tool": tc.function.name,
                    "args": str(args)[:200], "result_len": len(tool_result),
                })

        result.elapsed_seconds = time.time() - t_start
        return result
