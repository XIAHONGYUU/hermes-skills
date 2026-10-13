# 升级后验证 —— 为什么"全绿"可能是假的

> 真实教训：一次 v0.20.0 → v0.21.6 升级后，版本、镜像 digest、端口映射、gateway 进程、
> config 全部通过 —— 但 `api_server` 实际是 `fatal`。**表面全绿，实际少了一个平台。**
> 原因是我把「端口映射存在」和「会话上下文里写着 api_server: Connected」当成了健康证据。两者都不是。

## 唯一权威来源：`/opt/data/gateway_state.json`

```sh
/opt/hermes/.venv/bin/hermes gateway status        # 人类可读版
python3 -c "
import json; d=json.load(open('/opt/data/gateway_state.json'))
for name, p in d.get('platforms', {}).items():
    print(f\"  {name:14} {p['state']:12} {p.get('error_message') or ''}\")
"
```

| `state` | 含义 |
|---|---|
| `connected` | 真的起来了 |
| `fatal` + `adapter_unavailable` | **终态** —— 见下 |

**`fatal` 是终态，不会自己好。** 网关在启动时创建平台适配器，依赖探针返回 False →
**记一条 fatal、放弃，本进程内不再重试**。用户会看到 `hermes gateway status` 一直报这条告警。

**判断 fatal 是否要紧**：看 `updated_at` 是否贴近容器 `StartedAt`。贴近 = 本次启动的真问题；
很久以前 = 可能是陈旧条目。

## 「host 上端口在 listen」不是证据

`docker port hermes` 输出的是**映射声明**。Docker 的端口映射代理**只要容器活着就 listen**，
与容器内有没有服务无关 —— host 看到 listen、容器内 Connection refused，这组合是完全可能的。

必须在**容器内部**测：

```sh
docker exec hermes /opt/hermes/.venv/bin/python3 -c "
import socket
for hp in [('127.0.0.1',8642),('127.0.0.1',9119)]:
    s=socket.socket(); s.settimeout(3)
    try: s.connect(hp); print(hp,'listen')
    except Exception as e: print(hp,'refused',e)
    finally: s.close()"
```

⚠️ 容器里通常**没有 `curl` / `ss` / `netstat`** —— 用 python socket，别用 curl。
（`docker exec` 也拿不到 s6 服务级环境变量，如 `HERMES_SITE`；`docker inspect .Config.Env` 只含容器级。）

## 完整验证清单

```sh
# ① 版本（看 upstream commit 与 release notes 是否对得上）
/opt/hermes/.venv/bin/hermes --version

# ② 容器状态（ExitCode=0 / RestartCount=0 / StartedAt）
docker inspect hermes --format 'Status={{.State.Status}} ExitCode={{.State.ExitCode}} RestartCount={{.RestartCount}} StartedAt={{.State.StartedAt}}'

# ③ 容器规格没丢（用反推工具，逐项对照升级前存档的 hermes-spec-*.json）
/opt/hermes/.venv/bin/python3 /opt/data/scripts/hermes_capture_spec.py

# ④ 端口映射声明
docker port hermes

# ⑤ gateway 进程
docker exec hermes ps -eo pid,args | grep -E 'gateway run|main-wrapper'

# ⑥ ★ 平台适配器真实状态（最容易被跳过、也最关键的一步）
/opt/hermes/.venv/bin/hermes gateway status
python3 -c "import json;d=json.load(open('/opt/data/gateway_state.json'));print({k:v['state'] for k,v in d.get('platforms',{}).items()})"

# ⑦ config schema 迁移改了什么（见下节）
docker logs hermes 2>&1 | grep -A30 'config-migrate'

# ⑧ 日志异常
docker logs --tail 120 hermes 2>&1 | grep -iE 'error|traceback|failed|critical'

# ⑨ 手工调过的 auxiliary 段没被重置
grep -c 'deepseek-v4-flash' /opt/data/config.yaml     # 期望值 = auxiliary 项数 + model.default + cron.model
```

## 升级会静默改掉一批配置默认值

`docker logs` 里的 `[config-migrate] Migrating config schema N -> M` 会**在没有交互提示的情况下**
提高一批默认值。**升级后必须逐条读这段输出并决定要不要改回。**

实测（schema 34 → 50）被改动的项：

| 键 | 旧 | 新 | 影响 |
|---|---|---|---|
| `delegation.max_concurrent_children` | 3 | **10** | 并行子代理上限；每个都独立烧 token |
| `delegation.max_iterations` | 50 | **250** | 每子代理工具调用预算 ×5 |
| `curator.stale_after_days` | 30 | **14** | 两周未用即标 stale |
| `curator.archive_after_days` | 90 | **30** | 一个月未用即归档 skill |
| `security.tirith_*` | 存在 | **被移除** | 扫描器不再随镜像打包 |

要点：
- 迁移前的快照会自动落到 `/opt/data/backups/config/`（`config.yaml.pre-docker-migrate.*`），可回退。
- **这些值会出现在别的 skill 文档里**（如并行子代理上限被写死成 3）。发现被改要**同步改 skill**，
  否则文档会持续误导 —— 正确写法是「读 `delegation.max_concurrent_children`，别把数字写死」。
- Curator 只动有 agent provenance 标记的 skill；无标记的（前台创建的）**永远不会被自动归档**。
  判断某人的原创 skill 安不安全，先跑 `hermes curator status` 看 active/stale/archived 与
  「unmanaged (no provenance marker)」的计数，别按总数推断。
- 升级可能同时换 Python 版本（实测 3.13 → 3.14）。`/opt/data/lazy-packages` 里若有按旧 ABI
  编译的 `.so`，会在新解释器下 import 失败 —— 这是检查项，不是可忽略项。

## 平台依赖（PM extras）—— 一份已实证的因果链

新版本把「按需依赖」交给 PM（package manager）管理，状态记录在：

```
/opt/data/installs/<id>/facts.json      →  "extras": ["feishu"]
/opt/data/installs/<id>/environments/<hash>/venv/   ← gateway 的 HERMES_SITE 指向这里
```

**机制**：镜像升级后，stage2 会在任何服务启动**之前**重新解析依赖生成物
（`[stage2] dependency environment: <name>` —— `base` = 回退到镜像自带环境，其他值 = PM 生成的 venv）。
生成物是「基础依赖 + facts.json 里记录的 extras」。

**关键推论**：**只有被记录进 `extras` 的东西才会出现在 gateway 的运行环境里。**
一个由镜像自动启用、但从没人给它记录依赖 extra 的平台，其适配器探针必然失败 → `fatal`。

实测因果链（同名平台一个活一个死，正是同一机制的两面）：

| 启动 | 依赖环境 | 结果 |
|---|---|---|
| #1 | `base`（解析未就绪 → 回退镜像自带环境） | feishu ❌（镜像里没有 `lark_oapi`）· api_server ✅（镜像自带 `aiohttp`） |
| #2 | PM 生成的 venv（只有 `feishu` extra） | feishu ✅ · api_server ❌（该 venv 无 `aiohttp`） |

**extra 与包的对应关系看仓库 `pyproject.toml` 的 `[project.optional-dependencies]`**。
实测易踩的坑：`feishu = ["lark-oapi", "qrcode"]` **不含 `aiohttp`**；`aiohttp` 属于
`messaging`（以及 `slack` / `sms` / `teams` / `matrix`）。所以「平台名字」不等于「能推出它要哪个 extra」。

诊断命令：

```sh
python3 -m json.tool /opt/data/installs/*/facts.json
SITE=$(docker exec hermes sh -c 'ls -d /opt/data/installs/*/environments/*/venv')
docker exec hermes sh -c "for m in aiohttp httpx lark_oapi; do printf '%s ' \$m; ls -d $SITE/lib/*/site-packages/\$m >/dev/null 2>&1 && echo present || echo MISSING; done"
docker logs hermes 2>&1 | grep -E 'dependency environment|aiohttp not installed|No adapter available'
```

### 为什么会「只有那个平台」缺依赖 —— sys.path 被整体替换（已复现）

不是镜像缺包，也不只是 extras 记录不全。真正的机制在 `pm/environments.py` 的
`activate_dependencies()`：

```python
sys.path[:] = [entry for entry in sys.path
               if Path(entry).name not in ("site-packages", "dist-packages")
               and Path(entry).resolve() != project_root.resolve()]
site.addsitedir(str(selected))     # selected = PM 生成 venv 的 site-packages
```

**它把所有 basename 叫 `site-packages` / `dist-packages` 的路径整体剔除** —— 于是
`/opt/hermes/.venv/lib/python3.14/site-packages`（镜像 venv，里面本来就有正确的 `aiohttp`）
被删掉，换上 PM 生成 venv 的那个。而 PM 生成 venv 只装「基础依赖 + `facts.json` 记录的 extras」。

**净结果：适配器的依赖只可能来自 (a) PM 生成 venv 的 extras，(b) 路径 basename 不是
`site-packages` 的目录（如 `/opt/data/lazy-packages`）。镜像 venv 里有什么都不作数。**

**可复现的探针**（必须触发激活后再 import —— 这是唯一能复现 gateway 视角的测法）：

```sh
docker exec -u hermes hermes sh -c 'cd /opt/data && /opt/hermes/.venv/bin/python -P -c "
import os, sys
os.environ.pop(\"PYTHONPATH\", None)
sys.path.insert(0, \"/opt/hermes\")
import pm.environments as E
E.activate_dependencies()          # ← 少了这一步，测什么都是假绿
for p in sys.path[:8]: print(\"   \", p[:105])
try:
    import aiohttp; print(\"  aiohttp:\", aiohttp.__file__)
except Exception as e: print(\"  aiohttp ❌\", type(e).__name__, e)
import gateway.platforms.api_server as m; print(\"  AIOHTTP_AVAILABLE =\", m.AIOHTTP_AVAILABLE)
"'
```

不调 `activate_dependencies()` 时 `aiohttp` 永远 import 得到（镜像 venv 还在 sys.path 上）。
**这就是为什么在容器里手工试怎么都对、而 gateway 里始终 False。**

### 怎么补 —— 依赖必须落在 PM 生成 venv 里

由上面机制推出（**方向确定；具体 extra 的选择未端到端验证**）：

1. 先确认 `facts.json` 的 `extras` 与所需包的关系，看仓库 `pyproject.toml` 的
   `[project.optional-dependencies]`。
2. **选一个「含所需包」且**在 `pm/extras.py::ANCHORS` 里有真实 anchor 的 extra** ——
   不在 ANCHORS 里的 extra 有被 PM 判为无效而丢弃的风险。实测对照：
   `messaging` → anchor `telegram`（含 `aiohttp` 但连带 telegram + `discord.py[voice]`，重且有 C 依赖）；
   `slack` → anchor `slack_bolt`（`[slack-bolt, slack-sdk, aiohttp]`，轻）；
   `sms` = 只含 `aiohttp`，但**不在 ANCHORS 里**。
   注意「平台名 ≠ extra 名」：`feishu` 的 extra 不含 `aiohttp`，`aiohttp` 的归属是 `messaging` 一族。
3. 改 `installs/<id>/facts.json` 的 `extras`（**先备份**；`stamp` 字段与 lock 关联，别动），
   再重启容器让 stage2 重新解析。
4. **注意回退分支**：stage2 解析失败（如离线）会**回退到镜像自带环境**，那会变成
   「镜像里有的包能用、只有 extra 才有的包不能用」—— 平台之间此消彼长。日志写
   `[stage2] dependency environment: base`。

⚠️ 改完仍失败就回去读 `docker logs | grep -A5 'dependency environment'`，
先确认这次是 `base` 还是生成物的名字 —— 那决定了上面哪条路径在生效。

**上游缺口（供报告用）**：`gateway/run.py::_instantiate_builtin_adapter` 在依赖探针返回 False 时
**只打警告、不尝试懒装**，尽管 `pm/extras.py::ensure_import` 就是为此存在的。
另外 `ANCHORS` 里没有「只用基础 HTTP 栈」那类平台（如 api_server）的条目，它的依赖归属
只能靠 `messaging`/`slack` 这种包含关系蹭到。

### 一个仍有用的旁路结论

先确认是否真有人消费该端点：`gateway_state.json` 里 `metrics.last_request_at` /
`active_runs` / `stored_runs` / `metrics_today.requests` 全为空或 0，且日志里该端口
从来没有访问记录 → 回环 OpenAI 兼容端点若无人使用，**挂着不管也是一个选项**
（代价是 `hermes gateway status` 永远报一条假警报，污染后续的健康判断）。
