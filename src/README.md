# EvoBench OpenHands CLI（`main.py`）

统一入口为 [`main.py`](main.py)。在仓库根目录执行时推荐：

```bash
python src/main.py <子命令> ...
```

容器内由宿主机 `launch` 拼出的命令为 `python3 src/main.py runner openhands ...`（工作目录为挂载后的仓库根）。

---

## 子命令一览

| 子命令 | 作用 |
|--------|------|
| `launch` | 宿主机：解析实验、冲突预检、Docker 并行启动评测容器 |
| `runner` | 容器内：OpenHands 执行评测（一般由 `launch` 调用） |
| `summarize` | 读取已有 `eval/container-runs/<run_id>/` 产物，生成汇总 JSON/CSV 或单 run 诊断 |
| `job_manage` | 展示/删除 run 痕迹，或检查 `run_id` 是否与已有目录/容器冲突 |
| `resume` | 在容器仍存在时尝试 `docker start -a` 复用同一容器续跑（见下文说明） |

---

## 现有行为与输出契约（单次 run 目录）

宿主机 `launch` 与容器内 `runner` 共同保证原始产物；`summarize` **只读**这些目录，不代为生成 run 内原始文件。

单次任务输出目录（默认在 `eval/container-runs/<run_id>/`）通常包含：

- **`metadata.json`**：启动参数、`run_id`、`model`、`tasks`、`context_mode`、镜像、`api_base`、cache 配置、容器名、`started_at`、`max_iterations`、`max_agent_hours` 等。
- **`exit_code`**：容器主进程退出码；`124` 表示外层 `timeout` 超时。
- **`console.log`**：容器内 `runner` 的标准输出/错误重定向（由 `launch` 拼入的内层 shell 重定向）。
- **`openhands_report.json`**：benchmark、backend、model、`context_mode`、`run_id`、时间戳、耗时、`tasks`、`metrics` 等。
- **`openhands-events/*.jsonl`**：runner 记录的 OpenHands 事件流。
- **`openhands-state/<task\|pipeline>/<uuid>/base_state.json`**：OpenHands 持久化状态、`execution_status`、LLM 用量与费用等。
- **`openhands-state/.../events/event-*.json`**：OpenHands 内部事件，可用于诊断末尾 `ConversationErrorEvent` 等。

汇总文件形态与计划一致：由 **`summarize`**（默认行为，等同原 `aggregate`）生成，例如：

- `eval/container-runs-summary-pipeline.json` / `.csv`
- `eval/container-runs-summary-per-task.json` / `.csv`

默认前缀规则：当未指定 `--output-prefix` 时，若本次扫描到的 run 的 `context_mode` 全部为 `pipeline`，则写入 `...-pipeline`；否则为 `...-per-task`。若无任何 run 目录，则回退到 `eval/container-runs-summary`。

汇总表字段类别（与实现中 `FIELDNAMES` 一致，可随版本扩展）：

- **身份**：`model`、`run_id`、`tasks`、`context_mode`、`backend`、`image`、`container`、`api_base`、`started_at`
- **进程与容器**：`exit_code`、`container_status`、`container_exists`、`output_dir_exists`、`has_report`、`has_openhands_state`、`has_openhands_events`
- **终止诊断**：`termination_status`、`termination_reason`、`termination_detail`、`openhands_execution_status`、`last_error_code`、`last_error_kind`、`last_error_detail`、`interrupted_by_user`
- **额度**：`max_iterations`、`successful_llm_calls`、`failed_llm_calls`、`remaining_iterations`、`max_agent_hours`、`elapsed_seconds`、`remaining_wall_seconds`
- **LLM 用量**：`llm_metrics_source`、`llm_tokens_logged`、`run_llm_*_tokens`、`run_llm_cost_usd` 等
- **Cache**：`cache_prompt_enabled`、`prompt_cache_retention`、`cache_hit`、`cache_hit_tokens`、`cache_write_tokens`
- **分数与 task**：`pipeline_score`、`mean_reward`、`pass_score`、`prior_non_full_score_count`、`zero_shot_pass`、`task0`…`task5`、`*_passed`、`*_agent_llm_rounds`、`*_llm_total_tokens`、`*_llm_cost_usd`
- **完整性**：`missing_outputs`、`report_task_count`、`expected_task_count`、`error`

**终止分析优先级**（实现逻辑）：先读 `metadata.json` 与 `exit_code`，再读 `openhands_report.json`，再读 `openhands-state/**/base_state.json`，并结合 state 中的错误事件等。

**说明**：已不再提供 Bash 时代的 `--no-resurrect`；runner 侧不再做「失败后第二轮修复」；CMake 初始化时 `TASK2_REVIVE`…`TASK5_REVIVE` 固定为 **ON**。汇总中的 `prior_non_full_score_count` 表示前序 task 中未满分的个数（语义上替代旧版「复活次数」类指标）。

---

## `launch`：启动评测

### 通用用法

```bash
python src/main.py launch [可选 test-cache] [选择项] [选项]
```

**实验规格** `MODEL[:TASKS[:RUN_ID]]`：

- 未写 `TASKS` 时用全局 `--tasks`（默认 `0-5`）。
- 未写 `RUN_ID` 时由 `--run-prefix` 与模型名、任务范围自动生成并做安全字符清洗；批次内 `run_id` 不得重复。

**选择项（可组合）**

- `--all-models`：从 `--models-file`（默认仓库根 `models.json`）读取全部模型，每个展开为 `MODEL:<默认tasks>`。
- `--model MODEL`：可重复多次，每次加一个模型。
- `--models m1,m2,...`：逗号分隔批量加模型。
- `--experiment SPEC`：可重复，显式追加 `MODEL[:TASKS[:RUN_ID]]`。
- `--experiments-file FILE`：每行一条 spec，空行与 `#` 开头行忽略。

**常用选项**

| 选项 | 说明 |
|------|------|
| `--models-file` | 默认 `models.json` |
| `--api-keys` | 默认 `api_keys.local.md`（Markdown 表格：`名字 \| 模型 \| Key`） |
| `--api-base` | 显式 API Base；否则 `OPENAI_API_BASE` 或 `.env` 或默认网关 |
| `--image` | 默认 `evobench-openhands:latest` |
| `--output-dir` | run 根目录，默认 `eval/container-runs` |
| `--tasks` | 仅作用于「未在 spec 中写 tasks」的模型展开，默认 `0-5` |
| `--max-iterations` | 传入容器内 runner，默认 `200` |
| `--context-mode` | `per-task` 或 `pipeline` |
| `--parallel` | 并行容器数，默认 `2` |
| `--run-prefix` | 生成 `run_id` 的前缀，默认 `run-YYYYmmdd-HHMMSS` |
| `--prompt-cache-retention` | 默认 `24h`，注入容器环境 |
| `--litellm-log-level` | 可选，如 `DEBUG` |
| `--max-agent-hours` | 容器内 `timeout` 墙钟上限（小时），`0` 表示不包 timeout |
| `--dry-run` | 只打印计划，不启动容器 |

**冲突预检**：启动前对本次批次所有 `run_id` 检查「输出子目录已存在」或「名为 `oh-<run_id>` 的容器已存在」；任一冲突则**整批不启动**，退出码 `2`。

**实时输出**：并行运行时在终端用 `\r` 刷新同一行，展示各 run 的额度、**墙钟已用/剩余**与分数摘要（格式如 `run_id: calls N/M fail K used=125s rem=3480s scores [task0,...,task5]`；未设 `--max-agent-hours` 或为 `0` 时 `rem=--`）。刷新周期由 `launch.py` 中常量 `STATUS_REFRESH_SEC` 控制（默认约 **1 秒**；可改为 `5.0` 以降低对输出目录的扫描频率）。实时行使用轻量快照（不每次 `docker inspect`、不扫全量 token 用量），`calls` 与 `openhands-events` 中的 LLM 事件对齐。**每个容器结束**时会换行打印 `exit_code`，并立刻再打印一行 `[状态]` 为当前所有已启动 run 的**最新**同格式摘要。**整批全部结束后**会再输出 `[本轮结束 · 最终状态]` 及同一格式的最终一行（不截断宽度，便于复制保存）。

**Ctrl+C**：向已启动容器发送 `docker stop`；各 run 的 `exit_code` 由后台 `docker wait` 写入。

### 示例

```bash
# 全模型 + 默认任务
python src/main.py launch --all-models --parallel 4

# 多模型 + 默认任务范围
python src/main.py launch --models qwen3.6-plus,glm-5 --parallel 2

# 显式实验与自定义 run_id
python src/main.py launch \
  --experiment qwen3.6-plus:0-2:qwen-t0t2 \
  --experiment glm-5:3-5:glm-t3t5

# 从文件读入多行 spec
python src/main.py launch --experiments-file path/to/experiments.txt

# 仅打印计划
python src/main.py launch --model mimo-v2.5-pro --dry-run
```

### `launch test-cache`

测试指定模型在开启 prompt cache 后是否出现 cache read 等命中信号（跑最小任务集，具体 `tasks` 由你指定）：

```bash
python src/main.py launch test-cache --model <MODEL> --tasks <TASKS> [--run-id <RUN_ID>] [其它与 launch 相同的通用选项]
```

- 使用与 `launch` 相同的 `--output-dir`、`--api-keys`、`--context-mode`、`--max-iterations`、`--max-agent-hours`、`--dry-run` 等。
- `--run-id` 仅用于 test-cache；未指定时自动生成 `<run-prefix>-<safe_model>-tasks-<safe_tasks>`。

---

## `runner`：容器内执行（通常不手动调用）

```bash
python src/main.py runner openhands \
  --model <MODEL> \
  --tasks <RANGE> \
  --max-iterations <N> \
  --context-mode per-task|pipeline \
  --workspace /workspace/YatCC \
  --run-id <RUN_ID> \
  --output-dir /workspace/output \
  [--no-caching-prompt] \
  [--prompt-cache-retention <STR>]
```

- 任务用户消息中会附带**资源预算**句：根据 `--output-dir/metadata.json` 的 `started_at` 与 `max_agent_hours` 估算剩余墙钟；**pipeline** 模式下根据 `openhands-events/pipeline.jsonl` 已出现的 `llm_response_id` 估算剩余迭代轮数；**per-task** 下每个 Task 为新对话，剩余轮数即 `--max-iterations`（见 `lib/_run_budget.py`）。

辅助子命令：

```bash
python src/main.py runner score --workspace <YatCC根> --task <0-5>
python src/main.py runner emit-report --output-dir <DIR> [--model ...] [--run-id ...] [--context-mode ...]
```

**`build_task_prompt` 上下文要点**（与计划一致，最终实现于 `core/_prompt.py`）：

- 路径说明：`/YatCC` 文档路径与实际 workspace 等价说明。
- **资源预算**：每次向 Agent 发送任务消息前，根据 `output_dir/metadata.json`（`started_at`、`max_agent_hours`）估算剩余墙钟，并结合 `openhands-events/pipeline.jsonl`（仅 pipeline 模式）估算已用 LLM 响应数以得到**剩余轮数**；在提示中写明「你的所剩时间为…，所剩轮数为…。你应当在当前预算下，拿到尽可能高的分数。」（逻辑见 `lib/_run_budget.py`）。
- **per-task**：`task_guides.md` 全局前言 + 当前 Task 小节 + 当前 `task/<id>/README.md` + 文件列表 + 构建/评分目标与流程。
- **pipeline**：首个 task 附带完整 `task_guides.md`；后续 task 仅 README + 文件列表 + 流程。
- 发送前会去掉连续空行（压缩空行）。

---

## `summarize`：汇总与诊断

### 默认：批量生成 `container-runs-summary-*.json` / `.csv`

不写子命令时即为汇总（原 `summarize aggregate` 行为）：

```bash
python src/main.py summarize \
  [--runs-dir <DIR>] \
  [--glob '<PATTERN>'] \
  [--output-prefix <路径前缀>] \
  [--no-tokens]
```

- `--glob`：作用于 `runs-dir` 下的**子目录名**（例如 `run-pipeline-*`），不是 run 内部文件 glob。
- `--output-prefix`：写入 `<前缀>.json` 与 `<前缀>.csv`；省略时使用上文默认规则。
- `--no-tokens`：不读取 `openhands-state/**/base_state.json` 中的 token/费用列（仍会尽量从事件解析轮次等）。

### `inspect`：单 run JSON 诊断

```bash
python src/main.py summarize inspect <run_id> [--runs-dir <DIR>]
```

---

## `job_manage`：痕迹与冲突

```bash
# 列出 runs-dir 下各 run 目录对应的痕迹（JSON）
python src/main.py job_manage list [--runs-dir <DIR>]

# 展示全部或单个 run_id
python src/main.py job_manage show [--runs-dir <DIR>]
python src/main.py job_manage show <run_id> [--runs-dir <DIR>]

# 删除容器 oh-<run_id> 与目录 <runs-dir>/<run_id>
python src/main.py job_manage delete <run_id> [--runs-dir <DIR>] [--force]

# 检查若干 run_id 是否与已有目录/容器冲突（供脚本使用，有冲突时退出码 1）
python src/main.py job_manage conflicts --run-id <ID1> [--run-id <ID2> ...] [--runs-dir <DIR>]
```

---

## `resume`：断点重续（当前实现说明）

```bash
python src/main.py resume <run_id> [--runs-dir <DIR>] [--dry-run]
```

- `--dry-run`：只打印诊断信息，不启动容器。
- **当前行为**：若 `summarize` 判定为已 `success_*` 正常结束，则直接退出；否则若 Docker 仍存在同名容器 `oh-<run_id>`，则执行 `docker start -a <容器>`；容器已删除时会报错退出。
- `resume` 侧 `--max-iterations` / `--max-agent-hours` 仍为 CLI 预留项（见 `resume.py`）；任务提示中的墙钟/轮数预算以输出目录内 **`metadata.json`**（由首次 `launch` 写入）为准。

---

## 宿主机与镜像

仓库以只读卷挂载到容器内 `/workspace/EvoBench` 时，更新宿主机代码即可让容器内 `python3 src/main.py ...` 使用最新逻辑，**不必**每次重建镜像（除非你要把代码 bake 进镜像或变更镜像内依赖）。

---

## 额度与失败 LLM 调用（概念）

- **成功 LLM 调用计数**：对 `base_state` 中 `token_usages` 的 `response_id` 与 `openhands-events/*.jsonl` 中的 `llm_response_id` **分别去重**，取两者数量的**较大值**（state 写入常滞后于网关日志，避免终端「卡在」旧调用数）；若两者皆空再回退到报告 `metrics` / 事件流按模式的回合统计（见 `lib/_openhands_events.py`）。
- **失败调用**：如 `ConversationErrorEvent` / `LLMBadRequestError` 等，用于诊断与 `failed_llm_calls` 统计，**不计入**成功调用额度。

更细的公式与字段含义以代码与汇总表头为准。
