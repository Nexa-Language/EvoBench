"""Agent 后端注册表。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import AgentBackend

# 后端名称 -> 模块路径的延迟注册
_BACKEND_REGISTRY: dict[str, str] = {
    "openai": "evo_cli.backends.openai_backend:OpenAIBackend",
    "claude-code": "evo_cli.backends.claude_code:ClaudeCodeBackend",
    "openhands": "evo_cli.backends.openhands_backend:OpenHandsBackend",
    "codex": "evo_cli.backends.codex_backend:CodexBackend",
    "kimi-cli": "evo_cli.backends.kimi_backend:KimiCLIBackend",
}


def get_backend(name: str, **kwargs) -> "AgentBackend":
    """根据名称获取 Agent 后端实例。

    :param name: 后端名称 (openai|claude-code|openhands|codex|kimi-cli)
    :param kwargs: 传递给后端构造函数的参数
    :return: AgentBackend 实例
    """
    if name not in _BACKEND_REGISTRY:
        available = ", ".join(_BACKEND_REGISTRY.keys())
        raise ValueError(f"未知后端: {name}。可用: {available}")

    module_path, class_name = _BACKEND_REGISTRY[name].rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(**kwargs)


def list_backends() -> dict[str, str]:
    """列出所有可用后端。"""
    return dict(_BACKEND_REGISTRY)