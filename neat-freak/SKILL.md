---
name: neat-freak
description: Post-session cleanup and sync — check what changed, align wiki/memory/skills/docs, and produce a structured change report. Trigger with /neat, "整理一下", or "同步一下".
---

# neat-freak — Hermes 版文档与记忆对齐工具

每次会话结束后，自动检查本次会话的变更，与 wiki、memory、skill、文档对齐，防止"脑腐"。

**核心哲学**：代码和项目在演进，但文档和记忆不会自动跟上。不是模型变笨，是文档过时了。

## 触发方式

- `/neat` — 直接命令
- "整理一下" / "同步一下" / "对齐文档" — 自然语言
- "sync up" / "neat" — English

## 检查的四层

| 层 | 受众 | Hermes 对应 |
|------|------|-------------|
| **记忆层** | 跨会话的 AI 自己 | memory、SOUL.md、insight 系统 |
| **知识库层** | 人类（你自己） | wiki 目录 — SCHEMA + index + log + 页面 |
| **研究层** | 人类 + AI | research 目录 — 分析报告、技术调研 |
| **工具层** | AI 和人类共同 | skills 目录、config.yaml、cron job、已安装包 |

---

## 执行流程

按以下步骤执行，每步给出报告，**默认不自动修改**，等用户确认。

### Step 1: 扫描本次会话做了什么

从 session_search 和当前对话上下文中提取：
- 本次会话创建/修改了哪些文件
- 新增/修改了哪些 skill
- 新增/修改了哪些 wiki 页面
- 新增/修改了哪些 memory
- 新增/修改了哪些 cron job
- 安装了哪些新工具/包
- 用户表达了哪些新的偏好/习惯/决定

### Step 2: 检查记忆层是否对齐

对照 memory 条目，检查：
- 有没有本次会话暴露的新事实应该存 memory 但还没存？（如新装工具、新项目、偏好变更）
- 有没有 memory 条目已经过时？
- memory 使用率是否接近上限（>80%），需要压缩？

### Step 3: 检查知识库层 + 研究层是否对齐

读 wiki 的 log.md 最近 5 条操作 + index.md：
- 本次会话的 wiki 操作是否全部记录在 log.md？
- index.md 的页数和日期是否正确？
- 有没有本次讨论的技术经验还没录入 wiki？

检查 research 目录：
- 本次会话是否创建了新的研究分析？
- 文件命名是否清晰可检索？
- 是否需要在 memory 中记录路径以便后续查找？

### Step 4: 检查工具层是否对齐

- 本次创建/修改的 skill，SKILL.md 是否正确、触发条件是否完整？
- 本次修改的 config.yaml，是否需要 `/reset` 或重启 gateway 才能生效？
- 本次新增/修改的 cron job，是否在列表里且状态正确？
- 本次安装的新工具（pip/uv），是否需要在 skill 或 memory 中记录？

### Step 5: 生成变更摘要

汇总为结构化报告，格式：

```
## neat-freak 变更摘要

### 本次会话操作
- 新建文件: N 个
- 修改文件: N 个
- Wiki 操作: N 条
- Memory 变更: N 条
- Skill 变更: N 个
- 新装工具: N 个

### 需要同步的事项
1. [memory] 建议新增: ...
2. [wiki] 建议记录: ...
3. [skill] 建议更新: ...
4. [memory] 建议清理: ...

### 已对齐，无需操作
- wiki log 完整 ✓
- index 页数正确 ✓
- ...

### 需要注意
- config.yaml 变更需重启 gateway 生效
- memory 使用率 N%，暂时安全
```

### Step 6: 等待用户确认

向用户展示摘要，逐项确认后再执行修改。**永远不要在没有用户确认的情况下修改 memory/wiki/skill。**

---

## 特殊情况处理

- **如果本次会话没有实质性变更**：直接说"本次会话没有需要同步的变更"，不生成空报告
- **如果会话很短（<5 轮对话）**：可选跳过，提示"会话较短，建议下次再跑"
- **如果发现严重不一致**（如 wiki log 缺失操作记录）：标红提醒，优先修复

## 与原始 neat-freak 的区别

原始 neat-freak 面向 Claude Code 生态（项目文档 + agent 规则 + memory），仅支持 Claude Code / Codex / OpenCode。

Hermes 版覆盖了 Hermes 独有的三层：
- **Wiki 系统**（SCHEMA + index + log + pages）— 知识库核心
- **Memory 系统**（精选条目 + 字符限制）— 结构化跨会话记忆
- **Skill + Cron + Config** — Hermes 的工具链层次

## 铁律

1. 只读扫描，不自动修改
2. 所有修改必须用户逐项确认
3. 报告简洁，不废话
4. 如果一切已对齐，直说"没有需要同步的"，不强行找问题

## Pitfalls

- **新装工具的 memory 决策**：不是所有新工具都需要进 memory。判断标准：工具改变了"我下次如何完成任务"（如 Tavily 替代了 DDGS 做 web_extract）→ 进 memory。工具只是一个可选项（如 Pandoc 只是多了一个转换能力）→ 不进 memory。默认问用户。
- **研究层易遗漏**：research 目录不在 wiki 里也不在 memory 里，但它包含高价值分析。每次扫描必须检查该目录是否有新增文件。
- **Gateway 重启积压**：多次会话累积的 config/plugin 变更如果一直不重启，会在报告里反复出现。如果发现同一变更出现在连续两次 neat-freak 报告中，标记为 🔴 积压提醒。

## 参考

- `references/session-20260605.md` — 首次实战记录：研究层遗漏发现、Gateway 积压修复、路径泛化、工具决策框架
