---
name: skill-writing-convention
description: "新建skill/修改skill/skill写作规范——description前57字符截断规则。"
author: XIAHONGYUU
license: MIT
version: 1.0.0
---

# Skill 写作规范 — triggers 字段

## ⚠️ 机制真相（2026-09-19 实测 Hermes 源码）

**Hermes 核心不读取 `triggers` 字段。**

- `grep -rn "triggers" /opt/hermes --include="*.py"` → 核心代码零命中（只有第三方库的无关匹配）
- 系统提示里的 skill 索引**只用 `description`**，且**截断到前 57 字符**（见 `agent/skill_commands.py:539`、`agent/learn_prompt.py:45`、`agent/curator.py:426`）
- curator 的原话：*"an agent searching skills matches on descriptions, not on exact names (note: long descriptions are truncated to 57 chars in the system prompt skill index — **keep the trigger class in that window**)"*
- learn_prompt 的原话：*"This is the most-violated rule and it is NOT cosmetic: ... anything past char 60 is silently cut and never routes."*

**结论**：
1. **真正决定 skill 能否被路由的是 `description` 的前 57 字符** —— 触发类别必须落在这个窗口内
2. `triggers` 字段是**本机自定义约定**，供 `skill-router` 等自建工具与人类阅读使用，**不参与系统提示渲染**
3. 只补 triggers 而不改 description，对自动触发**无效**

**实测证据**：实测样本中 **57%的 description 超过 57 字符**——超出部分每次会话都被静默截断。典型失败：

| Skill | 系统提示里实际显示的（前57字符） | 问题 |
|-------|--------------------------------|------|
| `dp-tool-research` | `Use when starting a new story, developing a premise, expl...` | 纯英文形式化描述，中文用户说「帮我构思故事」无法命中 |
| `triple-lens` | `Deep structural analysis through three converging lenses....` | 用户说「深入分析」命不中 |
| `ziwei-doushu` | `紫微斗数命理分析。基于《紫微斗数全书》（南北山人）、《紫微斗数精成》（大德山人）...` | 有效，中文关键词在窗口内 ✅ |

**写法**：description 开头 57 字符内必须塞进**用户会说的话语**。

```yaml
# ✗ 差：前57字符是英文形式化描述，中文触发词全在截断线之后
description: "Use when starting a new story, developing a premise, exploring narrative concepts..."

# ✓ 好：中文触发词在最前面，窗口内全是用户会说的话
description: "故事构思/题材选择/创意发散（开题前头脑风暴）。"
```

**判断方法**：把 description 切成前 57 字符，问「只看这 57 个字符，用户说哪句话时我能想起它？」

### 改动何时生效（三层缓存）

skill 索引构建有三层（见 `agent/prompt_builder.py`）：

| 层 | 位置 | 失效条件 |
|----|------|---------|
| 1. 进程内 LRU | `_SKILLS_PROMPT_CACHE` | cache_key **不含文件 mtime** → 改文件后**仍可能命中旧值** |
| 2. 磁盘快照 | `/opt/data/.skills_prompt_snapshot.json` | 带 mtime/size manifest，SKILL.md 一变即失效 ✅ |
| 3. 冷路径 | 全盘扫描 `iter_skill_index_files` | 快照失效时执行，并重写快照 |

**结论**：改完 SKILL.md，磁盘快照会自动失效（manifest 校验），但**进程内 LRU 不会** —— 所以要让新 description 真正进入系统提示，**必须重启 gateway**（或调用 `clear_skills_system_prompt_cache(clear_snapshot=True)`）。

**验证方法**：读 `/opt/data/.skills_prompt_snapshot.json`，看目标 skill 的 `description` 字段是否已是新值。若快照里是新值但会话里仍旧，就是 LRU 缓存没清。

### ⚠️ YAML 引号陷阱（2026-09-19 实测踩坑）

用 `/` 分隔多个变体时，**引号必须包裹整条，不能给每个变体单独加引号**：

```yaml
# ✗ 错：每段单独加引号 —— YAML 整块解析失败，整个 frontmatter 报废
triggers:
  - "有什么相关的skill" / "加载哪个skill" / "用哪个skill"

# ✓ 对：引号包裹整条（含分隔符）
triggers:
  - "有什么相关的skill / 加载哪个skill / 用哪个skill"
```

**后果**：解析失败是**静默**的——`yaml.safe_load` 抛异常，该 skill 的 name/description/triggers 全部读不到。
实测 `skill-router` 因此坏了数月无人发现（讽刺的是，它的职责正是「解决忘了用 skill」）。

**检查方法**：改完 frontmatter 后跑一次全量校验——
```python
import yaml, re, os
for p in <all SKILL.md>:
    fm = re.match(r'^---\n(.*?)\n---', open(p).read(), re.S).group(1)
    yaml.safe_load(fm)   # 抛异常即 YAML 报废
```

## 核心规则（2026-06-07）

**`triggers` 字段是可选的本机约定** —— 它不参与系统提示渲染，仅对本机自建工具（如 `skill-router`）与人类阅读有用。
**真正决定路由的永远是 `description` 的前 57 字符。** 若要写 triggers，见下方格式；不写也不影响自动触发。

### 格式

`triggers:` 跟在 `description` 行之后，位于 `version` 之前：

```yaml
---
name: skill-name
description: Skill description...
triggers:
  - trigger condition 1 (中英文关键词 + 用户短语)
  - trigger condition 2
  - trigger condition 3
version: 1.0.0
---
```

### 触发条件数量

**3-5 条**触发条件，每条包含：
- 中英文关键词
- 典型用户短语
- 适用场景描述

### 示例

```yaml
triggers:
  - user wants to deploy or run A-share quant strategies, backtests, or stock screening
  - user asks about 板块轮动, 行业轮动, 择时策略, 因子构建, or sector rotation
  - user asks "哪些板块在低位" or wants sector valuation ranking/percentile analysis
  - user references QuantsPlaybook, sector-tracker, or similar quant projects
  - user wants daily automated stock analysis or cron-based quant workflows
```

## 执行规则

1. **新建 skill 时**：自动添加 `triggers:` 字段，从 body 或 description 提取触发条件
2. **修改 skill 时**：检查是否有 `triggers:`，若缺失则补充
3. **高频 skill 优先维护**：用户最常使用的 skill 确保 triggers 完整
4. **triggers 放在 description 之后**：在所有 frontmatter 字段中紧随 description

## 关键规则：自然语言触发（2026-07-27 新增）

**triggers 必须用用户实际对话中会说的语言，不是形式化描述。**

### 错误 vs 正确

| ✗ 不会命中 | ✓ 会命中 |
|-----------|---------|
| `user wants to perform triple lens structural analysis` | `深入分析一下`, `拆到本质`, `这个问题的根源` |
| `user desires first-principles decomposition` | `挖到底`, `底层逻辑是什么`, `帮我拆解` |
| `user requests autodecision framework` | `该不该辞职`, `帮我做个决定`, `分析一下利弊` |
| `user wants compatibility matching` | `合不合适`, `两个人能不能在一起`, `帮我看看` |

### 判断标准

对每条 trigger 问：**用户在飞书里打字的时候，会说出这句话吗？**

- 用户会说「帮我分析一下该不该辞职」→ ✓
- 用户不会说「我想使用 autodecision 决策框架」→ ✗
- 用户会说「把这个存到 wiki」→ ✓
- 用户不会说「将当前会话内容持久化到 analog knowledge base」→ ✗

### 覆盖策略

每条 trigger 可以有多个变体（用 `/` 分隔），覆盖用户可能的说法：

```yaml
triggers:
  - "深入分析 / 拆到本质 / 挖到底 / 这个问题的根源 / 帮我拆解"
  - "该不该辞职 / 该不该分手 / 帮我做决定 / 分析一下利弊"
```

### 审计时机

当用户抱怨「某个 skill 从来没用过」时，先查 triggers——大概率是写成了形式化描述而非用户语言。

### 精确触发 vs 模糊触发（2026-07-27 skill-router 教训）

**对于需要用户显式调用的 skill，triggers 必须精确到锚点短语，不能模糊到被日常对话淹没。**

案例：`skill-router` 原 triggers 包含 `"帮我分析"`——这句话在日常对话中每分钟出现一次，导致 skill-router 被大量误触发或完全被忽略。修改为 `"用哪个skill"`、`"有什么相关的skill"` 后，只有用户真正需要技能推荐时才命中。

| 场景 | ✗ 模糊（不要用） | ✓ 精确（用这个） |
|------|-----------------|-----------------|
| 技能路由推荐 | `帮我分析`、`帮我看看` | `用哪个skill`、`有什么相关的skill` |
| 决策引擎 | `纠结`、`怎么选` | `帮我做决策`、`跑 autodecision` |
| Wiki 记录 | `记录一下` | `存到wiki`、`更新wiki` |

**判断标准**：如果一条 trigger 短语在用户日常对话中每周出现 3 次以上但该 skill 不需要每周触发 3 次，这条 trigger 太模糊了。

## SKILL.md 精简原则（2026-07-27 新增）

**SKILL.md 是 agent 的执行指令，不是给人类看的操作笔记。** 臃肿的 SKILL.md 在每次加载时吃掉 context window。

### 什么必须留在 SKILL.md

- 核心工作流步骤（agent 执行需要知道的操作序列）
- 必需的配置参数（文件路径、命令、API 端点）
- 输出格式模板
- 高频触发的 pitfall 规则（≤5 条，会导致执行失败的）

### 什么应该移到 references/

- **历史数据**：field notes、star 追踪、运行日志。不影响下次执行。
- **详细案例研究**：完整决策模板、run learning 叙事。保留 3 行摘要 + `详见 references/xxx.md`。
- **低频 pitfall**：发生过一次且有 workaround 的边界情况。保留一句话警告，移详细修复方案到 references。
- **超过 3 条的同主题细节**：如 JSONL append 有三种方法——只留最可靠的 Method A，其余移 references。

### 判断标准

对 SKILL.md 里的每一段问：**如果删掉这一段，agent 下次执行这个 skill 会出错吗？**

- 会出错 → 留在 SKILL.md
- 不会出错，但对人类阅读有帮助 → 移到 references/
- 都不会 → 删除

### 修剪后验证

1. `skill_view(name='xxx')` 确认加载正常
2. 对照原版检查核心工作流是否完整、关键 pitfall 是否保留
3. 检查 references 链接是否正确

### 案例参考

- `weekly-skill-hunter`（2026-07-27）：18,847→6,927 字节（-63%）。移走：8 周 field notes、带日期的 pitfall 叙事。保留：查询模板、API 命令、过滤标准、输出格式。
- `autodecision`（2026-07-27）：22,681→19,279 字节（-15%）。移走：3 个推荐模式完整模板 → `references/recommendation-patterns.md`。

---

> **关于支持文件**：本仓库只收录可公开发布的部分。正文中提到的部分 `references/*.md` 是本机带日期的审计快照/案例存档，未随仓库发布。
