---
name: workspace-status-audit
description: "检视工作区/生成状态总结/磁盘占用/哪些过时了/备份检查——mtime与磁盘大头审计。"
author: XIAHONGYUU
license: MIT
version: 1.1.0
metadata:
  hermes:
    tags: [workspace, audit, status, disk, career, migration, delta-export]
    related_skills: [workspace-file-relocation, resume-editing, shipping-enforcer, neat-freak, personal-data-tracking]
    support_files: [references/workspace-delta-export.md, references/2026-09-25-baseline.md, references/2026-08-29-snapshot.md]
---

# Workspace Status Audit

## When to Use

用户（或父代理/定时任务）要求「检视工作区状态」「生成状态总结」「看磁盘占用」「哪些东西过时了」「从某个具体目标角度看缺什么」时使用。典型触发：过渡期/阶段转换/申请季开始前的整体盘查，或任何需要判断「工作区现实 vs 用户现实」是否脱节的场景。

对用户的 `/opt/data` 数据工作区做**只读**审计，输出：每项状态 + 缺口 + 磁盘大头分布 + 按用户当前目标排序的待办。**不修改任何文件。**

## Workflow

0. **多 agent 并行模式**（用户偏好：主动要求「多agent分析/检视」）：
   - 用户明确要求多 agent 时，用 `delegate_task(tasks=[...])` 分批派发，**每批 ≤3 个**（delegation.max_concurrent_children=3，超出报错）。
   - 按域分组：创作（小说）/ 金融（量化）/ 职业-研究-系统 / 运维，每任务独立 context 说明该域背景 + 明确「只读不修改」。
   - 输出统一结构：每工作区「角色 + 内容量 + 健康度 + 缺口 + 最该推进的一件事」。结果自动回到会话，合并成一张总表。

1. **磁盘大盘**：跑 `scripts/measure-workspace-sizes.py` —— **三分法**（真数据 / `.git` / 依赖缓存）。
   ⚠️ 两种错法都要避开：`du -sh /opt/data/*`（通配符混入伪顶层项）**和**自己手写带 `SKIP={'.venv','.git'}` 的 walk（会把 751 MB 报成 2 MB）。两条都在 Pitfalls 里，成因相反。
   跑完再分清「数据目录」和「缓存目录」，缓存类单独标注可回收量。
   **别默认任务目标是"腾空间"** —— 先看 `du -smx`：如果还剩 30–40% 空闲，这次审计的真价值是**收口决策**，不是清理（实测用户 18 GB 空闲时，最有价值的产出是"哪些该归档/冻结"而不是"能删多少"）。⚠️ **`df` 报的是宿主整块盘，不是工作区** —— 见 Pitfalls 里的 bind mount 条。
2. **逐工作区盘点**：`search_files(target='files')` 列文件（结果按 mtime 排序）或 `ls -la`；读取关键状态文件（README、config.yaml、current.md、最新 data.csv、jsonl 尾部）。
3. **过时检测**：比较**文件 mtime** 与用户现实状态。所有文件冻结在日期 X、而用户生活状态在 X 之后已变化 = 红旗（典型：简历仍写「在职」而现实已在两个月前改变）。
4. **上下文交叉验证** ⚠️ 任务/提示词里给的数字会滞后（实测：任务写「体重 98→97.5」，实际 data.csv 最新是 97.0）。引用任何指标前直接读原始数据文件。
5. **用户状态源**：最新生活/职业状态在 `self-profile/conversation-log.jsonl`（尾部）和 `insights.jsonl`。判断这类文档是否过时前，先读这里。
6. **系统健康**：检查 `backups/` 是否为空（空 = 无备份机制，红旗）、`cache/` 大小、`state.db` 体积、`home/` 下隐藏缓存。磁盘「谜团目录」（如 935M 的 home）要拆解到子目录找原因，通常是大头缓存。
7. **输出结构**（用户偏好：中文、结论具体、不啰嗦）：
   - 每项：状态（✅/⚠️）+ 缺口
   - 磁盘大头分布（含谜团拆解）
   - 按用户当前目标排序的「最该补的事」，带优先级

## 使用轨迹分析（问题变成「我是如何使用 AI 的」时）

工作区审计只回答「有什么」；当用户问「评估一下我是如何使用 AI 的」时，磁盘之外还要叠一层 **state.db 行为分析**。可复用的探查脚本：`scripts/ai-usage-stats.py`（一次跑出下面全部指标）。

| 维度 | 查询 |
|------|------|
| 规模 | `SELECT COUNT(*) FROM sessions` / `FROM messages` |
| 交互 vs 自动化 | `SELECT source, COUNT(*) FROM sessions GROUP BY source`（cron 占比高 = 自动化主导） |
| **真实活跃度** | 按周统计 `role='user'` 的**消息数**：`SELECT strftime('%Y-%W', datetime(timestamp,'unixepoch')) wk, COUNT(*) FROM messages WHERE role='user' GROUP BY wk` |
| 工具分布 | `SELECT tool_name, COUNT(*) FROM messages WHERE role='tool' GROUP BY tool_name` |
| Token/成本 | `SELECT source, SUM(input_tokens), SUM(output_tokens), SUM(estimated_cost_usd) FROM sessions GROUP BY source` |
| Cron 清单 | `/opt/data/cron/jobs.json`（数量/启用/暂停/是否 pin 模型） |

**解读要点**（决定结论质量，不是机械输出）：
- **cron 会话多但成本低 + 交互会话少但成本高** = 用户对话密度高，不是碎片化聊天——这是正面信号，别读成「自动化空转」。反过来才是问题。
- **delegate_task / execute_code 占比 <2%** = 并行与程序化能力闲置，是最大的可提升杠杆（用户为此付了单线程的代价）。
- **装了 N 个 skill 但只用过 M 个**（从 tool_calls 解析）——未加载的 skill 是每次对话的 token 税，给出「清理到 X 个」的具体建议。
- **工作区半衰期规律**：有 cron 或有外部 deadline 的活跃；纯靠「我想做」的停滞（实测：运维型 0-2 天 vs 项目型 90-102 天）。这个对比比单列「哪些过时」有说服力。
- 结论要给**可执行杠杆**（3 条以内），不要只给画像描述。

## 文件归位（用户批准后的执行动作）

审计本身只读；但当用户说「把散落文件分类/归位」时按以下顺序做。**移动文件会打断已有的路径引用链**，所以顺序不能颠倒。详见 `references/file-relocation-sync.md`。

1. **先做引用检查，再动文件**：`grep -rl "<文件名>"` 扫 `skills/ memories/ cron/ scripts/ self-profile/ *.md`。这一步决定后面要同批修哪些文件。
2. **区分「能移」和「根目录锚定文件」**：有些文件**故意放在根目录**，移动会破坏功能——实测 `SOUL.md`（Hermes 人设，系统在根目录读取）和 `PROJECTS.md`（项目索引，供主动读取定位项目）。**这类文件在清单里明确标 SKIP 并说明理由**，不要默默移走。
3. **决策优先「归入已有目录」而非新建**：先查目标是否已存在（如 `self-profile/decisions/phd-decision/` 已有档案结构）→ 归入；根目录裸放的旧轮次输出应进**带日期的子目录**（`council-2026-06-01/`），保留时间线而非混入现行目录。
4. **移动 + 记日志**：用 `shutil.move`（先 `os.makedirs(dest, exist_ok=True)`、查目标是否已存在同名以避免覆盖），逐项记 `cache/file-relocation-log.json`（时间戳 + file→to），这是回滚依据。
5. **移动后必须同步引用**（漏掉会制造新故障）：
   - `skills/**/SKILL.md` 里的路径 → **必须改**（活文档，路径错=功能错）
   - `memories/MEMORY.md` / `USER.md` 里的路径 → **必须改**（用 memory 工具的 replace，不是直接编辑文件）
   - `references/*-snapshot.md` / `asset-inventory-*.md` → **不要改内容**（历史快照要保留原样供 diff），改为在文件顶部**加一段注记**说明「XX 日期已迁移，下列旧路径失效，现行路径以 SKILL.md 为准」
   - `cron/output/`、`conversation-log.jsonl` → **不动**（历史记录）
6. **收尾验证**：跑双向检查——原位置 12/12 已移走 + 目标位置 6/6 到位，并把结果给用户。

**陷阱**：`patch`/`write_file` 到 `memories/` 会被拒（记忆要走 memory 工具）；但 skill 目录下的 `references/` 用 `skill_manage write_file` 正常。

## 增量导出（用户把内容迁到别的机器之后）

当用户的工作区是**分布式**的（服务器端 Hermes + 本地 Codex / DeepSeek 等），且他曾基于某次审计清单把内容拷到本地时，任务会从「看现状」变成「算差量」。核心判断：**服务器是真相源，本地是消费端，只做单向导出，绝不双向同步**——两个 agent 都会写，双向冲突是静默的（后写覆盖，不报错）。

完整流程见 `references/workspace-delta-export.md`：基线钉定、mtime 差量、噪音/信号取舍（实测 43 MB 噪音 vs 2.66 MB 真内容）、manifest 格式、README 三条使用规则、小工作区为何要整拷。

## 上传候选评估（问题变成「哪些东西适合上传 GitHub」时）

审计只回答「有什么」；当用户问「**工作区里哪些东西适合上传 GitHub**」时，任务变成**可发布性分级**：能公开 / 只能 private / 不能上 / 另走 fork-PR。

**交付形状是一张分类清单，不是清理方案，也不是打包动作** —— 清单之外的动作只在用户要求时才做。

四个要点先记住（完整流程见 `references/github-upload-candidacy.md`）：
1. **先验证上传通道，再排候选** —— PAT 用**认证端点**（`curl -H "Authorization: token $T" https://api.github.com/user`，200=有效），SSH 用 `ssh -T git@github.com`（`Permission denied (publickey)` = 公钥未注册）。**两条路都要试**；一个「可上传但不能推」的清单对用户没有价值。
2. **真正的障碍通常不是隐私，是可移植性** —— 隐私类打 private 就解决了；而脚本里硬编码的本机绝对路径/私有 venv 必须逐个参数化，否则「发出去别人跑不了」。实测运维脚本目录 **27/31** 命中。
3. **一定要查 LICENSE**，两个方向：上游无 LICENSE → **没有合法的贡献路径**（如实说，别讲「fork 一下就好」）；用户自己的仓库无 LICENSE → 等于「保留所有权利」，值得指出。
4. **private 仓库也是「上传」** —— 对含个人/健康/心理数据的目录，它解决的是**异地备份**（正好补 `backups/` 为空那个红旗）。主动说明，否则用户会把大半个工作区误划掉。

**另有两类只有在这里才暴露的坑**：**雇主 IP**（前公司文档归档 → 任何形式都不行，private 也不行）和**衍生作品版权**（基于他人 IP 的同人创作 → public 有风险、private 可以）。

## Pitfalls

- **用户说的时间不可信，基线必须钉到日期**：实测用户说「前几天」，实际是 **13 天前**。基线错则整个差量全错。
- **`session_search` 对中文多词查询经常返回 0**：`"目录结构" OR "工作区结构"` 这类带引号的 OR 组合实测 0 命中，而同一批内容**直查 `state.db` 立刻命中**。失手时不要放弃，改为直查：user 侧 `content LIKE '%kw%'` 找「他什么时候问的」，assistant 侧同法找「我交付的是什么」——**两个角色都要查**，缺一侧只能答一半。**绝不因为 session_search 返回 0 就告诉用户「找不到那次讨论」**。
- **差量必须过滤掉可再生内容**：每日自动生成的报告 JSON + 自动重生的 sqlite 可占差量的 76%（实测 43 MB / 56.85 MB）。不筛就交付一个十几倍虚胖的包，用户会以为要搬很多东西。筛法见 `references/workspace-delta-export.md`。
- **`du -sh /opt/data/*` 不可靠**：shell 通配符会把 `.venv`/`site-packages` 等嵌套目录也展开进来，输出里混入 `python3.13`、`lib` 这类伪顶层项。改用 Python：`for name in os.listdir(TOP)` + `subprocess.run(['du','-sm',p])`，或 `du -sh /opt/data/*/ | sort -rh`。
- **量磁盘别"顺手跳过" `.venv`/`.git`** ⚠️（2026-10-11 实际翻车，误差 43 倍）：扫描时按「读代码」的习惯写了 `SKIP={'.venv','node_modules','.git',...}`，结果 `stock-picker-check` 报 **2 MB**（实际 **751 MB**，其中 749 MB 是 `.venv`）、`silicon-eye` 报 96 MB（实际 **469 MB** = 277 MB `.venv` + 96 MB `.git`）、`novel-workspace-pipeline` 报 633 MB（实际 806 MB）、`home/` 报 225 MB（实际 1,006 MB，830 MB 是 `.cache`）。**用户会据此以为"没东西可清"，与事实完全相反。** 这与上一条 `du` 通配符陷阱是**相反方向**的两个错：`du /opt/data/*` 多算，带 SKIP 的手写 walk 少算。**磁盘审计一律跑 `scripts/measure-workspace-sizes.py`（三分法），并把「`.git` 与依赖」和「真数据」分开报** —— 前者可再生，后者才是用户的资产；对"该不该收口"的判断，`真数据 96 MB + 依赖 277 MB` 比 `合计 469 MB` 有用得多。
- **停滞项目能不能回收，先查 git 远端 + 未提交改动**：对每个待处置目录跑 `git -C <dir> remote -v`（有远端 → 本地可重建）和 `git -C <dir> status --porcelain`（**未提交改动是唯一不可再生的部分**）。实测 2026-10-11：`novel-workspaces` / `novel-projects` **无远端 = 本地唯一副本，不可删**；`TradingAgents`（TauricResearch）、`dreampowers`（skyfiredao）是第三方 clone，随时可重来；而 `stock-picker-check` 是**别人的仓库（xiaopengm3-ai）却带着 21 个未提交改动** —— 这种必须先提交或明确丢弃，否则"清理"就是静默销毁在制品。⚠️ `.git` 可能远大于代码本身（实测 pipeline 的 `.git` = **170 MB**，silicon-eye 的 = 96 MB），它是**版本历史**不是依赖，回收前必须确认远端**真的可用** —— 凭据失效不等于可用（见下条 GitHub 凭据）。
- **第三方仓库里的未提交改动，八成是用户自己的作品 —— 查「最后一次提交的作者」** ⚠️（2026-10-11 实测）：`stock-picker-check` 有 21 项未提交，`git log -1` 显示最后一次提交是**上游作者 Leo**，而工作区 diff 是 **811 增 / 250 删、19 个文件** + 3 个新文件；`TradingAgents` 同样（上游提交 + 用户新增 `tushare_provider.py`）。**两次都是同一条工作线：Tushare 数据接入** —— 一个在框架层（provider 抽象），一个在应用层（27 因子引擎）。
  **判据**：`git log -1 --format='%an'` 不是用户本人 **且** `git diff --stat` 规模大 **且** 改动集中在有主题性的模块 → **这是用户的在制品，不是上游残留**。
  **给用户的结论要直说后果**：「清理这两个目录 = 静默销毁 811 行你自己写的代码」——比列一行「21 个未提交改动」有力得多，用户才知道这不能"顺手清"。
  **零风险第一步**（不删不改）：`git -C <dir> diff > patch` 存改动，或 commit 到本地分支（远端是别人的仓库，推不上去；需自己的 fork）。
- **区分「停滞」和「死亡」用停滞天数的增量** ⚠️：把每个目录的最新 mtime 换算成「距今天数」，**与上一次基线逐项对比** —— 若所有死目录的天数**整齐地 +N（N = 两次审计的间隔天数）**，说明这 N 天里零字节变化。**这不是「停滞」，是「已经结束」。**（实测 9/25→10/11 间隔 16 天，8 个项目全部严格 +16。）这个判据比单看「停了 127 天」有说服力：它排除了「偶尔碰一下」的可能，也让「该收口了」这个结论无法被含糊过去。
- **`df` 报的可能是宿主整块盘，不是工作区** ⚠️（2026-10-11 实测；此前连续多轮审计都在报错）：`df -h /opt/data` 显示 `40G 21G 18G 54%`，但 `/opt/data` 只是宿主 `/opt/hermes-agent/data` 的 **bind mount**（`findmnt -T /opt/data` 可见 `SOURCE=/dev/vda2[/opt/hermes-agent/data]`）。**同一块 `/dev/vda2` 上还压着 Docker 镜像层 + 容器根（`/usr`+`/opt/hermes`）**，从容器内**看不见、也回收不了**。实测：`df` 报 21G，而 `du -smx /opt/data` = **7.06G**，差额 ~10.7G 全在容器外（容器可见的 `/` 另占 3.26G）。**规则：量工作区一律用 `du -smx <path>`；`df` 只用来判断宿主盘还剩多少。第一步先跑 `findmnt -T <path>` 确认是不是挂载点** —— 否则会把宿主盘数字当工作区，连续几个月报给用户，用户也会据此做错决策。
- **⚠️「处理一下工作区」默认是「盘点」，既不是「清理」也不是「处置」**（2026-10-11 实际翻车）：用户说「处理一下我们的工作区，再评估一下我们各个工作区都是什么内容」，我直接上了三层处置方案（T1 回收 / T2 拍板 / T3 备份 + 磁盘三分法 + 未提交改动风险），用户明确纠正：**「我们不是要清理空间，我只是想看一下各个工作区都有什么，现在的磁盘空间远远没到要清理的时候。」**
  **规则：先出纯内容盘点** —— 每个目录里*装的是什么*（文件构成、关键子文件、内容主题、最后动了什么），**不带任何回收/归档/冻结/处置框架**。处置建议只在用户明确问「能清什么 / 该归档什么 / 该冻什么 / 能不能删」时才给。
  「处理」这个词有歧义，但在工作区语境下用户多半指「让我看懂里面有什么」。**别用磁盘数字开篇**（那会立刻把话题锁到空间上）。本条优先级高于下面那条 `df` 陷阱——下面是「别做成空间优化」，本条是「连处置都不必给」。
- **别默认任务目标是"腾空间"**（同族陷阱，次优先）：先看 `df`。实测用户 21G/40G 时说"处理一下工作区"，此时**最有价值的产出不是"能删多少"，是"哪些该归档/冻结/收口"**。把审计做成空间优化，会让真正的问题（`backups/` 为空、停滞项无人拍板）被一个漂亮的合计数掩住。**注意：这条只在你已经确认用户要「处置」之后适用。**
- **`df /opt/data` 报的不是工作区大小**（2026-10-11 实测）：`/opt/data` 常是宿主目录的 bind mount（`findmnt -T /opt/data` 看到 `SOURCE /dev/vdaX[/host/path]`），`df` 给的是**整块分区**的数字——里面同时有 Docker 镜像层和容器根。实测用户被告知几周的「21G/40G」实际是宿主分区，**工作区只有 7.06 GB**，另有约 10.7 GB 在容器外、从里面动不了。**要报工作区大小，一律用 `du -smx /opt/data`。**
- **state.db 没有 `created_at` 列**：时间列是 `started_at` / `last_activity_at`（unix 秒）；messages 的时间列是 `timestamp`。`datetime(col,'unixepoch')` 转换。
- **会话数 ≠ 活跃度**：长会话会被复用（一个 session 跨多天），按 session 计数会把活跃用户误判成「使用下降」。判断趋势一律用 **user role 的消息数按周聚合**。
- **解析 skill 使用不能用正则 `"name":"..."`**：`messages.tool_calls` 是 JSON，里面既有工具名也有参数名。要 `json.loads(tc)` 后筛 `function.name == 'skill_view'`，再取 `arguments.name`（字符串需二次 `json.loads`）。用正则会把 `session_search`/`terminal` 这类工具名误统计成 skill。
- **目录 mtime 不可靠**（文件增删会更新目录 mtime），过时判断一律用文件 mtime。
- **任务上下文 ≠ 事实**：任务描述的数字、甚至旧洞察里的趋势判断都会滞后或已反转，一律以原始数据文件为准。
- **历史 baseline 里的「审计发现」是快照，不是现状** ⚠️（2026-10-08 实际翻车）：9/25 清单的 P0 写着「`current.md` 仍写『在职』」，我在交付差量包时**直接转述成「❌ 13 天未处理」——实际 9/27 就被 cron 修好了**（第 3 行已无「在职」、工作经历改为 `2021.08 – 2026.08`、docx 内 XML 原地替换后「在职」「至今」各 0 次；只剩 `versions/2026-06-07_v4.md` 保留原样，那是**版本归档，本就该冻着**）。**规则：baseline 只用来回答「那天是什么样」；任何「现在是否仍然如此」的断言，必须逐条回读原文件再加 ❌/✅。** 差量报告里最容易被无意识继承的就是「❌ 未处理」这类标记——它描述的是基线当天，不是你交付的那天。
- **占位符残留**：工作区常有占位符（如简历里的 `[公司名称]`）与真实信息（在 targets/ 等其他文件里）并存——主动交叉查找，指出「占位符未填」这种低成本缺口。
- **材料散落**：同名主题文件常散在多个目录（实测：`research/` 只有 1 个文件，其余气敏研究材料散在 `/opt/data` 根目录）——审计要全局搜索，不要只信目录名。
- **只读原则**：审计不修文件；修复建议写进输出。
- **版本链≠新鲜度**：versions/ 里 v1–v6 齐全不代表文档最新——版本停留在半年前，看最新 mtime 而非版本号。
- **判「重复」前先查符号链接** ⚠️（本条优先于下一条）：近名目录并存时**第一步是 `ls -la` 看有没有 `->`**，不要直接 `diff`。实测 `novel-workspaces/巫师世界 -> wushishijie`、`战锤巫师 -> zhanchuifashi` 是**有意设计的别名软链**，不是冗余副本。跳过这步会得出危险结论：`du -sm` 对软链返回 ~0MB，与实体目录并列；`diff -rq 软链 实体` 无输出 → 极易误读成「内容完全一致，可删一份」。**诱因特征：两处文件数完全相同 + 其中之一 `du` 为 0MB。**
- **重复副本判定权威版**：同名工作区出现在多个目录时，用 `diff -rq A B` 判断：文件更多（超集）+ mtime 更新 + 无「Only in A」= 权威版，另一份可合并/归档。实测：novel-workspaces/wushishijie（6/12）是权威，pipeline/巫师世界（6/2）是旧副本（117M 可删）。⚠️ **先 `ls -la` 排除 symlink**——符号链接会被 `diff` 误报为「完全一致」，`du` 显示 0MB（实测 `巫师世界 -> wushishijie`、`战锤巫师 -> zhanchuifashi` 是**有意设计的中文别名链接**，不是重复副本，删了就丢数据）。
- **⚠️ 输出层会把 `Authorization: token <值>` 里的值掩成 `***` —— 别据此判断「脚本坏了」**（2026-10-13 实际误判）：读 `check_gh_cred.sh` / `probe_gh_perm.sh` 时，显示出来的是 `curl -H "Authorization: token ***"`，我据此几乎断定「token 变量没被使用，脚本是坏的」。**实际文件里写的是 `$TOKEN`** —— 是**工具输出层对凭据样式的误掩码**（把变量引用也当成密钥）。**验证靠计数不靠肉眼**：`grep -c 'Authorization: token \*\*\*'` → **0 处**，`grep -c '\$TOKEN'` → 1/3 处，且与 Authorization 行数吻合。**规则：任何"这段代码看起来是坏的"的判断，落结论前先用 grep 计数在原始文件上验证一遍。** 掩码只影响显示，不影响磁盘。
- **⚠️ ssh 找不到钥匙：容器的 `/etc/passwd` 家目录 ≠ `$HOME`**（2026-10-13 实测，会被误读成「公钥没注册」）：hermes 容器的 passwd 条目是 `hermes:x:10000:10000::/opt/data:/bin/sh`（家目录 `/opt/data`），而 **`$HOME=/opt/data/home`** —— 钥匙与 `~/.ssh/config` 都在后者。于是 `ssh -T git@github.com` 去 `/opt/data/.ssh/` 找钥匙 → `Permission denied (publickey)`，**而 `ssh -i /opt/data/home/.ssh/id_ed25519 -T git@github.com` 直接成功**。**症状与「公钥没注册/凭据失效」完全相同，极易误判 —— 会让人白去 GitHub 重加一次公钥。**
  **诊断顺序**：`echo $HOME` → `getent passwd $(whoami)` → `ssh -vT git@github.com 2>&1 | grep -i 'identity file|offering'`（看它实际在哪个路径找）。
  **修法两道，都落在卷内所以能扛容器重建**：① `git config --global core.sshCommand "ssh -i /opt/data/home/.ssh/id_ed25519 -o IdentitiesOnly=yes"` → git-over-ssh 全可用；② 在 `/opt/data/.ssh/` 给 `config` / `id_ed25519` / `id_ed25519.pub` 建软链 → plain `ssh -T` 也通。**②不能省** —— 否则下次（或别的 agent）跑 `ssh -T` 会看到一个误导性的 `Permission denied`，把已经修好的事又判成坏的。
  顺带：`git config --global user.name/user.email` 在这种容器里可能**是空的**，push 前一并补上。
- **GitHub 凭据复用有前提，别默认它有效**：容器无 gh CLI 时，查 remote 找内嵌 token（`git remote get-url origin`）只是**候选来源**，不是保证。2026-09-12 实测：silicon-eye remote 内嵌的 x-access-token 已失效（API 401），且**从源仓库重新提取的老办法是死路**（源仓库本身失效）。验证必须用**认证端点** `curl -H "Authorization: token $TOKEN" https://api.github.com/rate_limit`（200=有效）——⚠️ **不要用 `git ls-remote` 验证**：公开仓库无需认证即可读，凭据失效时它照样 exit 0（假阳性）。完整流程与长期方案见 `references/github-credential-persistence.md`。

## References

- `scripts/ai-usage-stats.py` — 一次跑出使用轨迹全部指标（规模/来源/活跃度/工具/skill/成本/cron/工作区半衰期）。配合「使用轨迹分析」章节解读。
- `scripts/measure-workspace-sizes.py` — **磁盘三分法量尺**（真数据 / `.git` / 依赖缓存），并单列 >50 MB 的可再生重灾区 + 回收前必查的 git 命令。磁盘审计的第一步，替换 `du -sh`。配合 Pitfalls 里的两条量尺陷阱。
- `references/file-relocation-sync.md` — 文件归位实战：归位决策表、根目录锚定文件（SOUL.md/PROJECTS.md 不可移）、移动后引用同步清单与注记模板、回滚日志格式。配合「文件归位」章节使用。
- `references/workspace-delta-export.md` — 跨机器增量导出：基线钉定、mtime 差量、噪音/信号取舍、manifest 格式、README 三条规则、单向导出而非双向同步。配合「增量导出」章节使用。
- `references/github-upload-candidacy.md` — **上传候选评估**：通道双路验证（PAT 认证端点 / SSH `ssh -T`）、七个分类轴（隐私·雇主 IP·第三方正文·凭据·衍生作品版权·可移植性）、**密钥扫描的两步法与假阳性陷阱**（CSS design token / `*_tokens` 字段名会淹没真信号；判据是「20+ 字符字面量被赋给密钥变量」命中 0）、LICENSE 对分发权与 fork-PR 合法性的决定作用、private 仓库作为异地备份、运维脚本目录的三档分法、输出模板。配合「上传候选评估」章节使用。
- `references/2026-10-11-audit.md` — **最新审计基线（算差量以此为起点）**：磁盘纠偏（`df` 21G vs `du` 7.06G 的成因）、三分法全表、工作区生命周期分类（5 活 / 3 新鲜 / 4 半死 / 8 死亡）、处置分级 T1–T3、9/25 六项发现的逐条回读结果。
- `references/2026-10-11-inventory.md` — **同日的「内容盘点」（与上篇互补，不是重复）**：上篇是处置视角，这篇是**用户真正问的那个问题的答案——每个工作区里装的是什么**。含逐目录的文件构成/关键子文件/最后改动、**git 安全表（远端 vs 未提交，哪些目录不能直接动）**、Tushare 工作线诊断（811 行是用户自己的在制品）、以及「用户澄清只想要盘点、不要处置方案」的记录。**用户再问「各个工作区都有什么」时，照这篇的形状交付。**
- `references/2026-09-25-baseline.md` — **用户实际迁移基线**：9/25「工作区内容评估清单」全文（A 活跃 16 / B 停滞 5 / C 长期停滞 8 / D 系统层 + 6 项审计发现）。用户本地那份拷贝对应的就是这个时点——**算差量用它，不是 8/29**。
- `references/2026-08-29-snapshot.md` — /opt/data 工作区地图、磁盘大头分布、用户职业/研究状态快照（2026-08-29 审计基线；后续审计可与之 diff 判断增量变化）。
- `references/2026-08-29-late-session.md` — 会话后增量：小说工作区 5 目录职责、旧方案归档、重复副本权威判定、GitHub x-access-token 凭据复用、系统配置状态（title_generator/内存/缓存）。
- `references/github-auth-longterm.md` — **凭据验证与长期方案（以此篇为准）**：`git ls-remote` 假阳性陷阱、认证端点验证法、x-access-token 失效与源仓库重提路径作废、SSH key + 无过期 classic PAT 配置。
- `references/github-credential-persistence.md` — 历史篇：token 发现与 store 持久化步骤（仍可用），但验证/恢复结论已被上篇修正。

---

> **关于支持文件**：本仓库只收录可公开发布的部分。正文中提到的部分 `references/*.md` 是本机带日期的审计快照/案例存档，未随仓库发布。
