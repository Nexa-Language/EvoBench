#!/usr/bin/env bash
# EvoBench 批量评测脚本
# 每次最多 5 个并行，跑完记录后删除工作区
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YATCC_SRC="$ROOT_DIR/data/YatCC"
WS_BASE="/home/agent/workspace"
LOG_DIR="$ROOT_DIR/eval/logs"
RESULT_DIR="$ROOT_DIR/eval/results/openhands"
VENV="$ROOT_DIR/.venv-openhands"

mkdir -p "$LOG_DIR" "$RESULT_DIR" "$WS_BASE"

export OPENHANDS_SUPPRESS_BANNER=1

# 模型列表（需要重跑的 10 个 + Task 4/5 重跑）
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
    "qwen3.6-plus"
)

# Task 4/5 重跑模型（之前 Task 4/5=0 的）
MODELS_T4T5=(
    "deepseek-v4-pro"
    "deepseek-v4-flash"
    "qwen3.6-max-preview"
    "minimax-m2.7"
    "glm-5.1"
    "mimo-v2.5-pro"
    "kimi-k2.6"
)

prepare_workspace() {
    local name="$1"
    local ws="$WS_BASE/$name"
    if [ ! -d "$ws/CMakeLists.txt" ]; then
        echo "  [WS] 创建: $ws ($(du -sh "$YATCC_SRC" | cut -f1))"
        cp -a "$YATCC_SRC" "$ws"
    fi
    echo "$ws"
}

cleanup_workspace() {
    local name="$1"
    local ws="$WS_BASE/$name"
    if [ -d "$ws" ]; then
        echo "  [清理] 删除: $ws"
        rm -rf "$ws"
    fi
}

collect_results() {
    local name="$1"
    local ws="$WS_BASE/$name"
    local result_file="$RESULT_DIR/${name}.json"
    
    echo "  [收集] 从 $ws 提取分数..."
    python3 -c "
import json, os, re
ws = '$ws'
name = '$name'
results = {'model': name, 'tasks': {}}
for task_id in range(6):
    score_file = os.path.join(ws, f'build/test/task{task_id}/score.json')
    if os.path.exists(score_file):
        try:
            d = json.load(open(score_file))
            t = d.get('tests', [])
            lb = d.get('leaderboard', [])
            s = float(lb[0]['value']) if lb else (sum(x['score'] for x in t)/len(t) if t else 0)
            results['tasks'][str(task_id)] = {'score': s, 'test_count': len(t)}
        except:
            results['tasks'][str(task_id)] = {'score': 0, 'test_count': 0}
json.dump(results, open('$result_file', 'w'), indent=2)
print(f'  结果已保存: $result_file')
" 2>/dev/null || echo "  [警告] 收集结果失败"
}

run_single() {
    local model="$1"
    local ws=$(prepare_workspace "$model")
    local log="$LOG_DIR/openhands-${model}.log"
    
    echo "[启动] OpenHands + $model"
    
    # 运行 OpenHands
    nohup bash -c "
        export PATH=\"\$HOME/.local/bin:\$PATH\"
        . '$VENV/bin/activate'
        export OPENHANDS_SUPPRESS_BANNER=1
        cd '$ROOT_DIR'
        python src/run_openhands.py --model '$model' --tasks 0-5 --max-iterations 100 --workspace '$ws' 2>&1
    " > "$log" 2>&1 &
    echo "  PID: $! | 日志: $log"
}

wait_and_cleanup() {
    echo ""
    echo "等待所有进程完成..."
    wait
    
    echo ""
    echo "收集结果并清理工作区..."
    for model in "$@"; do
        collect_results "$model"
        cleanup_workspace "$model"
    done
}

# ─── 主流程 ────────────────────────────────────────────────

echo "=========================================="
echo "  EvoBench 批量评测"
echo "  时间: $(date)"
echo "  磁盘: $(df -h / | tail -1 | awk '{print $4}') 可用"
echo "=========================================="

# 第一批：10 个异常模型（每批 5 个）
echo ""
echo "=== 第一批 (1-5): ${MODELS[0]} ${MODELS[1]} ${MODELS[2]} ${MODELS[3]} ${MODELS[4]} ==="
for i in 0 1 2 3 4; do
    run_single "${MODELS[$i]}"
done
wait_and_cleanup "${MODELS[0]}" "${MODELS[1]}" "${MODELS[2]}" "${MODELS[3]}"

echo ""
echo "=== 第二批 (6-10): ${MODELS[5]} ${MODELS[6]} ${MODELS[7]} ${MODELS[8]} ${MODELS[9]} ==="
for i in 5 6 7 8 9; do
    run_single "${MODELS[$i]}"
done
wait_and_cleanup "${MODELS[5]}" "${MODELS[6]}" "${MODELS[7]}" "${MODELS[8]}"

echo ""
echo "=========================================="
echo "  批量评测完成！"
echo "  结果目录: $RESULT_DIR/"
echo "  磁盘: $(df -h / | tail -1 | awk '{print $4}') 可用"
echo "=========================================="
