# -*- coding: utf-8 -*-
"""🚫 已废弃（2026-09-30，T15）—— 请勿再使用，保留仅作事故记录。

**为什么废弃**：本脚本代表的是「把人工登记表补回商品资料.xlsx」这条老思路，
而 T15 已经**把两张登记表迁到独立文件** `资产文件\\SKU登记表.xlsx`：
    · 后端 `etl/products_loader.import_registry()`（护栏：异常绝不清库）
    · 离线 `流程复盘/route_check_sys.py` 的 `read_alias_map/read_hold_map`
⇒ 商品资料.xlsx 以后**随便替换**都不再影响登记表，**不需要也不应该**再往它里面缝子表。
⇒ 真正要恢复登记数据，改回文件后跑 `python backend/etl/products_loader.py --force` 即可。
详见 `docs/system_design.md` §9 T15。

--- 以下为原始说明（历史）---

把「SKU换号」+「人工登记」两个子表**补回当前**商品资料.xlsx（zip 手术），再重导。

⚠️ 背景：2026-09-29 11:02:46 商品资料.xlsx 被整体替换（换成含「8月销量得分」的新导出），
   两个自建子表随之消失 → 离线读不到（已换号 0 / 已豁免 0），且 DB 一旦重导就会被清空。
   本脚本只**追加**两个子表，其它 part 逐字节保留（用户的新内容不动）。
"""
import hashlib
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(r"D:\Desktop\work-main\数据后台系统")
REAL = Path(r"D:\Desktop\work-main\资产文件\商品资料.xlsx")
BK = ROOT / "backups" / "商品资料_备份_20260929-1630_补子表前.xlsx"
T1 = REAL.with_name("商品资料._tmp1.xlsx")
T2 = REAL.with_name("商品资料._tmp2.xlsx")

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
import xlsx_append_sheet as xas                                     # noqa: E402
from app.sku_alias import load_aliases                              # noqa: E402
from app.sku_hold import load_holds                                 # noqa: E402


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sheets_of(p):
    with zipfile.ZipFile(p) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
    return re.findall(r'<sheet[^>]*?name="([^"]*)"', wb)


print("=== 0. 现状 ===")
print("file:", REAL, "size", REAL.stat().st_size)
cur = sheets_of(REAL)
print("现有子表:", cur)
if "SKU换号" in cur or "人工登记" in cur:
    raise SystemExit("中止：已存在目标子表，需人工确认")

# 从 DB 复原两个子表的内容（DB 仍是 10:30 导入的、未被污染的那份）
al = load_aliases(force=True)
ho = load_holds(force=True)
print("DB 换号 =", len(al), "| DB 豁免 =", len(ho))
if len(al) != 2 or len(ho) != 6:
    raise SystemExit(f"中止：DB 登记数异常（换号 {len(al)} / 豁免 {len(ho)}），先人工核对")

alias_rows = [[k, v["new"], v["note"], v["effectiveAt"]] for k, v in sorted(al.items())]
hold_rows = [[k, v["reason"], v["registeredAt"]] for k, v in sorted(ho.items())]
print("换号行:", alias_rows)
print("豁免行:", [[r[0], r[2]] for r in hold_rows])

print("\n=== 1. 备份 ===")
BK.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(REAL, BK)
print("→", BK, md5(BK))
before = md5(REAL)

print("\n=== 2. 追加两个子表 ===")
for t in (T1, T2):
    if t.exists():
        t.unlink()
i1 = xas.append_sheet(REAL, T1, "SKU换号", ["旧SKU", "新SKU", "换号说明", "生效日期"], alias_rows)
print("换号:", i1)
i2 = xas.append_sheet(T1, T2, "人工登记", ["SKU", "说明", "登记日期"], hold_rows)
print("豁免:", i2)

print("\n=== 3. 自检（逐张比：REAL→T1 只多 1 个 part，T1→T2 只多 1 个 part）===")
rc = xas.selfcheck(REAL, T1, "SKU换号")
rc |= xas.selfcheck(T1, T2, "人工登记")
if rc != 0:
    raise SystemExit("中止：自检 FAIL")

from openpyxl import load_workbook                               # noqa: E402
_wb = load_workbook(T2, read_only=True, data_only=True)
try:
    for nm in ("SKU换号", "人工登记"):
        got = list(_wb[nm].iter_rows(values_only=True))
        print(f"[复核] 『{nm}』{len(got)} 行：{got[0] if got else '—'}")
    print("[复核] 全部子表:", _wb.sheetnames)
finally:
    _wb.close()

print("\n=== 4. 原子替换 ===")
assert md5(REAL) == before, "真文件在流程中被改动"
os.replace(T2, REAL)
if T1.exists():
    T1.unlink()
print("已替换 →", REAL)
print("子表:", sheets_of(REAL))
print("md5 before/after:", before[:8], "/", md5(REAL)[:8])
