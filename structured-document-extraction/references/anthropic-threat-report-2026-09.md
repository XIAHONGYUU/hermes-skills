# 实战案例：154 页威胁情报报告 → 42 案例数据集

**源**：Anthropic《Detecting and countering misuse of AI: September 2026》（2026-09-11 处理）
**规模**：154 页 PDF / 259,139 字符 / ~62K token / 46 个顶级章节
**产出**：42 案例结构化数据集 + IoC 清单 + 分析报告 + 检验报告
**总耗时**：约 15 分钟（含一次失败重跑），token 成本可忽略

---

## 一、勘察数据（Phase 0 实例）

| 指标 | 值 |
|------|-----|
| 总字符 | 259,139 |
| 估算 token | ~62,000（英文 ÷ 4.2） |
| 顶级章节 | 46 |
| 最大章节 | 13,978 字符（~3,300 token） |
| 最小章节 | 1,300 字符（~310 token） |

**关键判断**：6.2 万 token 完全在单次上下文窗口内 → **最初设想的"25-35 个子代理"是过度设计**。真正约束不是输入，是输出。

## 二、批次划分（两次尝试）

### 第一次（失败）

| 批次 | 章节数 | 输入 token | 案例数 | 结果 |
|------|--------|-----------|--------|------|
| B1 | 8 | 16,936 | 6 | ✅ |
| B2 | 19 | 26,874 | 19 | ✅ |
| B3 | 19 | 19,099 | 19 | ❌ **失败** |

### 失败详情（B3）

```
12:47:10 think  | I have the full file (520 lines, complete). Now let me build and write the struc...
12:47:10 final  | status=completed duration=81.95s summary: Response truncated due to output length limit
12:48:09 final  | end status=completed exit_reason=max_iterations
```

- 子代理**读完了文件**，但在单次响应里输出 19 条 × ~1.5K 字符 ≈ 28K 字符的 JSON
- 响应被截断 → 文件未写入 → `B3.json` 不存在
- **父代理看到的只是"任务 completed"**——必须去查产物文件是否存在才能发现

### 第二次（成功）

拆成 3 个小批次 + 改用 JSONL 增量写入：

| 批次 | 章节 | 输入 token | 案例数 | 结果 |
|------|------|-----------|--------|------|
| B3a_weapons | 7 | 7,716 | 6 | ✅ |
| B3b_bio_scam | 5 | 5,408 | 5 | ✅ |
| B3c_distillation | 7 | 5,975 | 6 | ✅ |

**增量写入指令原文**（可直接复用）：

> 1. `read_file` 读取批次文件
> 2. 【关键】用 `write_file` 创建空文件 `<path>.jsonl`（内容写空字符串）
> 3. 逐个案例处理：每提取完 1 个案例，就用 `read_file` 读出当前 jsonl 内容 + 追加新行 + `write_file` 写回。每次只处理 1 个案例！
> 4. 最后写 `<path>_meta.json` 存批次级信息（summary/trends/methodology）

子代理会自行优化为"用 Python 读取全文+追加一行+写回"，效果等价且不触发 read_file 大文件回显截断。

## 三、提取结果

**42 案例 / 7 领域**：监控 10、影响力行动 9、网络行动 6、常规武器 6、非法蒸馏 6、生物滥用 4、诈骗 1

**统一 schema（15 字段）**：
`gtg_id / title / actor / attribution / domain / targets / ai_role / key_techniques / human_role / scale / timeframe / uplift{speed,scale,depth} / has_iocs / defense_lessons / notable_facts`

## 四、程序化提取的坑（Phase 2b 实例）

### 坑 1：防御性脱敏导致正则全灭

报告用 `104.145.210[.]184`、`ms365-live[.]com` 格式防误点。第一版正则提取到 **0 个 IP**。

修正——先脱敏再正则：
```python
def deobf(s):
    return s.replace('[.]', '.').replace('[:]', ':').replace('(.)', '.').replace('hxxp', 'http').replace('hxxps', 'https')
```
结果：13 个 IoC 段落 → 49 唯一 IP / 58 唯一域名 / 3 SHA256 / 4 TG 群 / 6 TG 账号 / 14 可执行文件名

### 坑 2：Telegram 群 ID 的负号破坏 `\b`

`| -1003893854338 |` 里 `-` 前无词边界 → `\b(-?100\d{8,12})\b` 匹配失败。
改用 `-100\d{8,13}` 直接匹配，并单独抽 `(?<![-.\d])\d{9,13}(?![-.\d])` 抓 bot/user ID。

## 五、检验结果（Phase 4 实例）

| 检验项 | 结果 |
|--------|------|
| 文件完整性 | 8/8 合法 |
| 字段完整性 | 42 × 15 = 630 字段位，0 缺失 |
| 案例完整性 | 原文 39 个 GTG 编号，**39/39 全提取** |
| 关键数字准确性 | **10/10** 回原文确认（1.51亿、1210万/14天、40万/20天、770,609、273账号、5,380账号…） |
| IoC 交叉验证 | ⚠️ 子代理标 15，程序化验证 **7** → 偏差 8 |

**发现 4 个问题**：
1. `has_iocs` 标记过宽（子代理把"提到域名"也算 IoC）→ 加 `has_iocs_verified` 权威字段
2. `attribution` 字段漂移（一个子代理写一句、另一个写 60 字说明）→ 加 `attribution_raw` + `attribution_norm`（17 条映射规则）
3. `## Conclusions` 被切进武器批次，**实际是生物滥用章的结论** → 子代理自己识别并标注了
4. `GTG-10002` 未提取 → 核实发现是"引用 2025-11 报告的历史案例"，**非遗漏**

## 六、可复用的时间/成本基准

| 阶段 | 耗时 |
|------|------|
| 勘察 + 切分 | <1 分钟 |
| 并行提取（3 子代理 × 1 轮） | 2–3 分钟 |
| 失败重跑（3 子代理 × 1 轮） | 1.5 分钟 |
| 聚合 + 规范化 + 检验 | 2–3 分钟 |

26 万字符 / 6.2 万 token 的报告，全程 15 分钟、成本几分钱（DeepSeek 价位）。

## 七、产物结构模板

```
<workspace>/
├── dataset.json          # 合并数据集（含 batch_metas）
├── dataset.csv           # 扁平表格
├── iocs.json             # 程序化提取的结构化指标
├── manifest.json         # 章节清单 + 批次划分 + token 统计
├── normalize.py          # 字段规范化脚本
├── ANALYSIS.md           # 跨领域分析报告
├── VALIDATION.md         # 检验报告（含已知局限）
├── sections/             # 逐章切分文件
├── batches/              # 批次合并文件（供子代理一次读取）
└── extracted/            # 各批次提取结果（.json / .jsonl / _meta.json）
```

## 八、给分析报告加"怀疑"章节

这份报告是 Anthropic IPO 前发布、数字来自其自有遥测、被点名公司无回应机会、归因置信度差异大。分析报告里单列一节说明这些，比直接复述结论有价值得多。
**通用做法**：结构化数据集是"事实层"，分析报告必须另设"解读层"，明确标注利益相关方、数据未验证之处、单方面陈述。
