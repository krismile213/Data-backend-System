# -*- coding: utf-8 -*-
"""
xlsx_append_sheet.py — 给已存在的 .xlsx **追加一张工作表**（OOXML zip 手术）
================================================================================
为什么不用 openpyxl 重存：`商品资料.xlsx` 里带**批注 / VML / 外部链接 / customXml**，
openpyxl 保存会**静默丢掉**这些 part（实测过，别再试）。

本脚本只做「追加」，改动面压到最小：
    新增 1 个 part : `xl/worksheets/sheetK.xml`
    修改 3 个登记处: `xl/workbook.xml` / `xl/_rels/workbook.xml.rels` / `[Content_Types].xml`
    其余 part      : **逐字节原样复制**
单元格用 `inlineStr`（直接内联字符串），因此**完全不触碰** `sharedStrings.xml`。

用法：
    python scripts/xlsx_append_sheet.py <src.xlsx> <dst.xlsx> <子表名> <rows.json>
    rows.json = {"header": ["SKU", "说明", "登记日期"],
                 "rows": [["G495", "…", "2026-09-28"], …]}

安全：
  - dst 已存在 → 拒绝（除非 --overwrite）；
  - 子表重名 → 拒绝；
  - 写完自动**自检**（读回行数 + 逐 part 比对，非登记 part 必须字节一致）。
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

WSP_TYPE = ("http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/worksheet")
WS_CTYPE = ("application/vnd.openxmlformats-officedocument."
            "spreadsheetml.worksheet+xml")

MUTABLE = ("xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml")


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _col(i: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(i)


def build_worksheet_xml(header, rows) -> bytes:
    """最小合法 worksheet（inlineStr）。header + rows 逐行写入。"""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main">',
             "<sheetData>"]
    for ri, vals in enumerate([list(header)] + [list(r) for r in rows], start=1):
        cells = []
        for ci, v in enumerate(vals, start=1):
            ref = f"{_col(ci)}{ri}"
            s = "" if v is None else str(v)
            if s == "":
                cells.append(f'<c r="{ref}"/>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr">'
                             f'<is><t xml:space="preserve">{_esc(s)}</t></is></c>')
        lines.append(f'<row r="{ri}">{"".join(cells)}</row>')
    lines += ["</sheetData>", "</worksheet>"]
    return "".join(lines).encode("utf-8")


def append_sheet(src, dst, sheet_name, header, rows) -> dict:
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise SystemExit(f"中止：源文件不存在 {src}")
    if dst.exists():
        raise SystemExit(f"中止：目标已存在 {dst}（先删或换名）")

    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        names = set(z.namelist())
        payload = {i.filename: z.read(i.filename) for i in infos if not i.is_dir()}
        wb = payload["xl/workbook.xml"].decode("utf-8")
        rels = payload["xl/_rels/workbook.xml.rels"].decode("utf-8")
        ct = payload["[Content_Types].xml"].decode("utf-8")

    if sheet_name in re.findall(r'<sheet[^>]*?name="([^"]*)"', wb):
        raise SystemExit(f"中止：已存在同名子表『{sheet_name}』")
    if "</sheets>" not in wb:
        raise SystemExit("中止：workbook.xml 里找不到 </sheets>")

    used = [int(m) for m in re.findall(r"xl/worksheets/sheet(\d+)\.xml", "\n".join(names))]
    k = max(used) + 1
    sid = max([int(m) for m in re.findall(r'sheetId="(\d+)"', wb)] or [0]) + 1
    rid = max([int(m) for m in re.findall(r'Id="rId(\d+)"', rels)] or [0]) + 1
    target = f"worksheets/sheet{k}.xml"

    wb2 = wb.replace(
        "</sheets>",
        f'<sheet name="{_esc(sheet_name)}" sheetId="{sid}" r:id="rId{rid}"/></sheets>')
    rels2 = rels.replace(
        "</Relationships>",
        f'<Relationship Id="rId{rid}" Type="{WSP_TYPE}" Target="{target}"/>'
        f"</Relationships>")
    ct2 = ct.replace(
        "</Types>",
        f'<Override PartName="/xl/{target}" ContentType="{WS_CTYPE}"/></Types>')

    new_ws = build_worksheet_xml(header, rows)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for info in infos:
            if info.is_dir():
                out.writestr(info, b"")
                continue
            name = info.filename
            if name == "xl/workbook.xml":
                data = wb2.encode("utf-8")
            elif name == "xl/_rels/workbook.xml.rels":
                data = rels2.encode("utf-8")
            elif name == "[Content_Types].xml":
                data = ct2.encode("utf-8")
            else:
                data = payload[name]
            out.writestr(info, data)
        zi = zipfile.ZipInfo(f"xl/{target}", date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        out.writestr(zi, new_ws)

    return {"sheet": sheet_name, "part": f"xl/{target}", "sheetId": sid,
            "rid": f"rId{rid}", "rows": len(rows), "cols": len(header)}


def selfcheck(src, dst, sheet_name) -> int:
    """自检：① 新表能读回；② 非登记 part 逐字节一致。返回 0/1。"""
    bad = 0
    from openpyxl import load_workbook

    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        na, nb = set(a.namelist()), set(b.namelist())
        added, removed = sorted(nb - na), sorted(na - nb)
        print(f"[自检] 新增 part = {added}")
        if removed:
            print(f"[自检] ✗ 有 part 丢失 = {removed}"); bad = 1
        if len(added) != 1 or not added[0].endswith(".xml"):
            print("[自检] ✗ 新增 part 应为 1 个 worksheet xml"); bad = 1

        shared = sorted(na & nb)
        diff = [n for n in shared if a.read(n) != b.read(n)]
        same = len(shared) - len(diff)
        print(f"[自检] 共有 part {len(shared)}：{same} 个字节一致，{len(diff)} 个有变 {diff}")
        if sorted(diff) != sorted(MUTABLE):
            print(f"[自检] ✗ 变动 part 应恰为 {sorted(MUTABLE)}"); bad = 1

    wb = load_workbook(dst, read_only=True, data_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            print(f"[自检] ✗ 『{sheet_name}』未出现在 sheetnames：{wb.sheetnames}"); bad = 1
        else:
            got = list(wb[sheet_name].iter_rows(values_only=True))
            print(f"[自检] 『{sheet_name}』读回 {len(got)} 行：")
            for r in got:
                print("        ", r)
            if len(got) < 2:
                print("[自检] ✗ 行数异常（至少 1 表头 + 1 数据）"); bad = 1
    finally:
        wb.close()
    print("[自检]", "PASS" if bad == 0 else "FAIL")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="给 xlsx 追加一张工作表（zip 手术）")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("sheet_name")
    ap.add_argument("rows_json", help='{"header":[...],"rows":[[...],…]}')
    ap.add_argument("--check", action="store_true", help="只跑自检（不写入）")
    args = ap.parse_args(argv)

    if args.check:
        return selfcheck(args.src, args.dst, args.sheet_name)

    spec = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
    info = append_sheet(args.src, args.dst, args.sheet_name,
                        spec["header"], spec["rows"])
    print(f"[写入] {info['part']} sheetId={info['sheetId']} {info['rid']} "
          f"{info['rows']} 行 × {info['cols']} 列 → {args.dst}")
    return selfcheck(args.src, args.dst, args.sheet_name)


if __name__ == "__main__":
    raise SystemExit(main())
