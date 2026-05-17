# EvoBench：容器内 OpenHands 并行评测

EvoBench 将 YatCC 编译实验串行为 Agent 评测流水线；**宿主机用 `launch` 起 Docker 容器**，在容器内跑 OpenHands，不在 Windows 宿主机直接跑 Agent。

**统一入口**（仓库根目录）：

```bash
python src/main.py <子命令> ...
```

容器内由宿主机拼出的命令为 `python3 src/main.py runner openhands ...`（工作目录为挂载后的仓库根）。**完整子命令、选项与输出字段说明见 [`src/README.md`](src/README.md)。**

---

## 目标

- 每个模型一个容器。
- 每个容器使用镜像内独立的 `/workspace/YatCC`，互不共享 YatCC 工作区。
- OpenHands 在容器内运行。
- 每个 run 有独立输出目录：控制台日志、评测报告、OpenHands 事件与状态等。

## 当前能力概要

- `Dockerfile.evo` 已安装 `openhands-sdk` / `openhands-tools` 及编译依赖。
- 宿主机 `python src/main.py launch`：解析实验、冲突预检、并行启动容器；容器内执行 `runner openhands`。
- 原始产物在 `eval/container-runs/<run_id>/`；`python src/main.py summarize` **只读**这些目录生成汇总 JSON/CSV。

---

## 0. 项目部署

1. 克隆本仓库到服务器（或评测机）。

2. 在 `data/YatCC/` 下准备 YatCC 源码（按课程/submodule 要求拉取；与镜像构建时复制进镜像的逻辑一致）。

3. **密钥表**：默认优先仓库根 `api_keys.local.md`，若不存在则使用 `_api_keys.local.md`（Markdown 表：名字、模型、Key、`base_url`；每行须含非空 `base_url`）。`launch` / `runner` 按 `--model` 选中行，用该行 **`base_url`** 作为 LLM 网关（写入 `metadata.json` 的 `api_base` 并注入容器）。

4. **模型列表**：可选 `models.json`，与 `--all-models` 等配合使用。

5. 上下文除镜像内文档外，宿主机侧还有 `data/task_guides.md` 等（详见 `src/README.md` 中 `build_task_prompt` 说明）。

---

## 1. 构建标准镜像

在仓库根目录执行（按需传入代理 build-arg）：

```bash
docker build -f Dockerfile.evo -t evobench-openhands:latest \
  --build-arg LLVM_BUILD_JOBS_MAX=48 \
  --build-arg HTTP_PROXY="${HTTP_PROXY}" \
  --build-arg HTTPS_PROXY="${HTTPS_PROXY}" \
  --build-arg NO_PROXY="${NO_PROXY}" \
  --build-arg http_proxy="${http_proxy}" \
  --build-arg https_proxy="${https_proxy}" \
  --build-arg no_proxy="${no_proxy}" \
  .
```

- `-f Dockerfile.evo`：评测环境镜像定义。
- `-t evobench-openhands:latest`：与下文 `launch --image` 一致即可。

构建成功后，常规评测统一使用该镜像名。

---

## 2. 启动评测（`launch`）

**常规 `launch` 须显式指定 `--image`**（`launch test-cache` 可不填镜像，缺省见 `src/README.md`）。

```bash
# 全模型（读 models.json）+ 默认任务 0-5，并行 4 个容器
python src/main.py launch --image evobench-openhands:latest --all-models --parallel 4

# 多模型 + 默认任务范围
python src/main.py launch --image evobench-openhands:latest --models qwen3.6-plus,glm-5 --parallel 2

# 显式实验与自定义 run_id
python src/main.py launch --image evobench-openhands:latest \
  --experiment qwen3.6-plus:0-2:qwen-t0t2 \
  --experiment glm-5:3-5:glm-t3t5

# 从文件读入多行 spec（空行与 # 行忽略）
python src/main.py launch --image evobench-openhands:latest --experiments-file path/to/experiments.txt

# 仅打印计划，不启容器
python src/main.py launch --image evobench-openhands:latest --model mimo-v2.5-pro --dry-run
```

### 常用选项（摘录）

| 选项 | 说明 |
|------|------|
| `--image` | **常规 launch 必填** Docker 镜像。 |
| `--models-file` | 默认 `models.json`。 |
| `--api-keys` | 默认优先 `api_keys.local.md`，否则 `_api_keys.local.md`。 |
| `--output-dir` | run 根目录，默认 `eval/container-runs`。 |
| `--tasks` | 未在 spec 中写 tasks 时的默认范围，默认 `0-5`。 |
| `--max-iterations` | 传入容器内 runner，默认 `200`。 |
| `--context-mode` | `per-task` 或 `pipeline`。 |
| `--parallel` | 并行容器数，默认 `2`。 |
| `--run-prefix` | 生成 `run_id` 的前缀。 |
| `--max-agent-hours` | 容器内墙钟上限（小时），`0` 表示不包 timeout。 |
| `--dry-run` | 只打印计划。 |

**冲突预检**：批次内所有 `run_id` 若与已有输出子目录或已有容器 `oh-<run_id>` 冲突，**整批不启动**，退出码 `2`。

**Ctrl+C**：向已启动容器发送 `docker stop`。

更多（`launch test-cache`、`runner`、`summarize`、`job_manage`、`resume`）见 [`src/README.md`](src/README.md)。

---

## 3. 单次 run 目录里有什么（摘要）

默认 `eval/container-runs/<run_id>/` 通常包含：

- `metadata.json`：启动参数、`run_id`、`model`、`tasks`、`context_mode`、镜像、`api_base` 等。
- `exit_code`：容器主进程退出码；`124` 常表示外层 `timeout` 超时。
- `console.log`：容器内 runner 标准输出/错误。
- `openhands_report.json`：benchmark、指标、耗时等。
- `openhands-events/*.jsonl`、`openhands-state/...`：事件流与 OpenHands 持久化状态（诊断用）。

汇总文件由 `python src/main.py summarize` 生成，默认前缀规则与字段类别见 [`src/README.md`](src/README.md)。

---

## 4. 宿主机与镜像代码热更新

仓库以只读卷挂载到容器内 `/workspace/EvoBench` 时，更新宿主机代码即可让容器内 `python3 src/main.py ...` 使用最新逻辑，**不必**每次重建镜像（除非变更镜像内依赖或要把代码 bake 进镜像）。

---

## License

MIT
