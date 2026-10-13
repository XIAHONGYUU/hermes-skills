# Hermes 容器内存诊断（verified 2026-08, v0.20.0, 2GB VPS）

诊断「容器内存紧张 / swap 增长 / 要不要扩容 / 是否泄漏」的完整套路。只分析，不改配置。

## 1. 快照命令（并行跑）

```sh
free -h && ps aux --sort=-%mem | head -15 && uptime
docker stats --no-stream hermes
docker inspect hermes --format '{{.State.StartedAt}} {{.RestartCount}} {{.HostConfig.Memory}}'  # Memory=0 → 无 --memory 限制
cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.high /sys/fs/cgroup/memory.current
head -8 /sys/fs/cgroup/memory.stat        # anon=进程实占, file=可回收 page cache, kernel=cgroup 记账
grep -E 'VmRSS|VmHWM|VmSize|VmData|Threads' /proc/<gateway_pid>/status
awk '/^Rss|^Pss_Anon|^Private_Dirty/{print}' /proc/<gateway_pid>/smaps_rollup
```

- 容器内 `free -h` 显示的是**宿主机** meminfo（未隔离），swap 已用是全局的，不一定是本容器造成。
  归属到进程（一行，实测有效，别再用 execute_code——cron 里被禁）：
  ```sh
  for f in /proc/*/status; do awk '/^Name:/{n=$2} /^VmSwap:/{if($2>1000) print $2" kB  "n}' $f 2>/dev/null; done | sort -rn | head -10
  ```
  例（2026-10-09）：宿主 swap 已用 865MB，其中 `hermes` 两进程 371MB + 107MB ≈ 478MB → **宿主的 swap 主要就是这个容器压出来的**，这才是有用的结论。
- `HostConfig.Memory=0` + `memory.max=max` = 容器与宿主机共享全部内存，cron/大任务并发时可能把宿主机压到 swap。
- 历史对照：`grep '\[MEMORY\]' /opt/data/logs/gateway.log`（老版本有记录，见下方 pitfall）。

## 2. 内存去向（v0.20.0 实测）

| 组件 | 典型 RSS | 说明 |
|---|---|---|
| gateway 主进程 | 启动 baseline ~100–130MB；长期运行 500–700MB | 唯一大户，38+ 线程 |
| dashboard (9119) | ~25MB | 可忽略 |
| s6/sleep/shell | <5MB | 可忽略 |

gateway 的匿名内存（smaps_rollup 里 Pss_Anon / Private_Dirty，~500MB+）几乎全部是 **agent 缓存**：
每个会话缓存一个 AIAgent，钉住完整 `_session_messages` transcript（工具密集会话几十 MB/个）。

## 3. 关键配置杠杆：`agent.agent_cache`（config.yaml）

config.yaml **未配置时用默认**（定义在 `hermes_cli/config_defaults.py`）：

```yaml
agent:
  agent_cache:
    max_size: 128          # LRU 条目上限（数条目不数字节！128 个热 transcript 可达数 GB）
    idle_ttl_secs: 3600    # 空闲 1h 才逐出
    memory_high_mb: auto   # 匿名 RSS 预算，auto = cgroup 限制的 65%（无限制时取总内存）
    max_evictions_per_pass: 16
    protect_recent: 8      # 最热 8 个会话永不逐出
```

- 压力清理（`gateway/agent_cache_pressure.py`）在**匿名 RSS 超过预算**时才触发。2GB 无限制容器 → 预算 ~1.3GB，545MB 时永远不会触发，缓存一直堆积。
- 降内存的稳妥改法：`max_size: 16~32` + `memory_high_mb: 800`（显式预算让清理提前）。
- 逐出是软路径：transcript 丢弃后下一轮从持久化 session 重建，会话不丢。
- 其他相关项：`delegation.max_concurrent_children`（子代理并发峰值）、`sessions.auto_prune`（state.db 磁盘增长，非内存）、`lsp.enabled`/`stt`（懒加载，平时不占）。

## 4. 泄漏判断（无时间序列时的替代证据）

- `VmHWM` vs `VmRSS`：两者接近 = 近期到过峰值；RSS 持续接近/刷新 HWM 才可疑。
- 跨进程对照：历史 gateway（6.6 天 uptime）HWM 675MB vs 当前（19 天）HWM 557MB → 无失控增长。
- 增长模式符合**已知设计**（agent 缓存 + transcript 累积，有 65% 压力清理兜底），不是 bug 式泄漏。
- 观测方法（memory_monitor 不可靠，见下）：重启后记 baseline，之后每天 `docker stats --no-stream hermes` 对比；无任务时 RSS 持续爬升超过 ~1GB 再考虑 `hermes gateway restart`（优雅，会话不丢）。

## 5. Pitfalls（踩过）

1. **`[MEMORY]` 日志记录的是 ru_maxrss（高水位 HWM），不是当前 RSS**（`gateway/memory_monitor.py` 用 `resource.getrusage().ru_maxrss`）。`rss=675MB` 表示生命周期峰值，只增不减，不能当趋势线。
2. **v0.20.0 中 memory_monitor 未接线**：模块存在但全源码无调用点（`grep -rn start_memory_monitoring /opt/hermes --include=*.py` 只命中定义处）。2026-06 之后 gateway.log 再无 `[MEMORY]` 行是正常的，别以为监控坏了去修。
3. **cron 任务跑在 gateway 进程内**（cron ticker），27 个 job（22 enabled）并发/堆积时会瞬时抬高 RSS；看 `cron/jobs.json` 的 schedule 评估峰值窗口。
4. 容器无 `--memory` 限制时，`memory_high_mb: auto` 预算按宿主机总内存算（2GB→1.3GB），与直觉的「按容器限制」不同。
5. 扩容判断：峰值 ~675MB、日常 anon ~530MB，2GB 足够单任务；只有「多重量级 cron 并行 + 交互大任务」并发才值得升 4GB，或先给容器加 `--memory 2g` 隔离 OOM。
