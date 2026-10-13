#!/usr/bin/env python3
"""Hermes cron 全表审计 — 只读，不改任何东西。

输出：状态 / **北京时间** / 上次运行 / 投递健康 / 死亡任务 / 每日推送预算

用法：
    /opt/hermes/.venv/bin/python3 cron-fleet-audit.py
    /opt/hermes/.venv/bin/python3 cron-fleet-audit.py --deaddays 30   # 死亡判定线（天）

设计要点（都是踩过的坑）：
  * 北京时间换算带**自检断言** —— 2026-10-11 曾把分/时写反，整张表全错。
  * 投递健康读 `last_delivery_error`，不看输出文件 —— 投递失败的任务输出文件是正常的。
  * `schedule` 是 UTC，服务器 localtime 也是 UTC，北京 = +8h。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JOBS_FILE = Path("/opt/data/cron/jobs.json")
OUTPUT_DIR = Path("/opt/data/cron/output")
DEAD_DAYS = 30

DOW = {"0": "周日", "1": "周一", "2": "周二", "3": "周三",
       "4": "周四", "5": "周五", "6": "周六", "7": "周日"}


# ────────────────────────────── 北京时间换算 ──────────────────────────────

def bj_hhmm(expr: str) -> tuple[str, bool]:
    """UTC cron → (北京时间 HH:MM, 是否跨到次日)。

    只处理纯数字时/分；`*/30`、`1-7`、`1,3` 等复杂形式原样返回 expr。
    北京时间小时 = (UTC小时 + 8) % 24 —— **分钟不动**。
    """
    f = str(expr).split()
    if len(f) != 5:
        return expr, False
    mi, hr = f[0], f[1]
    if not (mi.isdigit() and hr.isdigit()):
        return expr, False
    h2 = (int(hr) + 8) % 24
    return f"{h2:02d}:{int(mi):02d}", h2 < int(hr)          # 跨日 = 加完 8 小时反而变小


# 自检：锚点在写代码时验证过一次，之后每次运行都再验一遍。
for _expr, _want in (("0 14 * * *", "22:00"),   # 北京晚 22:00（用户设定的每日五问）
                     ("0 0 * * *", "08:00"),    # 北京早 8:00
                     ("0 18 * * *", "02:00")):  # 跨日：北京次日凌晨 2:00
    _got = bj_hhmm(_expr)[0]
    assert _got == _want, f"时区换算自检失败: '{_expr}' -> {_got}，应为 {_want}"


def cadence(expr: str) -> str:
    """把 cron 表达式翻译成中文频率描述（尽力而为）。"""
    f = str(expr).split()
    if len(f) != 5:
        return str(expr)
    _, _, dom, mon, dow = f
    parts: list[str] = []
    if dow == "*":
        parts.append("每天")
    elif dow == "1-5":
        parts.append("工作日")
    elif dow.isdigit():
        parts.append(DOW.get(dow, f"周{dow}"))
    elif "-" in dow or "," in dow:
        toks = dow.replace("-", ",").split(",")
        parts.append("/".join(DOW.get(t, t) for t in toks[:2]) if len(toks) == 2
                     else "/".join(DOW.get(t, t) for t in toks))
    else:
        parts.append(f"周{dow}")
    if mon != "*":
        parts.append(f"{mon}月")
    if dom != "*":
        parts.append(f"{dom}号")
    return " ".join(parts)


# ────────────────────────────── 载入 ──────────────────────────────

def load_jobs() -> list[dict]:
    if not JOBS_FILE.exists():
        print(f"❌ 找不到 {JOBS_FILE}")
        sys.exit(1)
    data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    if isinstance(jobs, dict):                 # 兼容 {id: {...}} 形式
        jobs = list(jobs.values())
    return [j for j in jobs if isinstance(j, dict)]


def parse_iso(ts) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_output(job_id: str) -> tuple[str, int] | None:
    d = OUTPUT_DIR / job_id
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.md"), reverse=True)
    if not files:
        return None
    try:
        return files[0].name, len(files[0].read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return files[0].name, -1


def main() -> int:
    dead_days = DEAD_DAYS
    if "--deaddays" in sys.argv:
        i = sys.argv.index("--deaddays")
        if i + 1 < len(sys.argv):
            dead_days = int(sys.argv[i + 1])

    now = datetime.now(timezone.utc)
    jobs = load_jobs()

    rows = []
    for j in jobs:
        jid = j.get("id") or j.get("job_id") or "?"
        sched = j.get("schedule")
        if isinstance(sched, dict):
            sched = sched.get("expr") or sched.get("display") or ""
        sched = str(sched)
        last = parse_iso(j.get("last_run_at"))
        rows.append({
            "id": jid, "name": str(j.get("name") or jid[:12]),
            "enabled": bool(j.get("enabled", True)),
            "sched": sched, "bj": bj_hhmm(sched)[0], "roll": bj_hhmm(sched)[1],
            "cad": cadence(sched),
            "last": last, "age": (now - last).days if last else None,
            "status": j.get("last_status"),
            "derr": j.get("last_delivery_error"),
            "deliver": str(j.get("deliver") or ""),
            "no_agent": bool(j.get("no_agent", False)),
            "out": last_output(jid),
        })

    act = [r for r in rows if r["enabled"]]
    pau = [r for r in rows if not r["enabled"]]

    def fmt(r, verbose=False) -> str:
        flags = []
        if r["derr"]:
            flags.append("🔴投递失败")
        if r["status"] == "error":
            flags.append("🔴运行报错")
        if r["last"] is None:
            flags.append("·从未运行")
        elif r["age"] is not None and r["age"] >= dead_days:
            flags.append(f"💀死物({r['age']}天)")
        if r["no_agent"]:
            flags.append("静默脚本")
        if r["deliver"] == "local":
            flags.append("local")
        last_s = r["last"].strftime("%m-%d %H:%M") + "UTC" if r["last"] else "—"
        bj = r["bj"] + ("⁺¹" if r["roll"] else "")
        out = f"{r['out'][1]}字" if r["out"] else "—"
        line = (f"  {r['id'][:12]:14s}{r['name'][:24]:26s}{r['cad']:10s}{bj:8s}"
                f"{last_s:13s}{out:>8s}  {' '.join(flags)}")
        return line

    print("=" * 118)
    print(f"启用 {len(act)} / 暂停 {len(pau)}      UTC {now:%Y-%m-%d %H:%M}  =  北京 {now + timedelta(hours=8):%Y-%m-%d %H:%M}")
    print("  ⚠️ 排程时间 = 拨表时刻(UTC)；北京 = +8h；⁺¹ 表示跨到次日")
    print("=" * 118)
    for r in sorted(act, key=lambda x: (x["bj"], x["name"])):
        print(fmt(r))

    # ── 投递健康 ──────────────────────────────────────────────────────
    bad = [r for r in act if r["derr"]]
    print(f"\n{'=' * 118}\n【投递失败 {len(bad)} 个】—— 任务在跑但消息到不了，last_status 仍是 ok")
    if not bad:
        print("  ✅ 无")
    for r in bad:
        recent = r["last"] and (now - r["last"]) <= timedelta(hours=48)
        print(f"  {'🔴' if recent else '⚪️陈旧'} {r['id'][:12]} {r['name']}  deliver={r['deliver']}")
        print(f"      ↳ {str(r['derr'])[:150]}")
        if "99992402" in str(r["derr"]):
            print(f"      ↳ 修法: cronjob action=update job_id={r['id']} name=\"{r['name']}\" "
                  f"deliver=feishu:oc_<chat_id>")

    # ── 死亡任务 ──────────────────────────────────────────────────────
    dead = [r for r in act if r["age"] is not None and r["age"] >= dead_days]
    print(f"\n【死亡任务 {len(dead)} 个】—— 启用中但 ≥{dead_days} 天没跑（多半引用已消失的生活场景）")
    if not dead:
        print("  ✅ 无")
    for r in sorted(dead, key=lambda x: -(x["age"] or 0)):
        print(f"  💀 {r['id'][:12]} {r['name'][:28]:30s} {r['cad']:10s} 最后 {r['last']:%Y-%m-%d}（{r['age']} 天前）")

    # ── 零信息噪音：no_agent 且输出内容每天相同 ────────────────────────
    print(f"\n【可疑零信息噪音】—— no_agent 脚本，最近 3 次输出内容完全相同 = 每天发同一句")
    for r in act:
        if not r["no_agent"]:
            continue
        d = OUTPUT_DIR / r["id"]
        files = sorted(d.glob("*.md"), reverse=True)[:3] if d.is_dir() else []
        if len(files) < 3:
            continue
        bodies = []
        for f in files:
            t = f.read_text(encoding="utf-8", errors="replace")
            bodies.append(t.split("---", 1)[-1].strip()[:200])
        if bodies[0] and len(set(bodies)) == 1:
            print(f"  🟡 {r['id'][:12]} {r['name'][:28]:30s} → 「{bodies[0][:60]}」")
            print("      ↳ 修法：脚本空闲时应 print 空 → 空 stdout = 静默不推送")

    # ── 推送预算 ──────────────────────────────────────────────────────
    def per_day(r) -> float:
        c = r["cad"]
        if "每天" in c:
            return 1.0
        if "工作日" in c:
            return 5 / 7
        return 0.0

    pushes = [r for r in act if r["deliver"] != "local" and r["no_agent"] is False]
    daily = sum(per_day(r) for r in act if r["deliver"] != "local")
    print(f"\n{'=' * 118}")
    print(f"【推送预算】面向用户发送的任务 {len(pushes)} 个 → 平均 {daily:.1f} 条/天"
          f"（不含 local 归档、每周/每月任务）")
    print(f"【暂停中的 {len(pau)} 个】")
    for r in pau:
        last_s = r["last"].strftime("%Y-%m-%d") if r["last"] else "从未运行"
        print(f"  ⏸  {r['id'][:12]} {r['name'][:28]:30s} {r['cad']:10s} 最后 {last_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
