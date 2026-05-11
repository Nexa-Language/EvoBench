#!/usr/bin/env bash
# EvoBench: 启动所有 Agent 评测
# 正确处理每个 CLI 的语法差异
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YATCC_SRC="$ROOT_DIR/data/YatCC"
WS_BASE="/home/agent/workspace"
LOG_DIR="$ROOT_DIR/eval/logs"
TASK_GUIDES="$ROOT_DIR/data/task_guides.md"

mkdir -p "$LOG_DIR" "$WS_BASE"

# ─── 工具函数 ───────────────────────────────────────────────

prepare_workspace() {
    local name=$1
    local ws="$WS_BASE/$name"
    if [ ! -d "$ws/CMakeLists.txt" ]; then
        echo "  [WS] 创建: $ws"
        cp -a "$YATCC_SRC" "$ws"
        chown -R agent:agent "$ws"
    fi
    echo "$ws"
}

build_prompt() {
    local task_id=$1
    local readme="$2"
    echo "请完成 YatCC 编译原理实验 Task ${task_id}。"
    echo ""
    echo "步骤："
    echo "1. 阅读 task/${task_id}/README.md"
    echo "2. 阅读 task/${task_id}/ 下的源文件"
    echo "3. 修改代码实现功能"
    echo "4. 编译: cmake --build build -t task${task_id}"
    echo "5. 评测: cmake --build build -t task${task_id}-score"
    echo "6. 查看结果: cat build/test/task${task_id}/score.txt"
    echo "7. 如果失败，分析错误并修复"
    echo "8. 得分 >= 60% 时任务完成"
}

# ─── Claude Code ────────────────────────────────────────────

launch_claude() {
    local name="claude-sonnet4.6"
    local ws=$(prepare_workspace "$name")
    local log="$LOG_DIR/${name}.log"

    echo "[启动] Claude Code (Sonnet 4.6)"
    nohup bash -c "
        cd '$ws'
        for task_id in 0 1 2 3 4 5; do
            echo \"=== Task \$task_id ===\"
            prompt='$(build_prompt 0)'
            prompt=\$(echo \"请完成 YatCC 编译原理实验 Task \$task_id。阅读 task/\$task_id/README.md，修改代码，编译 cmake --build build -t task\$task_id，评测 cmake --build build -t task\${task_id}-score。查看 cat build/test/task\$task_id/score.txt。得分>=60%时完成。\" | sed \"s/'/'\\\\''/g\")
            claude -p \"\$prompt\" --dangerously-skip-permissions --max-turns 50 2>&1
            echo \"=== Task \$task_id Done ===\"
        done
    " > "$log" 2>&1 &
    echo "  PID: $! | 日志: $log"
}

# ─── Codex CLI ──────────────────────────────────────────────

launch_codex() {
    local name="codex-gpt5.5"
    local ws=$(prepare_workspace "$name")
    local log="$LOG_DIR/${name}.log"

    echo "[启动] Codex CLI (GPT-5.5)"
    nohup bash -c "
        cd '$ws'
        for task_id in 0 1 2 3 4 5; do
            echo \"=== Task \$task_id ===\"
            codex exec \"请完成 YatCC 编译原理实验 Task \$task_id。阅读 task/\$task_id/README.md，修改代码，编译 cmake --build build -t task\$task_id，评测 cmake --build build -t task\${task_id}-score。\" 2>&1
            echo \"=== Task \$task_id Done ===\"
        done
    " > "$log" 2>&1 &
    echo "  PID: $! | 日志: $log"
}

# ─── Kimi CLI ───────────────────────────────────────────────

launch_kimi() {
    local model=$1
    local name="kimi-${model}"
    local ws=$(prepare_workspace "$name")
    local log="$LOG_DIR/${name}.log"

    echo "[启动] Kimi CLI ($model)"
    nohup bash -c "
        cd '$ws'
        for task_id in 0 1 2 3 4 5; do
            echo \"=== Task \$task_id ===\"
            kimi --model $model --yes \"请完成 YatCC 编译原理实验 Task \$task_id。阅读 task/\$task_id/README.md，修改代码，编译 cmake --build build -t task\$task_id，评测 cmake --build build -t task\${task_id}-score。\"
            echo \"=== Task \$task_id Done ===\"
        done
    " > "$log" 2>&1 &
    echo "  PID: $! | 日志: $log"
}

# ─── OpenHands SDK（独立工作区）────────────────────────────────

launch_openhands() {
    local model=$1
    local safe_name=$(echo "$model" | tr '/' '-' | tr '.' '-')
    local name="openhands-${safe_name}"
    local ws=$(prepare_workspace "$name")
    local log="$LOG_DIR/${name}.log"
    local venv="$ROOT_DIR/.venv-openhands"

    echo "[启动] OpenHands ($model)"
    nohup bash -c "
        export PATH=\"\$HOME/.local/bin:\$PATH\"
        . '$venv/bin/activate'
        export OPENHANDS_SUPPRESS_BANNER=1
        cd '$ROOT_DIR'
        python src/run_openhands.py --model '$model' --tasks 0-5 --max-iterations 100 --workspace '$ws' 2>&1
    " > "$log" 2>&1 &
    echo "  PID: $! | 日志: $log"
}

# ─── 主流程 ────────────────────────────────────────────────

echo "=========================================="
echo "  EvoBench 全量评测启动"
echo "  时间: $(date)"
echo "=========================================="

# CLI Agent 测试
launch_claude
launch_codex
launch_kimi "kimi-k2.6"
launch_kimi "kimi-k2.5"
launch_kimi "mimo-v2.5-pro"

# OpenHands 重跑（Task 0 = 0 的模型，使用独立工作区）
launch_openhands "deepseek-chat"
launch_openhands "deepseek-reasoner"
launch_openhands "glm-4.7"
launch_openhands "glm-5"
launch_openhands "glm-5.1"
launch_openhands "kimi-k2-thinking"
launch_openhands "minimax-m2.5"
launch_openhands "qwen-omni-turbo"
launch_openhands "qwen3-coder-flash"
launch_openhands "qwen3.5-plus-2026-02-15"
launch_openhands "qwen3.6-plus"

echo ""
echo "=========================================="
echo "  所有评测已启动！"
echo "  查看状态: ps aux | grep 'claude\|codex\|kimi\|run_openhands' | grep -v grep | wc -l"
echo "  查看日志: tail -f $LOG_DIR/*.log"
echo "=========================================="
