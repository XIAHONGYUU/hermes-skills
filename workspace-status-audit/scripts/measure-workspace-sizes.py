#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工作区磁盘三分法量尺 —— 真数据 / .git / 依赖缓存。

为什么需要它：两种常见的"量磁盘"错法方向相反。
  * `du -sh /opt/data/*`            → shell 通配符把嵌套 .venv/site-packages 也展开，
                                      输出里混入 `python3.13`、`lib` 这类伪顶层项（多算/噪声）。
  * 手写 os.walk + SKIP={'.venv','.git'} → 少算。实测把 751 MB 报成 2 MB（43 倍），
                                      用户会据此以为"没东西可清"，结论完全相反。

三分法不只是更准，是更有用：对"该不该收口"的判断，
"真数据 96 MB + 依赖 277 MB" 比 "合计 469 MB" 有用得多 ——
前者告诉你有 277 MB 可再生，后者只给一个无法行动的合计数。

用法：
    python3 measure-workspace-sizes.py            # /opt/data
    python3 measure-workspace-sizes.py /some/path
    python3 measure-workspace-sizes.py --top 12   # 只列最大的 12 项

输出：每个工作区一行 + 合计；再单列 >50 MB 的依赖/缓存重灾区，附可回收估算。
纯 stdlib，无依赖。
"""
import os
import sys

REGEN = {'.venv', 'venv', 'node_modules', '__pycache__', '.cache',
         'site-packages', '.pytest_cache', '.mypy_cache', '.ruff_cache'}
GIT = {'.git'}


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def measure(root):
    """Return (real, git, dep). Symlinks are counted as ~0 and never followed."""
    real = git = dep = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for d in list(dirnames):
            dp = os.path.join(dirpath, d)
            if os.path.islink(dp):           # 别名软链：不跟随、不重复计
                dirnames.remove(d)
                continue
            if d in GIT:
                git += _du(dp)
                dirnames.remove(d)           # 已计入，不再下探
            elif d in REGEN:
                dep += _du(dp)
                dirnames.remove(d)
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                real += os.lstat(fp).st_size  # lstat：软链本身不展开
            except OSError:
                pass
    return real, git, dep


def _du(p):
    t = 0
    for r, _, fs in os.walk(p):
        for f in fs:
            try:
                t += os.lstat(os.path.join(r, f)).st_size
            except OSError:
                pass
    return t


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    top_n = 12
    if '--top' in sys.argv:
        try:
            top_n = int(sys.argv[sys.argv.index('--top') + 1])
        except (IndexError, ValueError):
            pass
    top = args[0] if args else '/opt/data'

    rows = []
    for name in sorted(os.listdir(top)):
        p = os.path.join(top, name)
        if not os.path.isdir(p) or os.path.islink(p):
            continue
        r, g, d = measure(p)
        total = r + g + d
        if total < 1024 * 1024:              # 忽略 <1 MB
            continue
        rows.append((name, r, g, d, total))
    rows.sort(key=lambda t: -t[4])

    print("=" * 78)
    print(f"三分法量尺  {top}")
    print("  真数据 = 你的内容 | .git = 版本历史 | 依赖/缓存 = 可再生")
    print("=" * 78)
    print(f"{'工作区':<28}{'真数据':>10}{'.git':>10}{'依赖/缓存':>12}{'合计':>10}")
    print("-" * 78)
    tr = tg = td = 0
    for name, r, g, d, total in rows[:top_n] if top_n else rows:
        print(f"{name:<28}{human(r):>10}{human(g):>10}{human(d):>12}{human(total):>10}")
        tr += r
        tg += g
        td += d
    print("-" * 78)
    print(f"{'小计（列出项）':<28}{human(tr):>10}{human(tg):>10}{human(td):>12}{human(tr + tg + td):>10}")

    # 重灾区：单独拆出 >50 MB 的依赖/缓存与 .git，这是"可回收"的候选池
    print("\n重灾区（>50 MB 的依赖/缓存 或 .git）—— 回收前逐项确认：")
    hot = []
    for name in sorted(os.listdir(top)):
        p = os.path.join(top, name)
        if not os.path.isdir(p) or os.path.islink(p):
            continue
        for dirpath, dirnames, _ in os.walk(p):
            for d in list(dirnames):
                dp = os.path.join(dirpath, d)
                if os.path.islink(dp):
                    dirnames.remove(d)
                    continue
                if d in REGEN | GIT:
                    s = _du(dp)
                    if s > 50 * 1024 * 1024:
                        kind = 'git 历史' if d in GIT else '可再生'
                        hot.append((s, dp.replace(top + '/', ''), kind))
                    dirnames.remove(d)
    for s, path, kind in sorted(hot, reverse=True):
        print(f"  {human(s):>8}  [{kind:<7}] {path}")
    if not hot:
        print("  （无）")

    print("\n回收前必查（.git 与本地唯一副本不适用'直接删'）：")
    print("  git -C <dir> remote -v          # 有远端 → 本地可重建")
    print("  git -C <dir> status --porcelain # 未提交改动 = 唯一不可再生部分")
    print("  ⚠️ 远端凭据失效 ≠ 远端可用（git ls-remote 对公开仓库会假阳性）")


if __name__ == '__main__':
    main()
