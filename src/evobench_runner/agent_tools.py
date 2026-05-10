"""Stage 5: 被测 Agent 交互接口 — Action Space API。

为被测 Agent 提供标准的 Tool 接口集合，包括：
- 文件读写（支持大规模文本的安全修改）
- 终端命令执行（返回 stdout 和 stderr 给 Agent debug）
- 代码搜索
"""

from __future__ import annotations

from typing import Any

# ─── OpenAI Function Calling 格式的 Tool 定义 ─────────────────────────────

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取指定路径的文件内容。支持分页读取大文件。"
                "返回文件内容（带行号）或错误信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于 YatCC 根目录）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based），默认 1",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大读取行数，默认 200",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "将内容写入指定路径的文件。会覆盖已有文件。"
                "用于创建新文件或完全重写已有文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于 YatCC 根目录）",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "对文件进行精确的行级修改。"
                "提供要替换的原始内容和替换后的新内容。"
                "适用于局部修改，避免重写整个文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于 YatCC 根目录）",
                    },
                    "old_content": {
                        "type": "string",
                        "description": "要替换的原始内容（必须精确匹配）",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "替换后的新内容",
                    },
                },
                "required": ["path", "old_content", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "在 YatCC 工作目录中执行 shell 命令。"
                "返回 (returncode, stdout, stderr)。"
                "用于编译、运行测试、查看错误信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 120",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "列出指定目录下的文件和子目录。"
                "用于了解项目结构和当前 Task 的文件组织。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（相对于 YatCC 根目录），默认 '.'",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出，默认 false",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "在代码库中搜索正则表达式匹配的内容。"
                "用于查找函数定义、变量引用、特定模式等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式搜索模式",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索目录（相对于 YatCC 根目录），默认 '.'",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "文件名过滤 glob，如 '*.cpp'，默认 '*'",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_task_readme",
            "description": (
                "读取当前 Task 的实验说明文档 (README.md)。"
                "包含任务要求、输入输出格式、基础代码说明和提示。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "Task ID (0-5)",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_ctest",
            "description": (
                "运行 CTest 评测当前 Task。"
                "返回测试结果摘要，包括通过/失败的测例列表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "Task ID (0-5)",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_build_errors",
            "description": (
                "获取最近一次构建或测试的错误日志。"
                "用于 Agent 自我 Debug。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ─── Tool 执行器 ──────────────────────────────────────────────────────────

class ToolExecutor:
    """在沙盒中执行 Agent 调用的 Tool。

    每个 Tool 调用都会被转发到沙盒中执行，
    确保 Agent 的操作不会污染宿主机环境。
    """

    def __init__(self, sandbox, container_id: str, yatcc_root) -> None:
        """初始化 Tool 执行器。

        :param sandbox: Sandbox 实例
        :param container_id: 容器 ID
        :param yatcc_root: YatCC 根目录 Path
        """
        self.sandbox = sandbox
        self.container_id = container_id
        self.yatcc_root = yatcc_root

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """执行 Tool 调用并返回结果字符串。

        :param tool_name: Tool 名称
        :param arguments: Tool 参数
        :return: 执行结果
        """
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return f"未知工具: {tool_name}"

        try:
            return handler(**arguments)
        except Exception as e:
            return f"工具执行错误 ({tool_name}): {e}"

    def _tool_read_file(
        self, path: str, offset: int = 1, limit: int = 200
    ) -> str:
        """读取文件内容。"""
        full_path = f"/workspace/YatCC/{path}"
        cmd = (
            f"if [ -f '{full_path}' ]; then "
            f"wc -l < '{full_path}'; "
            f"tail -n +{offset} '{full_path}' | head -n {limit}; "
            f"else echo 'FILE_NOT_FOUND'; fi"
        )
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=30)
        if "FILE_NOT_FOUND" in stdout:
            return f"文件不存在: {path}"
        lines = stdout.strip().split("\n")
        if len(lines) > 1:
            total = lines[0]
            content = "\n".join(lines[1:])
            return f"[{path} (行 {offset}-{min(offset+limit-1, int(total))} / 共 {total} 行)]\n{content}"
        return f"[{path} (空文件)]"

    def _tool_write_file(self, path: str, content: str) -> str:
        """写入文件内容。"""
        full_path = f"/workspace/YatCC/{path}"
        # 使用 base64 避免特殊字符问题
        import base64
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        cmd = (
            f"mkdir -p '$(dirname \"{full_path}\")' && "
            f"echo '{encoded}' | base64 -d > '{full_path}' && "
            f"echo 'WRITE_OK' && wc -l < '{full_path}'"
        )
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=30)
        if "WRITE_OK" in stdout:
            lines = stdout.strip().split("\n")
            line_count = lines[-1] if len(lines) > 1 else "?"
            return f"写入成功: {path} ({line_count} 行)"
        return f"写入失败: {stderr}"

    def _tool_patch_file(
        self, path: str, old_content: str, new_content: str
    ) -> str:
        """行级修改文件。"""
        full_path = f"/workspace/YatCC/{path}"
        # 使用 Python 进行精确替换
        import base64
        old_enc = base64.b64encode(old_content.encode("utf-8")).decode("ascii")
        new_enc = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        script = (
            f"import base64; "
            f"old = base64.b64decode('{old_enc}').decode('utf-8'); "
            f"new = base64.b64decode('{new_enc}').decode('utf-8'); "
            f"content = open('{full_path}', 'r').read(); "
            f"if old not in content: print('PATCH_NOT_FOUND'); exit(1); "
            f"content = content.replace(old, new, 1); "
            f"open('{full_path}', 'w').write(content); "
            f"print('PATCH_OK')"
        )
        cmd = f"python3 -c \"{script}\""
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=30)
        if "PATCH_OK" in stdout:
            return f"修改成功: {path}"
        elif "PATCH_NOT_FOUND" in stdout:
            return f"修改失败: 在 {path} 中未找到要替换的内容"
        return f"修改失败: {stderr}"

    def _tool_run_command(
        self, command: str, timeout: int = 120
    ) -> str:
        """执行 shell 命令。"""
        rc, stdout, stderr = self.sandbox.exec(
            self.container_id, command, timeout=timeout,
        )
        result = f"[returncode: {rc}]\n"
        if stdout:
            result += f"--- STDOUT ---\n{stdout[-5000:]}\n"
        if stderr:
            result += f"--- STDERR ---\n{stderr[-5000:]}\n"
        return result

    def _tool_list_files(
        self, path: str = ".", recursive: bool = False
    ) -> str:
        """列出目录文件。"""
        full_path = f"/workspace/YatCC/{path}"
        if recursive:
            cmd = f"find '{full_path}' -type f | head -200"
        else:
            cmd = f"ls -la '{full_path}' 2>/dev/null || echo 'DIR_NOT_FOUND'"
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=30)
        if "DIR_NOT_FOUND" in stdout:
            return f"目录不存在: {path}"
        return f"[{path}]\n{stdout.strip()}"

    def _tool_search_code(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str = "*",
    ) -> str:
        """搜索代码。"""
        full_path = f"/workspace/YatCC/{path}"
        # 使用 grep -rn，限制输出
        cmd = (
            f"grep -rn --include='{file_pattern}' '{pattern}' '{full_path}' "
            f"2>/dev/null | head -50 || echo 'NO_MATCH'"
        )
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=30)
        if not stdout.strip() or "NO_MATCH" in stdout:
            return f"未找到匹配: {pattern}"
        return f"[搜索: {pattern} in {path}]\n{stdout.strip()}"

    def _tool_read_task_readme(self, task_id: int) -> str:
        """读取 Task README。"""
        return self._tool_read_file(f"task/{task_id}/README.md")

    def _tool_run_ctest(self, task_id: int) -> str:
        """运行 CTest 评测。"""
        score_targets = {
            0: "task0-score", 1: "task1-score", 2: "task2-score",
            3: "task3-score", 4: "task4-score", 5: "task5-classic-score",
        }
        target = score_targets.get(task_id, f"task{task_id}-score")
        cmd = f"cd /workspace/YatCC && cmake --build build -t {target} 2>&1"
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=300)

        # 尝试读取 score.txt
        score_paths = {
            0: "build/test/task0/score.txt",
            1: "build/test/task1/score.txt",
            2: "build/test/task2/score.txt",
            3: "build/test/task3/score.txt",
            4: "build/test/task4/score.txt",
            5: "build/test/task5/score.txt",
        }
        score_rel = score_paths.get(task_id)
        score_output = ""
        if score_rel:
            rc2, score_output, _ = self.sandbox.exec(
                self.container_id,
                f"cat /workspace/YatCC/{score_rel} 2>/dev/null || echo ''",
                timeout=10,
            )

        return (
            f"[Task {task_id} 评测结果]\n"
            f"--- 构建输出 ---\n{stdout[-3000:]}{stderr[-2000:]}\n"
            f"--- 评分 ---\n{score_output}"
        )

    def _tool_get_build_errors(self) -> str:
        """获取构建错误日志。"""
        cmd = (
            "cd /workspace/YatCC && "
            "find build/Testing -name '*.log' -exec tail -100 {} \\; 2>/dev/null | head -200"
        )
        rc, stdout, stderr = self.sandbox.exec(self.container_id, cmd, timeout=30)
        if not stdout.strip():
            return "没有找到构建错误日志"
        return f"[构建错误日志]\n{stdout.strip()}"