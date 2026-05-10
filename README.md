<p align="center">
  <h1 align="center">⚡ EvoBench</h1>
  <p align="center"><strong>Serial Agent Benchmark with Resurrection Mechanism</strong></p>
  <p align="center">
    <a href="https://evobench.nexa-lang.com">🌐 Website</a> ·
    <a href="https://evobench.nexa-lang.com/leaderboard.html">📊 Leaderboard</a> ·
    <a href="https://evobench.nexa-lang.com/blog.html">📝 Blog</a> ·
    <a href="#quickstart">🚀 Quick Start</a>
  </p>
</p>

---

## What is EvoBench?

EvoBench is the **first serial agent benchmark** that tests whether AI agents can **evolve** — building a compiler from scratch in 6 sequential tasks, each depending on the previous one.

| Task | Name | Description |
|------|------|-------------|
| 0 | Setup | Environment verification |
| 1 | Lexer | Lexical analysis (tokenization) |
| 2 | Parser | Syntax analysis (AST generation) |
| 3 | IR Gen | LLVM IR generation |
| 4 | IR Opt | LLVM IR optimization |
| 5 | Asm Gen | RV64 assembly generation |

### Key Innovation: Resurrection Mechanism

When an agent fails at Task N, EvoBench injects **golden answers** so it can still evaluate Tasks N+1 through N+k. This solves the **cascading failure problem** in serial evaluation.

### Current Leaderboard (Top 5)

| Rank | Model | Mean Reward | Pass Score |
|------|-------|-------------|------------|
| 🥇 | glm-5 | 55.17 | 66.67% |
| 🥈 | deepseek-v4-pro | 53.33 | 44.44% |
| 🥉 | qwen3.6-max-preview | 47.53 | 44.44% |
| 4 | minimax-m2.7 | 46.71 | 44.44% |
| 5 | minimax-m2.5 | 46.67 | 33.33% |

> **21 models evaluated** using OpenHands SDK and CLI agents. See full results at [evobench.nexa-lang.com/leaderboard](https://evobench.nexa-lang.com/leaderboard.html).

## Supported Agent Backends

| Backend | Type | Command |
|---------|------|---------|
| **OpenHands SDK** | Full Agent (infinite self-loop) | `python src/run_openhands.py --model mimo-v2.5-pro` |
| **Claude Code** | CLI Agent | `python src/run_cli_agent.py --backend claude` |
| **Codex CLI** | CLI Agent | `python src/run_cli_agent.py --backend codex` |
| **Gemini CLI** | CLI Agent | `python src/run_cli_agent.py --backend gemini` |
| **Kimi CLI** | CLI Agent | `python src/run_cli_agent.py --backend kimi` |
| **OpenAI API** | Raw API + Tool Calling | `python src/evo_cli/cli.py run --backend openai` |

## Quick Start

### Prerequisites

- Python 3.10+ (main framework) and Python 3.12+ (OpenHands SDK)
- CMake 3.20+, Ninja
- LLVM 18, ANTLR 4.13, RISC-V cross-compilation toolchain

### Installation

```bash
# Clone with submodules
git clone https://github.com/Nexa-Language/EvoBench.git --recursive
cd EvoBench

# Install Python dependencies
pip install -e .

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Prepare YatCC dependencies (first time only)
cd data/YatCC
./antlr/setup.sh && ./llvm/setup.sh && ./task5_setup.sh
cd ../..
```

### Run Evaluation

```bash
# OpenHands SDK (recommended - full agent with infinite self-loop)
uv venv .venv-openhands --python 3.12
. .venv-openhands/bin/activate
uv pip install openhands-sdk openhands-tools

python src/run_openhands.py --model mimo-v2.5-pro --tasks 0-5

# CLI Agents
python src/run_cli_agent.py --backend claude --tasks 0-5
python src/run_cli_agent.py --backend codex --tasks 0-5
python src/run_cli_agent.py --backend gemini --tasks 0-5
python src/run_cli_agent.py --backend kimi --tasks 0-5

# Generate Leaderboard
python src/metrics.py
```

## Metrics

### Mean Reward (Primary Metric)

```
Mean Reward = Σ(score[i] × weight[i] × bonus[i]) / Σ(weight[i])

weights  = [1, 2, 2, 3, 3, 4]    # Task 0-5, increasing importance
bonus    = 1.2 (no resurrection)  # Direct pass gets 20% bonus
           1.0 (resurrected)      # Resurrected pass gets no bonus
```

### Pass Score

```
Pass Score = Σ(pass[i] × pass_bonus[i]) / (6 × 1.5) × 100

pass_bonus = 1.5 (no resurrection)  # Direct pass gets 50% bonus
             1.0 (resurrected)      # Resurrected pass gets no bonus
```

## Project Structure

```
EvoBench/
├── src/                    # Source code
│   ├── evo_cli/            # Evo-CLI (multi-backend framework)
│   ├── run_openhands.py    # OpenHands SDK runner
│   ├── run_cli_agent.py    # CLI agent runner (claude/codex/gemini/kimi)
│   └── metrics.py          # Metrics calculation & leaderboard generation
├── data/                   # Benchmark data
│   ├── YatCC/              # YatCC compiler project (submodule)
│   ├── YatCC-docs/         # YatCC documentation
│   └── task_guides.md      # Simplified task guides for agents
├── eval/                   # Evaluation
│   ├── results/            # Evaluation results (JSON)
│   ├── logs/               # Execution logs (gitignored)
│   └── workspaces/         # Agent workspaces (gitignored)
├── site/                   # Website (GitHub Pages)
│   ├── index.html          # Homepage with particle effects
│   ├── leaderboard.html    # Interactive leaderboard
│   └── blog.html           # Blog with expandable articles
└── .github/workflows/      # GitHub Actions (auto-deploy)
```

## Adding a New Agent Backend

See [docs/adding-backends.md](docs/adding-backends.md) for a guide on integrating new agent frameworks.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Citation

```bibtex
@misc{evobench2026,
  title={EvoBench: A Serial Agent Benchmark with Resurrection Mechanism},
  author={EvoBench Team},
  year={2026},
  url={https://github.com/Nexa-Language/EvoBench}
}
```

## Acknowledgments

- [YatCC](https://github.com/arcsysu/YatCC) — 中山大学编译原理课程实验
- [OpenHands](https://github.com/All-Hands-AI/OpenHands) — Agent framework

## License

MIT
