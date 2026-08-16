# -*- coding: utf-8 -*-
"""agent_cost_log.py — 派活成本自动记录工具（简历数据验证用）

用法:
    python agent_cost_log.py --task "任务名" --agent oc     # 记录 OC token(精确,读 opencode.db)
    python agent_cost_log.py --task "任务名" --agent codex  # 记录 Codex token(读 state_5.sqlite)
    python agent_cost_log.py --task "任务名" --agent wb     # WB（hy3 免费，成本=0 也记录）
    python agent_cost_log.py --list                         # 查看台账
    python agent_cost_log.py --summary                      # 汇总统计（对比基线）

原理(2026-08-16 盲审修正):
    - OC: 读 ~/.local/share/opencode/opencode.db 的 session 表，列名 tokens_input/tokens_output/
      tokens_reasoning/tokens_cache_read/tokens_cache_write/cost，其中 cost 为 USD，按汇率换算成元
    - Codex: 读 ~/.codex/state_5.sqlite 的 threads 表 tokens_used 字段(codex 无 input/output 拆分、
      无 cost 字段，成本按模型单价估算并标注"估算")
    - WB: hy3-preview 免费，记 0 元 + 派活记录
    记录追加到 <工作流目录>/board/cost-ledger/台账.csv
"""
import csv
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# 工作流根目录：环境变量 AGENT_WORKFLOW_DIR 优先；默认脚本所在目录
BASE_DIR = Path(os.environ.get("AGENT_WORKFLOW_DIR") or Path(__file__).resolve().parent)
LEDGER_DIR = BASE_DIR / "board" / "cost-ledger"
LEDGER = LEDGER_DIR / "台账.csv"
FIELDS = ["时间", "任务", "agent", "模型", "input_tokens", "output_tokens",
          "reasoning_tokens", "cache_read", "cache_write", "cost_元", "备注"]

# 汇率(USD->CNY，近似) 与 Codex 成本估算单价(元/百万 token，保守按输入价)
USD_CNY = 7.1
PRICE_CODEX_FLASH = 1.0
PRICE_CODEX_PRO = 2.0

# 调洪基线（历史数据，2026-08 实测，来自 opencode.db 统计；成本统一为"元"）
BASELINE = {
    "oc": {"cost_元": round(2.25 * USD_CNY, 2), "cache_read": 647000000,
           "note": "调洪项目全程，含一次卡死 2h20m (OC $2.25×7.1≈¥16)"},
    "codex": {"cost_元": None, "cache_read": None,
              "note": "调洪项目审计，账单经 state_5.sqlite"},
}


def _model_id(model_json):
    """opencode 的 model 字段是 JSON 字符串，提取其中 id。"""
    if not model_json:
        return ""
    if isinstance(model_json, dict):
        return model_json.get("id", "")
    try:
        d = json.loads(model_json)
        return d.get("id", model_json) if isinstance(d, dict) else model_json
    except Exception:
        return model_json


def ensure_ledger():
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    if not LEDGER.exists():
        with open(LEDGER, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(FIELDS)


def read_oc_stats():
    """从 opencode.db 读取最近一个 session 的 token 统计(列名已修正为 tokens_input 等)。

    取最新创建的 session(串行派活下即本次任务)。返回 dict，无则 None。
    cost 字段为 USD，调用方负责换算。
    """
    db_path = Path.home() / ".local/share/opencode/opencode.db"
    if not db_path.exists():
        return None
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM session ORDER BY time_created DESC LIMIT 1").fetchone()
    con.close()
    if not row:
        return None
    return {
        "input": row["tokens_input"],
        "output": row["tokens_output"],
        "reasoning": row["tokens_reasoning"],
        "cache_read": row["tokens_cache_read"],
        "cache_write": row["tokens_cache_write"],
        "cost_usd": row["cost"],
        "session_id": row["id"],
        "title": row["title"],
        "model": row["model"],
    }


def read_codex_stats():
    """从 state_5.sqlite threads 表读取最近一个 session 的 tokens_used(原读 jsonl 无效)。

    codex 只提供 tokens_used 单一数字，无 input/output 拆分、无 cost 字段。
    返回 dict，无则 None。
    """
    db_path = Path.home() / ".codex/state_5.sqlite"
    if not db_path.exists():
        return None
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM threads ORDER BY created_at_ms DESC LIMIT 1").fetchone()
    con.close()
    if not row:
        return None
    return {
        "tokens_used": row["tokens_used"],
        "model": row["model"],
        "title": row["title"],
        "session_id": row["id"],
    }


def log_entry(task, agent, model, stats, cost_元, note=""):
    ensure_ledger()
    now = time.strftime("%Y-%m-%d %H:%M")
    row = [now, task, agent, model,
           stats.get("input", ""), stats.get("output", ""),
           stats.get("reasoning", ""), stats.get("cache_read", ""),
           stats.get("cache_write", ""), cost_元, note]
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)
    print(f"已记录: {now} | {task} | {agent} | 成本 {cost_元} 元")


def summary():
    if not LEDGER.exists():
        print("台账不存在，先记录几条")
        return
    rows = list(csv.reader(open(LEDGER, encoding="utf-8")))[1:]
    total_cost = sum(float(r[9]) for r in rows if len(r) > 9 and r[9])
    base = BASELINE["oc"]["cost_元"]
    print("=== 即忆重构成本台账汇总 ===")
    print(f"派活次数: {len(rows)}")
    print(f"总花费: ¥{total_cost:.3f}")
    print(f"对比调洪基线: OC ¥{base} ({BASELINE['oc']['note']})")
    if total_cost > 0:
        print(f"即忆重构花费 ≈ 基线的 {total_cost / base * 100:.0f}% (规模相当,含工作流改进)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    if args[0] == "--list":
        ensure_ledger()
        for line in open(LEDGER, encoding="utf-8"):
            print(line.strip())
    elif args[0] == "--summary":
        summary()
    elif args[0] == "--task":
        task = args[1]
        agent = "oc"
        note = ""
        for i, a in enumerate(args[2:], start=2):
            if a == "--agent" and i + 1 < len(args):
                agent = args[i + 1]
            elif a == "--note" and i + 1 < len(args):
                note = args[i + 1]

        stats = {}
        model = "unknown"
        cost_元 = 0.0
        if agent == "oc":
            stats = read_oc_stats() or {}
            model = _model_id(stats.get("model", "")) or "deepseek-v4-flash"
            cost_元 = round((stats.get("cost_usd") or 0) * USD_CNY, 6)
            sid = (stats.get("session_id") or "")[:8]
            title = (stats.get("title") or "")[:30]
            note = f"{note} | OC精确 session={sid} '{title}'".strip(" |")
        elif agent == "codex":
            stats = read_codex_stats() or {}
            model = stats.get("model", "unknown")
            tokens = stats.get("tokens_used") or 0
            price = PRICE_CODEX_PRO if "pro" in str(model) else PRICE_CODEX_FLASH
            cost_元 = round(tokens * price / 1e6, 6)
            sid = (stats.get("session_id") or "")[:8]
            title = (stats.get("title") or "")[:30]
            note = f"{note} | Codex估算 tokens_used={tokens} session={sid} '{title}'".strip(" |")
            stats = {"input": tokens}  # codex 无拆分，tokens_used 暂存 input 列
        elif agent == "wb":
            stats = {"input": 0}
            model = "hy3-preview"
            cost_元 = 0.0
            note = f"{note} | WB hy3-preview 免费".strip(" |")
        else:
            print(f"未知 agent: {agent}（应为 oc/codex/wb）")
            sys.exit(1)
        log_entry(task, agent, model, stats, cost_元, note)
    else:
        print(__doc__)
