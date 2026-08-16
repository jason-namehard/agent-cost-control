---
name: agent-cost-control
description: "Delegate to OC/Codex cheaply: watchdog, flash, merge brief."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, Cost-Control, Watchdog, OpenCode, Codex]
    related_skills: [opencode, codex]
---

# Agent 成本控制标准流程

> 背景:2026-08 一次 OC 任务卡死 2.5h 空转烧掉大量 token(cache read 6.47 亿)后固化。
> 核心思想:防卡死、防重复、便宜模型、少读文件。

## When to Use

- 每次向 OC(OpenCode)或 Codex 派发编码/审计任务前
- 长任务需要后台运行、无法人工盯守时
- 任何想控制 agent token 开销的会话

## 四条规则

### 1. 超时看门狗(必用,防卡死)

所有 agent 派活必须经 `oc_watchdog.py` 启动:

```bash
# OC 写代码(注意:命令放 --cmd 后,argparse 不支持 "--" 分隔符)
# --handoff 让派活结束后自动生成续接 .bat,用户双击即续接该会话复用缓存(见 board/sessions.md)
python oc_watchdog.py --workdir <项目目录> --timeout 600 \
  --handoff session_handoff.py \
  --cmd opencode run "<提示词>"

# Codex 审计(flash 模型)
python oc_watchdog.py --workdir <项目目录> --timeout 600 \
  --handoff session_handoff.py \
  --cmd codex exec --skip-git-repo-check --model deepseek-v4-flash "<提示词>"
```

- 判定:子进程存活但工作目录(排除 .git/__pycache__)文件 mtime 连续 timeout 秒无变化 → kill
- 退出码:0=正常完成;2=超时终止(保留已产出文件,检查后决定续跑);3=子进程异常;4=超轮次;5=超预算
- 卡死处置:已产出可用则**不重派整任务**,只补缺失部分(省一次完整会话)
- opencode run 输出被管道缓冲看不到进度 → 用文件 mtime 判断是否在干活

**v2 三道闸(2026-08-16,盲审后诚实降级)**:
- `--timeout N` 超时无产出即杀 → **唯一可靠硬闸**(基于文件 mtime,实测有效;Windows 下用 taskkill /T 杀整棵进程树)
- `--max-turns N` 轮次上限 → 依赖 stdout 出现 "assistant:"/"AI:" 行计数,而 opencode/codex 默认不输出此类行,多数场景不触发(实验性,勿当卖点)
- `--max-budget-usd X` 预算上限 → 依赖 stdout 流式输出 token 用量,CLI harness 默认不输出,多数场景不触发(实验性,勿当卖点)
- `--tier architect|executor` 任务分级(仅提示输出,不强制)

**任务 2 派活前快照**:wb_fix_assign.py 含派活前 `git add -A + commit`(Aider Git-native 思路)。**注意:依赖目标目录已 git init;E:\Hermesspace 当前未 init,此功能未生效——要么先 `git init`,要么删掉这条宣传**。

**任务 4 合并回收**:`agent_batch.py`(agent_batch.py):
- `merge` 多任务合并成一个 brief(少起会话)
- `collect` 扫描任务单状态汇总
- `report` 台账+任务单汇总,生成简历成本对比小结

**超时标准(2026-08 与用户定稿)**:
| 项目规模 | 定义(用户口径) | 超时 |
|---|---|---|
| 小型 | 单文件/简单脚本 | 5 分钟 |
| 中型 | 带数据库/多模块/多表(如调洪工具箱,后续含安全防护/账号/保密) | **10 分钟** |
| 大型 | 用户不开发;如需则 20-30 分钟人工评估 | — |

10 分钟语义 = 10 分钟**没有任何文件变化**(不是任务总时长);正常任务持续写文件不受限。

### 2. 任务合并(少起会话)

- 多条相关需求合成一个任务包(一个 brief),不要一条需求一个会话
- 派活前自问:这个能和别的合并吗?
- **人力半小时内的小改:自己直接做,不派 agent(小任务派活=净亏损:agent 重新读整个项目,消耗远大于直接改)**
- 派活决策:小修复/小改 → 自己做;大模块/独立新功能/长上下文探索 → 才派 agent
- 连续迭代同一项目时,用 `opencode -c` / `codex --resume` 续接会话,减少每次全量重读

### 2b. 审计工具(2026-08 用户定稿)

- **Codex 不再默认做审计**;用户改用 deepseek harness 审查代码
- 如用户仍要求 agent 审计,才用 codex --model deepseek-v4-flash
- 尊重用户的审查工具选择,不默认派 Codex

### 3. 模型便宜优先

- OC(opencode):deepseek-v4-flash(默认配置已锁,勿改)
- **Codex 审计:必须显式 `--model deepseek-v4-flash`**,禁止默认 pro 模型整审(贵数倍)
- 确需深度推理:先 flash 跑通,再对关键函数用 pro 按需复检,不默认全量

### 4. 上下文收敛(少读文件)

- 派活 prompt 明确:"只读以下文件: <清单>",禁止全目录扫读
- 审计类:给文件清单 + "data/ 数据文件只读不读"(除非任务相关)
- brief 里写明不需要的目录,减少每轮 token

## 派活前检查清单

- [ ] watchdog 就绪(scripts/oc_watchdog.py 存在)
- [ ] 任务已合并(一个 brief 尽量覆盖全部需求)
- [ ] prompt 明确读哪些文件、不读哪些
- [ ] Codex 用 --model deepseek-v4-flash
- [ ] 验收标准写进 brief(测试命令 + 预期结果)
- [ ] 验收时独立复跑测试,不信 agent 自报

## CLI 调度成本真相与人工修正(2026-08 用户定稿,黑板工作流强制条款)

**成本真相(必须严格执行,不回避)**:

1. **CLI 调度 = 每次全量未命中缓存**:`opencode run` / `codex exec` 每次都是全新会话,无历史前缀,
   DeepSeek 前缀缓存命中率≈0,输入 token 全价;任务越大、重读文件越多,消耗越高
2. **卡住即反复烧 token**:CLI 任务卡死/空转时,agent 仍持续重读文件、生成无意义输出,
   每轮都在烧输入 token(cache read 6.47 亿教训);必须 watchdog 兜底 + 及时 kill
3. **切换模型缓存清零**:同一会话内 Pro/flash 互切,缓存按模型隔离,切换后首次请求全价
4. **长 session 缓存最友好**:Hermes 主会话持续累积前缀,后续轮次便宜;不要把活拆成大量短会话

**人工修正环节(省钱关键,用户定稿)**:

- **任务描述由小马做**:如何派任务、任务该做什么,由小马(对 agent 能力边界和任务描述更清晰)负责,
  写在 brief 里;不要直接让用户去写 agent prompt
- **二次修改交给人**:对 agent 产物的二次修改,由用户主动与 agent 沟通(人看清产出后再让 agent 改),
  避免小马转述导致多轮会话浪费
- **原则**:一次派活把任务描述到位,中间人工修正尽量一次到位,不反复开新会话

**会话续接(缓存命中,2026-08-16 OC/Codex 已解决)**:

- **OC/Codex 已解决**:派活加 `--handoff` 参数,watchdog 跑完自动调 session_handoff.py 生成续接 .bat;
  用户双击 .bat = `opencode -s <sid>` / `codex resume <sid> --include-non-interactive` 进入会话复用前缀缓存
- 统一管理窗口: `board/sessions.md`(注册表) + `sessions/*.bat`(续接脚本)
- **关键认知(实测)**:CLI 派活每次全新会话缓存命中≈0,小修小改若重新派活=全价重读;正确做法是派活后**续接同一会话**

**未解决问题(记录待办,勿当已解决)**:

- **WB(ACP 会话)续接未解决**:WB 走 ACP 协议,会话句柄透传与续接需 WB 侧支持
- 用户看不到 CLI 调起的 agent 的"实时进度"(只能派活后续接,不能边跑边看),待平台支持

## 坑(实测)

- opencode run 的 `-f` 在 Windows v1.18.x 有 bug(报 `File not found: <message>`);改用 message 让 agent 自己读文件
- opencode run 无中间输出(被 tail 缓冲),卡死判断靠文件 mtime,别等输出
- PYTHONPATH 指向 Hermes venv 会污染其他解释器(py=3.13 加载 cp311 numpy 崩溃);用户工具用 run.bat(Hermes venv python)规避
- 派活前验证 agent 可跑:opencode run '回复 OK' / codex exec '回复 OK'(各约 1 万 token 冒烟)
- **bash 包装导致 handoff 静默失效(2026-08-16 实战)**:`--cmd bash -lc "opencode run ..."` 时 cmd[0]=bash,
  detect_agent 旧版只看 cmd[0] → "未识别 agent 类型" → 续接脚本从未生成。**v2.1 已修(全命令扫描)**;
  派活结束后必须确认输出出现 `[watchdog] 续接脚本:` 一行,没有就是 handoff 又断了
- **CLI 显示的 session ID 是截断的(2026-08-16 实战)**:`opencode session list` 截断显示(如
  `ses_ff4adf058ffeQ3Ks` 实为 `ses_ff4adf058ffeQ3KsVxqs44Ku0R`),用截断 ID 续接必报 Session not found。
  **续接用完整 ID**:从 session_handoff.py 输出或 `opencode session list --format json` 拿
- **GitHub 被墙环境下 OC 会卡死在联网调研(2026-08-16 实战)**:派活 1 全程查文档 70 秒零产出 exit 0。
  修复=BRIEF 铁律"禁止联网"+ API 参考本地化(SHERPA_API.md 模式)+ prompt 明示"最多试 1 次联网,失败即弃"
- **熔断后正确处置=续接同一会话,不是开新会话(2026-08-16 实战)**:watchdog 熔断杀掉的只是当前子进程,
  会话记录还在;`opencode run -s <完整ID> '继续完成未做完的 X'` 续接,复用前缀缓存。
  开新会话=全价重读(本次教训:一个任务 4 次全价重读)
- **派活结束必须向用户汇报 session 管理信息**:session ID(完整)、完成/失败点、续接 .bat 路径。
  用户需要能介入 Agent 会话做二次修改——这是工作流设计初衷,不汇报等于没做

## 配套脚本

- `scripts/oc_watchdog.py`(随项目分发;无项目时放临时目录使用;`--handoff` 可自动生成续接脚本)
- `session_handoff.py`(会话续接脚本生成器;读最新 session 生成 .bat + 更新注册表)
- `agent_cost_log.py`(派活成本记账,已修正列名/币种/归属)
