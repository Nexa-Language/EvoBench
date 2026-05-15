from __future__ import annotations

import re
from pathlib import Path

from core._score import BUILD_TARGETS, SCORE_TARGETS


class TaskGuides:
    def __init__(self, repo_root: Path) -> None:
        self.full = ""
        self.preamble = ""
        self.sections: dict[int, str] = {}
        path = repo_root / "data" / "task_guides.md"
        if not path.is_file():
            return
        self.full = path.read_text(encoding="utf-8")
        parts = re.split(r"^## Task (\d+):", self.full, flags=re.MULTILINE)
        self.preamble = parts[0].strip() if parts else ""
        for i in range(1, len(parts), 2):
            task_id = int(parts[i])
            self.sections[task_id] = f"## Task {task_id}: {parts[i + 1].lstrip()}".strip()


def _without_blank_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()) + "\n"


def _file_list(task_dir: Path) -> str:
    if not task_dir.is_dir():
        return "- （无）"
    files = [str(path.relative_to(task_dir)).replace("\\", "/") for path in sorted(task_dir.rglob("*")) if path.is_file() and not path.name.startswith(".")]
    return "\n".join(f"- {item}" for item in files) if files else "- （无）"


def build_task_prompt(
    *,
    task_id: int,
    context_mode: str,
    workspace: Path,
    guides: TaskGuides,
    pipeline_stage: str | None = None,
    budget_wall_display: str,
    budget_remaining_iterations: int,
) -> str:
    readme_path = workspace / "task" / str(task_id) / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else ""
    files = _file_list(workspace / "task" / str(task_id))
    build_target = BUILD_TARGETS.get(task_id, f"task{task_id}")
    score_target = SCORE_TARGETS.get(task_id, f"task{task_id}-score")
    path_note = (
        f"说明：文档中的 /YatCC 等价于当前工作区 `{str(workspace).replace(chr(92), '/')}`；"
        f"`task/{task_id}/README.md` 即当前工作区下的对应路径。"
    )
    budget_line = (
        f"你的所剩时间为{budget_wall_display}，所剩轮数为{budget_remaining_iterations}。"
        f"你应当在当前预算下，拿到尽可能高的分数。"
    )
    workflow = f"""
## 资源预算
{budget_line}

## 工作流程
{path_note}
1. 阅读任务指南与 README，理解输入输出和评分标准
2. 阅读 `task/{task_id}/` 下需要修改的源文件
3. 编写或修改代码实现功能
4. 编译: `cmake --build build -t {build_target}`
5. 评测: `cmake --build build -t {score_target}`
6. 查看结果: `cat build/test/task{task_id}/score.txt` 或读取对应 score JSON
7. 若失败，分析错误并修复，重复构建和评测
8. 得分达到要求后结束当前 Task
请开始完成 Task {task_id}。
"""
    if context_mode == "pipeline" and pipeline_stage == "first":
        prompt = f"""
# Pipeline 模式：连续完成多个 Task
## `@data/task_guides.md` 全文
{guides.full}
---
# Task {task_id}: 编译原理实验
## `@data/YatCC/task/{task_id}/README.md` 实验说明
{readme}
## 当前 Task 代码文件
{files}
{workflow}
"""
    elif context_mode == "pipeline" and pipeline_stage == "continue":
        prompt = f"""
# Task {task_id}: 编译原理实验（Pipeline 延续）
全局指南已在对话开头提供；本阶段只重复当前 Task 的局部上下文。
## `@data/YatCC/task/{task_id}/README.md` 实验说明
{readme}
## 当前 Task 代码文件
{files}
{workflow}
"""
    else:
        prompt = f"""
# Task {task_id}: 编译原理实验
## `@data/task_guides.md` 全局说明
{guides.preamble}
## `@data/task_guides.md` Task {task_id} 小节
{guides.sections.get(task_id, "（该 Task 在 task_guides.md 中无独立小节）")}
## `@data/YatCC/task/{task_id}/README.md` 实验说明
{readme}
## 当前 Task 代码文件
{files}
{workflow}
"""
    return _without_blank_lines(prompt)
