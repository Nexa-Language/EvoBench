#!/bin/bash
# EvoBench 补测脚本 — 剩余 API 模型
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT_DIR/.venv-openhands"
LOG_DIR="$ROOT_DIR/eval/logs"
mkdir -p "$LOG_DIR"
export OPENHANDS_SUPPRESS_BANNER=1

MODELS=(
  "deepseek-chat"
  "deepseek-reasoner"
  "glm-4.7"
  "glm-5"
  "kimi-k2-thinking"
  "minimax-m2.5"
  "qwen-omni-turbo"
  "qwen3-coder-flash"
  "qwen3.5-plus-2026-02-15"
  "qwen3.6-flash"
  "qwen3.6-plus"
)

for model in "${MODELS[@]}"; do
  safe=$(echo "$model" | tr '/' '-' | tr '.' '-')
  log="$LOG_DIR/openhands-${safe}.log"
  echo "[启动] OpenHands + $model → $log"
  nohup bash -c "
    export PATH=\"\$HOME/.local/bin:\$PATH\"
    . $VENV/bin/activate
    export OPENHANDS_SUPPRESS_BANNER=1
    cd $ROOT_DIR
    python src/run_openhands.py --model '$model' --tasks 0-5 --max-iterations 100 2>&1
  " > "$log" 2>&1 &
  echo "  PID: $!"
done

# mimo-v2.5-pro 用 claude-code 后端测试
# (claude 已配置 glm-5，先测一次)
echo ""
echo "所有补测已启动！"
echo "查看进度: tail -f $LOG_DIR/*.log"
