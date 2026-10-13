---
name: structured-document-extraction
description: "全量分析/结构化提取/做成数据集/报告太长了——长文档→JSONL增量写+交叉检验。"
author: XIAHONGYUU
license: MIT
version: 2.0.0
metadata:
  hermes:
    tags: [documents, extraction, dataset, delegation, jsonl, verification]
    related_skills: [image-document-ocr, ocr-and-documents, document-to-action-items, subagent-driven-development, long-document-triage]
---

# Structured Document Extraction — 长文档 → 结构化数据集

把长文档（报告/白皮书/年报/论文集/说明书）转成**逐条记录 + 固定字段**的可统计数据集，而不是摘要。

## When to Use / 不适用

**用**：文档 100+ 页或 100k+ 字符；用户要「提取/结构化/做成数据集/做成表格」；同一事实散落多章需要按主键去重；用户要你先**设计并评估**一个处理流程。

**不用**：只要一段摘要（直接读+总结）；只要行动项（用 `document-to-action-items`）；PDF 无文字层（先用 `image-document-ocr` 拿文本）。

## 与其他 skill 的分工

| 需求 | 用哪个 |
|------|--------|
| PDF 无文字层（转曲/扫描件） | `image-document-ocr` |
| 只要要点/导览/摘要 | `long-document-triage`（先出结构导览） |
| 只要行动项/义务/截止日期 | `document-to-action-items` |
| **逐条记录 + 统一字段 + 可统计** | **本 skill** |
| **评估其结论可信度 / 是否夸大** | `claim-evidence-audit`（提取完成后常需要的下一步） |

> **提取完成后用户常追问「这报告说得对吗 / 是不是耸人听闻」** → 把本流程产出的 `dataset.json` 直接喂给 `claim-evidence-audit` 的定量检验（证据来源正则分类、IoC 组间对比、案例类型分布、归因链条拆解）。**结构化数据集是证据审计的最佳输入**——这是本流程值得认真做的额外理由。

## 铁律 1 — 先量体，再设计（Step 0）

**页数会骗人，token 才作数。** 先跑：

```python
text = open(path).read()
print(len(text), 'chars  ~', round(len(text)/4.2), 'tokens (英文；中文约 /1.6)')
print(len(re.findall(r'^## ', text, re.M)), '个顶级章节')
```

| 体量 | 做法 |
|------|------|
| < 40k tokens | 自己读 2-3 遍，**不要**委派 |
| 40k-400k tokens | 切分 + 3-6 并行子代理（本文流程） |
| > 400k tokens | 同流程，但要多轮 + 加程序化预筛 |

**校准案例**：154 页 / 18.6MB PDF 看似巨大 → 曾提议「25-35 个子代理」；实测文本仅 **259k 字符 ≈ 62k token**（几十次调用就够）。方案**过度设计约 5 倍**。诚实说出「比预想小，我先前的方案过度设计了」本身就是答案的价值部分。

**先把实测体量和分批数告诉用户，再动手。**

## 铁律 2 — 按输出量分批，不是按输入量

**实测失败**：19 个案例的批次，输入仅 19k token（很安全），但子代理要**一次性输出** 19 条完整 JSON ≈ 28k 字符 → 超出单次响应长度上限 → 响应截断 → **文件根本没写** → `exit_reason=max_iterations`。

**输入预算与输出预算是两个独立限制，必须分别算。** 每条记录 ≈ 1.5-2k 字符输出；**案例数 ≤ 6-8 / 子代理**是安全区。

## Workflow

### Phase 1 — 切分与分批

1. 按顶级标题切成一节一文件（`sections/NN_title.md`），写 `manifest.json`（序号/标题/文件名/起止行/字符/token）——**档案是后续校验的唯一依据，别跳**。
2. 合批：每批一个子代理（15-25k token 舒适区），**同主题章节放一起**，让子代理看到跨案例上下文。用机器可识别的分隔符：
   ```
   ===== SECTION: <title> =====
   ```
3. **批次均衡按预期输出量**，不是字符数。永远不要让子代理读整份文档——只发它一个批次文件。

### Phase 2 — 并行结构化提取

- `delegate_task(tasks=[...])` 一次派发；**批次数 ≤ `delegation.max_concurrent_children`**（本机为 3）。超出则合并小批次或分多轮。
- 每个子代理 prompt 必须含：① 要读的文件路径 ② **逐字段写死的 JSON schema + 一个示例** ③ 输出路径 ④ 「保留具体细节，不要泛化」 ⑤ 「归因/敏感内容严格按原文口径转述，不自行推断、不加评论」 ⑥ 「把原文含糊处或你的推断写进 `extraction_notes`」

**Schema 形态**（批次信封 + 逐条记录）：
```json
{
  "batch": "B1", "domain_summary": "150-250字（CJK）",
  "cases": [{"id": "<主键>", "title": "", "actor": "", "attribution": "",
    "domain": "", "targets": [], "ai_role": "", "key_techniques": [],
    "human_role": "", "scale": null, "timeframe": "",
    "uplift": {"speed": "", "scale": "", "depth": ""},
    "has_indicators": true, "defense_lessons": [], "notable_facts": []}],
  "trends": [], "methodology_notes": [], "extraction_notes": []
}
```

**案例数 >5 时必须 JSONL 增量写**（见下）。**数量 >10 必须拆批**。

#### JSONL 增量写入（输出量超限的修复模式）

绝不让孩子代理攒到最后一次输出。指令：
1. 先 `write_file` 一个空的 `<path>.jsonl`（内容 `""`）
2. 逐条处理：**每提取 1 条立即落盘 1 行**，一行一个完整 JSON 对象（行内不能有真实换行）
3. 单行 JSON 用 `json.dumps(obj, ensure_ascii=False)` 生成，**不要手打** JSON 文本（引号/转义必错）
4. 每次追加后 `json.loads` 校验该行

**批次级元信息**（概述/趋势/方法论/章节级列表）另存 `<batch>_meta.json`——避免把所有东西塞进一个巨型对象。实测：6 案例批次 → `B3c_cases.jsonl`(6行) + `B3c_meta.json`。

**追加的实现细节（两个坑）**：
- **`read_file` 会截断长文件的返回内容**（显示 `...[truncated]`）。用「读→追加→写」循环时，若依据 `read_file` 的返回值，会**静默丢掉前面的行**。要在**一次 `execute_code`/脚本**里完成 `open().read()` → append → `write()`。
- **暂存文件必须在 workspace 根目录下**，不能放 `/tmp`——`write_file` 被沙箱限制在 `HERMES_WRITE_SAFE_ROOT`（通常是 `/opt/data`），`/tmp` 会被拒（`Write denied: outside HERMES_WRITE_SAFE_ROOT`）。这是路径范围守卫，不是工具坏了；换个位置继续用这个模式即可。
- 现成脚本：`scripts/append_jsonl_line.py`（解析暂存文件 → 校验 → 追加一行 → 报告新行数 → 删除暂存文件）。

**校验必须断言 key-set，不只是能否解析**：一条缺字段的记录 `json.loads` 完全能过。追加完跑一次 `python3 -c`，逐行 `json.loads` 并断言 `set(obj) >= required_fields`，打印条数与主键列表（截断/重复立即可见）。

### Phase 3 — 程序化提取，不要交给 LLM

IP、域名、哈希、编号、日期、金额这类**有固定字面形式**的指标用正则从原文抽——正则不会幻觉，而子代理抄 50 个 IP 必漏几个。

```python
# 威胁情报/安全报告常有防御性脱敏：104.145.210[.]184 / ms365-live[.]com
body = raw.replace('[.]', '.').replace('[:]', ':')   # ← 先还原，否则 0 命中
ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', body)
```
**陷阱**：不还原脱敏标记会得到「0 个指标」的假结果，容易误判成「原文没有指标」。**在重试去脱敏之前，不要报「未发现指标」。**

### Phase 4 — 规范化与合并

不同子代理会写出**长度/粒度不一致的同名字段**（schema 漂移）。合并时：
- 原文口径字段 → `xxx_raw`（保留）+ `xxx_norm`（规则映射成可统计标签）
- 输出 `dataset.json`（完整）+ `dataset.csv`（扁平可读，便于排序筛选）

### Phase 5 — 交叉检验（必做，别省）

用户常明确要求「完成以后检验一下」。静默错误正是藏在这种流程里：

| 检验 | 方法 |
|------|------|
| 文件合法性 | 逐个 `json.load` / 逐行 `json.loads` |
| 字段完整性 | N 条 × M 必填字段，列出缺失位 |
| **记录完整性** | 用原文标识符（编号/ID）做集合差：`原文集合 - 提取集合` |
| **数字准确性** | 挑 10 个关键数字，逐个回原文正则确认 |
| 标记可信度 | 子代理的布尔标记 vs 程序化验证，差异如实报告并加权威字段 |
| **索引交叉核验** | 程序化关键词扫描**全部**章节建实体索引，与手工索引对照（见 Pitfalls） |

**差集里的「缺失项」要逐个核实是否真漏**——实测 `GTG-10002` 只在趋势章被引用（历史案例），不是遗漏。**如实报告「看似遗漏实为引用」，不要直接算成覆盖率缺口。**

## Pitfalls

- **按页数/词数估工作量**：先跑 Step 0 拿 token 数。为 62k token 的文档设计 25-35 个子代理就是失败模式。
- **信任自己的第一版索引**：实测手工索引漏了 4 个案例——它们被归在主题与内容不符的章节下（监控类案例住在「影响力行动」章里）。只有程序化关键词全章扫描才找得到。**机器索引是覆盖率的权威，人工阅读用于解释。**
- **子代理的布尔标记偏宽松**（把「提到过某类值」算成「有该类指标」）：实测声称 15 个有指标、程序化验证只有 7 个。保留原字段 + 新增 `xxx_verified` 权威字段。
- **批次划分会错位**：按文本顺序切批可能把「某章的结论」切进上一批（实测 `## Conclusions` 实为**生物章**的结论，被划进武器批）。**章节归属看它*包含什么*，不看它的标题**；要求子代理把这类错位写进 `extraction_notes`。
- **schema 漂移**：schema 不逐字粘贴到每个任务的 `context` 里，子代理就会自创字段（示例里 `has_iocs` vs `has_indicators` 这类不一致会让 `assert key-set` 失败）。
- **子代理产出是自报**：声称「已写 B2.json 含 19 条」的可能只写了 16 条。Phase 5 必须自己数记录数，不能信摘要。
- **"150-250字"指 CJK 字符数，不是总字符数**：用 `len(re.findall(r'[\u4e00-\u9fff]', s))` 计数。含英文标识符（GTG-20006、ShinyHunters）的中文摘要总长可达 270-390 字符而 CJK 只有约 130——按总长「修正」会一直剪错东西。实测为此浪费 4 轮迭代。
- **源文档要留底**：派发前把抽取的文本复制到持久路径（如 `research/<topic>/raw/`）。子代理要跑几分钟，之后需要反复回查。
- **分类字段原文没给就推断 + 记录**：schema 要评级（高/中/低）但原文只有定性描述（"increased their speed"）时，按语义赋值并**在 `extraction_notes` 里记下这是推断**。绝不把猜测当原文断言呈现。
- **`scale` 等原文缺失的字段填 `null`**，不要猜、不要写「大量」；在 `extraction_notes` 里说明该字段在本批次确实缺失。

## 产出结构

```
<workspace>/
├── ANALYSIS.md            跨域分析（核心交付）
├── VALIDATION.md          检验报告（含已知局限与偏差修正）
├── dataset.json/.csv      合并数据集
├── iocs.json 等           程序化提取的指标
├── manifest.json          章节 + 批次 + token 台账
├── sections/              切分后的原始章节
├── batches/               批次合并文件（可追溯每批读了什么）
└── extracted/             各批子代理产出 + meta
```

**可复现性**：保留 `batches/` 与 `manifest.json`，任何人能重跑同一批次划分。

**交付物通常是三样**：(a) 可导航的导览/索引 (b) 结构化数据集 (c) 跨域分析。确认用户要哪些，或全给。

## 变体 B — 多文件表格批量归档（Excel / CSV 树）

用户一次扔来一批表格（xlsx/xls/csv）说「**整理一下存档**」时，目标不是摘要，是**可检索的档案**：
原件保真 + 每 sheet 转 CSV + 人读的 Markdown 概览 + 机读 manifest。

### 目录约定（沿用宿主知识库的 references/ 惯例）

```
<archive>/<topic>-<YYYY-MM>/
├── README.md      索引：每个文件是什么、状态、统计、格式判定依据
├── manifest.json  机读：源文件 → 各 sheet 行列数 → 对应 CSV
├── raw/           原件（干净命名、保真；改名映射写进 manifest）
├── extracted/     <file>__<sheet>.csv（全量）+ <file>.md（概览）
└── media/         xlsx 内嵌图片（xl/media/*）
```

归档完**必须同步宿主的索引**（如 `index.md`）+ 追加 `log.md` —— 否则档案等于不存在。
现成脚本：`scripts/archive_excel_tree.py`（探格式 → 选引擎 → CSV+MD → raw/media → manifest，一次跑完）。

**依赖怎么装（第一步就会卡）**：宿主 agent 的 venv **通常没有** openpyxl / xlrd，而且 `pip` 可能本身就不存在（PEP 668 环境）。
**不要试图装进 venv**，用一次性环境跑：

```bash
uv run --quiet --with openpyxl --with xlrd python scripts/archive_excel_tree.py
```

先 `python -c "import openpyxl"` 探一下，别等脚本抛 `ModuleNotFoundError` 才发现。
（`.xls` 顺手把 `--with xlrd` 一起带上，反正是一次性的。）

### 解析引擎选择（两个必须记住的坑）

| 坑 | 症状 | 正解 |
|---|---|---|
| **`openpyxl` `read_only=True` 会误报维度** | `max_row=1, max_column=1`，**看起来是空表** | 用 `read_only=False`。实测两个文件真实是 **49×13** 与 **110×7**。`ReadOnlyWorksheet` 甚至没有 `.dimensions` 属性（AttributeError） |
| **`.xls` 不是 zip** | `openpyxl` 抛 `BadZipFile` | 用 `xlrd`。而 `xlrd` 遇非 OLE2 会抛 `XLRDError: Expected BOF record; found b'...'` |

**`read_only=True` 的误报是这条工作流最危险的坑**——它**静默**把有内容的表判成空表，而你会据此告诉用户「这个文件是空的」。
**判「空表」之前，先数 XML 里的行**：`python -c "import zipfile,re;x=zipfile.ZipFile(p).read('xl/worksheets/sheet1.xml');print(x.count(b'<row '))"`。别信 `max_row`。

### 每个文件先探格式（诊断顺序，逐条记录结果）

1. **头部魔数**：ZIP `PK\x03\x04` → 真 xlsx；OLE2 `D0CF11E0` → 老 xls / 加密 Office
2. **ZIP 内部清单**：必须含 `xl/workbook.xml`，否则不是标准 xlsx
3. **全文找内嵌档案签名**：`PK` / `D0CF11E0` / `1f8b`(gzip) / `7z\xbc\xaf` / `\xfd7zXZ`
4. **单字节 XOR 1–255 扫描**，看能否还原出 ZIP/OLE 头
5. **熵**（>7.5 bits/byte ≈ 加密或强压缩）
6. **固定字段比对**：多个不相关文件在**同一偏移**有**相同字节** → 是**带固定头的容器**，不是裸密文

### 判定为「企业加密（DLP / 透明加密）」时的处置

半导体等企业普遍部署文档加密，文件一出受控终端即加密。特征**组合**：头部不匹配任何已知签名 + 全文无内嵌档案签名 + 单字节 XOR 无解 + 熵 6.5–7.7 + **多个不相关文件在偏移 20–31 字节完全相同**。

**处置**：
- 原件保留在 `raw/`（保真存档），**如实说明打不开**
- 建议回受控终端导成明文 PDF / 截图，或找 IT 要脱密导出
- **不要尝试破解**——通常不是唯一来源，且涉及前雇主的文件管控边界
- **判定依据要写进归档 README**，否则下次重踩

> **不要把它写成「这个工具坏了」**——是文件被加密了，不是解析器的问题。下次遇到同类文件，先探格式再下结论。

### 配套的关键词统计（归档顺带产出）

表格归档常能顺手产出用户真正想要的结论。拿到数据后**按列做分布统计**（提出人 / 模块 / 项目 / 状态、日期范围、总量），比让用户自己翻 800 行有效得多：
- 按某一列 `Counter().most_common()` → 找**复发模式**（同一问题在多个项目重复出现 = 系统性缺陷，价值远高于单条记录）
- 日期列解析区间 + 去重计数 → 覆盖率/跨度
- 逐字节比对去重（`open(a,'rb').read() == open(b,'rb').read()`）→ 发现重复上传

## References

- `references/anthropic-threat-report-2026-09.md` — 完整实战（长文档路径）：154 页威胁情报报告 → 42 案例数据集，含 JSONL 增量修复前后对比、IoC 去脱敏、schema 漂移规范化、5 类检验的真实结果。
- `references/excel-bulk-archive.md` — 完整实战（变体 B）：9 个 Excel 的逐条格式诊断结果、DLP 加密判定表、`read_only` 误报实例、归档目录与索引同步流程、统计产出示例。
- `scripts/append_jsonl_line.py` — 安全的逐行 JSONL 追加器：解析暂存文件、校验 JSON、追加一行、报告新行数、清理暂存文件。
- `scripts/archive_excel_tree.py` — 多文件表格批量归档器：探格式 → 选引擎（openpyxl/xlrd）→ 全 sheet 转 CSV + 每文件 MD 概览 → 收内嵌图片 → 写 manifest；加密/损坏文件如实标记不中断。

## 历史

- v2.0.1（2026-09-26）：变体 B 补「依赖怎么装」——宿主 venv 常无 openpyxl/xlrd 且无 pip，用 `uv run --with` 跑一次性解析，不要装进 venv。
- v2.0.0（2026-09-19）：合并三个重复 skill（`long-document-structured-extraction`、`large-document-structured-extraction`）为单一 skill。三者都是同一份 154 页报告实战的产物（英文详版 / 中文详版 / 中文精简版），同时被触发会导致歧义。
  **教训**：合并/删除 skill 前，先迁移其 `references/` 与 `scripts/` 里的文件——删除是递归的，随 skill 一起消失的内容无备份（本次丢失了一份英文版 worked case）。

---

> **关于支持文件**：本仓库只收录可公开发布的部分。正文中提到的部分 `references/*.md` 是本机带日期的审计快照/案例存档，未随仓库发布。
