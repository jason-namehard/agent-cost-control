# agent-cost-control

给 CLI 编码 Agent（opencode / codex）装上一套「成本刹车 + 会话续接 + 成本记账」的薄封装。不是又一个 Agent 框架，而是现有 CLI Agent 的**成本副驾驶**。

## 解决什么问题

真实项目中，AI 编码 Agent 一次卡死空转 2 小时 20 分、反复重读文件烧掉 6.47 亿 tokens 后，固化的省钱工作流。核心四件套：

| 脚本 | 作用 | 可靠度 |
|---|---|---|
| `oc_watchdog.py` | 超时无产出即杀（防卡死） | ✅ 核心硬闸，实测有效 |
| `session_handoff.py` | 派活后生成续接脚本，复用前缀缓存 | ✅ 实测有效 |
| `agent_cost_log.py` | 派活成本自动记账 | ✅ 已修列名/币种 |
| `agent_batch.py` | 任务合并 + 回收 + 产物校验 | ✅ 可用 |

> 诚实声明：watchdog 里的 `--max-turns`（轮次闸）和 `--max-budget-usd`（预算闸）依赖 CLI 的 stdout 输出，而 opencode/codex 默认不流式输出 token/轮次，因此这两个闸在多数场景**不触发**。**超时闸才是唯一可靠硬闸。**

## 核心机制：会话续接（缓存命中）

CLI 派活 = 每次全新会话 = 前缀缓存命中率 ≈ 0。小修小改若重新派活，等于把整个项目上下文再全价读一遍。

正确做法：派活后**续接同一个会话**，复用前缀缓存。

```
派活命令加 --handoff → 跑完自动生成续接 .bat → 双击即续接
```

```bash
# opencode 派活 + 自动生成续接脚本
python oc_watchdog.py --workdir <项目目录> --timeout 600 \
  --handoff ./session_handoff.py \
  --cmd opencode run "<提示词>"

# 之后双击 sessions/OC_*.bat → 直接进入该会话继续对话（复用缓存）
```

## 快速开始

依赖：Python 3.10+、opencode 或 codex CLI 已安装。

```bash
git clone <本仓库> agent-cost-control
cd agent-cost-control
python agent_cost_log.py --task "冒烟测试" --agent oc   # 记账冒烟
```

## 路径配置

脚本默认把数据目录（`inbox/` `board/` `sessions/`）放在**脚本所在目录**下。要改到别处，设环境变量：

```bash
# Windows
set AGENT_WORKFLOW_DIR=D:\my\workflow

# Linux/macOS
export AGENT_WORKFLOW_DIR=/home/me/workflow
```

## 目录结构

```
agent-cost-control/
├── oc_watchdog.py        # 看门狗（防卡死）
├── session_handoff.py    # 会话续接生成器
├── agent_cost_log.py     # 成本记账
├── agent_batch.py        # 任务合并回收
├── SKILL.md              # 方法论主文档
├── DESIGN.md             # 多 Agent 工作流设计
└── (运行时生成: inbox/ board/ sessions/)
```

## License

MIT
