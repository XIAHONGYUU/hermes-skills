# 飞书投递故障排查 — 实测记录（2026-10-11）

用户提问原话：*"有些 cron，包括技能捕手之类的 cron 很久没有收到了，是不是飞书本身的限制？"*

**答案：不是。** 是两件不同的事被混在了一起。以下是完整证据链，供复现与回归。

---

## 一、A 类：真投递失败 —— `99992402`

### 失效链

```
deliver=origin  +  origin.thread_id = 已失效的飞书话题 ID (omt_...)
      ↓ 走 standalone 直发路径，把 omt_ 当普通字段传给飞书 API
飞书拒绝：[99992402] field validation failed
      ↓ Hermes 降级为纯文本重发
仍是 99992402   ← 关键证据：不是 markdown 格式问题，是「投递目标字段非法」
      ↓ 任务照报 last_status: ok
静默失败 ← 可哑数月而无人知
```

### 两个受害者

| job | 名称 | 症状 | 失效 thread_id |
|---|---|---|---|
| `735ef98d7ec9` | 某价格提醒任务 | **388 次投递失败** + 475 次静默；从 2026-06-01 哑到 2026-10-11 = **4 个月** | `<platform>_<id>` |
| `a440a8025188` | 季度持仓审查 | 同款错误（下次触发 12-01） | `<platform>_<id>` |

### 判据（精确到可操作）

遍历全表后：**只有 2 个 job 同时满足「`deliver=origin` 且有 `origin.thread_id`」—— 恰好就是失败的那 2 个。**
其余带 thread_id 的 job 都用显式 `deliver=feishu:oc_...` 覆盖了，不受影响。
技能猎手 `5bf9a56dbde6` 的 `origin.thread_id = None` → 不走这条路径 → 投递成功。

### 复现 / 定位命令

```sh
# 错误计数
grep -c 99992402 /opt/data/logs/errors.log      # 本次实测：858 次
grep -c 99992402 /opt/data/logs/agent.log       # 本次实测：142 次

# 逐 job 看投递字段
python3 -c "
import json;d=json.load(open('/opt/data/cron/jobs.json'))
js=d.get('jobs',d) if isinstance(d,dict) else d
js=list(js.values()) if isinstance(js,dict) else js
for j in js:
    if j.get('last_delivery_error'):
        print(j['id'][:12], j.get('name'), '| deliver=',j.get('deliver'))
"
```

### 修复（已执行）

```
cronjob action=update job_id=735ef98d7ec9 name="某价格提醒任务" \
        deliver=feishu:oc_<your_chat_id>
```
⚠️ `action=update` 只传 deliver 会报 `No updates provided`，必须附一个 name/prompt/schedule。

---

## 二、B 类：长度 —— 被飞书切开

### 三个常量（源码实测）

| 常量 | 值 | 位置 |
|---|---|---|
| `MAX_PLATFORM_OUTPUT` | 4000 | `gateway/delivery.py:29` |
| `_SPLIT_THRESHOLD` | 4000 | `plugins/platforms/feishu/adapter.py:1496` |
| `MAX_MESSAGE_LENGTH` | **8000** | 同上 `:1490` |

`_SPLIT_THRESHOLD` 的**源码注释原文**：

> Uses a longer delay when the latest chunk is near **Feishu's ~4096-char split point**,
> since a continuation chunk is almost certain.

（该常量的用途是**入站**分块，但它自己写明了飞书的真实切分点 ≈4096。）

**结论：飞书 ≈4096 < Hermes 允许的 8000 = 空隙。** 4,000–8,000 字符的消息会整条发出，被飞书切开。

### 技能猎手（`5bf9a56dbde6`）实证

**服务端日志（10-01 的运行）：**
```
00:03:19,156  gateway.delivery: Cron output preserved for chunking adapter (4689 chars)
              — full output saved to /opt/data/cron/output/5bf9a56dbde6_20261001_000319.txt
00:03:19,983  cron.scheduler: Job '5bf9a56dbde6': delivered to feishu:oc_721b... via live adapter
```

**它是投递成功的**（无错误记录，走了 live adapter 分块路径）。用户之所以觉得"很久没收到"，最可能是**被切开+没认出来**。

| 日期 | Response 长度 | 判定 |
|---|---|---|
| 2026-09-01 | 3,217 | 低于两个阈值 → 单条发出，应正常收到 |
| 2026-10-01 | **4,532** | 越过 4096 → 被切开 / 渲染异常 |

**已执行的修复**：给 `weekly-skill-hunter` 的 `## Output Format` 加了**硬上限 3,500 字符**，并把三个常量表和"宁可报 3 项不要 5 项写满"的取舍规则写进 skill。

> ⚠️ 未根治项：`MAX_MESSAGE_LENGTH = 8000` 本身设错了。改成 4000 可让所有任务自动预先切分，
> 但那是**镜像层源码**（`/opt/hermes/plugins/`），容器重建即丢 —— 要做得写进重建清单。

---

## 三、看门狗盲区

`aa958d178664`（每 3 小时）**本该抓到上面两个，但它一次都没报。**

**原因**：旧实现只 grep 输出文件首行找 `"FAILED"`。而投递失败的任务**输出文件是完全正常的**（任务是 `ok`），故障信息只在 `jobs.json` 的 `last_delivery_error` 里 —— 它**根本不读这个字段**。

**重写后**（`/opt/data/scripts/cron-watchdog.py`，2026-10-11）：

| 分支 | 条件 |
|---|---|
| ① 投递异常 | `last_delivery_error` 非空 **且** `last_run_at` 在 48h 内 |
| ② 运行状态 | `last_status == "error"` |
| ③ 脚本级失败 | 输出首行含 `FAILED` / 文件不可读 |

**防误报规则**：投递异常只在最近 48h 跑过时才报 —— 否则季度任务 9/1 的旧错会被每 3 小时重报一次。
**静默即正常**：无告警时不打印任何内容（空 stdout = 不推送）。

**验证方式（合成测试，不依赖真实故障）**：造一份临时 `jobs.json`，塞三个 job ——
① 在跑+投递失败 ② 陈旧投递失败 ③ 运行报错 —— 把脚本里的 `JOBS_FILE` 路径替换掉再跑。
本次三条分支判定为：① 抓到 ② 正确跳过 ③ 抓到。

---

## 四、C 类：no_agent 的静默语义误用

`b8f47deb5989`（每日用户洞察自动提取，`no_agent`）连续测出 10-08 / 10-09 / 10-10 三天输出**完全相同**：

```
No new conversations to process.     （150 字符）
```

`no_agent` 的语义是 **非空 stdout → 逐字投递**，所以用户**每天 23:00 收到一句零信息消息**。

**管道本身没坏**：`self-profile/conversation-log.jsonl` 66 条全部 `extracted=True`，说明历史上正常干活。
问题是**空闲时仍打印**。

**正确写法**：空闲 → `return`（不 print）；有活 → 才 print。空 stdout = 静默。

**同类嫌疑排查**（已写进 `scripts/cron-fleet-audit.py`）：取某 no_agent job 最近 3 份输出，
剥掉 `# Cron Job:` 头部后比较正文 —— 三份全同 = 每天发同一句。

---

## 五、时区踩坑记录（教训）

`last_run_at` 与 `schedule` **都是 UTC**，服务器 localtime 也是 UTC，北京 = **+8h**。

**两个坑，都实际踩过：**

1. **临场写的换算函数把分和时写反** → 整张表的北京时间全错。
   正确：`北京时间小时 = (UTC小时 + 8) % 24`，**分钟原样不动**。
   → 已把自检断言写进 `scripts/cron-fleet-audit.py`（`0 14 * * *` 必须 = `22:00`）。

2. **`last_run_at` 的异常小时 ≠ 时区搞错。**
   实例：`8d1fc265a9d3` 排程 `0 14 * * *`（=北京 22:00），但 `last_run_at = 10-10T22:00`（=22:00 UTC）。
   一度以为"时区基准不对"。**真实原因：它的 schedule 当天刚被改过，10-10 那次跑的还是旧的 `0 22 * * *`。**
   → 遇到异常小时，先问"这个 job 的排程最近是不是改过"。

**通用教训**：任何派生表格（时区/单位/百分比换算）在呈现给用户之前，用 2–3 个已知锚点验一遍。
用户对**编造的精确表格**零容忍 —— 一张全错的时间表比"我没算准"伤害大得多。
