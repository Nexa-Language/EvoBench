# EVA Paper — Figures Specification

## Figure 1: System Architecture Overview
**Type**: Schematic diagram (TikZ or draw.io)
**Placement**: Section 2 (Framework Design), full page width
**Description**: A horizontal layered architecture diagram showing the five-component EVA evaluation framework:

```
┌─────────────────────────────────────────────────────────────────┐
│                   Observation & Leaderboard Layer                │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│   │ Metrics  │  │Leaderboard│  │ Failure  │  │ Process Logs │  │
│   │ Dashboard│  │  (Web)   │  │ Taxonomy │  │  (Tokens,etc)│  │
│   └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                     Task Orchestrator                           │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│   │Scheduling│  │Dependency│  │Resurrection│ │Pipeline Mgmt │  │
│   │  (T0→T5) │  │ Resolution│ │  Trigger  │  │              │  │
│   └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                     Agent Runtime Layer                         │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│   │OpenHands │  │Claude Code│ │ Codex CLI │  │  Kimi CLI    │  │
│   │   SDK    │  │          │  │           │  │              │  │
│   └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                   Model Access Layer (AIhub)                    │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │     Unified API Gateway (https://aihub.arcsysu.cn)       │ │
│   │     21 Models: GPT, Claude, DeepSeek, Qwen, GLM, ...     │ │
│   └──────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                   Execution Environment                         │
│   ┌──────────────────┐              ┌────────────────────────┐ │
│   │  YatCC Workspace │              │ YatCC-Hard Container   │ │
│   │  (CMake + LLVM)  │              │ (Isolated, ephemeral)  │ │
│   └──────────────────┘              └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Style**: Clean, minimal, dark-on-light. Blue for normal flow, green for resurrection, red for failure. Use rounded rectangles with subtle shadows.

---

## Figure 2: Serial Dependency Chain with Resurrection
**Type**: Flow diagram (TikZ)
**Placement**: Section 2.3 (Workloads), single column width
**Description**: A horizontal pipeline showing the 6-task dependency chain with resurrection branching:

```
T0 ──→ T1 ──→ T2 ──→ T3 ──→ T4 ──→ T5
        │      │      │      │      │
        ▼      ▼      ▼      ▼      ▼
     [Golden] [Golden] [Golden] [Golden] [Golden]
     Artifact Artifact Artifact Artifact Artifact
        │      │      │      │      │
        └──────┴──────┴──────┴──────┘
                    │
            Resurrection Injection
           (when score < 60%)
```

**Key elements**:
- 6 task nodes (T0-T5) connected by arrows showing artifact flow
- Below each task: a "golden artifact" box (dashed border, green) connected via resurrection trigger
- Resurrection trigger: red dashed arrow from failed task to golden artifact
- Labels on arrows: "Token Stream", "AST/ASG", "LLVM IR", "Optimized IR", "RV64 Assembly"

**Style**: Task nodes in blue, golden artifacts in green, resurrection arrows in red dashed. Use `\resizebox` to fit column width.

---

## Figure 3: Resurrection Gain Distribution
**Type**: Bar chart (matplotlib) — already exists as `fig_resurrection_gain.png`
**Placement**: Section 4.2 (Resurrection), single column width
**Status**: ✅ EXISTS — may need color adjustment for IEEEtran

---

## Figure 4: Cost-Performance Frontier
**Type**: Scatter plot (matplotlib) — already exists as `fig_cost_performance.png`
**Placement**: Section 4.4 (Process Diagnostics), single column width
**Status**: ✅ EXISTS — may need color adjustment for IEEEtran

---

## Figure 5: Failure Taxonomy Composition
**Type**: Stacked bar chart (matplotlib) — already exists as `fig_failure_taxonomy.png`
**Placement**: Section 4.4 (Process Diagnostics), single column width
**Status**: ✅ EXISTS — may need color adjustment for IEEEtran

---

## Figure 6: YatCC vs YatCC-Hard Difficulty Gap
**Type**: Paired bar chart (matplotlib) — NEW
**Placement**: Section 4.5 (Scaffolding Gap), single column width
**Description**: Side-by-side comparison of PL scores for models evaluated on both workloads.
- X-axis: Model names (top 10 common to both)
- Y-axis: Pipeline Score (0-100)
- Two bars per model: YatCC (blue) and YatCC-Hard (orange)
- Gap annotated with delta values
- Sorted by YatCC-Hard score descending

---

## Color Palette (IEEEtran-compatible)
- Primary blue: #3b82f6
- Success green: #22c55e (light: #bbf7d0)
- Warning orange: #f59e0b
- Danger red: #ef4444 (light: #fca5a5)
- Neutral gray: #6b7280
- Background: white
- Text: #1a1a2e

## General Guidelines
- All figures: 300 DPI minimum
- Single-column figures: width = 3.5 inches (IEEEtran column width)
- Full-page figures: width = 7.16 inches (IEEEtran text width)
- Font sizes: ≥ 8pt for readability
- No dark backgrounds (IEEEtran prints on white paper)
- Use `\includegraphics[width=\linewidth]` for single-column, `\textwidth` for full-page