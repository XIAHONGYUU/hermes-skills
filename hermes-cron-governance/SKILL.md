---
name: hermes-cron-governance
description: "列一下cron/暂停cron/cron很久没收到/投递失败——Hermes cron审计与看门狗。"
author: XIAHONGYUU
license: MIT
version: 1.0.0
metadata:
  hermes:
    tags: [hermes, cron, feishu, delivery, watchdog, ops]
    related_skills: [hermes-docker-maintenance, top-level-state-sync, workspace-status-audit]
---

# Hermes Cron Governance

Fleet-level operations for the user's 30-job cron fleet: audit it, find the jobs that
are silently broken, cut push volume, and design watchdogs that actually catch failures.

## When to use

- "列一下我们的 cron 列表" / "需要暂停一些 cron" / "哪些 cron 还在正常跑"
- "XX cron 很久没收到了" / "是不是飞书本身的限制"
- Designing or repairing a cron watchdog
- Authoring a `no_agent` script cron

## Ground rules (verified against source, not guessed)

| 事实 | 值 | 出处 |
|---|---|---|
| `schedule` 字段时区 | **UTC** | 服务器 localtime = UTC；北京 = **+8h** |
| `last_run_at` 时区 | **UTC** | 与 output 文件名一致 |
| no_agent 空 stdout | **静默，不推送** | 非空 stdout = 逐字投递 |
| 飞书单条实际上限 | **≈4096 字符** | `plugins/platforms/feishu/adapter.py` 注释 |
| **cron 输出安全预算** | **≤3,500 字符** | 同时低于下面两个阈值 |

## Procedure

### 1. 先跑审计脚本，别手搓查询

```sh
/opt/hermes/.venv/bin/python3 /opt/data/skills/devops/hermes-cron-governance/scripts/cron-fleet-audit.py
```

一次拿到：启用/暂停、**北京时间**、上次运行、投递健康、死亡任务、每日推送预算。
**脚本内置时区自检断言**（`0 14 * * *` 必须等于 22:00 北京）——见 Pitfalls 第 1 条。

### 2. 投递失败三分法

先分清用户口中的"收不到"是哪一类，别混成一句"飞书限制"：

| 类 | 症状 | 根因 | 判据 |
|---|---|---|---|
| **A. 真投递失败** | 一条都没有 | `deliver=origin` + **失效的 thread_id** | `last_delivery_error` 含 `99992402` |
| **B. 长度被切** | 收到但不完整 / 没认出来 | 消息落在 **4,000–8,000 字符**空隙 | Response 长度 > 4096 |
| **C. 静默设计** | 没消息 = 正常 | no_agent 空 stdout | 输出文件为空 |

### 3. 修 A 类（`99992402`）

```
deliver=origin + origin.thread_id = 失效飞书话题 ID (omt_...)
   ↓ standalone 直发，把 omt_ 当字段传过去
飞书拒绝：99992402 field validation failed
   ↓ 降级纯文本重发 → 仍是 99992402
（证明：不是 markdown 格式问题，是**投递目标字段非法**）
   ↓ 任务照报 last_status: ok
静默失败 ← 能这样哑掉几个月
```

**修法** —— 显式指定 chat_id，话题 ID 会被丢弃，改走 live adapter（其余健康任务就是这么发的）：

```
cronjob action=update job_id=<id> name=<原名字> deliver=feishu:oc_<chat_id>
```
⚠️ `action=update` 必须**同时带一个别的字段**（name/prompt/schedule），否则报 "No updates provided"。

### 4. 修 B 类（长度）

三个常量，注意**第三个设错了**：

| 常量 | 值 | 位置 |
|---|---|---|
| `MAX_PLATFORM_OUTPUT` | 4000 | `gateway/delivery.py:29` |
| `_SPLIT_THRESHOLD` | 4000 | `plugins/platforms/feishu/adapter.py:1496` |
| `MAX_MESSAGE_LENGTH` | **8000** ⚠️ | 同上 `:1490` — **高于飞书真实上限** |

**飞书 ≈4096 < Hermes 8000 = 空隙。** 落在 4,000–8,000 的消息整条发出、被飞书切开。

修法优先级：
1. **逐个任务压到 ≤3,500 字符**（把上限写进该任务的 skill / prompt，附原因和取舍规则）
2. 根治 = 把 `MAX_MESSAGE_LENGTH` 改成 4000，所有任务自动预先切分。
   ⚠️ 那是**镜像层源码**（`/opt/hermes/plugins/`），容器重建即丢 —— 改之前先写进重建清单。

### 5. 看门狗：必须读 `last_delivery_error`

**看门狗只 grep 输出文件首行找 `FAILED` = 抓不到投递失败**（这类故障任务本身是 `ok`，根本不写进输出文件）。
历史教训：一个 job 连续 388 次投递失败、哑了 4 个月，看门狗一次都没报。

正确设计 = 三分支 + 一条防误报规则：

| 分支 | 条件 |
|---|---|
| ① **投递异常** | `last_delivery_error` 非空 **且** `last_run_at` 在 48h 内 |
| ② 运行状态 | `last_status == "error"` |
| ③ 脚本级失败 | 输出文件首行含 `FAILED`，或文件不可读 |

**防误报**：投递异常只在最近跑过时才报，否则**季度任务**的旧错会被每 3 小时重报一次。
**静默即正常**：无告警时 `return 0` 且不打印 —— 空 stdout = 不打扰用户。

参考实现：`/opt/data/scripts/cron-watchdog.py`（2026-10-11 重写，三分支已合成测试验证）。

### 6. Fleet hygiene —— 找该暂停的任务

按这四个信号扫，比逐条读 prompt 快得多：

| 信号 | 判据 | 例 |
|---|---|---|
| **死物** | prompt/脚本引用用户**已离开的场景** | 情境结束后每晚仍追问旧项目进展 |
| **僵硬** | 输出连续多天是**同一句模板** | 看 output 目录最近 3 份 |
| **零信息** | no_agent 空闲时仍打印 | 每天一句 `No new conversations to process.` |
| **报平安** | 指标长期在安全区还天天报 | 磁盘 55% 报日报 |

**推送预算** —— 用"每天几条定时推送"当治理指标，比"有几个 job"直观：
> 实测示例：25 个启用 job = **8 条/天**固定推送 + 工作日再加 2。砍掉 6 项后 → 3 条/天。

**发信前的最后一问**：这些 cron 里，有几个在服务**当前主线**？
（在本机实测中，绝大多数 cron 任务与用户当下主线无关 —— 这个数字本身就是结论。）

## Pitfalls

- **⚠️ 时区换算必须锚点自检，再拿给用户看。** 2026-10-11 实测踩坑：临场写的换算函数把**分和时位置写反**，整张表的北京时间全错。
  正确换算：`北京时间小时 = (UTC小时 + 8) % 24`，分钟**原样不动**。
  任何"派生表格"（单位换算/时区/百分比）在呈现前，至少用 **2–3 个已知锚点**验一遍。用户对**编造的精确表格**零容忍。
- **`last_run_at` 的异常小时 ≠ 时区搞错。** 若某 job 上次跑在"不对的时间"，先想一层：**它的 schedule 是不是刚被改过？**（改排程后，上一次残留的是**旧排程**的时刻。）2026-10-11 实例：`0 14 * * *` 的上次运行显示 22:00，因为那次跑的还是改之前的 `0 22 * * *`。
- **cron `script` 参数只能用文件名，且文件必须在 `/opt/data/scripts/`**（实测：写 `self-profile/dashboard/regen.sh` 会失败）。脚本放别处要软链或搬迁。
- **`cronjob action=update` 不能只改一个字段** —— 见第 3 节。
- **cron 上下文有工具限制**：`execute_code` 被硬拦（除非 `approvals.cron_mode: approve`），`write_file` 限于 `HERMES_WRITE_SAFE_ROOT=/opt/data`。详见 `hermes-docker-maintenance` Pitfalls。
- **改全局模型后 unpinned cron 会被跳过** —— 见 `hermes-docker-maintenance`「Cron model-drift guard」。
- **暂停 ≠ 删除**：用户对"关掉"比"删掉"接受度高得多。先 `action=pause` 保留定义，可能几周后又要开。

## References

- `references/feishu-delivery-triage.md` — 99992402 完整失效链、长度常量实测、
  技能猎手 10/1 投递证据、看门狗盲区复现。
- `scripts/cron-fleet-audit.py` — 一键全表审计（含时区自检）。
- 配套：`weekly-skill-hunter` 的输出硬长度上限（3,500 字符）就是本 skill 第 4 节的应用。

---

> **关于支持文件**：本仓库只收录可公开发布的部分。正文中提到的部分 `references/*.md` 是本机带日期的审计快照/案例存档，未随仓库发布。
