#!/usr/bin/env python3
"""Evo-CLI 主入口 — 一条命令指定 Agent 框架 + 模型运行 EvoBench 评测。

用法:
    evo run --backend openai --model mimo-v2.5-pro --tasks 0-5
    evo run --backend claude-code --tasks 0-3
    evo run --backend openai --model deepseek-v4-pro --tasks 0-3
    evo list-backends
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .config import EvoConfig, parse_task_range


@click.group()
@click.version_option(version="0.2.0", prog_name="evo")
def main():
    """EvoBench — 串行 Agent 评测基准 CLI

    一条命令指定 Agent 框架 + 模型，运行完整编译原理实验评测。
    """
    pass


@main.command()
@click.option("--backend", "-b", default="openai",
              type=click.Choice(["openai", "claude-code", "openhands", "codex", "kimi-cli"]),
              help="Agent 后端框架")
@click.option("--model", "-m", default=None, help="模型名称（默认从 .env 读取）")
@click.option("--tasks", "-t", default="0-5", help="任务范围（如 0-5 或 0,1,2,3）")
@click.option("--max-turns", default=20, type=int, help="每个 Task 最大交互轮次")
@click.option("--pass-threshold", default=60.0, type=float, help="及格线百分比")
@click.option("--resurrect/--no-resurrect", default=True, help="是否启用复活机制")
@click.option("--workspace", "-w", default=None, type=click.Path(exists=True), help="YatCC 工作区路径")
@click.option("--output", "-o", default=None, type=click.Path(), help="报告输出目录")
@click.option("--env-file", default=None, type=click.Path(exists=True), help="环境变量文件")
@click.option("--api-base", default=None, help="API Base URL（覆盖 .env）")
@click.option("--api-key", default=None, help="API Key（覆盖 .env）")
def run(backend, model, tasks, max_turns, pass_threshold, resurrect,
        workspace, output, env_file, api_base, api_key):
    """运行 EvoBench 评测。

    示例:

        evo run --backend openai --model mimo-v2.5-pro

        evo run -b claude-code -t 0-3

        evo run -b openai -m deepseek-v4-pro -t 0,1,2,3
    """
    from .config import EvoConfig
    from .orchestrator import BenchmarkOrchestrator

    # 加载配置
    env_path = Path(env_file) if env_file else None
    config = EvoConfig.from_env(env_path)

    # 覆盖命令行参数
    config.backend = backend
    config.tasks = parse_task_range(tasks)
    config.max_turns = max_turns
    config.pass_threshold = pass_threshold
    config.resurrect = resurrect

    if model:
        config.model = model
    if workspace:
        config.workspace = Path(workspace)
    if output:
        config.output_dir = Path(output)
    if api_base:
        config.api_base = api_base
    if api_key:
        config.api_key = api_key

    # 验证
    errors = config.validate()
    if errors:
        for e in errors:
            click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    # 运行
    orchestrator = BenchmarkOrchestrator(config)
    result = orchestrator.run()

    # 退出码
    sys.exit(0 if result.zero_shot_pass else 1)


@main.command("list-backends")
def list_backends_cmd():
    """列出所有可用的 Agent 后端。"""
    from .backends import list_backends

    backends = list_backends()
    click.echo("\n可用的 Agent 后端:\n")
    for name, desc in backends.items():
        click.echo(f"  {name:<15} {desc}")
    click.echo(f"\n用法: evo run --backend <name> --model <model>")
    click.echo()


@main.command()
@click.option("--workspace", "-w", default=None, type=click.Path(exists=True), help="YatCC 工作区路径")
def check(workspace):
    """检查环境依赖是否就绪。"""
    from .evaluator import Evaluator

    ws = Path(workspace) if workspace else Path.cwd() / "YatCC"
    ev = Evaluator(ws)

    checks = [
        ("YatCC 工作区", ws.exists()),
        ("CMakeLists.txt", (ws / "CMakeLists.txt").exists()),
        ("config.cmake", (ws / "config.cmake").exists()),
        ("ANTLR install", (ws / "antlr/install").exists()),
        ("LLVM install", (ws / "llvm/install").exists()),
        ("pybind11 install", (ws / "pybind11/install").exists()),
        ("build 目录", (ws / "build").exists()),
    ]

    # 检查构建系统
    import subprocess
    try:
        r = subprocess.run(["cmake", "--version"], capture_output=True, timeout=5)
        checks.append(("cmake", r.returncode == 0))
    except FileNotFoundError:
        checks.append(("cmake", False))

    try:
        r = subprocess.run(["ninja", "--version"], capture_output=True, timeout=5)
        checks.append(("ninja", r.returncode == 0))
    except FileNotFoundError:
        checks.append(("ninja", False))

    # 检查后端（传入配置参数）
    from .backends import list_backends, get_backend
    from .config import EvoConfig
    cfg = EvoConfig.from_env()
    for name in list_backends():
        try:
            b = get_backend(name, model=cfg.model, api_base=cfg.api_base, api_key=cfg.api_key)
            checks.append((f"后端: {name}", b.is_available()))
        except Exception:
            checks.append((f"后端: {name}", False))

    click.echo("\n环境检查:\n")
    all_ok = True
    for name, ok in checks:
        icon = "✅" if ok else "❌"
        click.echo(f"  {icon} {name}")
        if not ok:
            all_ok = False

    click.echo()
    if all_ok:
        click.echo("✅ 所有依赖就绪，可以运行评测。")
    else:
        click.echo("❌ 部分依赖缺失，请先安装。")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
