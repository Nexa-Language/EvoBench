#!/usr/bin/env bash
# EvoBench: 在 Docker 容器中运行 CLI Agent
# 用法:
#   ./run_agent_container.sh claude --tasks 0-5
#   ./run_agent_container.sh codex --tasks 0-5
#   ./run_agent_container.sh kimi --model kimi-k2.6 --tasks 0-5
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="evobench-agent:latest"
BACKEND="${1:?Usage: $0 <backend> [--tasks N-M] [--model NAME]}"
shift

# 解析额外参数
TASKS="0-5"
MODEL=""
EXTRA_ARGS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tasks) TASKS="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        *) EXTRA_ARGS="$EXTRA_ARGS $1"; shift ;;
    esac
done

# 容器名
CONTAINER="evo-${BACKEND}-$(date +%s)"

echo "=========================================="
echo "  EvoBench Container Agent Runner"
echo "  Backend: $BACKEND"
echo "  Tasks: $TASKS"
echo "  Model: ${MODEL:-default}"
echo "  Container: $CONTAINER"
echo "=========================================="

# 创建临时结果目录
RESULTS_DIR="$ROOT_DIR/eval/results"
mkdir -p "$RESULTS_DIR"

# 启动容器
docker run -d --name "$CONTAINER" \
    -v "$ROOT_DIR/eval/results:/workspace/results" \
    -e OPENAI_API_BASE="${OPENAI_API_BASE:-https://aihub.arcsysu.cn/v1}" \
    -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
    -e OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-mimo-v2.5-pro}" \
    "$IMAGE" \
    sleep infinity

echo "容器已启动: $CONTAINER"

# 根据后端执行不同的命令
case "$BACKEND" in
    claude)
        # 复制 claude 配置（如果有）
        if [ -d "$HOME/.claude" ]; then
            docker cp "$HOME/.claude" "$CONTAINER:/home/agent/.claude"
            echo "已复制 claude 配置"
        fi
        # 构建任务提示并运行
        docker exec -u agent "$CONTAINER" bash -c "
            claude -p '请完成 YatCC 编译原理实验 Task 0-5。阅读每个 task/X/README.md 了解要求，修改代码，编译评测。' \
                --output-format stream-json \
                --max-turns 100 \
                --verbose 2>&1 | tee /workspace/results/claude_output.log
        "
        ;;
    codex)
        docker exec -u agent "$CONTAINER" bash -c "
            codex --full-auto '请完成 YatCC 编译原理实验 Task 0-5。' \
                2>&1 | tee /workspace/results/codex_output.log
        "
        ;;
    kimi)
        docker exec -u agent "$CONTAINER" bash -c "
            kimi --model ${MODEL:-kimi-k2.6} --yes '请完成 YatCC 编译原理实验 Task 0-5。' \
                2>&1 | tee /workspace/results/kimi_output.log
        "
        ;;
    *)
        echo "未知后端: $BACKEND"
        docker rm -f "$CONTAINER"
        exit 1
        ;;
esac

# 收集结果
echo ""
echo "=========================================="
echo "  Agent 完成，收集结果..."
echo "=========================================="

# 在容器内运行评分
for task_id in 0 1 2 3 4 5; do
    docker exec "$CONTAINER" bash -c "
        cd /workspace/YatCC && \
        cmake --build build -t task${task_id} 2>/dev/null && \
        cmake --build build -t task${task_id}-score 2>/dev/null
    " 2>/dev/null || true
done

# 复制结果
docker cp "$CONTAINER:/workspace/YatCC/build/test/" "$RESULTS_DIR/build_test_${BACKEND}/" 2>/dev/null || true
docker cp "$CONTAINER:/workspace/results/" "$RESULTS_DIR/container_${BACKEND}/" 2>/dev/null || true

# 清理容器
docker rm -f "$CONTAINER" 2>/dev/null || true

echo "结果已保存到: $RESULTS_DIR"
echo "=========================================="
