#!/usr/bin/env bash
# EvoBench 重跑失败模型脚本
# 使用独立工作区副本，避免 Agent 之间互相干扰
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YATCC_SRC="$ROOT_DIR/data/YatCC"
VENV="$ROOT_DIR/.venv-openhands"
LOG_DIR="$ROOT_DIR/eval/logs"
WORKSPACE_DIR="$ROOT_DIR/eval/workspaces"
mkdir -p "$LOG_DIR" "$WORKSPACE_DIR"

export OPENHANDS_SUPPRESS_BANNER=1

# 需要重跑的模型（Task 0 = 0 或其他异常）
MODELS=(
  "deepseek-chat"
  "deepseek-reasoner"
  "glm-4.7"
  "glm-5"
  "glm-5.1"
  "kimi-k2-thinking"
  "minimax-m2.5"
  "qwen-omni-turbo"
  "qwen3.5-plus-2026-02-15"
  "qwen3.6-plus"
  "qwen3-coder-flash"
)

# Gemini 各模型
GEMINI_MODELS=(
  "gemini-2.5-pro"
  "gemini-2.5-flash"
  "gemini-2.0-flash"
)

prepare_workspace() {
    local model="$1"
    local ws="$WORKSPACE_DIR/openhands-${model}"
    if [ ! -d "$ws" ]; then
        echo "  创建独立工作区: $ws"
        cp -a "$YATCC_SRC" "$ws"
    fi
    echo "$ws"
}

start_eval() {
    local model="$1"
    local safe_name=$(echo "$model" | tr '/' '-' | tr '.' '-')
    local log="$LOG_DIR/openhands-${safe_name}.log"
    local ws=$(prepare_workspace "$safe_name")

    echo "[启动] OpenHands + $model → $log"
    nohup bash -c "
        export PATH=\"\$HOME/.local/bin:\$PATH\"
        . $VENV/bin/activate
        export OPENHANDS_SUPPRESS_BANNER=1
        cd $ROOT_DIR
        python src/run_openhands.py --model '$model' --tasks 0-5 --max-iterations 100 --workspace '$ws' 2>&1
    " > "$log" 2>&1 &
    echo "  PID: $!"
}

# 启动 AI Hub 模型重跑
for model in "${MODELS[@]}"; do
    start_eval "$model"
done

# 启动 Gemini 模型（需要不同的 API 配置）
# 这些通过 gemini-cli 跑，不需要 OpenHands
echo ""
echo "=== 重跑已启动 ==="
echo "查看进度: tail -f $LOG_DIR/openhands-*.log"
