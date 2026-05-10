"""Evo-CLI 配置管理。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass
class EvoConfig:
    """EvoBench 全局配置。"""

    # Agent 配置
    backend: str = "openai"
    model: str = ""
    api_base: str = ""
    api_key: str = ""

    # 评测配置
    tasks: list[int] = field(default_factory=lambda: list(range(6)))
    max_turns: int = 20
    pass_threshold: float = 60.0
    resurrect: bool = True

    # 路径配置
    workspace: Path = field(default_factory=lambda: Path.cwd() / "YatCC")
    output_dir: Path = field(default_factory=lambda: Path.cwd() / "evobench_output")
    env_file: Optional[Path] = None

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "EvoConfig":
        """从环境变量加载配置。"""
        if env_file and env_file.exists():
            load_dotenv(env_file)
        else:
            load_dotenv(Path.cwd() / ".env")

        return cls(
            model=os.getenv("OPENAI_MODEL_NAME", "mimo-v2.5-pro"),
            api_base=os.getenv("OPENAI_API_BASE", "https://aihub.arcsysu.cn/v1"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
        )

    def validate(self) -> list[str]:
        """验证配置，返回错误列表。"""
        errors = []
        if not self.workspace.exists():
            errors.append(f"工作区不存在: {self.workspace}")
        if self.backend in ("openai",) and not self.api_key:
            errors.append("openai 后端需要设置 OPENAI_API_KEY")
        if not self.model and self.backend in ("openai", "openhands"):
            errors.append("需要指定 --model")
        return errors


def parse_task_range(spec: str) -> list[int]:
    """解析任务范围字符串。

    支持格式:
    - "0-5" -> [0, 1, 2, 3, 4, 5]
    - "0,1,2" -> [0, 1, 2]
    - "0-3,5" -> [0, 1, 2, 3, 5]
    """
    tasks = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            tasks.extend(range(int(start), int(end) + 1))
        else:
            tasks.append(int(part))
    return sorted(set(tasks))