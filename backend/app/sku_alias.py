# -*- coding: utf-8 -*-
"""
sku_alias.py — SKU 换号对照（旧号 → 新号）的**唯一读写入口**
============================================================
用途：某些新品在流程中途**换了编码**（旧号停用、新号承接，常见于机型/方案变更）。
旧号的钉钉流程会永远停在半路 → 复盘报「缺节点」、加急预警把它当卡点催办 → **多余异常**。

本模块把「谁换成了谁」这件事收敛成一处：
    真源 = `资产文件\\商品资料.xlsx` 的 **`SKU换号`** 子表
    落库 = MySQL `sku_alias`（由 `backend/etl/products_loader.py` 随商品资料一并导入）
    读取 = `load_aliases()`（DB，带指纹缓存）／`read_alias_sheet()`（直接读 xlsx，给离线脚本用）

⚠️ 为什么只认人工维护、不做自动推导（已实测，别再试）：
  ① **流程单据里没有结构化痕迹**：G493/G494/G495 自己的单据完全不提 G510/G533；
     反向只有 G510 的 Part2 一处自由文本（「按 G493 的发货计划和采购计划进行」）。
  ② **商品资料装配表的「共用<旧号>」不是换号**：全库 125 个 SKU / 96 个被引用旧号都有该标注
     （如 G388 被 11 个新号共用），绝大多数是**刀模/神器共用**。按它推导必然误伤。
  ③ `参考SKU` 列是「**上一代同系列产品**」（G593 参考 G511=Poco F8 Ultra → 新机 F9 Ultra），
     语义是"参考方案"，**不是**换号。

表结构（第 1 行表头，列名按包含匹配，容忍全角空格/大小写）：
    | 旧SKU | 新SKU | 换号说明 | 生效日期 |
"""

import re
from datetime import datetime

# --- 子表与表头契约（改这里即可换列名，别在各消费方各写一份）-------------------
ALIAS_SHEET = "SKU换号"
HDR_OLD_KEYS = ("旧SKU", "原SKU", "被替代SKU", "旧编码", "原编码")
HDR_NEW_KEYS = ("新SKU", "承接SKU", "替代SKU", "新编码", "变更后SKU")
HDR_NOTE_KEYS = ("说明", "备注", "原因")
HDR_DATE_KEYS = ("生效", "日期", "时间")

MAX_HDR_SCAN = 6          # 表头只在前 6 行里找（给「第 1 行写说明」的手工习惯留余地）
SKU_RE = re.compile(r"^[A-Za-z]{0,3}\d{1,6}[A-Za-z]{0,4}$")

#: 换号判定的**唯一**口径说明（前端规则页 / 接口 describe() 直接回显，避免各处改文案）
DEFINITION = {
    "source": "商品资料.xlsx · SKU换号 子表",
    "effect": "旧号移出加急催办（单列「已换号·不催办」），复盘不把它的缺节点当异常，"
              "新号反向标注「承接旧号」",
    "why": "换号是业务决策；流程单据与商品资料装配表都没有可靠信号（装配表「共用」是刀模共用）",
}


# ---------------------------------------------------------------------------
# 纯读：xlsx → 行（离线脚本与导入器共用同一份解析逻辑，保证同源）
# ---------------------------------------------------------------------------
def _s(v) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none") else t


def _pick(hdr_cells, keys):
    """按包含匹配找列（1 基列号）；找不到返回 None。"""
    for i, h in enumerate(hdr_cells, start=1):
        hs = _s(h).replace(" ", "").replace("\u3000", "")
        if not hs:
            continue
        if any(k.replace(" ", "") in hs for k in keys):
            return i
    return None


#: 供**同源子表**模块复用（如 sku_hold.py）：表头按包含匹配 + 单元格清洗。
#: 两张子表同属商品资料.xlsx，列名容忍度必须一致 —— 只此一份实现，别在别处再写。
pick_col = _pick
cell_str = _s


def read_alias_sheet(path) -> tuple:
    """读「SKU换号」子表 → (rows, notes)。见 `read_alias_from_wb`。"""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as e:              # noqa: BLE001
        return [], [f"⚠ 换号表读取失败：{type(e).__name__}: {e}"]
    try:
        return read_alias_from_wb(wb)
    finally:
        try:
            wb.close()
        except Exception:                # noqa: BLE001
            pass


def read_alias_from_wb(wb) -> tuple:
    """已打开的 workbook → (rows, notes)。供导入器复用，避免二次开文件。

    rows = [{"old","new","note","effectiveAt"}, …]（只做**结构**清洗，不做业务校验）
    子表不存在 → ([], [提示])，不抛异常（商品资料还是能正常导入）。
    """
    notes = []
    if ALIAS_SHEET not in wb.sheetnames:
        return [], [f"ℹ 商品资料没有『{ALIAS_SHEET}』子表 → 本次换号对照为空"
                    f"（现有子表：{wb.sheetnames}）"]
    ws = wb[ALIAS_SHEET]
    hdr_row, ci = None, {}
    rows = []
    for i, vals in enumerate(ws.iter_rows(values_only=True), start=1):
        if hdr_row is None:
            if i > MAX_HDR_SCAN:
                break
            c_old = _pick(vals, HDR_OLD_KEYS)
            c_new = _pick(vals, HDR_NEW_KEYS)
            if c_old and c_new:
                hdr_row = i
                ci = {
                    "old": c_old, "new": c_new,
                    "note": _pick(vals, HDR_NOTE_KEYS),
                    "date": _pick(vals, HDR_DATE_KEYS),
                }
            continue

        def _cell(key, _v=vals):
            idx = ci.get(key)
            return _v[idx - 1] if (idx and idx - 1 < len(_v)) else None

        old, new = _s(_cell("old")), _s(_cell("new"))
        if not old and not new:
            continue
        rows.append({
            "old": old.upper(), "new": new.upper(),
            "note": _s(_cell("note")),
            "effectiveAt": _s(_cell("date")),
        })
    if hdr_row is None:
        return [], [f"⚠ 『{ALIAS_SHEET}』前 {MAX_HDR_SCAN} 行未找到含『旧SKU』+『新SKU』的表头行"]
    return rows, notes


# ---------------------------------------------------------------------------
# 业务校验：自指 / 空值 / 重复旧号 / 成环 / 新号不存在
# ---------------------------------------------------------------------------
def validate_alias_rows(rows, known_skus=None) -> tuple:
    """(clean_rows, notes)。known_skus 给了就校验新号是否存在（缺了只告警不拦）。"""
    notes = []
    clean = []
    seen = {}
    known = {str(x).strip().upper() for x in (known_skus or []) if str(x).strip()}
    for r in rows:
        old, new = r.get("old", ""), r.get("new", "")
        if not old or not new:
            notes.append(f"⚠ 换号行缺旧号或新号，已忽略：{r}")
            continue
        if old == new:
            notes.append(f"⚠ 换号新旧号相同（{old}），已忽略")
            continue
        if not SKU_RE.match(old) or not SKU_RE.match(new):
            notes.append(f"⚠ 换号编码格式可疑（{old}→{new}），已保留但请核对")
        if old in seen:
            notes.append(f"⚠ 旧号 {old} 重复登记（{seen[old]} → {new}），以最后一行为准")
            clean = [x for x in clean if x["old"] != old]
        seen[old] = new
        if known and new not in known:
            notes.append(f"⚠ 换号新号 {new} 不在商品资料 SKU 清单里（{old}→{new}），请核对")
        clean.append(r)

    # 成环检测（A→B→A 会让"谁承接谁"没有答案）
    nxt = {x["old"]: x["new"] for x in clean}
    for start in list(nxt):
        seen_p, cur = set(), start
        while cur in nxt and cur not in seen_p:
            seen_p.add(cur)
            cur = nxt[cur]
        if cur == start and len(seen_p) > 1:
            notes.append(f"⚠ 换号成环：{' → '.join(list(seen_p) + [start])}，请修正")
    return clean, notes


# ---------------------------------------------------------------------------
# 读：DB（带指纹缓存）
# ---------------------------------------------------------------------------
_CACHE = {"fp": None, "out": {}, "inn": {}}
_EMPTY_FP = "empty"


def _db_fingerprint(session) -> str:
    from sqlalchemy import func, select

    from .models import SkuAlias
    n, latest = session.execute(
        select(func.count(SkuAlias.old_sku), func.max(SkuAlias.updated_at))).one()
    if not n:
        return _EMPTY_FP
    return f"{n}|{latest.strftime('%Y%m%d%H%M%S') if latest else '-'}"


def load_aliases(force: bool = False) -> dict:
    """{旧号: {new, note, effectiveAt, updatedAt}}。DB 不可用时返回 {}（绝不抛）。"""
    try:
        from .db import SessionLocal
        from .models import SkuAlias

        with SessionLocal() as s:
            fp = _db_fingerprint(s)
            if not force and _CACHE["fp"] == fp:
                return _CACHE["out"]
            out = {}
            for a in s.query(SkuAlias).all():
                out[str(a.old_sku).strip().upper()] = {
                    "new": str(a.new_sku or "").strip().upper(),
                    "note": a.note or "",
                    "effectiveAt": a.effective_at or "",
                    "updatedAt": a.updated_at.strftime("%Y-%m-%d %H:%M") if a.updated_at else "",
                }
            _CACHE.update(fp=fp, out=out)
            return out
    except Exception:                    # noqa: BLE001 — 复盘不该因换号表读不到而挂
        return _CACHE["out"] or {}


def alias_out(sku) -> dict:
    """该 SKU 作为**旧号**的换号信息；不是旧号则 {}。"""
    k = str(sku or "").strip().upper()
    return load_aliases().get(k) or {}


def alias_in(sku) -> dict:
    """该 SKU 作为**新号**时，被哪些旧号承接而来 → {"olds": [旧号…]}。"""
    k = str(sku or "").strip().upper()
    if not k:
        return {}
    olds = [o for o, v in load_aliases().items() if v.get("new") == k]
    return {"olds": sorted(olds)} if olds else {}


def is_alias_old(sku) -> bool:
    return bool(alias_out(sku))


def snapshot() -> dict:
    """一次性给页面/接口用的结构。"""
    al = load_aliases()
    inn = {}
    for o, v in al.items():
        inn.setdefault(v["new"], []).append(o)
    for v in inn.values():
        v.sort()
    return {
        "count": len(al),
        "out": al,
        "in": inn,
        "definition": DEFINITION,
        "sheet": ALIAS_SHEET,
        "validatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def describe() -> dict:
    """口径说明（规则页/接口回显，与 DEFINITION 同源）。"""
    return dict(DEFINITION, sheet=ALIAS_SHEET, columns=["旧SKU", "新SKU", "换号说明", "生效日期"])
