# 文件归位与引用同步（2026-09-19 实战）

12 个根目录散落文件归位的完整记录 + 可复用的引用同步清单。

## 本次归位目录表（决策依据）

| 文件 | 归入 | 判断依据 |
|------|------|---------|
| 气敏传感器读出SoC项目讨论纪要.md | `research/` | 研究方向权威文档 |
| 气敏传感器接口芯片研究介绍.md | `research/` | 同方向旧版 |
| future_self_phd_decision.json 等 **6 个** `*_analysis.json` | `self-profile/decisions/phd-decision/council-2026-06-01/` | 与已存在的 `council-2026-06-07/` 是**同一决策的两轮记录** |
| finance_top3.py / finance_top3_ak.py | `stock-picker-check/scripts/` | 量化脚本 |
| 战锤巫师世界书.json / 雷恩·奥古斯都.png | `novel-workspaces/zhanchuifashi/` | 该作设定（SillyTavern 世界书格式）与角色图 |

**关键判断：孤儿文件往往不是孤儿。** 根目录那 6 个 JSON 初看像历史遗留，实际与 `self-profile/decisions/phd-decision/` 下已有的第二轮 council 是同一条决策链——**归入带日期子目录保留两轮时间线**，而不是当垃圾清理。动手前先看目标目录已有结构，能避免毁掉档案的完整性。

## 根目录锚定文件（不可移动）

| 文件 | 为什么必须在根目录 |
|------|------------------|
| `SOUL.md` | Hermes 人设文件，系统按根目录路径读取；移动会破坏人格加载。被 `neat-freak`、`hermes-s6-container-supervision` 等 skill 引用 |
| `PROJECTS.md` | 项目索引，设计上供主动读取以定位项目（文件内自述「Hermes 在处理『检查XX项目』类请求时，优先读取此索引定位目录」） |

**顺带发现**：`PROJECTS.md` 内容会过时（实测停留在 6-06：缺 sector-tracker/self-profile/research 等新工作区、novel-workspaces 说 2 本实际 5 本）。**归位工作完成后应主动指出这个缺口并询问是否更新**——它是活文档，过时会导致项目定位错误。

## 引用同步清单（移动后必查）

扫描命令：

```bash
grep -rl "<文件名>" /opt/data/skills /opt/data/memories /opt/data/cron \
  /opt/data/scripts /opt/data/self-profile /opt/data/*.md
```

| 命中位置 | 处理 |
|---------|------|
| `skills/**/SKILL.md` | **改**（活文档） |
| `memories/MEMORY.md`、`USER.md` | **改**——但必须走 `memory` 工具的 `replace`，不能直接写文件（会拒绝） |
| `skills/**/references/*-snapshot.md`、`asset-inventory-*.md` | **不加改内容，改为顶部加注记**：说明迁移日期 + 新路径 + 「现行以 SKILL.md 为准」 |
| `cron/output/*.md`、`self-profile/conversation-log.jsonl` | **不动**（历史记录） |

**注记模板**（加在 references 历史快照文件最前面）：

```markdown
# ⚠️ 历史快照说明（YYYY-MM-DD 注记）

> **本文档是 YYYY-MM-DD 的审计快照，保留原样用于时间线对比。**
> YYYY-MM-DD 已执行的路径变更（见 `cache/file-relocation-log.json`）：
> - `旧文件.md` → `新目录/`
> 下文中的旧路径为**当时状态**，现已失效。当前路径以 `SKILL.md` 为准。
```

## 回滚

移动日志（append-only JSONL）在 `/opt/data/cache/file-relocation-log.json`：

```json
{"timestamp": "...", "moved": [{"file": "x.md", "to": "research"}]}
```

按 log 反向 `shutil.move` 即可回滚。**移动用 `shutil.move` 而非 `mv` 命令行**，便于在 Python 里同时做存在性检查与日志记录。

## 为什么顺序不能颠倒

本次实测：12 个待移文件中 **8 个被 skill 文档引用**（作为资产清单/审计基线）。若先移后查，会同时坏掉 `phd-application-docs/SKILL.md` 的 4 处路径 + 两个记忆文件——而这些都是「下一次会话读到的第一手信息」，坏了就是持续的路径错误。**先 grep，再 move。**
