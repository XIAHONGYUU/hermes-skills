#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive_excel_tree.py — 多文件表格批量归档器（Structured Document Extraction · 变体 B）

把一批 xlsx/xls/csv 表格整理成可检索的档案：

    <dest>/
      README-data.json  机读清单（源文件 → sheet 行列数 → CSV；含格式判定）
      manifest.json     同上（脚本主产物）
      raw/              原件，保真
      extracted/        <file>__<sheet>.csv 全量 + <file>.md 概览
      media/            xlsx 内嵌图片（xl/media/*）

依赖（Hermes 主 venv 里没有，用 uv 临时装）：
    uv run --quiet --with openpyxl --with xlrd python archive_excel_tree.py --src DIR --dest DIR

用法：
    python archive_excel_tree.py --src /opt/data/cache/documents --dest /opt/data/wiki/references/<topic>
    python archive_excel_tree.py --src '/path/*.xlsx' --dest out/ --sheet-rows 150 --preview-rows 25

设计要点（都是踩过的坑，别改）：
  * openpyxl read_only=True 会误报维度（真表被读成 1×1）→ 一律 read_only=False，
    并用 XML <row> 计数做交叉校验，把不一致写进 manifest。
  * .xls 走 xlrd；非 OLE2 会抛 XLRDError → 判为未知格式，不中断整批。
  * 未知格式做完整探测（魔数/内嵌签名/单字节 XOR/熵）后标记为加密或损坏，
    原件照旧收进 raw/ —— 归档不因单个坏文件失败。
  * 逐字节比对去重（重复上传的文件只留一份，并在 manifest 记录被去重者）。
"""

import argparse
import csv
import datetime
import glob
import json
import math
import os
import re
import shutil
import zipfile

# ---------------------------------------------------------------- 格式探测

KNOWN_SIGS = [
    (b"PK\x03\x04", "zip/xlsx"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole2/xls"),
    (b"\x1f\x8b", "gzip"),
    (b"7z\xbc\xaf", "7z"),
    (b"\xfd7zXZ", "xz"),
    (b"{\\rtf", "rtf"),
    (b"<?xml", "xml"),
    (b"<html", "html"),
]

EMBEDDED_SIGS = [b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x1f\x8b", b"7z\xbc\xaf", b"\xfd7zXZ"]


def entropy(buf):
    if not buf:
        return 0.0
    counts = [0] * 256
    for b in buf:
        counts[b] += 1
    n = len(buf)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


def probe(path):
    """返回该文件的格式判定 dict。只读，不做解析。"""
    with open(path, "rb") as fh:
        head = fh.read(64)
    raw = open(path, "rb").read()

    info = {"size": len(raw), "head_hex": head[:32].hex(), "kind": "unknown"}
    for sig, name in KNOWN_SIGS:
        if head.startswith(sig):
            info["kind"] = name
            break

    embedded = [name for sig, name in KNOWN_SIGS[:5] if raw.find(sig) > 0]
    info["embedded_sigs"] = embedded
    info["entropy"] = round(entropy(raw[:131072]), 3)

    if info["kind"] == "zip/xlsx":
        try:
            names = zipfile.ZipFile(path).namelist()
            info["is_real_xlsx"] = "xl/workbook.xml" in names
        except Exception as exc:  # noqa: BLE001
            info["is_real_xlsx"] = False
            info["zip_error"] = f"{type(exc).__name__}: {exc}"

    # 只有既非已知签名、又无内嵌档案时，才值得做 XOR 扫描
    if info["kind"] == "unknown" and not embedded:
        recovered = []
        for key in range(1, 256):
            if any(bytes(b ^ key for b in head[:8]).startswith(s) for s in EMBEDDED_SIGS[:2]):
                recovered.append(key)
        info["xor_recovered_keys"] = recovered
        if not recovered and info["entropy"] > 6.0:
            info["verdict"] = "likely_encrypted_or_opaque_container"
        else:
            info["verdict"] = "unknown_binary"
    return info


# ---------------------------------------------------------------- 解析

def fmt_cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")
    return str(v).replace("\n", " ").replace("|", "\\|").strip()


def xml_row_count(path):
    """openpyxl 的 read_only 维度不可信时，用 XML 里真实的 <row> 数校验。"""
    try:
        z = zipfile.ZipFile(path)
        sheet = next((n for n in z.namelist() if n.startswith("xl/worksheets/sheet")), None)
        if not sheet:
            return None
        return z.read(sheet).count(b"<row ")
    except Exception:  # noqa: BLE001
        return None


def load_book(path):
    """返回 (engine, sheet_provider)；失败抛异常由调用方记为不可解析。"""
    if path.lower().endswith(".xls"):
        import xlrd
        return "xlrd", xlrd.open_workbook(path)
    import openpyxl
    # read_only 一律 False：read_only=True 会静默误报维度
    return "openpyxl", openpyxl.load_workbook(path, data_only=True, read_only=False)


def sheet_names(engine, wb):
    return wb.sheet_names() if engine == "xlrd" else wb.sheetnames


def read_sheet(engine, wb, name):
    if engine == "xlrd":
        sh = wb.sheet_by_name(name)
        rows = [[fmt_cell(sh.cell_value(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)]
        return rows, sh.nrows, sh.ncols
    sh = wb[name]
    rows = [[fmt_cell(v) for v in row] for row in sh.iter_rows(values_only=True)]
    return rows, sh.max_row or 0, sh.max_column or 0


def md_table(rows, preview_rows, full_limit):
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return "_（空表）_\n"
    limit = None if (len(rows) <= full_limit) else preview_rows
    body = rows[:limit] if limit else rows
    head = body[0]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in body[1:]:
        r = list(r) + [""] * (len(head) - len(r))
        out.append("| " + " | ".join(r[: len(head)]) + " |")
    if limit and len(rows) > limit:
        out.append(f"\n_（共 {len(rows) - 1} 数据行，此处仅前 {limit - 1} 行；完整数据见同名 CSV）_")
    return "\n".join(out) + "\n"


def safe(name):
    return re.sub(r'[\\/:*?"<>|]', "_", str(name)).strip() or "sheet"


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="多文件表格批量归档器")
    ap.add_argument("--src", required=True, help="源目录，或带 glob 的路径（需加引号）")
    ap.add_argument("--dest", required=True, help="归档根目录")
    ap.add_argument("--pattern", default="*.xls*", help="源目录下的匹配模式（默认 *.xls*）")
    ap.add_argument("--preview-rows", type=int, default=25, help="大表 MD 概览行数")
    ap.add_argument("--full-limit", type=int, default=150, help="小于此行数×≤15列则 MD 输出全表")
    args = ap.parse_args()

    if any(ch in args.src for ch in "*?["):
        files = sorted(glob.glob(args.src))
    else:
        files = sorted(
            os.path.join(args.src, f)
            for f in os.listdir(args.src)
            if glob.fnmatch.fnmatch(f, args.pattern)
        )
    if not files:
        raise SystemExit(f"没有匹配到文件: {args.src} / {args.pattern}")

    raw_dir = os.path.join(args.dest, "raw")
    ext_dir = os.path.join(args.dest, "extracted")
    med_dir = os.path.join(args.dest, "media")
    for d in (raw_dir, ext_dir, med_dir):
        os.makedirs(d, exist_ok=True)

    seen_hashes = {}
    report = []

    for src in files:
        base = os.path.basename(src)
        # 去掉 Hermes 附件前缀 doc_<hex>_
        clean = re.sub(r"^doc_[0-9a-f]+_", "", base)
        info = {"file": clean, "source_name": base, "probe": probe(src)}

        # 逐字节去重
        blob = open(src, "rb").read()
        if blob in seen_hashes:
            info["status"] = "duplicate_skipped"
            info["duplicate_of"] = seen_hashes[blob]
            report.append(info)
            continue
        seen_hashes[blob] = clean

        dst = os.path.join(raw_dir, clean)
        shutil.copy2(src, dst)
        stem = os.path.splitext(clean)[0]

        # 收内嵌图片
        if info["probe"]["kind"] == "zip/xlsx":
            try:
                z = zipfile.ZipFile(dst)
                for n in z.namelist():
                    if n.startswith("xl/media/"):
                        out = os.path.join(med_dir, f"{stem}__{os.path.basename(n)}")
                        open(out, "wb").write(z.read(n))
                        info.setdefault("media", []).append(os.path.basename(out))
            except Exception:  # noqa: BLE001
                pass

        try:
            engine, wb = load_book(dst)
        except Exception as exc:  # noqa: BLE001
            info["status"] = f"unparsable: {type(exc).__name__}: {exc}"
            report.append(info)
            continue

        names = sheet_names(engine, wb)
        info["engine"] = engine
        info["sheets"] = []
        md = [
            f"# {clean}\n",
            f"> 归档于 {datetime.date.today()} ｜ 原件 `raw/{clean}` ｜ "
            f"{info['probe']['size']:,} B ｜ sheet 数 {len(names)}\n",
            f"**Sheet 列表**：{', '.join(names)}\n",
            "---\n",
        ]

        xml_rows = xml_row_count(dst) if engine == "openpyxl" else None
        for s in names:
            rows, nrows, ncols = read_sheet(engine, wb, s)
            csv_name = f"{stem}__{safe(s)}.csv"
            with open(os.path.join(ext_dir, csv_name), "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(rows)
            info["sheets"].append({"name": s, "rows": nrows, "cols": ncols, "csv": csv_name})
            md.append(f"## sheet「{s}」\n")
            md.append(f"行×列 = **{nrows} × {ncols}** ｜ 完整 CSV：`extracted/{csv_name}`\n")
            md.append(md_table(rows, args.preview_rows, args.full_limit))
            md.append("\n---\n")

        # 维度交叉校验：openpyxl 只认第一个 sheet 的 XML，作粗信号
        if xml_rows is not None and info["sheets"] and info["sheets"][0]["rows"] and xml_rows:
            if xml_rows > 1 and info["sheets"][0]["rows"] <= 1:
                info["WARNING"] = (
                    f"维度可疑：sheet1 max_row={info['sheets'][0]['rows']} "
                    f"但 XML <row> 数={xml_rows}。改用 read_only=False 重跑。"
                )

        open(os.path.join(ext_dir, f"{stem}.md"), "w", encoding="utf-8").write("\n".join(md))
        info["status"] = "ok"
        report.append(info)

    manifest = {
        "generated": datetime.datetime.now().isoformat(),
        "source": args.src,
        "files": report,
        "summary": {
            "total": len(report),
            "ok": sum(1 for r in report if r.get("status") == "ok"),
            "unparsable": sum(1 for r in report if str(r.get("status", "")).startswith("unparsable")),
            "duplicate_skipped": sum(1 for r in report if r.get("status") == "duplicate_skipped"),
        },
    }
    with open(os.path.join(args.dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    for r in report:
        st = r.get("status")
        if st == "ok":
            sheets = ", ".join(f"{s['name']}({s['rows']}×{s['cols']})" for s in r["sheets"])
            print(f"OK    {r['file']:<45} {r['engine']:<10} {sheets}")
        elif st == "duplicate_skipped":
            print(f"DUP   {r['file']:<45} 与 {r['duplicate_of']} 逐字节相同")
        else:
            print(f"FAIL  {r['file']:<45} {st}")
            print(f"      probe={json.dumps(r['probe'], ensure_ascii=False)}")
        if r.get("WARNING"):
            print(f"      ⚠️  {r['WARNING']}")

    print(f"\n清单: {os.path.join(args.dest, 'manifest.json')}")
    print(f"统计: {manifest['summary']}")


if __name__ == "__main__":
    main()
