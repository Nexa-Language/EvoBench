#!/bin/bash
# Codex CLI 评测脚本
# 用法: ./run_codex_eval.sh [model_name]
# 会临时修改 codex config 指向我们的 API，评测完恢复
set -euo pipefail

PROJ_DIR="/root/proj/papers/EXPERIMENT/EvoBench"
YATCC_DIR="$PROJ_DIR/data/YatCC"
LOG_DIR="$PROJ_DIR/eval/logs"
RESULT_DIR="$PROJ_DIR/eval/results/codex"
CODEX_CONFIG="$HOME/.codex/config.toml"
CODEX_CONFIG_BACKUP="$HOME/.codex/config.toml.evo-backup"

MODEL="${1:-deepseek-v4-pro}"
TASK_ID="${2:-0}"

mkdir -p "$LOG_DIR" "$RESULT_DIR"

echo "=== Codex 评测: model=$MODEL task=$TASK_ID ==="
echo "  时间: $(date)"
echo "  日志: $LOG_DIR/codex-${MODEL}-task${TASK_ID}.log"

# 备份原始配置
cp "$CODEX_CONFIG" "$CODEX_CONFIG_BACKUP"

# 临时修改配置指向我们的 API
cat > "$CODEX_CONFIG" << EOF
model_provider = "custom"
model = "$MODEL"
model_reasoning_effort = "high"
disable_response_storage = true

[model_providers.custom]
name = "custom"
wire_api = "chat"
requires_openai_auth = true
base_url = "https://aihub.arcsysu.cn/v1"

[tui.model_availability_nux]
"$MODEL" = 1
EOF

# 设置 API Key
export OPENAI_API_KEY="sk-Yq-qWVwmUvF8EpJdM-We2Q"
export OPENAI_API_BASE="https://aihub.arcsysu.cn/v1"

# 读取 Task README
README_FILE="$YATCC_DIR/task/$TASK_ID/README.md"
if [ -f "$README_FILE" ]; then
    TASK_DESC=$(cat "$README_FILE")
else
    TASK_DESC="完成 Task $TASK_ID"
fi

PROMPT="请完成 YatCC 编译原理实验 Task $TASK_ID。

$TASK_DESC

工作流程：
1. 阅读 task/$TASK_ID/README.md 和源文件
2. 修改代码实现功能
3. 编译: cmake --build build -t task$TASK_ID
4. 评测: cmake --build build -t task${TASK_ID}-score
5. 查看结果: cat build/test/task${TASK_ID}/score.txt
6. 如果失败，分析错误并修复
7. 得分 >= 60% 时任务完成"

# 运行 Codex
cd "$YATCC_DIR"
codex exec "$PROMPT" --model "$MODEL" 2>&1 | tee "$LOG_DIR/codex-${MODEL}-task${TASK_ID}.log"

# 恢复原始配置
cp "$CODEX_CONFIG_BACKUP" "$CODEX_CONFIG"
rm -f "$CODEX_CONFIG_BACKUP"

echo "=== Codex 评测完成: model=$MODEL task=$TASK_ID ==="
