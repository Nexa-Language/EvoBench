"""Stage 3: 自动化工作流引擎 — 构建+评测工作流。

按照 Task 0 -> Task 5 的顺序，自动在沙盒内执行 CMake 构建和 Python 评测脚本。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .sandbox import Sandbox, LocalSandbox
from .score_parser import TaskScore, parse_score


@dataclass
class BuildConfig:
    """CMake 构建配置。"""
    student_id: str = "EvoBench"
    student_name: str = "Agent"
    task1_with: str = "flex"
    task2_with: str = "bison"
    revive_task2: bool = False
    revive_task3: bool = False
    revive_task4: bool = False
    revive_task5: bool = False
    generator: str = "Ninja"

    def to_cmake_flags(self) -> list[str]:
        """生成 CMake 配置参数。"""
        return [
            f"-DSTUDENT_ID={self.student_id}",
            f"-DSTUDENT_NAME={self.student_name}",
            f"-DTASK1_WITH={self.task1_with}",
            f"-DTASK2_WITH={self.task2_with}",
            f"-DTASK2_REVIVE={'ON' if self.revive_task2 else 'OFF'}",
            f"-DTASK3_REVIVE={'ON' if self.revive_task3 else 'OFF'}",
            f"-DTASK4_REVIVE={'ON' if self.revive_task4 else 'OFF'}",
            f"-DTASK5_REVIVE={'ON' if self.revive_task5 else 'OFF'}",
        ]


@dataclass
class TaskAttempt:
    """单次 Task 尝试记录。"""
    task_id: int
    attempt_number: int
    build_stdout: str = ""
    build_stderr: str = ""
    score: Optional[TaskScore] = None
    resurrected: bool = False
    elapsed_seconds: float = 0.0
    error_message: str = ""


@dataclass
class PipelineResult:
    """完整流水线执行结果。"""
    config: BuildConfig
    attempts: list[TaskAttempt] = field(default_factory=list)
    total_elapsed: float = 0.0

    @property
    def resurrection_count(self) -> int:
        """总复活次数。"""
        return sum(1 for a in self.attempts if a.resurrected)

    @property
    def passed_tasks(self) -> list[int]:
        """通过的 Task ID 列表。"""
        return [a.task_id for a in self.attempts
                if a.score and a.score.is_passing]

    @property
    def zero_shot_pass(self) -> bool:
        """是否零复活全部通过。"""
        return self.resurrection_count == 0 and len(self.passed_tasks) == 6


class WorkflowEngine:
    """自动化工作流引擎。

    负责在沙盒中按顺序执行 Task 0-5 的构建和评测。
    """

    # Task 构建目标映射
    TASK_BUILD_TARGETS: dict[int, str] = {
        0: "task0",
        1: "task1",
        2: "task2",
        3: "task3",
        4: "task4",
        5: "task5-classic",
    }

    # Task 评分目标映射
    TASK_SCORE_TARGETS: dict[int, str] = {
        0: "task0-score",
        1: "task1-score",
        2: "task2-score",
        3: "task3-score",
        4: "task4-score",
        5: "task5-classic-score",
    }

    # Task 答案目标映射
    TASK_ANSWER_TARGETS: dict[int, str] = {
        0: "task0-answer",
        1: "task1-answer",
        2: "task2-answer",
        3: "task3-answer",
        5: "task5-answer",
    }

    # score.json 相对路径
    SCORE_JSON_PATHS: dict[int, str] = {
        0: "build/test/task0/score.json",
        1: "build/test/task1/score.json",
        2: "build/test/task2/score.json",
        3: "build/test/task3/score.json",
        4: "build/test/task4/score.json",
        5: "build/test/task5/score.json",
    }

    def __init__(
        self,
        sandbox: Sandbox,
        yatcc_root: Path,
        config: Optional[BuildConfig] = None,
        pass_threshold: float = 60.0,
        max_retries: int = 5,
    ) -> None:
        """初始化工作流引擎。

        :param sandbox: 沙盒实例
        :param yatcc_root: YatCC 源码根目录
        :param config: 构建配置
        :param pass_threshold: 及格线（百分比）
        :param max_retries: 每个 Task 最大重试次数
        """
        self.sandbox = sandbox
        self.yatcc_root = yatcc_root
        self.config = config or BuildConfig()
        self.pass_threshold = pass_threshold
        self.max_retries = max_retries
        self._container_id: Optional[str] = None

    def run_pipeline(self) -> PipelineResult:
        """执行完整流水线 Task 0 -> Task 5。

        :return: PipelineResult
        """
        result = PipelineResult(config=self.config)
        t_start = time.time()

        # 启动沙盒
        self._container_id = self.sandbox.launch(self.yatcc_root)

        try:
            # Step 1: 环境准备（ANTLR + LLVM + task5_setup）
            self._setup_environment()

            # Step 2: CMake 配置
            self._cmake_configure()

            # Step 3: 构建运行时库
            self._build_target("test-rtlib", "运行时库")

            # Step 4: 生成所有标准答案
            self._generate_all_answers()

            # Step 5: 逐个 Task 构建 + 评测
            for task_id in range(6):
                attempt = self._run_single_task(task_id)
                result.attempts.append(attempt)

                # 如果失败且需要复活，触发复活后重试
                if attempt.score and not attempt.score.is_passing:
                    resurrected = self._handle_resurrection(task_id, result)
                    if resurrected:
                        # 复活后重试一次
                        retry_attempt = self._run_single_task(task_id)
                        retry_attempt.resurrected = True
                        result.attempts.append(retry_attempt)

        finally:
            self.sandbox.destroy(self._container_id)

        result.total_elapsed = time.time() - t_start
        return result

    def _setup_environment(self) -> None:
        """在沙盒中准备 ANTLR、LLVM 和 Task5 环境。"""
        cid = self._container_id
        # ANTLR
        self.sandbox.exec(cid, "cd /workspace/YatCC && bash antlr/setup.sh", timeout=300)
        # LLVM
        self.sandbox.exec(cid, "cd /workspace/YatCC && bash llvm/setup.sh", timeout=600)
        # Task5 额外依赖
        self.sandbox.exec(cid, "cd /workspace/YatCC && bash task5_setup.sh", timeout=300)

    def _cmake_configure(self) -> None:
        """执行 CMake 配置。"""
        cid = self._container_id
        flags = " ".join(self.config.to_cmake_flags())
        cmd = (
            f"cd /workspace/YatCC && "
            f"cmake -S . -B build -G{self.config.generator} {flags}"
        )
        rc, stdout, stderr = self.sandbox.exec(cid, cmd, timeout=120)
        if rc != 0:
            raise RuntimeError(f"CMake 配置失败:\n{stderr}")

    def _build_target(self, target: str, description: str = "") -> None:
        """构建指定 CMake 目标。"""
        cid = self._container_id
        cmd = f"cd /workspace/YatCC && cmake --build build -t {target}"
        rc, stdout, stderr = self.sandbox.exec(cid, cmd, timeout=300)
        if rc != 0:
            raise RuntimeError(f"构建 {description or target} 失败:\n{stderr}")

    def _generate_all_answers(self) -> None:
        """生成所有 Task 的标准答案。"""
        for task_id in [0, 1, 2, 3, 5]:
            target = self.TASK_ANSWER_TARGETS.get(task_id)
            if target:
                self._build_target(target, f"Task {task_id} 标准答案")

    def _run_single_task(self, task_id: int) -> TaskAttempt:
        """执行单个 Task 的构建和评测。

        :param task_id: Task ID (0-5)
        :return: TaskAttempt
        """
        attempt = TaskAttempt(task_id=task_id, attempt_number=1)
        t_start = time.time()
        cid = self._container_id

        # 构建 Task
        build_target = self.TASK_BUILD_TARGETS.get(task_id)
        if build_target:
            rc, stdout, stderr = self.sandbox.exec(
                cid,
                f"cd /workspace/YatCC && cmake --build build -t {build_target}",
                timeout=300,
            )
            attempt.build_stdout = stdout
            attempt.build_stderr = stderr
            if rc != 0:
                attempt.error_message = f"构建失败:\n{stderr[-1000:]}"
                attempt.elapsed_seconds = time.time() - t_start
                return attempt

        # 运行评分
        score_target = self.TASK_SCORE_TARGETS.get(task_id)
        if score_target:
            rc, stdout, stderr = self.sandbox.exec(
                cid,
                f"cd /workspace/YatCC && cmake --build build -t {score_target}",
                timeout=300,
            )
            if rc != 0:
                attempt.error_message = f"评分运行失败:\n{stderr[-1000:]}"

        # 解析评分结果
        score_rel = self.SCORE_JSON_PATHS.get(task_id)
        if score_rel:
            score_path = self.yatcc_root / score_rel
            if score_path.exists():
                try:
                    attempt.score = parse_score(score_path)
                except Exception as e:
                    attempt.error_message = f"解析评分失败: {e}"

        attempt.elapsed_seconds = time.time() - t_start
        return attempt

    def _handle_resurrection(
        self, task_id: int, result: PipelineResult
    ) -> bool:
        """处理复活：修改 config.cmake 并重新配置 CMake。

        :param task_id: 失败的 Task ID
        :param result: 当前流水线结果
        :return: 是否成功触发复活
        """
        if task_id < 2:
            # Task 0 和 Task 1 没有复活机制
            return False

        # 修改 config.cmake 开启后续所有 Task 的复活
        config_path = self.yatcc_root / "config.cmake"
        if not config_path.exists():
            return False

        content = config_path.read_text()

        for i in range(task_id, 6):
            content = re.sub(
                rf'set\(TASK{i}_REVIVE\s+\w+\)',
                f'set(TASK{i}_REVIVE ON)',
                content,
            )

        config_path.write_text(content)

        # 更新内存中的配置
        revive_map = {2: "revive_task2", 3: "revive_task3",
                       4: "revive_task4", 5: "revive_task5"}
        for i in range(task_id, 6):
            attr = revive_map.get(i)
            if attr:
                setattr(self.config, attr, True)

        # 重新 CMake 配置
        self._cmake_configure()

        # 重新生成当前 Task 的标准答案
        answer_target = self.TASK_ANSWER_TARGETS.get(task_id)
        if answer_target:
            self._build_target(answer_target, f"Task {task_id} 标准答案(复活)")

        return True

    def get_build_errors(self, task_id: int) -> str:
        """获取最近一次构建的错误信息（供 Agent debug 使用）。

        :param task_id: Task ID
        :return: 错误信息字符串
        """
        cid = self._container_id
        if not cid:
            return ""

        # 尝试从 CTest 日志获取错误
        rc, stdout, stderr = self.sandbox.exec(
            cid,
            "cd /workspace/YatCC && "
            "find build/Testing -name '*.log' -exec tail -100 {} \\; 2>/dev/null || echo ''",
            timeout=30,
        )
        return stderr if stderr else stdout