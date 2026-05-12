#!/bin/bash
# Task 4/5 重跑脚本
# 只重跑 Task 4 和 Task 5，复用已有的 build 目录
set -e

PROJ_DIR="/root/proj/papers/EXPERIMENT/EvoBench"
YATCC_DIR="$PROJ_DIR/data/YatCC"
LOG_DIR="$PROJ_DIR/eval/logs"
RESULT_DIR="$PROJ_DIR/eval/results/openhands"
VENV="$PROJ_DIR/.venv-openhands"

mkdir -p "$LOG_DIR" "$RESULT_DIR"

# 需要重跑 Task 4/5 的模型（Task 4/5 = 0 的）
MODELS=(
  "deepseek-v4-pro"
  "qwen3.6-max-preview"
  "minimax-m2.7"
  "qwen3.6-flash"
  "glm-4.7"
  "deepseek-v4-flash"
  "glm-5.1"
  "mimo-v2.5-pro"
)

echo "=== Task 4/5 重跑 ==="
echo "  时间: $(date)"
echo "  模型: ${MODELS[*]}"
echo ""

# 确保 build 目录存在
cd "$YATCC_DIR"
if [ ! -f "build/build.ninja" ]; then
  echo "  [初始化] cmake 配置..."
  cmake -S . -B build -GNinja -DSTUDENT_ID=EvoBench -DSTUDENT_NAME=Agent -DTASK1_WITH=flex -DTASK2_WITH=bison -DTASK2_REVIVE=ON -DTASK3_REVIVE=ON -DTASK4_REVIVE=ON -DTASK5_REVIVE=ON
fi

# 确保标准答案存在
echo "  [初始化] 生成标准答案..."
cmake --build build -t task0-answer task1-answer task2-answer task3-answer task5-answer 2>/dev/null || true
cmake --build build -t test-rtlib 2>/dev/null || true

# 并行启动评测（最多 5 个）
BATCH_SIZE=5
for ((i=0; i<${#MODELS[@]}; i+=BATCH_SIZE)); do
  BATCH=("${MODELS[@]:i:BATCH_SIZE}")
  echo ""
  echo "=== 批次 $((i/BATCH_SIZE + 1)): ${BATCH[*]} ==="
  
  for model in "${BATCH[@]}"; do
    echo "  [启动] $model"
    nohup bash -c "
      export PATH=\"\$HOME/.local/bin:\$PATH\"
      . '$VENV/bin/activate'
      export OPENHANDS_SUPPRESS_BANNER=1
      cd '$PROJ_DIR'
      python src/run_openhands.py --model '$model' --tasks 4-5 --max-iterations 100 2>&1
    " > "$LOG_DIR/openhands-${model}-t4t5.log" 2>&1 &
    echo "    PID: $!"
  done
  
  echo "  等待批次完成..."
  wait
  
  echo "  收集结果..."
  python3 eval/collect_and_update.py 2>/dev/null || true
done

echo ""
echo "=== Task 4/5 重跑完成 ==="
echo "  时间: $(date)"
echo "  结果: $RESULT_DIR/"
