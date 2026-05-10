#!/bin/bash
# EvoBench 并行评测脚本
# 每个模型使用独立的工作区副本，避免冲突
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
YATCC_SRC="$ROOT_DIR/YatCC"
LOG_DIR="$ROOT_DIR/eval_logs"
VENV="$ROOT_DIR/.venv-openhands"
TASKS="0-5"

mkdir -p "$LOG_DIR"

export OPENHANDS_SUPPRESS_BANNER=1

echo "=========================================="
echo "  EvoBench 并行评测启动"
echo "  任务: Task $TASKS"
echo "  日志: $LOG_DIR/"
echo "=========================================="

# 准备独立工作区（复制 build 和源码引用）
prepare_workspace() {
    local model_name="$1"
    local ws="$ROOT_DIR/eval_workspaces/$model_name"
    mkdir -p "$ws"
    # 使用 symlink 共享大部分文件，只复制 build 目录
    if [ ! -L "$ws/YatCC" ]; then
        ln -sf "$YATCC_SRC" "$ws/YatCC"
    fi
    echo "$ws"
}

# 启动 OpenHands 评测
start_openhands_eval() {
    local model="$1"
    local safe_name=$(echo "$model" | tr '/' '-' | tr '.' '-')
    local log_file="$LOG_DIR/openhands-${safe_name}.log"
    local ws=$(prepare_workspace "openhands-${safe_name}")

    echo "[启动] OpenHands + $model"
    echo "  日志: $log_file"

    cd "$ROOT_DIR"
    nohup bash -c "
        export PATH=\"\$HOME/.local/bin:\$PATH\"
        . $VENV/bin/activate
        export OPENHANDS_SUPPRESS_BANNER=1
        cd $ROOT_DIR
        python run_openhands.py --model '$model' --tasks '$TASKS' --max-iterations 100 2>&1
    " > "$log_file" 2>&1 &
    echo "  PID: $!"
}

# OpenHands 模型列表
start_openhands_eval "deepseek-v4-pro"
start_openhands_eval "deepseek-v4-flash"
start_openhands_eval "qwen3.6-max-preview"
start_openhands_eval "glm-5.1"
start_openhands_eval "minimax-m2.7"
start_openhands_eval "mimo-v2.5-pro"

# Kimi CLI 评测
KIMI_LOG="$LOG_DIR/kimi-cli-kimi-k2.6.log"
echo "[启动] Kimi CLI + kimi-k2.6"
echo "  日志: $KIMI_LOG"
cd "$ROOT_DIR"
nohup bash -c "
    export PATH=\"\$HOME/.local/bin:\$PATH\"
    . $VENV/bin/activate
    export OPENHANDS_SUPPRESS_BANNER=1
    cd $ROOT_DIR
    python run_openhands.py --model 'kimi-k2.6' --tasks '$TASKS' --max-iterations 100 2>&1
" > "$KIMI_LOG" 2>&1 &
echo "  PID: $!"

echo ""
echo "=========================================="
echo "  所有评测已启动！"
echo "  查看进度: tail -f $LOG_DIR/*.log"
echo "  查看结果: cat $ROOT_DIR/evobench_output/*.json"
echo "=========================================="
