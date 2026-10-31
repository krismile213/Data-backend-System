# -*- coding: utf-8 -*-
"""seed_sku_registry.py — 生成独立登记表 `资产文件\\SKU登记表.xlsx`（搬家 + 幂等重建）

背景
----
两张**人工维护**的登记子表（`SKU换号` / `人工登记`）原本被塞在
`资产文件\\商品资料.xlsx` 里。而 `商品资料.xlsx` 是**机器每月重新导出的商品主数据**，
用户会不定期**整体替换**它 —— 一换文件，这两张子表就消失 → 后端 `import_products`
（全量覆盖）会在下次导入时**静默清空** `sku_alias` / `sku_hold`，离线脚本则静默读到空。
（2026-09-29 已真实发生过一次。）

本脚本把这两张登记表**搬进独立文件** `SKU登记表.xlsx`：
  - 子表名与表头**逐字**保持原样（`SKU换号`: 旧SKU|新SKU|换号说明|生效日期；
    `人工登记`: SKU|说明|登记日期），保证后端与离线两条读法零改动即可继续工作；
  - 数据来源优先 DB（`app.db.SessionLocal` + `app.models.SkuAlias/SkuHold`），
    DB 不可用时用**硬编码兜底**（与 2026-09-30 的库内容一致）；
  - **幂等**：重复运行得到相同内容（整体重写，不做增量）。

目标路径 = `app.sku_alias.resolve_registry_path()`
（env `SKU_REGISTRY_XLSX` → rule_config `registry_xlsx_path` → 内置默认），
与后端/离线**共用同一份真源常量**，避免三处各写一个路径。

用法：
    python scripts/seed_sku_registry.py [--path X.xlsx] [--no-db]
"""

import argparse
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]                      # 数据后台系统/
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from openpyxl import Workbook, load_workbook                        # noqa: E402

from app.sku_alias import (ALIAS_SHEET, REGISTRY_XLSX_DEFAULT,       # noqa: E402
                           resolve_registry_path)
from app.sku_hold import HOLD_SHEET                                  # noqa: E402

# 两个子表**逐字**表头（顺序即列序，第 1 行）
ALIAS_HEADER = ["旧SKU", "新SKU", "换号说明", "生效日期"]
HOLD_HEADER = ["SKU", "说明", "登记日期"]

# ---- 硬编码兜底（DB 不可用时使用；与 2026-09-30 的 sku_alias / sku_hold 内容一致）----
FALLBACK_ALIASES = [
    {"old": "G493", "new": "G510",
     "note": "机型由 Galaxy S26 Pro 5G 变更为 Galaxy S26 5G；旧号流程停在 Part6，不再使用",
     "effectiveAt": "2025-11-19"},
    {"old": "G494", "new": "G533",
     "note": "机型由 Galaxy S26 Edge 5G 变更为 Galaxy S26+ 5G；旧号流程停在 Part6/Part9，不再使用",
     "effectiveAt": "2026-03-02"},
]
FALLBACK_HOLDS = [
    {"sku": "G495", "registeredAt": "2026-09-28",
     "reason": "业务上已不需走完流程（Samsung Galaxy S26 Ultra 5G，货已在售）；"
               "Part8 两张「手机上架确认」为历史悬挂单据，不计入时效"},
    {"sku": "G524", "registeredAt": "2026-09-28",
     "reason": "业务上已不需走完流程（Realme 16 Pro 5G，货已在售，老单停留超半年）"},
    {"sku": "G525", "registeredAt": "2026-09-28",
     "reason": "业务上已不需走完流程（Honor Magic8 Lite 5G，货已在售，老单停留超半年）"},
    {"sku": "G526", "registeredAt": "2026-09-28",
     "reason": "业务上已不需走完流程（Oppo Reno15 Pro Mini 5G，货已在售，老单停留超半年）"},
    {"sku": "G527", "registeredAt": "2026-09-28",
     "reason": "业务上已不需走完流程（Oppo Reno15 Pro 5G，货已在售，老单停留超半年）"},
    {"sku": "G528", "registeredAt": "2026-09-28",
     "reason": "业务上已不需走完流程（Oppo Reno15 5G，货已在售，老单停留超半年）"},
]


def _s(v) -> str:
    """单元格/DB 值 → 去空白字符串；None/NaN 统一为 ''（避免写进 'None'）。"""
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none") else t


def load_from_db():
    """从 DB 读两张登记表 → (alias_rows, hold_rows)。DB 不可用/为空 → (None, None)。"""
    try:
        from app.db import SessionLocal
        from app.models import SkuAlias, SkuHold
    except Exception as e:                      # noqa: BLE001
        print(f"⚠ 无法导入 DB 模块：{type(e).__name__}: {e}")
        return None, None
    try:
        with SessionLocal() as s:
            alias_rows = [{"old": _s(a.old_sku), "new": _s(a.new_sku),
                           "note": _s(a.note), "effectiveAt": _s(a.effective_at)}
                          for a in s.query(SkuAlias).all()]
            hold_rows = [{"sku": _s(h.sku), "reason": _s(h.reason),
                          "registeredAt": _s(h.registered_at)}
                         for h in s.query(SkuHold).all()]
        if not alias_rows and not hold_rows:
            print("ℹ DB 两张登记表都为空 → 改走硬编码兜底")
            return None, None
        return alias_rows, hold_rows
    except Exception as e:                      # noqa: BLE001
        print(f"⚠ DB 不可用（{type(e).__name__}: {e}）→ 改走硬编码兜底")
        return None, None


def _sort_rows(rows, key):
    return sorted(rows, key=lambda r: _s(r.get(key)))


def write_registry(path: Path, alias_rows, hold_rows) -> None:
    """新建（整体重写）登记表；两张子表名与表头逐字固定，数据从第 2 行起。"""
    wb = Workbook()
    # 固定文档属性时间戳 → 同一份内容重复运行得到**逐字节相同**的文件（真幂等）
    _fixed = datetime(2026, 9, 30, 0, 0, 0)
    wb.properties.created = _fixed
    wb.properties.modified = _fixed
    wb.properties.creator = "seed_sku_registry.py"
    wb.properties.lastModifiedBy = "seed_sku_registry.py"
    # openpyxl 新建工作簿自带一个默认表 → 复用它当第一张子表，避免留下空 Sheet
    ws_alias = wb.active
    ws_alias.title = ALIAS_SHEET
    ws_alias.append(ALIAS_HEADER)
    for r in alias_rows:
        ws_alias.append([_s(r.get("old")), _s(r.get("new")),
                         _s(r.get("note")), _s(r.get("effectiveAt"))])

    ws_hold = wb.create_sheet(HOLD_SHEET)
    ws_hold.append(HOLD_HEADER)
    for r in hold_rows:
        ws_hold.append([_s(r.get("sku")), _s(r.get("reason")),
                        _s(r.get("registeredAt"))])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    _normalize_zip(path)


#: 归一化 zip 内所有条目的时间戳 → 同一份内容得到**逐字节相同**的 xlsx（真幂等）
_ZIP_STAMP = (2026, 9, 30, 0, 0, 0)
_FIXED_W3CDTF = b"2026-09-30T00:00:00Z"
_CORE_TS_RE = re.compile(rb'(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)')


def _normalize_zip(path: Path) -> None:
    """把 xlsx（zip）内条目时间戳与 core.xml 时间戳统一为固定值 —— 消除时间抖动，得到真幂等输出。

    （openpyxl 保存时会把 `dcterms:modified` 重置为当前时间，故必须在此处兜底改写。）
    """
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(path) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = _CORE_TS_RE.sub(rb"\g<1>" + _FIXED_W3CDTF + rb"\g<2>", data)
            zinfo = zipfile.ZipInfo(item.filename, date_time=_ZIP_STAMP)
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zinfo.external_attr = item.external_attr
            zout.writestr(zinfo, data)
    os.replace(tmp, path)


def selfcheck(path: Path) -> tuple:
    """重开文件复核：子表名、表头、数据行数。返回 (alias_n, hold_n)。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        assert ALIAS_SHEET in wb.sheetnames, f"缺子表 {ALIAS_SHEET}：{wb.sheetnames}"
        assert HOLD_SHEET in wb.sheetnames, f"缺子表 {HOLD_SHEET}：{wb.sheetnames}"
        a_rows = list(wb[ALIAS_SHEET].iter_rows(values_only=True))
        h_rows = list(wb[HOLD_SHEET].iter_rows(values_only=True))
        assert [_s(x) for x in a_rows[0][:4]] == ALIAS_HEADER, \
            f"{ALIAS_SHEET} 表头不符：{a_rows[0]}"
        assert [_s(x) for x in h_rows[0][:3]] == HOLD_HEADER, \
            f"{HOLD_SHEET} 表头不符：{h_rows[0]}"
        a_n = sum(1 for r in a_rows[1:] if _s(r[0]) or _s(r[1]))
        h_n = sum(1 for r in h_rows[1:] if _s(r[0]))
        return a_n, h_n
    finally:
        wb.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成独立登记表 SKU登记表.xlsx（幂等）")
    ap.add_argument("--path", default=None,
                    help="输出路径（默认 = app.sku_alias.resolve_registry_path()）")
    ap.add_argument("--no-db", action="store_true",
                    help="跳过 DB，直接用硬编码兜底（用于 DB 不可用场景演练）")
    args = ap.parse_args(argv)

    target = Path(args.path) if args.path else resolve_registry_path()
    existed = target.is_file()

    if args.no_db:
        alias_rows, hold_rows = None, None
    else:
        alias_rows, hold_rows = load_from_db()
    if alias_rows is None:
        alias_rows = list(FALLBACK_ALIASES)
        hold_rows = list(FALLBACK_HOLDS)
        source = "硬编码兜底"
    else:
        source = "DB"

    alias_rows = _sort_rows(alias_rows, "old")
    hold_rows = _sort_rows(hold_rows, "sku")

    write_registry(target, alias_rows, hold_rows)
    a_n, h_n = selfcheck(target)

    size = target.stat().st_size
    print("=" * 68)
    print(f"SKU登记表 生成完毕（来源={source}，{'覆盖' if existed else '新建'}）")
    print(f"  路径   : {target}")
    print(f"  字节数 : {size}")
    print(f"  子表   : {[ALIAS_SHEET, HOLD_SHEET]}")
    print(f"  {ALIAS_SHEET} 行数 = {a_n}（表头 {ALIAS_HEADER}）")
    print(f"  {HOLD_SHEET} 行数 = {h_n}（表头 {HOLD_HEADER}）")
    print(f"  生成于 : {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  内置默认路径 : {REGISTRY_XLSX_DEFAULT}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
