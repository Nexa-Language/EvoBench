from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from lib._agent_events import append_agent_event
from lib._claude_usage import (
    claude_jsonl_last_text,
    claude_jsonl_usage_total,
    claude_latest_session_jsonl,
    claude_result_from_stdout,
    claude_make_session_readable,
    write_claude_usage_snapshot,
)
from lib._kimi_stream_json import (
    kimi_session_id,
    kimi_stream_json_assistant_lines,
    kimi_wire_path,
    kimi_wire_usage_total,
)


SUPPORTED_BACKENDS = ("openhands", "kimi", "claude", "codex")


@dataclass(frozen=True)
class BackendContext:
    backend: str
    model: str
    api_key: str
    api_base: str
    workspace: Path
    output_dir: Path
    run_id: str
    max_iterations: int
    context_mode: str = "per-task"


def ensure_backend(name: str) -> str:
    if name not in SUPPORTED_BACKENDS:
        raise ValueError(f"不支持的 backend: {name}，可选: {', '.join(SUPPORTED_BACKENDS)}")
    return name


def _base_env(ctx: BackendContext) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OPENAI_API_KEY": ctx.api_key,
            "OPENAI_BASE_URL": ctx.api_base,
            "OPENAI_API_BASE": ctx.api_base,
        }
    )
    return env


def _run_command(
    ctx: BackendContext,
    task_id: int,
    command: list[str],
    env: dict[str, str],
    *,
    llm_step_budget: int | None = None,
    pipeline_stage: str | None = None,
) -> None:
    event_path = ctx.output_dir / "agent-events" / f"task{task_id}.jsonl"
    start_payload: dict = {
        "event_type": "agent_start",
        "backend": ctx.backend,
        "task_id": task_id,
        "command": command[:2],
        "context_mode": ctx.context_mode,
        "pipeline_stage": pipeline_stage,
    }
    if llm_step_budget is not None:
        start_payload["llm_step_budget"] = llm_step_budget
    append_agent_event(event_path, start_payload)
    if ctx.backend == "kimi":
        sub_timeout: int | None = None
    elif ctx.max_iterations > 0:
        sub_timeout = ctx.max_iterations * 300
    else:
        sub_timeout = None
    proc = subprocess.run(
        command,
        cwd=ctx.workspace,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=sub_timeout,
    )
    out = proc.stdout or ""
    if ctx.backend == "kimi" and out.strip():
        assistant_lines = kimi_stream_json_assistant_lines(out)
        if assistant_lines:
            for i, snippet in enumerate(assistant_lines):
                append_agent_event(
                    event_path,
                    {
                        "event_type": "llm_response",
                        "backend": ctx.backend,
                        "task_id": task_id,
                        "llm_response_id": str(uuid.uuid4()),
                        "kimi_stream_json": True,
                        "kimi_assistant_index": i,
                        "message": snippet[:12000],
                    },
                )
        else:
            append_agent_event(
                event_path,
                {
                    "event_type": "llm_response",
                    "backend": ctx.backend,
                    "task_id": task_id,
                    "llm_response_id": str(uuid.uuid4()),
                    "kimi_stream_json": False,
                    "message": out[-8000:],
                },
            )
    elif ctx.backend == "claude":
        claude_home = ctx.output_dir / "agent-state" / "claude" / "claude"
        usage = None
        session_id = ""
        display = (out or "").strip()
        jsonl = claude_latest_session_jsonl(claude_home)
        if jsonl is not None:
            session_id = jsonl.stem
            usage = claude_jsonl_usage_total(jsonl)
            display = claude_jsonl_last_text(jsonl) or display
        if usage is None and display.startswith("{"):
            usage, display, session_id = claude_result_from_stdout(display)
        if usage:
            write_claude_usage_snapshot(
                claude_home, task_id, usage, session_id=session_id, source_jsonl=jsonl
            )
            claude_make_session_readable(jsonl)
            payload = {
                "event_type": "llm_usage",
                "backend": ctx.backend,
                "task_id": task_id,
                "usage_normalized": usage,
            }
            if session_id:
                payload["claude_session_id"] = session_id
            append_agent_event(event_path, payload)
        if display.strip():
            append_agent_event(
                event_path,
                {
                    "event_type": "llm_response",
                    "backend": ctx.backend,
                    "task_id": task_id,
                    "llm_response_id": str(uuid.uuid4()),
                    "message": display,
                },
            )
    elif out:
        append_agent_event(
            event_path,
            {
                "event_type": "llm_response",
                "backend": ctx.backend,
                "task_id": task_id,
                "llm_response_id": str(uuid.uuid4()),
                "message": out[-8000:],
            },
        )
    stderr_text = proc.stderr or ""
    if stderr_text:
        append_agent_event(
            event_path,
            {
                "event_type": "agent_stderr",
                "backend": ctx.backend,
                "task_id": task_id,
                "detail": stderr_text[-8000:],
            },
        )
    if ctx.backend == "kimi":
        wire = kimi_wire_path(ctx.output_dir / "agent-state" / "kimi", kimi_session_id(stderr_text))
        usage = kimi_wire_usage_total(wire) if wire else None
        if usage:
            append_agent_event(
                event_path,
                {
                    "event_type": "llm_usage",
                    "backend": ctx.backend,
                    "task_id": task_id,
                    "usage_normalized": usage,
                },
            )
    append_agent_event(
        event_path,
        {
            "event_type": "agent_exit",
            "backend": ctx.backend,
            "task_id": task_id,
            "returncode": proc.returncode,
        },
    )
    if proc.returncode != 0:
        append_agent_event(
            event_path,
            {
                "event_type": "error",
                "backend": ctx.backend,
                "task_id": task_id,
                "code": f"{ctx.backend}.exit_{proc.returncode}",
                "kind": "AgentProcessError",
                "detail": (proc.stderr or proc.stdout)[-8000:],
            },
        )
        raise RuntimeError(f"{ctx.backend} backend exited with {proc.returncode}")


def run_cli_backend_task(
    ctx: BackendContext,
    task_id: int,
    prompt: str,
    *,
    llm_step_budget: int | None = None,
    pipeline_stage: str | None = None,
) -> None:
    ensure_backend(ctx.backend)
    if ctx.backend == "openhands":
        raise ValueError("OpenHands backend is handled by the OpenHands SDK runner")
    env = _base_env(ctx)
    if ctx.backend == "claude":
        if shutil.which("claude") is None:
            raise RuntimeError("Claude backend 需要镜像内安装 Claude Code CLI: claude")
        # --bare uses ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL (not OAuth / cc-switch GUI).
        env.update(
            {
                "ANTHROPIC_API_KEY": ctx.api_key,
                "ANTHROPIC_BASE_URL": ctx.api_base,
                "ANTHROPIC_MODEL": ctx.model,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1",
            }
        )
        claude_home = ctx.output_dir / "agent-state" / "claude" / "claude"
        claude_home.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(claude_home)
        step_cap = max(1, llm_step_budget) if llm_step_budget is not None else max(ctx.max_iterations, 1)
        command = [
            "claude",
            "--bare",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--allowedTools",
            "Read,Edit,Write,Bash,Glob,Grep",
            "--max-turns",
            str(step_cap),
        ]
    elif ctx.backend == "codex":
        if shutil.which("codex") is None:
            raise RuntimeError("Codex backend 需要镜像内安装 Codex CLI: codex")
        codex_home = ctx.output_dir / "agent-state" / "codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        env["CODEX_HOME"] = str(codex_home)
        config = codex_home / "config.toml"
        config.write_text(
            "\n".join(
                [
                    f'model = "{ctx.model}"',
                    'model_provider = "evobench"',
                    'approval_policy = "never"',
                    '[model_providers.evobench]',
                    'name = "EvoBench OpenAI-compatible"',
                    f'base_url = "{ctx.api_base}"',
                    'env_key = "OPENAI_API_KEY"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        command = ["codex", "exec", "--sandbox", "workspace-write", "--json", prompt]
    elif ctx.backend == "kimi":
        if shutil.which("kimi") is None:
            raise RuntimeError("Kimi backend 需要镜像内安装 Kimi Code CLI: kimi")
        kimi_home = ctx.output_dir / "agent-state" / "kimi"
        kimi_home.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "KIMI_SHARE_DIR": str(kimi_home),
                "KIMI_CLI_NO_AUTO_UPDATE": "1",
                "KIMI_API_KEY": ctx.api_key,
                "KIMI_BASE_URL": ctx.api_base,
                "KIMI_MODEL_NAME": ctx.model,
            }
        )
        step_cap = max(1, llm_step_budget) if llm_step_budget is not None else max(ctx.max_iterations, 1)
        command = [
            "kimi",
            "--print",
            "-p",
            prompt,
            "--output-format=stream-json",
            "--max-steps-per-turn",
            str(step_cap),
        ]
    else:
        raise ValueError(f"不支持的 CLI backend: {ctx.backend}")
    _run_command(
        ctx,
        task_id,
        command,
        env,
        llm_step_budget=llm_step_budget,
        pipeline_stage=pipeline_stage,
    )
