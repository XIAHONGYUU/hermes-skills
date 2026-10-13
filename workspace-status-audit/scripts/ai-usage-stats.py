#!/usr/bin/env python3
"""AI 使用轨迹探查 — 只读分析 state.db / cron / skills，回答「用户是如何使用 AI 的」。

用法: python3 ai-usage-stats.py
输出: 规模 / 交互vs自动化 / 真实活跃度(按周) / 工具分布 / skill 使用 / token成本 / cron 清单
不改任何文件。配合 workspace-status-audit 的「使用轨迹分析」章节解读。
"""
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime

DATA = os.environ.get('HERMES_DATA', '/opt/data')
DB = f'{DATA}/state.db'


def q(sql, args=()):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def section(t):
    print('\n' + '=' * 72)
    print(t)
    print('=' * 72)


section('一、规模')
print('总会话:', q('SELECT COUNT(*) FROM sessions')[0][0])
print('总消息:', q('SELECT COUNT(*) FROM messages')[0][0])
print('工具调用:', q("SELECT COUNT(*) FROM messages WHERE role='tool'")[0][0])

section('二、交互 vs 自动化（按来源）')
for src, n, cost in q("""SELECT source, COUNT(*), SUM(estimated_cost_usd)
                         FROM sessions GROUP BY source ORDER BY 2 DESC"""):
    print(f'  {src:10s} 会话 {n:>5d}  成本 ${cost or 0:.2f}')

section('三、真实活跃度 — user 消息按周（会话数会被长会话复用扭曲，必须用消息数）')
for wk, n in q("""SELECT strftime('%Y-%W', datetime(timestamp,'unixepoch')) wk, COUNT(*)
                  FROM messages WHERE role='user' AND timestamp > 0
                  GROUP BY wk ORDER BY wk DESC LIMIT 16"""):
    print(f'  {wk}  {n:>4d}  ' + '#' * min(n, 60))

section('四、工具分布 (Top 20)')
rows = q("""SELECT tool_name, COUNT(*) n FROM messages
            WHERE role='tool' AND tool_name IS NOT NULL
            GROUP BY tool_name ORDER BY n DESC LIMIT 20""")
total = q("SELECT COUNT(*) FROM messages WHERE role='tool'")[0][0] or 1
for name, n in rows:
    print(f'  {name:30s} {n:>6d}  ({n / total * 100:4.1f}%)')
print(f'\n  并行/程序化占比: delegate_task + execute_code = '
      f'{sum(n for nm, n in rows if nm in ("delegate_task", "execute_code")) / total * 100:.1f}%  (<2% = 能力闲置)')

section('五、Skill 使用（解析 tool_calls JSON，不要用正则匹配 name）')
counts = Counter()
for (tc,) in q('SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL'):
    if not tc or 'skill_view' not in tc:
        continue
    try:
        for c in json.loads(tc):
            fn = c.get('function', {})
            if fn.get('name') != 'skill_view':
                continue
            args = fn.get('arguments', '{}')
            a = json.loads(args) if isinstance(args, str) else args
            if a.get('name'):
                counts[a['name']] += 1
    except Exception:
        continue
r = subprocess.run(['find', f'{DATA}/skills', '-name', 'SKILL.md'], capture_output=True, text=True)
n_installed = len([x for x in r.stdout.strip().split('\n') if x])
print(f'已安装 {n_installed} | 实际加载过 {len(counts)} 个 | 加载次数 {sum(counts.values())}')
for s, n in counts.most_common(20):
    print(f'  {s:38s} {n:>4d}')

section('六、Token 与成本')
it, ot, cr, cost = q("""SELECT SUM(input_tokens), SUM(output_tokens),
                        SUM(cache_read_tokens), SUM(estimated_cost_usd) FROM sessions""")[0]
print(f'输入 {it or 0:,} | 输出 {ot or 0:,} | 缓存读 {cr or 0:,} | 估算 ${cost or 0:.2f}')
for m, n in q('SELECT model, COUNT(*) FROM sessions GROUP BY model ORDER BY 2 DESC LIMIT 6'):
    print(f'  {m}: {n}')

section('七、Cron 任务')
try:
    jobs = json.load(open(f'{DATA}/cron/jobs.json'))
    if isinstance(jobs, dict):
        jobs = jobs.get('jobs', list(jobs.values()))
    en = [j for j in jobs if j.get('enabled')]
    print(f'总数 {len(jobs)} | 启用 {len(en)} | 暂停 {len(jobs) - len(en)}')
    for j in jobs:
        print(f"  {'OK ' if j.get('enabled') else 'PAUSE'} {(j.get('name') or '')[:44]:46s} model={j.get('model') or '-'}")
except Exception as e:
    print('  读取失败:', e)

section('八、工作区活跃度（文件 mtime，非目录 mtime）')
now = datetime.now()
for name in sorted(os.listdir(DATA)):
    p = os.path.join(DATA, name)
    if not os.path.isdir(p) or name.startswith('.'):
        continue
    latest, nf = 0, 0
    for root, dirs, files in os.walk(p):
        if any(x in root for x in ('.git', 'node_modules', '.venv', 'site-packages')):
            continue
        for f in files:
            nf += 1
            try:
                latest = max(latest, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    if latest:
        days = (now - datetime.fromtimestamp(latest)).days
        flag = 'GREEN' if days <= 7 else ('YELLOW' if days <= 30 else 'RED  ')
        print(f'  {flag} {name:34s} {days:>4d}d  {nf:>6d} files')
