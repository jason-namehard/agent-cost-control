# -*- coding: utf-8 -*-
"""Agent 任务看门狗 v2:超时熔断 + 轮次上限 + 预算上限(借鉴 Claude Code/SWE-agent)。

v2 新增(2026-08-16,基于对标报告优化):
1. --max-turns: 子进程轮次上限(借鉴 Claude Code max_turns, runtime 级强制)
2. --max-budget-usd: 美元预算硬上限(借鉴 Claude Code max_budget_usd / SWE-agent $3/实例)
   - 通过轮询子进程 stdout 中的 token 用量行估算成本,超限即 kill
   - token 行格式: "X tokens" / "tokens: X" / JSON 含 usage
3. --tier: 任务分级(architect=贵模型规划/executor=flash执行),只影响提示输出与建议,不强制
4. 退出码: 0=正常完成; 2=超时无产出; 4=超轮次; 5=超预算; 3=子进程异常

用法:
    python oc_watchdog.py --workdir <目录> --timeout <秒> [--max-turns N] [--max-budget-usd X] --cmd <命令...>
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

IGNORE_DIRS = {".git", "__pycache__", ".opencode", "node_modules", ".venv", "venv"}

# 默认单价(元/百万 token,按 DeepSeek flash 2026-08 官方价)
PRICE_INPUT_CNY = 1.0
PRICE_OUTPUT_CNY = 2.0
# 汇率(USD->CNY,近似)
USD_CNY = 7.1


def latest_mtime(workdir):
    """工作目录内最新文件修改时间(排除忽略目录)。无文件返回 0。"""
    newest = 0.0
    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            try:
                p = os.path.join(root, f)
                newest = max(newest, os.path.getmtime(p))
            except OSError:
                pass
    return newest


def _kill_tree(proc):
    """终止子进程整棵进程树。

    Windows 下 opencode/codex 是启动器，底层 node/agent 进程是孙进程，仅 proc.kill()
    杀不掉真正烧 token 的进程；用 taskkill /T /F 连带杀整棵树。
    """
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def detect_agent(cmd):
    """从启动命令推断 agent 类型(opencode→oc, codex→codex)。

    v2.1 修复: 命令经 bash -lc 包装时 cmd[0] 是 "bash"，原逻辑失效导致
    handoff 静默跳过、续接脚本从未生成。改为全命令扫描。
    """
    if not cmd:
        return None
    joined = " ".join(cmd).lower()
    if "opencode" in joined:
        return "oc"
    if "codex" in joined:
        return "codex"
    return None


def _do_handoff(script_path, cmd):
    """派活结束后调用 session_handoff.py 生成续接脚本(用户可续接该 session 复用缓存)。"""
    agent = detect_agent(cmd)
    if not agent:
        print("[watchdog] 未识别 agent 类型，跳过续接脚本生成", flush=True)
        return
    try:
        r = subprocess.run([sys.executable, script_path, "--agent", agent],
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip()
        if out:
            print("[watchdog] " + out.replace("\n", "\n[watchdog] "), flush=True)
        if r.returncode != 0:
            print("[watchdog] handoff 退出码 %d: %s" % (r.returncode, (r.stderr or "").strip()), flush=True)
    except Exception as e:
        print("[watchdog] handoff 异常: %s" % e, flush=True)


def _find_usage(obj):
    """递归查找 dict 里的 usage 对象(处理 {"payload":{"usage":...}} 等嵌套包装)。"""
    if isinstance(obj, dict):
        if "usage" in obj and isinstance(obj["usage"], dict):
            return obj["usage"]
        for v in obj.values():
            r = _find_usage(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_usage(v)
            if r:
                return r
    return None


def estimate_cost_from_output(text):
    """从子进程输出中解析 token 用量并估算成本(元)。

    逐行尝试 JSON 解析并递归找 usage(支持 payload 包装、嵌套 details)。
    再兜底 "N tokens" 行。返回 (cost_cny, tokens_in, tokens_out) 或 (None,0,0)。

    注意:opencode/codex 默认不在 stdout 流式输出 usage，故多数场景解析不到、
    预算闸不触发；这属于 CLI harness 的客观局限，超时闸才是可靠硬闸。
    """
    tokens_in = tokens_out = 0
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        u = _find_usage(d)
        if u:
            tokens_in += int(u.get("prompt_tokens", 0) or 0)
            tokens_out += int(u.get("completion_tokens", 0) or 0)
    if tokens_in == 0 and tokens_out == 0:
        m = re.search(r"(\d+)\s*tokens?(?:\s+used)?", text)
        if m:
            tokens_in = int(m.group(1))
    if tokens_in or tokens_out:
        cost = (tokens_in * PRICE_INPUT_CNY + tokens_out * PRICE_OUTPUT_CNY) / 1e6
        return cost, tokens_in, tokens_out
    return None, 0, 0


def main():
    ap = argparse.ArgumentParser(description="Agent 任务看门狗 v2(超时/轮次/预算三道闸)")
    ap.add_argument("--workdir", required=True, help="监控的工作目录")
    ap.add_argument("--timeout", type=int, default=600, help="无产出超时秒数(默认 600=10分钟)")
    ap.add_argument("--check-interval", type=int, default=10, help="检查间隔秒数(默认 10)")
    ap.add_argument("--max-turns", type=int, default=0, help="轮次上限(0=不限,借鉴 Claude Code max_turns)")
    ap.add_argument("--max-budget-usd", type=float, default=0.0, help="美元预算硬上限(0=不限,借鉴 SWE-agent)")
    ap.add_argument("--tier", choices=["architect", "executor", "default"], default="default",
                    help="任务分级: architect=贵模型规划/executor=flash执行(影响提示与建议)")
    ap.add_argument("--cmd", nargs=argparse.REMAINDER, required=True, help="要执行的命令")
    ap.add_argument("--handoff", default=None, metavar="SCRIPT",
                    help="派活结束后调用 session_handoff.py 生成续接脚本(传脚本路径,如 ./session_handoff.py)")
    args = ap.parse_args()

    workdir = args.workdir
    timeout = args.timeout
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    print("[watchdog] 工作目录: %s" % workdir)
    print("[watchdog] 超时阈值: %d 秒无文件变化即终止" % timeout)
    if args.max_turns:
        print("[watchdog] 轮次上限: %d" % args.max_turns)
    if args.max_budget_usd:
        print("[watchdog] 预算上限: $%.2f (约 ¥%.2f)" % (args.max_budget_usd, args.max_budget_usd * USD_CNY))
    if args.tier != "default":
        print("[watchdog] 任务分级: %s (%s)" % (args.tier, "贵模型规划" if args.tier == "architect" else "flash执行"))
    print("[watchdog] 启动命令: %s" % " ".join(cmd))

    try:
        proc = subprocess.Popen(cmd, cwd=workdir, shell=False,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError as e:
        print("[watchdog] 命令不存在: %s" % e)
        return 3

    last_mtime = latest_mtime(workdir)
    last_change = time.time()
    start = time.time()
    killed = False
    kill_reason = ""
    out_buf = []
    turns = 0

    # 后台线程持续读 stdout,避免 Windows pipe select 不可用
    def _pump():
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                out_buf.append(line)
                if len(out_buf) > 400:
                    out_buf.pop(0)
        except Exception:
            pass

    pump_thread = threading.Thread(target=_pump, daemon=True)
    pump_thread.start()

    def current_text():
        return "".join(out_buf)

    def count_turns():
        # 以输出中出现的 "assistant"/"AI:" 行粗估轮次
        return len(re.findall(r"(?m)^(?:assistant|AI)[:\s]", current_text()))

    while proc.poll() is None:
        time.sleep(args.check_interval)
        m = latest_mtime(workdir)
        now = time.time()
        # 闸 1:超时无产出
        if m > last_mtime:
            last_mtime = m
            last_change = now
        elif now - last_change > timeout:
            elapsed = now - start
            print("[watchdog] 超时熔断: 运行 %d 秒,最后文件变化在 %d 秒前,判定卡死" % (elapsed, now - last_change), flush=True)
            _kill_tree(proc)
            killed = True
            kill_reason = "timeout"
            break
        # 闸 2:轮次上限
        if args.max_turns and count_turns() >= args.max_turns:
            print("[watchdog] 轮次熔断: 已达 %d 轮上限" % count_turns(), flush=True)
            _kill_tree(proc)
            killed = True
            kill_reason = "turns"
            break
        # 闸 3:预算上限
        if args.max_budget_usd:
            cost, ti, to = estimate_cost_from_output(current_text())
            if cost and cost / USD_CNY > args.max_budget_usd:
                print("[watchdog] 预算熔断: 估算 $%.2f 超上限 $%.2f" % (cost / USD_CNY, args.max_budget_usd), flush=True)
                _kill_tree(proc)
                killed = True
                kill_reason = "budget"
                break

    # 收尾读取剩余输出
    try:
        pump_thread.join(timeout=2)
    except Exception:
        pass

    proc.wait()
    rc = proc.returncode

    cost, ti, to = estimate_cost_from_output(current_text())
    cost_str = "估算成本 ¥%.3f (in %d / out %d tokens)" % (cost, ti, to) if cost else "成本未知(未解析到用量)"
    print("[watchdog] %s" % cost_str, flush=True)

    # 派活后自动生成续接脚本(用户可续接该 session 复用前缀缓存)
    if args.handoff:
        _do_handoff(args.handoff, cmd)

    if killed:
        reason_map = {"timeout": "超时无产出", "turns": "超轮次上限", "budget": "超预算上限"}
        print("[watchdog] 子进程已终止(%s)。已产出文件保留,请检查后决定是否续跑。" % reason_map.get(kill_reason, kill_reason), flush=True)
        return {"timeout": 2, "turns": 4, "budget": 5}[kill_reason]
    print("[watchdog] 子进程结束,退出码 %d,耗时 %d 秒" % (rc, int(time.time() - start)), flush=True)
    return 0 if rc == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
