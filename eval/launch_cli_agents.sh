#!/usr/bin/env bash
# EvoBench CLI Agent Launcher
# 使用 tmux 启动真正的交互式 Agent 会话
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YATCC_SRC="$ROOT_DIR/data/YatCC"
WORKSPACE_DIR="$ROOT_DIR/eval/workspaces"
LOG_DIR="$ROOT_DIR/eval/logs"
TASK_GUIDES="$ROOT_DIR/data/task_guides.md"

mkdir -p "$WORKSPACE_DIR" "$LOG_DIR"

# 构建任务提示
build_prompt() {
    local task_id=$1
    local readme="$YATCC_SRC/task/$task_id/README.md"
    local guide=""
    # 从 task_guides.md 提取对应 Task 的指南
    if [ -f "$TASK_GUIDES" ]; then
        guide=$(awk "/^## Task $task_id:/{found=1; next} /^## Task [0-9]:/{found=0} found" "$TASK_GUIDES")
    fi
    
    cat << PROMPT
请完成 YatCC 编译原理实验 Task $task_id。

## 实验说明
$(cat "$readme" 2>/dev/null || echo "README 不存在")

${guide:+## 任务指南
$guide}

## 工作流程
1. 阅读 task/$task_id/README.md 和上面的任务指南
2. 阅读 task/$task_id/ 下的源文件
3. 编写/修改代码
4. 编译: cmake --build build -t task$task_id
5. 评测: cmake --build build -t task${task_id}-score
6. 查看结果: cat build/test/task$task_id/score.txt
7. 如果失败，分析错误并修复，重复 4-6
8. 得分 >= 60% 时任务完成，然后告诉我 "TASK $task_id COMPLETE"
PROMPT
}

# 准备独立工作区
prepare_workspace() {
    local name=$1
    local ws="$WORKSPACE_DIR/$name"
    if [ ! -d "$ws" ]; then
        echo "  创建工作区: $ws"
        cp -a "$YATCC_SRC" "$ws"
        # CMake 配置
        cd "$ws" && cmake -S . -B build -GNinja \
            -DSTUDENT_ID=EvoBench -DSTUDENT_NAME=Agent \
            -DTASK1_WITH=flex -DTASK2_WITH=bison \
            -DTASK2_REVIVE=OFF -DTASK3_REVIVE=OFF \
            -DTASK4_REVIVE=OFF -DTASK5_REVIVE=OFF 2>/dev/null
        # 生成标准答案
        for ans in task0-answer task1-answer task2-answer task3-answer task5-answer; do
            cmake --build build -t "$ans" 2>/dev/null || true
        done
        # 重置复活标志
        sed -i 's/set(TASK[2-5]_REVIVE ON)/set(TASK& REVIVE OFF)/' config.cmake 2>/dev/null || true
    fi
    echo "$ws"
}

# 启动 Claude Code Agent
launch_claude() {
    local name="claude-sonnet4.6"
    local ws=$(prepare_workspace "$name")
    local session="evo-$name"
    
    echo "[启动] Claude Code ($name)"
    tmux kill-session -t "$session" 2>/dev/null || true
    tmux new-session -d -s "$session" -c "$ws"
    
    # 启动 claude 并发送任务
    tmux send-keys -t "$session" -l -- "claude --dangerously-skip-permissions"
    tmux send-keys -t "$session" Enter
    sleep 3
    
    # 发送 Task 0 提示
    local prompt=$(build_prompt 0)
    tmux send-keys -t "$session" -l -- "$prompt"
    tmux send-keys -t "$session" Enter
    
    echo "  会话: tmux attach -t $session"
    echo "  工作区: $ws"
}

# 启动 Codex Agent
launch_codex() {
    local name="codex-gpt5.5"
    local ws=$(prepare_workspace "$name")
    local session="evo-$name"
    
    echo "[启动] Codex CLI ($name)"
    tmux kill-session -t "$session" 2>/dev/null || true
    tmux new-session -d -s "$session" -c "$ws"
    
    # 启动 codex --full-auto
    local prompt=$(build_prompt 0)
    tmux send-keys -t "$session" -l -- "codex --full-auto '$prompt'"
    tmux send-keys -t "$session" Enter
    
    echo "  会话: tmux attach -t $session"
    echo "  工作区: $ws"
}

# 启动 Kimi Agent（指定模型）
launch_kimi() {
    local model=$1
    local name="kimi-$model"
    local ws=$(prepare_workspace "$name")
    local session="evo-$name"
    
    echo "[启动] Kimi CLI ($model)"
    tmux kill-session -t "$session" 2>/dev/null || true
    tmux new-session -d -s "$session" -c "$ws"
    
    # 启动 kimi
    tmux send-keys -t "$session" -l -- "kimi"
    tmux send-keys -t "$session" Enter
    sleep 3
    
    # 切换模型（如果需要）
    # kimi-k2.6 是默认模型，kimi-k2.5 和 mimo-v2.5-pro 需要切换
    if [ "$model" != "kimi-k2.6" ]; then
        tmux send-keys -t "$session" "/model" Enter
        sleep 1
        # 模型选择需要交互，先用默认
    fi
    
    # 发送任务
    local prompt=$(build_prompt 0)
    tmux send-keys -t "$session" -l -- "$prompt"
    tmux send-keys -t "$session" Enter
    
    echo "  会话: tmux attach -t $session"
    echo "  工作区: $ws"
}

# 主流程
echo "=========================================="
echo "  EvoBench CLI Agent Launcher"
echo "=========================================="

# 启动 Claude Code (Sonnet 4.6)
launch_claude

# 启动 Codex (GPT-5.5)
launch_codex

# 启动 Kimi (kimi-k2.6)
launch_kimi "kimi-k2.6"

echo ""
echo "=========================================="
echo "  所有 Agent 已启动！"
echo ""
echo "  查看状态: tmux ls | grep evo-"
echo "  监控 Claude: tmux attach -t evo-claude-sonnet4.6"
echo "  监控 Codex: tmux attach -t evo-codex-gpt5.5"
echo "  监控 Kimi: tmux attach -t evo-kimi-kimi-k2.6"
echo ""
echo "  截取输出: tmux capture-pane -t evo-claude-sonnet4.6 -p | tail -20"
echo "=========================================="
