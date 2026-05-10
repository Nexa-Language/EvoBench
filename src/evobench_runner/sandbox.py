"""Stage 2: 评测沙盒构建 — Docker 容器调度逻辑。

提供纯净、无状态的评测物理环境。每次评测新 Agent 时启动全新隔离容器。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional


class SandboxError(Exception):
    """沙盒操作异常。"""


class Sandbox:
    """Docker 容器沙盒管理器。

    每次评测启动一个全新容器，挂载 YatCC 工作区副本，
    评测完成后销毁容器，确保环境无状态。
    """

    def __init__(
        self,
        image: str = "evobench:latest",
        yatcc_root: Optional[Path] = None,
        timeout: int = 600,
    ) -> None:
        """初始化沙盒管理器。

        :param image: Docker 镜像名
        :param yatcc_root: YatCC 源码根目录（宿主机路径）
        :param timeout: 命令执行超时（秒）
        """
        self.image = image
        self.yatcc_root = yatcc_root or Path.cwd() / "YatCC"
        self.timeout = timeout
        self._container_id: Optional[str] = None

    def launch(self, workspace: Path, env: Optional[dict[str, str]] = None) -> str:
        """启动新容器并挂载工作区。

        :param workspace: 宿主机工作区路径（将挂载到容器 /workspace/YatCC）
        :param env: 额外环境变量
        :return: 容器 ID
        """
        workspace = workspace.resolve()
        cmd = [
            "docker", "run",
            "-d",                          # 后台运行
            "--rm",                        # 退出后自动删除
            "-v", f"{workspace}:/workspace/YatCC",
            "-w", "/workspace/YatCC",
        ]
        if env:
            for k, v in env.items():
                cmd.extend(["-e", f"{k}={v}"])

        cmd.append(self.image)
        cmd.append("sleep")
        cmd.append("infinity")             # 保持容器运行

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise SandboxError(f"容器启动失败: {result.stderr.strip()}")

        self._container_id = result.stdout.strip()
        return self._container_id

    def exec(
        self,
        container_id: str,
        command: str,
        timeout: Optional[int] = None,
        cwd: str = "/workspace/YatCC",
    ) -> tuple[int, str, str]:
        """在容器内执行命令。

        :param container_id: 容器 ID
        :param command: 要执行的 shell 命令
        :param timeout: 超时（秒），默认使用实例 timeout
        :param cwd: 工作目录
        :return: (returncode, stdout, stderr)
        """
        cmd = [
            "docker", "exec",
            "-w", cwd,
            container_id,
            "bash", "-c", command,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"命令超时 ({timeout or self.timeout}s): {command[:200]}"

    def destroy(self, container_id: str) -> None:
        """销毁容器。

        :param container_id: 容器 ID
        """
        subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            timeout=30,
        )
        self._container_id = None

    def is_running(self, container_id: str) -> bool:
        """检查容器是否仍在运行。"""
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "true"

    def copy_to(self, container_id: str, src: Path, dst: str) -> None:
        """将宿主机文件复制到容器内。

        :param container_id: 容器 ID
        :param src: 宿主机源路径
        :param dst: 容器内目标路径
        """
        subprocess.run(
            ["docker", "cp", str(src), f"{container_id}:{dst}"],
            capture_output=True, timeout=30,
        )

    def copy_from(self, container_id: str, src: str, dst: Path) -> None:
        """将容器内文件复制到宿主机。

        :param container_id: 容器 ID
        :param src: 容器内源路径
        :param dst: 宿主机目标路径
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["docker", "cp", f"{container_id}:{src}", str(dst)],
            capture_output=True, timeout=30,
        )


class LocalSandbox(Sandbox):
    """本地沙盒（不使用 Docker，直接在宿主机执行）。

    用于开发调试或 Docker 不可用的场景。
    """

    def launch(self, workspace: Path, env: Optional[dict[str, str]] = None) -> str:
        self._container_id = "local"
        return "local"

    def exec(
        self,
        container_id: str,
        command: str,
        timeout: Optional[int] = None,
        cwd: str = "/workspace/YatCC",
    ) -> tuple[int, str, str]:
        import os
        actual_cwd = str(self.yatcc_root) if cwd == "/workspace/YatCC" else cwd
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                cwd=actual_cwd,
                env={**os.environ},
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"命令超时 ({timeout or self.timeout}s): {command[:200]}"

    def destroy(self, container_id: str) -> None:
        self._container_id = None

    def is_running(self, container_id: str) -> bool:
        return True

    def copy_to(self, container_id: str, src: Path, dst: str) -> None:
        import shutil
        actual_dst = str(self.yatcc_root / Path(dst).relative_to("/workspace/YatCC"))
        if src.is_dir():
            shutil.copytree(src, actual_dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, actual_dst)

    def copy_from(self, container_id: str, src: str, dst: Path) -> None:
        import shutil
        actual_src = str(self.yatcc_root / Path(src).relative_to("/workspace/YatCC"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if Path(actual_src).is_dir():
            shutil.copytree(actual_src, str(dst), dirs_exist_ok=True)
        else:
            shutil.copy2(actual_src, str(dst))