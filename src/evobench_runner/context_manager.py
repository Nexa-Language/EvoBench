"""Stage 6: 上下文与 Prompt 动态组装 — Context Manager。

控制被测 Agent 在每个阶段能"看"到什么：
- 动态读取 task/X/README.md
- 当前代码树状态
- 上一次编译失败的报错信息
- Token 预算控制
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class ContextManager:
    """动态上下文管理器。

    为每个 Task 组装最优的 System Prompt，
    在信息量和 Token 消耗之间取得平衡。
    """

    # Token 预算（估算：1 token ≈ 4 字符 for 中文，≈ 3 字符 for 英文/代码）
    MAX_SYSTEM_PROMPT_CHARS = 32000   # ≈ 8000 tokens
    MAX_ERROR_LOG_CHARS = 8000        # ≈ 2000 tokens
    MAX_CODE_TREE_CHARS = 4000        # ≈ 1000 tokens

    def __init__(self, yatcc_root: Path) -> None:
        """初始化上下文管理器。

        :param yatcc_root: YatCC 源码根目录
        """
        self.yatcc_root = yatcc_root
        self._last_build_stderr: str = ""
        self._last_build_stdout: str = ""
        self._task_history: dict[int, str] = {}  # task_id -> 简要结果

    def set_build_errors(self, stdout: str, stderr: str) -> None:
        """记录最近一次构建的错误信息。

        :param stdout: 构建 stdout
        :param stderr: 构建 stderr
        """
        self._last_build_stdout = stdout
        self._last_build_stderr = stderr

    def set_task_result(self, task_id: int, summary: str) -> None:
        """记录 Task 完成结果。

        :param task_id: Task ID
        :param summary: 结果摘要
        """
        self._task_history[task_id] = summary

    def build_system_prompt(
        self,
        task_id: int,
        include_history: bool = True,
        include_errors: bool = True,
    ) -> str:
        """为当前 Task 组装 System Prompt。

        :param task_id: 当前 Task ID (0-5)
        :param include_history: 是否包含前置 Task 历史
        :param include_errors: 是否包含上次构建错误
        :return: 组装好的 System Prompt
        """
        parts: list[str] = []

        # 1. 角色设定
        parts.append(self._persona_prompt())

        # 2. 项目概述
        parts.append(self._project_overview())

        # 3. 前置 Task 历史
        if include_history and self._task_history:
            parts.append(self._history_summary(task_id))

        # 4. 当前 Task 说明
        parts.append(self._task_readme(task_id))

        # 5. 当前代码树状态
        parts.append(self._code_tree_summary(task_id))

        # 6. 构建错误信息
        if include_errors and (self._last_build_stderr or self._last_build_stdout):
            parts.append(self._error_summary())

        # 7. 可用工具说明
        parts.append(self._tools_guide())

        # 8. 工作流程建议
        parts.append(self._workflow_guide(task_id))

        # 拼接并截断
        full = "\n\n".join(parts)
        return self._truncate(full, self.MAX_SYSTEM_PROMPT_CHARS)

    def _persona_prompt(self) -> str:
        """角色设定。"""
        return """## 角色设定

你是一位精通编译原理的 AI 软件工程师。你正在参与 YatCC 编译原理课程实验，
需要从零开始逐步实现一个完整的编译器。

你的目标是在不依赖外部帮助的情况下，独立完成从词法分析到后端代码生成的
全部 6 个实验任务（Task 0-5）。

你需要：
1. 仔细阅读每个 Task 的实验说明
2. 理解基础代码的结构
3. 编写/修改代码实现缺失的功能
4. 通过编译和测试验证你的实现
5. 遇到错误时，分析错误信息并修复代码"""

    def _project_overview(self) -> str:
        """项目概述。"""
        return """## 项目概述

YatCC 是一个教学用编译器项目，使用 C++ 和 CMake 构建系统。
项目结构：

- `task/0/` - Task 0: 环境准备
- `task/1/` - Task 1: 词法分析 (Flex/ANTLR)
- `task/2/` - Task 2: 语法分析 (Bison/ANTLR)
- `task/3/` - Task 3: 中间代码生成 (LLVM IR)
- `task/4/` - Task 4: 中间代码优化 (LLVM Pass)
- `task/5/` - Task 5: 后端代码生成 (RV64 Asm)
- `test/` - 评测脚本和测例
- `config.cmake` - 项目配置文件

构建命令：
- 配置: `cmake -S . -B build -GNinja`
- 构建: `cmake --build build -t task{N}`
- 评测: `cmake --build build -t task{N}-score`"""

    def _history_summary(self, current_task_id: int) -> str:
        """前置 Task 历史摘要。"""
        lines = ["## 前置任务完成情况"]
        for tid in range(current_task_id):
            if tid in self._task_history:
                lines.append(f"- Task {tid}: {self._task_history[tid]}")
            else:
                lines.append(f"- Task {tid}: 未完成或未记录")
        return "\n".join(lines)

    def _task_readme(self, task_id: int) -> str:
        """读取当前 Task 的 README。"""
        readme_path = self.yatcc_root / "task" / str(task_id) / "README.md"
        if readme_path.exists():
            content = readme_path.read_text(encoding="utf-8")
            return f"## 当前任务: Task {task_id}\n\n{content}"
        return f"## 当前任务: Task {task_id}\n\n(README.md 未找到)"

    def _code_tree_summary(self, task_id: int) -> str:
        """当前 Task 的代码树摘要。"""
        task_dir = self.yatcc_root / "task" / str(task_id)
        if not task_dir.exists():
            return "## 当前代码文件\n\n(目录不存在)"

        lines = ["## 当前 Task 代码文件"]
        for f in sorted(task_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                rel = f.relative_to(task_dir)
                size = f.stat().st_size
                lines.append(f"- `{rel}` ({size} bytes)")

        result = "\n".join(lines)
        return self._truncate(result, self.MAX_CODE_TREE_CHARS)

    def _error_summary(self) -> str:
        """构建错误摘要。"""
        parts = ["## 上次构建/测试输出"]

        if self._last_build_stderr:
            stderr_truncated = self._last_build_stderr[-self.MAX_ERROR_LOG_CHARS:]
            parts.append(f"### STDERR\n```\n{stderr_truncated}\n```")

        if self._last_build_stdout:
            stdout_truncated = self._last_build_stdout[-self.MAX_ERROR_LOG_CHARS // 2:]
            parts.append(f"### STDOUT\n```\n{stdout_truncated}\n```")

        return "\n".join(parts)

    def _tools_guide(self) -> str:
        """可用工具说明。"""
        return """## 可用工具

你可以使用以下工具来完成实验：

| 工具 | 用途 |
|------|------|
| `read_file` | 读取文件内容（支持分页） |
| `write_file` | 写入/覆盖文件 |
| `patch_file` | 精确的行级修改 |
| `run_command` | 执行 shell 命令（编译、测试等） |
| `list_files` | 列出目录文件 |
| `search_code` | 搜索代码模式 |
| `read_task_readme` | 读取实验说明 |
| `run_ctest` | 运行 CTest 评测 |
| `get_build_errors` | 获取构建错误日志 |

**重要提示**：
- 修改代码后，务必运行编译命令验证
- 编译通过后再运行评测
- 遇到错误时，仔细阅读错误信息定位问题"""

    def _workflow_guide(self, task_id: int) -> str:
        """工作流程建议。"""
        return f"""## 建议工作流程

1. 首先使用 `read_task_readme({task_id})` 仔细阅读实验要求
2. 使用 `list_files` 了解当前 Task 的代码结构
3. 使用 `read_file` 阅读需要修改的源文件
4. 编写/修改代码实现功能
5. 运行编译命令验证: `cmake --build build -t task{task_id}`
6. 编译通过后运行评测: `cmake --build build -t task{task_id}-score`
7. 如果评测未通过，分析错误并修复

请开始完成 Task {task_id}。"""

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        """截断文本到指定字符数。"""
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 100] + "\n\n... (内容已截断以控制上下文长度)"