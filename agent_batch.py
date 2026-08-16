# -*- coding: utf-8 -*-
"""agent_batch.py — 任务合并 + 结果回收机制(借鉴 Cline Kanban/Claude Agent Teams)。

解决的问题:
- 多个小而相关的需求 → 合并成一个 brief 派活(少起会话,省启动+上下文开销)
- 派活后自动回收结果 → 校验任务单状态/产物文件 → 汇总报告
- 失败任务标记 → 支持"补缺失部分"而非整任务重派(省钱关键)

用法:
    python agent_batch.py merge "任务A" "任务B" "任务C" --title "合并任务名"
        → 生成合并 brief 到 inbox/,输出合并任务单
    python agent_batch.py collect --since <时间>
        → 扫描 inbox/ 任务单,汇总已完成/失败状态,输出回收报告
    python agent_batch.py report
        → 汇总台账 + 回收报告,生成给简历用的成本对比小结

设计要点(对标报告结论落地):
- 合并 = 把相关需求打包成一个 prompt 段落,减少独立会话数(Cline Kanban 思路)
- 回收 = 校验任务单"状态: ✅已完成" + 产物文件存在(不信 agent 自报)
- 失败处置 = 只补缺失部分,不重派整任务(agent-cost-control 规则)
"""
import csv
import os
import re
import sys
import time
from pathlib import Path

# 工作流根目录：环境变量 AGENT_WORKFLOW_DIR 优先；默认脚本所在目录
BASE_DIR = Path(os.environ.get("AGENT_WORKFLOW_DIR") or Path(__file__).resolve().parent)
INBOX = BASE_DIR / "inbox"
BOARD = BASE_DIR / "board"
LEDGER = BOARD / "cost-ledger" / "台账.csv"


def merge_tasks(tasks, title=None):
    """合并多个任务为一个 brief。返回任务单路径。"""
    if len(tasks) < 2:
        print("合并至少需要 2 个任务;单任务直接派活")
        return None
    title = title or "合并任务-%s" % time.strftime("%H%M%S")
    ts = time.strftime("%Y%m%d-%H%M%S")
    brief = ["# 合并任务单", "", "标题: %s" % title, "", "本单由 %d 个子任务合并而成(任务合并机制):" % len(tasks), ""]
    for i, t in enumerate(tasks, 1):
        brief.append("## 子任务 %d" % i)
        brief.append(t.strip())
        brief.append("")
    brief.append("状态: 📥已派发")
    brief.append("")
    brief.append("【验收标准】(派活前由小马填写,回收时逐条核,不信自报)")
    brief.append("1. 测试命令: <填写,如 pytest tests/ -q>")
    brief.append("2. 预期结果: <填写,如 全部通过>")
    brief.append("")
    brief.append("【产物清单】(每子任务完成后填产物文件路径,回收时校验存在)")
    brief.append("1. 子任务1 产物: <文件路径>")
    brief.append("")
    brief.append("【必须人工介入点】(命中即停下问用户,不得擅自决定)")
    brief.append("1. <如: 引入新依赖/架构选型/删除文件/动敏感数据>")
    brief.append("")
    brief.append("【回收校验清单】")
    brief.append("1. 每个子任务完成后,把结果写入本单对应子任务段")
    brief.append("2. 全部完成后,把状态改为 ✅已完成")
    brief.append("3. 若某子任务无法完成,标注 ❌失败 + 原因,不要整单重做")
    path = INBOX / f"任务单-{ts}.md"
    path.write_text("\n".join(brief), encoding="utf-8")
    print(f"✅ 合并任务单: {path.name} ({len(tasks)} 个子任务)")
    return path


def _ticket_ts(name):
    """从 任务单-YYYYMMDD-HHMMSS.md 提取时间戳字符串，失败返回 ''。"""
    m = re.search(r"任务单-(\d{8}-\d{6})\.md", name)
    return m.group(1) if m else ""


def parse_artifacts(text):
    """解析任务单里声明的产物文件路径。返回列表。"""
    arts = []
    for m in re.finditer(r"产物[:：]\s*([^\n|]+)", text):
        for p in m.group(1).split():
            p = p.strip().strip("`")
            if p and not p.startswith("<"):
                arts.append(p)
    return arts


def collect(since=None):
    """扫描任务单,汇总状态。返回统计 dict。since 为 YYYYMMDD-HHMMSS 格式。"""
    stats = {"total": 0, "done": 0, "pending": 0, "failed": 0, "artifact_missing": 0}
    rows = []
    for f in sorted(INBOX.glob("任务单-*.md")):
        if since and _ticket_ts(f.name) < since:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        stats["total"] += 1
        title_m = re.search(r"标题[:：]\s*(.+)", text)
        status_m = re.search(r"状态[:：]\s*(.+)", text)
        title = title_m.group(1).strip() if title_m else f.name
        status = status_m.group(1).strip() if status_m else "未知"
        if "已完成" in status:
            stats["done"] += 1
            # 产物校验(不信自报):已完成单里声明的产物文件必须真实存在
            missing = [a for a in parse_artifacts(text) if not Path(a).exists()]
            if missing:
                stats["artifact_missing"] += 1
                status += " ⚠️产物缺失:" + ",".join(missing)
        elif "失败" in status:
            stats["failed"] += 1
        else:
            stats["pending"] += 1
        rows.append((f.name, title, status))
    return stats, rows


def report():
    """汇总台账 + 任务单状态,生成成本对比小结(简历用)。"""
    stats, rows = collect()
    print("=" * 50)
    print("Agent 派活回收报告")
    print("=" * 50)
    print(f"任务单总数: {stats['total']} | 完成: {stats['done']} | 进行中: {stats['pending']} | 失败: {stats['failed']}")
    if stats.get("artifact_missing"):
        print(f"⚠️ 产物缺失 {stats['artifact_missing']} 单(不信自报:已完成单声明的产物文件不存在):")
        for name, title, st in rows:
            if "产物缺失" in st:
                print(f"  - {title} ({name})")
    if stats["failed"]:
        print("⚠️ 失败任务(只补缺,不重派):")
        for name, title, st in rows:
            if "失败" in st:
                print(f"  - {title} ({name})")
    # 台账汇总(台账 cost_元 列已统一为"元")
    if LEDGER.exists():
        with open(LEDGER, encoding="utf-8") as f:
            ledger_rows = list(csv.reader(f))[1:]
        total_cost = sum(float(r[9]) for r in ledger_rows if len(r) > 9 and r[9])
        print(f"\n成本台账: {len(ledger_rows)} 条记录 | 总花费 ¥{total_cost:.3f}")
        print(f"对比调洪基线: OC ¥16($2.25×7.1,含一次 2h20m 卡死)")
        if total_cost > 0:
            print(f"即忆重构花费 ≈ 基线的 {total_cost / 16 * 100:.0f}%")
    print("=" * 50)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
    elif args[0] == "merge":
        title = None
        if "--title" in args:
            i = args.index("--title")
            title = args[i + 1]
            tasks = args[1:i] + args[i + 2:]
        else:
            tasks = args[1:]
        merge_tasks(tasks, title)
    elif args[0] == "collect":
        since = None
        if "--since" in args:
            since = args[args.index("--since") + 1]
        stats, rows = collect(since)
        print("任务单状态汇总:")
        for name, title, st in rows[-15:]:
            print(f"  {st} | {title} | {name}")
    elif args[0] == "report":
        report()
