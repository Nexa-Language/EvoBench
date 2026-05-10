# EvoBench Phase 2 改进计划

## 背景

第一轮 7 个模型评测完成，发现以下问题需要改进。

## 问题分析

### ⑤ T1=0 但 T2=100 的异常 — 不是 Bug

这是**复活机制正确工作**的表现：
- Agent 在 Task 1（Lexer）失败 → 触发复活 → Task 2 拿到 Task 1 的标准答案
- Agent 在 Task 2（Parser）上成功实现 → 100%
- 这正是 EvoBench 的设计意图

### ③ Task 4/5 全是 0 分

**Task 4（IR Opt）**：评分公式是 `score = sqrt(标准时间/学生时间) * 100`。
- 如果 Agent 没有实现任何优化 Pass，输出的 IR 等同于输入（O0），性能可能比 O2 差很多
- 需要实现 ConstantFolding、Mem2Reg 等 Pass 才能得分
- 100 轮迭代可能不够，且 Agent 可能不理解 LLVM Pass 框架

**Task 5（RV64 后端）**：实际上是一个**填空题**！
- 只需要在 `EmitMIR.cpp` 中实现 4 个函数：`emitBinary`、`emitICmpInst`、`emitLoadInst`、`emitStoreInst`
- 基础框架已经实现了函数序言/尾声、分支跳转、函数调用、PHI 处理
- 但 `task5-answer` 部分失败（ABI 兼容性问题），没有 answer 的测例直接 0 分
- **建议**：修复 answer 生成，给 Agent 更清晰的 Task 5 文档

### ④ 文档注入（最高优先级）

当前 `build_task_prompt()` 只读取 `task/X/README.md`（很简略）。
YatCC-docs 分支有详细的任务文档，包括：
- Task 1: token 格式、行标记处理、评分标准
- Task 3: JSON→ASG→LLVM IR 流程、EmitIR 类结构
- Task 4: 优化 Pass 框架、评分公式、LLM Agent 优化方法
- Task 5: 4 个待实现函数的详细说明、MIR 框架、虚拟寄存器

**精简策略**：只给 Agent 任务要求和关键信息，不给引导文档。

## 执行计划

### Step 1: 目录整理

```
EvoBench/
├── src/                    # 源代码
│   ├── evo_cli/            # Evo-CLI 核心包
│   ├── evobench_runner/    # 原始 Runner
│   └── run_openhands.py    # OpenHands runner
├── data/                   # 数据
│   ├── YatCC/              # YatCC 主分支（git submodule）
│   ├── YatCC-docs/         # YatCC docs 分支
│   └── docs/               # 任务文档精简版
├── eval/                   # 评测
│   ├── logs/               # 评测日志
│   ├── workspaces/         # 评测工作区
│   ├── results/            # 评测结果 JSON
│   └── run_parallel.sh     # 并行评测脚本
├── site/                   # 网站
├── plans/                  # 计划文档
├── .github/                # GitHub Actions
├── pyproject.toml
├── README.md
└── ROADMAP.md
```

### Step 2: 指标公式

```python
# 1. Mean Reward (加权平均分)
TASK_WEIGHTS = [1.0, 2.0, 2.0, 3.0, 3.0, 4.0]  # Task 0-5，递增权重
RESURRECTION_BONUS = 1.0  # 复活通过的权重系数（无 bonus）
NO_RESURRECTION_BONUS = 1.2  # 直接通过的 bonus（+20%）

mean_reward = sum(
    score[i] * weight[i] * (NO_RESURRECTION_BONUS if not resurrected[i] else RESURRECTION_BONUS)
    for i in range(6)
) / sum(weight)

# 2. Pass Score (加权通过分)
PASS_BONUS_NO_RESURRECTION = 1.5  # 直接通过 bonus +50%
PASS_BONUS_RESURRECTION = 1.0     # 复活通过无 bonus

pass_score = sum(
    (1 if score[i] >= 60 else 0) * (PASS_BONUS_NO_RESURRECTION if not resurrected[i] else PASS_BONUS_RESURRECTION)
    for i in range(6)
) / (6 * PASS_BONUS_NO_RESURRECTION) * 100

# 3. 其他指标
# - zero_shot_pass: 不使用任何复活，全部通过
# - resurrection_count: 复活次数
# - node_pass_rate: 各 Task 独立通过率
```

### Step 3: 文档精简注入

为每个 Task 创建精简版任务清单（从 YatCC-docs 提取）：

**Task 1 任务清单**：
- 输入：预处理后的 C 源码（含行标记 `# linenum filename flags`）
- 输出：与 `clang -cc1 -dump-tokens` 格式一致的 token 流
- 每行格式：`token_name 'token_value' [StartOfLine] [LeadingSpace] Loc=<file:line:col>`
- 注意：行标记不是源码内容，但决定文件名和行号
- 评分：token 类型 60% + 位置 30% + 无关字符 10%

**Task 3 任务清单**：
- 输入：JSON 格式的 AST（Task 2 输出）
- 输出：LLVM IR（.ll 文件）
- 关键文件：`EmitIR.hpp/cpp`（只需修改这两个）
- 评分：生成的 IR 执行后，输出和返回值与 clang 一致即可

**Task 4 任务清单**：
- 输入：LLVM IR（O0 级别）
- 输出：优化后的 LLVM IR
- 评分：`score = sqrt(标准时间/学生时间) * 100`（正确性优先）
- 禁止：直接调用 LLVM 内置 Transform Pass

**Task 5 任务清单**：
- 输入：LLVM IR
- 输出：RV64 汇编（.s 文件）
- **只需实现 4 个函数**：`emitBinary`、`emitICmpInst`、`emitLoadInst`、`emitStoreInst`
- 框架已实现：函数序言/尾声、分支跳转、函数调用、PHI 处理
- 使用 `emitMC` 和 `emitV*` 辅助函数生成 MIR

### Step 4: 补测模型列表

**OpenHands SDK 补测**：
- gemini-2.5-pro
- gemini-2.5-flash
- gemini-2.0-flash
- 其他 aihub 支持的模型

**mimo-v2.5-pro 多后端**：
- openhands（已完成）
- claude-code（已安装，需配置）
- codex（已安装，需配置）
- kimi-cli（已安装）
- gemini-cli（已安装，用 pro 账号）

### Step 5: 执行顺序

1. 目录整理（重构 src/data/eval 结构）
2. 文档精简注入（修改 run_openhands.py 的 build_task_prompt）
3. 指标公式实现（修改 Leaderboard 计算逻辑）
4. 补测模型（后台并行运行）
5. mimo 多后端测试
6. 更新 Leaderboard + push
