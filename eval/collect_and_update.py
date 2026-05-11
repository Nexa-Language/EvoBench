#!/usr/bin/env python3
"""收集评测结果并更新 Leaderboard。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
WS_BASE = Path("/home/agent/workspace")
LEADERBOARD = ROOT / "site" / "assets" / "data" / "leaderboard.json"

def read_score(ws: Path, task_id: int) -> dict:
    """从工作区读取指定 Task 的分数。"""
    for suffix in ["", "-classic", "-llm"]:
        f = ws / f"build/test/task{task_id}/score{suffix}.json"
        if f.exists():
            try:
                data = json.loads(f.read_text())
                tests = data.get("tests", [])
                lb = data.get("leaderboard", [])
                if lb:
                    score = float(lb[0]["value"])
                elif tests:
                    score = sum(t.get("score", 0) for t in tests) / len(tests)
                else:
                    score = 0.0
                passed = sum(1 for t in tests if t.get("score", 0) >= t.get("max_score", 100))
                return {"score": score, "test_count": len(tests), "passed_count": passed}
            except:
                pass
    return {"score": 0.0, "test_count": 0, "passed_count": 0}

# 读取现有 Leaderboard
if LEADERBOARD.exists():
    existing = json.loads(LEADERBOARD.read_text())
else:
    existing = []

# 收集所有工作区结果
entries = []
for ws_dir in WS_BASE.glob("openhands-*"):
    name = ws_dir.name.replace("openhands-", "")
    if not (ws_dir / "CMakeLists.txt").exists():
        continue
    
    entry = {
        "model": name,
        "backend": "openhands",
        "date": "2026-05-11",
    }
    task_scores = []
    for task_id in range(6):
        s = read_score(ws_dir, task_id)
        entry[f"task{task_id}"] = s["score"]
        task_scores.append(s["score"])
    
    # 计算 pipeline score
    pipeline = sum(task_scores) / len(task_scores) if task_scores else 0
    entry["pipeline_score"] = round(pipeline, 2)
    
    # 从日志提取复活次数
    log_file = ROOT / "eval" / "logs" / f"openhands-{name}.log"
    if log_file.exists():
        content = log_file.read_text(errors="replace")
        entry["resurrections"] = content.count("触发复活")
    else:
        entry["resurrections"] = 0
    
    entries.append(entry)

# 合并现有数据（保留未被重跑的旧数据）
existing_models = {e["model"] for e in entries}
for old in existing:
    if old["model"] not in existing_models:
        entries.append(old)

# 按 pipeline_score 排序
entries.sort(key=lambda x: x.get("pipeline_score", 0), reverse=True)
for i, e in enumerate(entries):
    e["rank"] = i + 1

# 写入
LEADERBOARD.write_text(json.dumps(entries, ensure_ascii=False, indent=2))
print(f"Leaderboard 已更新: {LEADERBOARD}")
print(f"共 {len(entries)} 个模型\n")
for e in entries:
    print(f"  {e['rank']:2d}. {e['model']:<35s} Pipeline: {e.get('pipeline_score', 0):6.1f}  R={e.get('resurrections', 0)}")
