# -*- coding: utf-8 -*-
"""session_handoff.py — Agent 会话续接脚本生成器（会话管理窗口）

派活完成后调用本脚本，读取刚创建的 Agent 会话，生成一个续接 .bat 脚本。
用户双击该 .bat，即进入对应 Agent 会话继续对话（复用前缀缓存，避免小修小改全价重读）。

用法:
    python session_handoff.py --agent oc      # opencode 会话
    python session_handoff.py --agent codex   # codex 会话

生成:
    <工作流目录>/sessions/OC_YYYYMMDD-HHMMSS.bat  (续接脚本，双击即续接)
    <工作流目录>/board/sessions.md                (会话注册表，统一管理窗口)
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# 工作流根目录：环境变量 AGENT_WORKFLOW_DIR 优先；默认脚本所在目录
BASE_DIR = Path(os.environ.get("AGENT_WORKFLOW_DIR") or Path(__file__).resolve().parent)
SESSIONS_DIR = BASE_DIR / "sessions"
REGISTRY = BASE_DIR / "board" / "sessions.md"


def get_latest_session(agent):
    """读取该 agent 最新一个 session 的 (id, title, model)。无则返回 None。"""
    if agent == "oc":
        db = Path.home() / ".local/share/opencode/opencode.db"
        sql = "SELECT id, title, model FROM session ORDER BY time_created DESC LIMIT 1"
    elif agent == "codex":
        db = Path.home() / ".codex/state_5.sqlite"
        sql = "SELECT id, title, model FROM threads ORDER BY created_at_ms DESC LIMIT 1"
    else:
        return None
    if not db.exists():
        return None
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    row = con.execute(sql).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row["id"], "title": row["title"] or "", "model": row["model"] or ""}


def resume_cmd(agent, sid):
    """该 agent 续接指定 session 的命令。"""
    if agent == "oc":
        return f'opencode -s "{sid}"'
    if agent == "codex":
        return f'codex resume "{sid}" --include-non-interactive'
    return None


def gen_bat(agent, sid, ts):
    """生成续接 bat，返回路径。注释全 ASCII(避免 cmd GBK 乱码)，标题信息见注册表。"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{agent.upper()}_{ts}.bat"
    path = SESSIONS_DIR / name
    cmd = resume_cmd(agent, sid)
    content = (
        "@echo off\n"
        "REM ============================================================\n"
        f"REM Resume {agent.upper()} session\n"
        f"REM Session ID: {sid}\n"
        f"REM Created by: Pony (Hermes Agent) {time.strftime('%Y-%m-%d %H:%M')}\n"
        "REM Run this = enter the session and keep chatting (reuse prefix cache)\n"
        "REM Title info: see board/sessions.md\n"
        "REM ============================================================\n"
        f"{cmd}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def update_registry(agent, title, sid, bat_name):
    """追加会话注册表。首次调用时写入表头。"""
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    if not REGISTRY.exists():
        REGISTRY.write_text(
            "# Agent 会话续接注册表\n\n"
            "> 本文件由 session_handoff.py 自动维护。双击「续接脚本」列的 .bat，"
            "即进入对应 Agent 会话继续对话（复用前缀缓存）。\n\n"
            "| 时间 | Agent | 标题 | session_id | 续接脚本 |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8")
    now = time.strftime("%Y-%m-%d %H:%M")
    with REGISTRY.open("a", encoding="utf-8") as f:
        f.write(f"| {now} | {agent.upper()} | {title} | `{sid}` | `sessions/{bat_name}` |\n")


def main():
    ap = argparse.ArgumentParser(description="Agent 会话续接脚本生成器")
    ap.add_argument("--agent", choices=["oc", "codex"], required=True,
                    help="要生成续接脚本的 agent 类型")
    args = ap.parse_args()

    s = get_latest_session(args.agent)
    if not s:
        print(f"[handoff] 未找到 {args.agent} 会话记录，跳过")
        return 1
    ts = time.strftime("%Y%m%d-%H%M%S")
    bat = gen_bat(args.agent, s["id"], ts)
    update_registry(args.agent, s["title"], s["id"], bat.name)
    print(f"[handoff] 续接脚本: {bat}")
    print(f"[handoff] 会话标题 : {s['title']}")
    print(f"[handoff] session_id: {s['id']}")
    print(f"[handoff] 双击该 .bat 即进入该 {args.agent.upper()} 会话继续对话(复用缓存)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
