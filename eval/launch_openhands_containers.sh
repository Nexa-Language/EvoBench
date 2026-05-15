#!/usr/bin/env bash
# Launch OpenHands EvoBench experiments in parallel Docker containers.
#
# Examples:
#   eval/launch_openhands_containers.sh --all-models --parallel 4
#   eval/launch_openhands_containers.sh --models qwen3.6-plus,glm-5 --tasks 0-5 --parallel 2
#   eval/launch_openhands_containers.sh --experiment qwen3.6-plus:0-2 --experiment glm-5:3-5:glm5-t3t5
#   eval/launch_openhands_containers.sh --experiments-file eval/openhands_experiments.txt
#
# Experiment spec format:
#   MODEL[:TASKS[:RUN_ID]]
#
# Each experiment gets a fresh container and uses the image's /workspace/YatCC.
# The host only mounts this repository read-only and a per-run output directory.
set -euo pipefail

find_repo_root() {
  local start="$1"
  while [[ "$start" != "/" ]]; do
    if [[ -f "$start/models.json" && -d "$start/data/YatCC" ]]; then
      printf '%s\n' "$start"
      return 0
    fi
    start="$(dirname "$start")"
  done
  return 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(find_repo_root "$SCRIPT_DIR" || find_repo_root "$PWD" || true)"
if [[ -z "$ROOT_DIR" ]]; then
  echo "错误: 无法定位 EvoBench 项目根目录，请在仓库内执行脚本。" >&2
  exit 1
fi
MODELS_FILE="$ROOT_DIR/models.json"
API_KEYS_FILE="$ROOT_DIR/api_keys.local.md"
IMAGE="${IMAGE:-evobench-openhands:latest}"
OUTPUT_ROOT="$ROOT_DIR/eval/container-runs"
API_BASE="${OPENAI_API_BASE:-}"
DEFAULT_TASKS="0-5"
MAX_ITERATIONS=200
CONTEXT_MODE="per-task"
PARALLEL=2
RUN_PREFIX="run-$(date +%Y%m%d-%H%M%S)"
DRY_RUN=0
REMOVE_CONTAINERS=0
NO_RESURRECT=0
ALL_MODELS=0
CACHE_PROMPT=1
PROMPT_CACHE_RETENTION=24h
MAX_AGENT_HOURS=6
LITELLM_LOG_LEVEL=""

declare -a MODEL_SELECTION=()
declare -a EXPERIMENT_SPECS=()
declare -a EXP_MODELS=()
declare -a EXP_TASKS=()
declare -a EXP_RUN_IDS=()
declare -a KEY_NAMES=()
declare -a KEY_MODELS=()
declare -a KEY_VALUES=()
declare -a RUNNING_CONTAINERS=()

usage() {
  cat <<EOF
Usage:
  $0 [selection] [options]

Selection:
  --all-models                       Run every model in models.json.
  --model MODEL                      Add one model, can be repeated.
  --models MODEL1,MODEL2             Add comma-separated models.
  --experiment MODEL[:TASKS[:RUN_ID]]
                                     Add one explicit experiment.
  --experiments-file FILE            Read experiment specs, one per line.

Options:
  --models-file FILE                 Default: models.json.
  --api-keys FILE                    Default: api_keys.local.md.
  --api-base URL                     Default: OPENAI_API_BASE, .env, or https://aihub.arcsysu.cn/v1.
  --image IMAGE                      Default: evobench-openhands:latest.
  --output-dir DIR                   Default: eval/container-runs.
  --tasks TASKS                      Default tasks for selected models, e.g. 0-5 or 0,2,4.
  --max-iterations N                 Default: 200.
  --context-mode MODE                per-task or pipeline. Default: per-task.
                                     per-task: every Task starts with an empty Conversation.
                                     pipeline: one Conversation completes all selected Tasks.
  --parallel N                       Default: 2.
  --run-prefix PREFIX                Default: run-YYYYmmdd-HHMMSS.
  --cache-prompt                     Enable OpenHands LLM prompt caching (default on).
  --no-cache-prompt                  Disable prompt caching (OPENHANDS_CACHING_PROMPT=0).
  --prompt-cache-retention STR       OPENHANDS_PROMPT_CACHE_RETENTION (default: 24h; Anthropic系常见 5m/1h).
  --litellm-log-level LEVEL          e.g. DEBUG — 传入容器为 LITELLM_LOG_LEVEL（默认不传）。
  --max-agent-hours N                Wall-clock cap inside the container (GNU timeout). Default: 6. Use 0 for no limit.
  --no-resurrect                     Pass --no-resurrect to src/run_openhands.py.
  --remove-containers                Remove containers after they exit. Outputs are kept.
  --dry-run                          Print the plan without starting containers.
  -h, --help                         Show this help.

API key selection:
  The script parses a markdown table with columns: 名字 | 模型 | Key.
  It chooses an exact model key first, then a key whose name/model starts with MODEL-,
  then an 'all' fallback key. Key values are never printed.

Prompt cache (OpenHands LLM):
  Containers get OPENHANDS_CACHING_PROMPT=1 and OPENHANDS_PROMPT_CACHE_RETENTION (default 24h) unless disabled.
  IMPORTANT: Many OpenAI-compatible gateways (including common Qwen routes) do NOT implement server-side
  prompt caching; cache_read_tokens staying at 0 is then expected — not a launch-script bug.
  Anthropic-style TTLs are often 5m or 1h; OpenHands may accept other strings — check your provider.
  Optional: set OPENHANDS_LITELLM_EXTRA_BODY in .env to a JSON object for LiteLLM extra_body (advanced).
  Use --no-cache-prompt to turn off. This script passes -e after --env-file so these variables override
  .env for the same keys when Docker applies later -e over env-file.

Agent wall time:
  Each container runs: timeout Nh ... run_openhands.py (default N=6). Exit code 124 means timeout.
  Set --max-agent-hours 0 to disable the timeout wrapper.
EOF
}

die() {
  echo "错误: $*" >&2
  exit 1
}

trim() {
  local value="$*"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

safe_name() {
  local value="$1"
  value="$(printf '%s' "$value" | sed -E 's/[^A-Za-z0-9_.-]+/-/g; s/^-+//; s/-+$//')"
  printf '%s' "${value:-run}"
}

load_env_api_base() {
  if [[ -n "$API_BASE" ]]; then
    return
  fi
  if [[ -f "$ROOT_DIR/.env" ]]; then
    API_BASE="$(
      awk -F= '
        /^[[:space:]]*OPENAI_API_BASE[[:space:]]*=/ {
          value=$0
          sub(/^[^=]*=/, "", value)
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
          gsub(/^["'"'"']|["'"'"']$/, "", value)
          print value
          exit
        }
      ' "$ROOT_DIR/.env"
    )"
  fi
  API_BASE="${API_BASE:-https://aihub.arcsysu.cn/v1}"
}

load_models_from_json() {
  [[ -f "$MODELS_FILE" ]] || die "找不到模型文件: $MODELS_FILE"
  python3 - "$MODELS_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, list):
    raise SystemExit(f"{path} must contain a JSON array")
for item in data:
    if isinstance(item, str) and item.strip():
        print(item.strip())
PY
}

add_models_csv() {
  local csv="$1"
  local item
  IFS=',' read -ra items <<<"$csv"
  for item in "${items[@]}"; do
    item="$(trim "$item")"
    [[ -n "$item" ]] && MODEL_SELECTION+=("$item")
  done
}

add_experiment_spec() {
  local spec="$1"
  spec="$(trim "$spec")"
  [[ -z "$spec" || "$spec" == \#* ]] && return
  EXPERIMENT_SPECS+=("$spec")
}

read_experiments_file() {
  local file="$1"
  [[ -f "$file" ]] || die "找不到实验列表文件: $file"
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    add_experiment_spec "$line"
  done <"$file"
}

parse_experiments() {
  local spec model tasks run_id safe_model safe_tasks

  for model in "${MODEL_SELECTION[@]}"; do
    spec="$model:$DEFAULT_TASKS"
    EXPERIMENT_SPECS+=("$spec")
  done

  [[ "${#EXPERIMENT_SPECS[@]}" -gt 0 ]] || die "没有选择实验。请使用 --model、--models、--experiment、--experiments-file 或 --all-models。"

  local seen=""
  for spec in "${EXPERIMENT_SPECS[@]}"; do
    IFS=':' read -r model tasks run_id _extra <<<"$spec"
    model="$(trim "${model:-}")"
    tasks="$(trim "${tasks:-}")"
    run_id="$(trim "${run_id:-}")"
    [[ -n "$model" ]] || die "实验缺少模型名: $spec"
    tasks="${tasks:-$DEFAULT_TASKS}"
    safe_model="$(safe_name "$model")"
    safe_tasks="$(safe_name "$tasks")"
    run_id="${run_id:-${RUN_PREFIX}-${safe_model}-tasks-${safe_tasks}}"
    run_id="$(safe_name "$run_id")"

    if [[ " $seen " == *" $run_id "* ]]; then
      die "run id 重复: $run_id"
    fi
    seen+=" $run_id"

    EXP_MODELS+=("$model")
    EXP_TASKS+=("$tasks")
    EXP_RUN_IDS+=("$run_id")
  done
}

load_api_keys() {
  [[ -f "$API_KEYS_FILE" ]] || die "找不到 API key 文件: $API_KEYS_FILE"
  local line name model key
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == \|* ]] || continue
    [[ "$line" == *"---"* ]] && continue
    IFS='|' read -r _ name model key _rest <<<"$line"
    name="$(trim "${name:-}")"
    model="$(trim "${model:-}")"
    key="$(trim "${key:-}")"
    [[ -n "$name" && -n "$model" && "$key" == sk-* ]] || continue
    KEY_NAMES+=("$name")
    KEY_MODELS+=("$model")
    KEY_VALUES+=("$key")
  done <"$API_KEYS_FILE"

  [[ "${#KEY_VALUES[@]}" -gt 0 ]] || die "API key 文件中没有可用 key: $API_KEYS_FILE"
}

select_key_index() {
  local model="$1"
  local i

  for i in "${!KEY_VALUES[@]}"; do
    if [[ "${KEY_MODELS[$i]}" == "$model" ]]; then
      printf '%s' "$i"
      return
    fi
  done

  for i in "${!KEY_VALUES[@]}"; do
    if [[ "${KEY_NAMES[$i]}" == "$model-"* || "${KEY_MODELS[$i]}" == "$model-"* ]]; then
      printf '%s' "$i"
      return
    fi
  done

  for i in "${!KEY_VALUES[@]}"; do
    if [[ "${KEY_MODELS[$i]}" == "all" ]]; then
      printf '%s' "$i"
      return
    fi
  done

  return 1
}

print_plan() {
  echo "=========================================="
  echo "  OpenHands Docker 并行评测计划"
  echo "  镜像: $IMAGE"
  echo "  API Base: $API_BASE"
  echo "  上下文模式: $CONTEXT_MODE"
  echo "  并行数: $PARALLEL"
  echo "  每 Task 最大迭代轮次: $MAX_ITERATIONS（传入容器内 run_openhands.py --max-iterations）"
  echo "  输出目录: $OUTPUT_ROOT"
  echo "  LLM 提示词缓存: CACHE_PROMPT=$CACHE_PROMPT PROMPT_CACHE_RETENTION=$PROMPT_CACHE_RETENTION"
  if [[ -n "${LITELLM_LOG_LEVEL:-}" ]]; then
    echo "  LiteLLM 日志级别: $LITELLM_LOG_LEVEL"
  fi
  if [[ "$MAX_AGENT_HOURS" -eq 0 ]]; then
    echo "  Agent 最长运行: 不限制"
  else
    echo "  Agent 最长运行: ${MAX_AGENT_HOURS}h（容器内 timeout）"
  fi
  echo "=========================================="

  local i key_idx
  for i in "${!EXP_MODELS[@]}"; do
    key_idx="$(select_key_index "${EXP_MODELS[$i]}")" || die "模型 ${EXP_MODELS[$i]} 找不到匹配 key，也没有 all fallback"
    echo "[$((i + 1))/${#EXP_MODELS[@]}] model=${EXP_MODELS[$i]} tasks=${EXP_TASKS[$i]} run_id=${EXP_RUN_IDS[$i]} key=${KEY_NAMES[$key_idx]}"
  done
}

launch_experiment() {
  local model="$1"
  local tasks="$2"
  local run_id="$3"
  local key_idx="$4"
  local api_key="${KEY_VALUES[$key_idx]}"
  local output_dir="$OUTPUT_ROOT/$run_id"
  local container="oh-$run_id"
  local inner_cmd cmd resurrect_arg env_file_args=()

  mkdir -p "$output_dir"

  resurrect_arg=""
  if [[ "$NO_RESURRECT" -eq 1 ]]; then
    resurrect_arg="--no-resurrect"
  fi

  printf -v inner_cmd 'python3 src/run_openhands.py --model %q --tasks %q --max-iterations %q --context-mode %q --workspace /workspace/YatCC --run-id %q --output-dir /workspace/output %s > /workspace/output/console.log 2>&1' \
    "$model" "$tasks" "$MAX_ITERATIONS" "$CONTEXT_MODE" "$run_id" "$resurrect_arg"

  if [[ "$MAX_AGENT_HOURS" -gt 0 ]]; then
    printf -v cmd 'timeout --signal=TERM --kill-after=120 %qh bash -c %q' "$MAX_AGENT_HOURS" "$inner_cmd"
  else
    cmd="$inner_cmd"
  fi

  cat >"$output_dir/metadata.json" <<EOF
{
  "run_id": "$run_id",
  "model": "$model",
  "tasks": "$tasks",
  "context_mode": "$CONTEXT_MODE",
  "max_iterations": $MAX_ITERATIONS,
  "image": "$IMAGE",
  "api_base": "$API_BASE",
  "api_key_name": "${KEY_NAMES[$key_idx]}",
  "llm_cache_prompt": $CACHE_PROMPT,
  "prompt_cache_retention": "$PROMPT_CACHE_RETENTION",
  "litellm_log_level": "${LITELLM_LOG_LEVEL:-}",
  "max_agent_hours": $MAX_AGENT_HOURS,
  "container": "$container",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

  if [[ -f "$ROOT_DIR/.env" ]]; then
    env_file_args=(--env-file "$ROOT_DIR/.env")
  fi

  local litellm_docker_env=()
  if [[ -n "${LITELLM_LOG_LEVEL:-}" ]]; then
    litellm_docker_env+=(-e "LITELLM_LOG_LEVEL=$LITELLM_LOG_LEVEL")
  fi

  echo "[启动] $run_id | model=$model | tasks=$tasks | key=${KEY_NAMES[$key_idx]}"
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker run -d --name "$container" \
    "${env_file_args[@]}" \
    -e OPENAI_API_BASE="$API_BASE" \
    -e OPENAI_API_KEY="$api_key" \
    -e OPENAI_MODEL_NAME="$model" \
    -e OPENHANDS_CACHING_PROMPT="$CACHE_PROMPT" \
    -e OPENHANDS_PROMPT_CACHE_RETENTION="$PROMPT_CACHE_RETENTION" \
    "${litellm_docker_env[@]}" \
    -v "$ROOT_DIR:/workspace/EvoBench:ro" \
    -v "$output_dir:/workspace/output" \
    -w /workspace/EvoBench \
    "$IMAGE" \
    bash -lc "$cmd" >/dev/null

  RUNNING_CONTAINERS+=("$container")

  (
    set +e
    local exit_code
    exit_code="$(docker wait "$container")"
    printf '%s\n' "$exit_code" >"$output_dir/exit_code"
    if [[ "$REMOVE_CONTAINERS" -eq 1 ]]; then
      docker rm "$container" >/dev/null 2>&1 || true
    fi
    exit "$exit_code"
  ) &
}

stop_running_containers() {
  if [[ "${#RUNNING_CONTAINERS[@]}" -gt 0 ]]; then
    echo ""
    echo "收到中断信号，停止已启动容器..."
    docker stop "${RUNNING_CONTAINERS[@]}" >/dev/null 2>&1 || true
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all-models)
      ALL_MODELS=1
      shift
      ;;
    --model)
      MODEL_SELECTION+=("${2:?--model 需要模型名}")
      shift 2
      ;;
    --models)
      add_models_csv "${2:?--models 需要逗号分隔模型名}"
      shift 2
      ;;
    --experiment)
      add_experiment_spec "${2:?--experiment 需要实验规格}"
      shift 2
      ;;
    --experiments-file)
      read_experiments_file "${2:?--experiments-file 需要文件路径}"
      shift 2
      ;;
    --models-file)
      MODELS_FILE="${2:?--models-file 需要文件路径}"
      shift 2
      ;;
    --api-keys)
      API_KEYS_FILE="${2:?--api-keys 需要文件路径}"
      shift 2
      ;;
    --api-base)
      API_BASE="${2:?--api-base 需要 URL}"
      shift 2
      ;;
    --image)
      IMAGE="${2:?--image 需要镜像名}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_ROOT="${2:?--output-dir 需要目录}"
      shift 2
      ;;
    --tasks)
      DEFAULT_TASKS="${2:?--tasks 需要任务范围}"
      shift 2
      ;;
    --max-iterations)
      MAX_ITERATIONS="${2:?--max-iterations 需要数字}"
      shift 2
      ;;
    --context-mode)
      CONTEXT_MODE="${2:?--context-mode 需要 per-task 或 pipeline}"
      shift 2
      ;;
    --parallel)
      PARALLEL="${2:?--parallel 需要数字}"
      shift 2
      ;;
    --run-prefix)
      RUN_PREFIX="${2:?--run-prefix 需要前缀}"
      shift 2
      ;;
    --cache-prompt)
      CACHE_PROMPT=1
      shift
      ;;
    --no-cache-prompt)
      CACHE_PROMPT=0
      shift
      ;;
    --prompt-cache-retention)
      PROMPT_CACHE_RETENTION="${2:?--prompt-cache-retention 需要参数（可为空字符串）}"
      shift 2
      ;;
    --litellm-log-level)
      LITELLM_LOG_LEVEL="${2:?--litellm-log-level 需要级别，如 DEBUG 或 INFO}"
      shift 2
      ;;
    --max-agent-hours)
      MAX_AGENT_HOURS="${2:?--max-agent-hours 需要非负整数（0=不限制）}"
      shift 2
      ;;
    --no-resurrect)
      NO_RESURRECT=1
      shift
      ;;
    --remove-containers)
      REMOVE_CONTAINERS=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数: $1"
      ;;
  esac
done

[[ "$PARALLEL" =~ ^[0-9]+$ && "$PARALLEL" -ge 1 ]] || die "--parallel 必须是正整数"
[[ "$MAX_ITERATIONS" =~ ^[0-9]+$ && "$MAX_ITERATIONS" -ge 1 ]] || die "--max-iterations 必须是正整数"
[[ "$MAX_AGENT_HOURS" =~ ^[0-9]+$ ]] || die "--max-agent-hours 必须为非负整数（0 表示不限制）"
[[ "$CONTEXT_MODE" == "per-task" || "$CONTEXT_MODE" == "pipeline" ]] || die "--context-mode 必须是 per-task 或 pipeline"

if [[ "$ALL_MODELS" -eq 1 ]]; then
  while IFS= read -r model; do
    MODEL_SELECTION+=("$model")
  done < <(load_models_from_json)
fi

load_env_api_base
load_api_keys
parse_experiments
print_plan

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo ""
  echo "dry-run：未启动容器。"
  exit 0
fi

mkdir -p "$OUTPUT_ROOT"
trap stop_running_containers INT TERM

active=0
failures=0
for i in "${!EXP_MODELS[@]}"; do
  key_idx="$(select_key_index "${EXP_MODELS[$i]}")" || die "模型 ${EXP_MODELS[$i]} 找不到匹配 key，也没有 all fallback"
  launch_experiment "${EXP_MODELS[$i]}" "${EXP_TASKS[$i]}" "${EXP_RUN_IDS[$i]}" "$key_idx"
  active=$((active + 1))

  if [[ "$active" -ge "$PARALLEL" ]]; then
    if ! wait -n; then
      failures=$((failures + 1))
    fi
    active=$((active - 1))
  fi
done

while [[ "$active" -gt 0 ]]; do
  if ! wait -n; then
    failures=$((failures + 1))
  fi
  active=$((active - 1))
done

echo ""
echo "=========================================="
echo "  OpenHands Docker 并行评测完成"
echo "  输出目录: $OUTPUT_ROOT"
echo "  失败容器数: $failures"
echo "  查看日志: tail -f $OUTPUT_ROOT/<run_id>/console.log"
echo "  查看状态: docker ps -a --filter 'name=oh-$RUN_PREFIX'"
echo "=========================================="

exit "$failures"
