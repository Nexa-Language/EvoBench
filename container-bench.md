# OpenHands + YatCC 并行评测容器使用指南

目标：

- 每个模型一个容器。
- 每个容器使用镜像内独立的 `/workspace/YatCC`，互不共享 YatCC 工作区。
- OpenHands 在容器内运行，不在 Windows 宿主机跑。
- 每个 run 有独立输出目录，包含控制台日志、评测报告、OpenHands event/message JSONL。

当前已具备的能力：

- `Dockerfile.evo` 已安装 `openhands-sdk` / `openhands-tools`。
- `src/run_openhands.py` 已支持 `--run-id`、`--output-dir`。
- `src/run_openhands.py` 会把 OpenHands events 写到 `openhands-events/*.jsonl`，SDK 状态写到 `openhands-state/`。

## 0. 项目部署

将本项目拉取到服务器上。

## 1. 构建标准镜像

在 PowerShell 执行：

```powershell
Set-Location D:\YatCC\EvoBench-main
docker build -f Dockerfile.evo -t evobench-openhands:latest .
```

含义：

- `Set-Location` 进入项目根目录。
- `docker build` 构建镜像。
- `-f Dockerfile.evo` 指定使用项目里的任务环境镜像文件。
- `-t evobench-openhands:latest` 给镜像起标准名字。
- `.` 表示构建上下文是当前目录。

如果这一步成功，以后本机跑 OpenHands bench 就统一用 `evobench-openhands:latest`。

## 2. 准备输出目录

```powershell
New-Item -ItemType Directory -Force .\eval\container-runs
```

含义：

- 创建宿主机输出根目录。
- `-Force` 表示目录已存在也不报错。

## 3. 启动一个模型的容器

示例：跑 `mimo-v2.5-pro`。

```powershell
$RUN_ID = "mimo-v25pro-001"
$MODEL = "mimo-v2.5-pro"

New-Item -ItemType Directory -Force ".\eval\container-runs\$RUN_ID"

docker run -d `
  --name "oh-$RUN_ID" `
  --env-file ".env" `
  -e OPENAI_MODEL_NAME="$MODEL" `
  -v "${PWD}:/workspace/EvoBench" `
  -v "${PWD}\eval\container-runs\${RUN_ID}:/workspace/output" `
  -w /workspace/EvoBench `
  evobench-openhands:latest `
  sleep infinity
```

含义：

- `$RUN_ID` 是本次评测唯一 ID，用于容器名和输出目录。
- `$MODEL` 是要测的模型名。
- `--name "oh-$RUN_ID"` 让容器名可读，例如 `oh-mimo-v25pro-001`。
- `--env-file ".env"` 把项目 `.env` 注入容器环境。
- `-e OPENAI_MODEL_NAME="$MODEL"` 单独覆盖模型名。
- `-v "${PWD}:/workspace/EvoBench"` 把 EvoBench 项目挂进容器，容器内可以执行 `src/run_openhands.py`。
- `-v "...:/workspace/output"` 把本次结果目录挂进容器。
- 注意：这里没有把 `data/YatCC` 挂到 `/workspace/YatCC`，所以容器使用的是镜像内独立的 YatCC 副本，适合并行。

## 4. 在容器里启动 OpenHands bench

```powershell
docker exec -d "oh-$RUN_ID" bash -lc "python3 src/run_openhands.py --model $MODEL --tasks 0-5 --max-iterations 200 --workspace /workspace/YatCC --run-id $RUN_ID --output-dir /workspace/output > /workspace/output/console.log 2>&1"
```

含义：

- `docker exec -d` 在已有容器里后台启动命令。
- `bash -lc "..."` 用 Linux shell 执行完整命令。
- `python3 src/run_openhands.py` 在容器内运行 OpenHands 评测脚本。
- `--workspace /workspace/YatCC` 指定 OpenHands 的工作区为容器内独立 YatCC。
- `--output-dir /workspace/output` 把报告、events、state 写到挂载出来的宿主机目录。
- `> /workspace/output/console.log 2>&1` 把标准输出和错误输出都写入 `console.log`。

## 5. 并行启动另一个模型

只换 `RUN_ID` 和 `MODEL`，重复第 3、4 步：

```powershell
$RUN_ID = "qwen36-plus-001"
$MODEL = "qwen3.6-plus"

New-Item -ItemType Directory -Force ".\eval\container-runs\$RUN_ID"

docker run -d `
  --name "oh-$RUN_ID" `
  --env-file ".env" `
  -e OPENAI_MODEL_NAME="$MODEL" `
  -v "${PWD}:/workspace/EvoBench" `
  -v "${PWD}\eval\container-runs\${RUN_ID}:/workspace/output" `
  -w /workspace/EvoBench `
  evobench-openhands:latest `
  sleep infinity

docker exec -d "oh-$RUN_ID" bash -lc "python3 src/run_openhands.py --model $MODEL --tasks 0-5 --max-iterations 200 --workspace /workspace/YatCC --run-id $RUN_ID --output-dir /workspace/output > /workspace/output/console.log 2>&1"
```

这样两个模型会在两个容器内各自使用独立的 `/workspace/YatCC`，不会互相污染。

## 6. 查看运行状态和输出

查看容器是否在：

```powershell
docker ps --filter "name=oh-"
```

看某次运行的控制台日志：

```powershell
Get-Content ".\eval\container-runs\$RUN_ID\console.log" -Wait
```

查看 OpenHands 实时 event/message：

```powershell
Get-Content ".\eval\container-runs\$RUN_ID\openhands-events\task0.jsonl" -Wait
```

运行完成后看报告：

```powershell
Get-Content ".\eval\container-runs\$RUN_ID\openhands_report.json"
```

合并所有 OpenHands events：

```powershell
Get-Content ".\eval\container-runs\$RUN_ID\openhands-events\*.jsonl" |
  Set-Content ".\eval\container-runs\$RUN_ID\all-openhands-events.jsonl"
```

输出目录结构大致是：

```text
eval/container-runs/<RUN_ID>/
  console.log
  openhands_report.json
  openhands-events/
    task0.jsonl
    task1.jsonl
    ...
  openhands-state/
    task0/
    task1/
    ...
```

其中：

- `console.log`：人类可读运行日志。
- `openhands_report.json`：评测汇总。
- `openhands-events/*.jsonl`：OpenHands 运行时产生的 event/message/action/observation 数据。
- `openhands-state/`：OpenHands SDK 的持久化状态，可用于后续排查。

## 7. 进入容器手动操作

```powershell
docker exec -it "oh-$RUN_ID" bash
```

含义：

- `-it` 进入交互终端。
- 进去后默认可手动检查 `/workspace/YatCC`、`/workspace/output`、运行 `cmake` 等。

例如容器里可执行：

```bash
cd /workspace/YatCC
cmake --build build -t task0-score
```

## 8. 结束和清理

停止容器：

```powershell
docker stop "oh-$RUN_ID"
```

删除已停止容器：

```powershell
docker rm "oh-$RUN_ID"
```

如果你还想保留容器现场，先不要 `docker rm`，只看输出目录即可。

## 最重要的使用规则

并行时不要让多个 agent 挂同一个宿主机 `data\YatCC`。

推荐做法就是上面这套：每个容器用镜像内自己的 `/workspace/YatCC`，只把 EvoBench 脚本和输出目录挂进去。这样最符合需求：多个 OpenHands agent 并行工作，但每个都有独立 YatCC 实验环境。
