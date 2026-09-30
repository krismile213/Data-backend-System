# -*- coding: utf-8 -*-
"""
sku_hold.py — 「人工登记 · 免催办」的唯一读写入口
==================================================
用途：有些新品在流程中途**业务上已决定不再走完**（渠道取消 / 并入其它型号 /
      末端环节实际不执行 …），但钉钉单据还挂在半路 → 复盘报「缺节点」、
      加急预警把它当卡点催办 → **多余异常**。
      实测：`G495` 卡在 Part8 两张「手机上架确认」（2026-03-25 起悬挂「审批中」，
      到 2026-09 已停滞 187 天）；`G524~G528` 卡在 Part9「新品环节检查」。
      这 6 个 SKU 商品资料里都是在售 —— 货在卖，流程走不完，属「业务上不需再走」。

本模块把「谁被人工豁免了」收敛成一处：
    真源 = `资产文件\\SKU登记表.xlsx` 的 **`人工登记`** 子表
    落库 = MySQL `sku_hold`（由 `backend/etl/products_loader.py` 的 `import_registry()` 导入）
    读取 = `load_holds()`（DB，带指纹缓存）／`read_hold_sheet()`（直接读 xlsx，给离线脚本用）

⚠️ 2026-09-30 迁移：与 `sku_alias.py` 同步 —— 登记表原本塞在会被整体替换的
   `商品资料.xlsx` 里，一换文件就消失（后端全量覆盖会**静默清空** `sku_hold`）。
   现迁到独立文件 `SKU登记表.xlsx`；过渡期保留回退兼容 + 废弃告警（见 sku_alias.DEPRECATED_NOTE）。

⚠️ 与另外两条「不催办」通道的区别（三者**互相独立**，不能互相覆盖）：
    · **人工登记（本模块）** = 业务登记「不再走完流程」，**没有承接号**（人工决策，最先判）；
    · **换号（`sku_alias.py`）** = 旧号停用、**新号承接** → 看新号（也是业务登记）；
    · **末端终止（作废单）** = **数据推断**：末尾 Part 单据全「终止」且全 SKU 无在跑单据。
  「人工登记」与「换号」只认人工维护 —— 流程单据里没有可推导的结构化痕迹（已实测）。

表结构（表头行在第 1 行起、最多扫 6 行；列名按**包含**匹配，容忍全角空格/大小写）：
    | SKU | 说明 | 登记日期 |
"""

from datetime import datetime
from pathlib import Path

#: 与「换号表」共用同一套表头解析工具 + 登记表路径真源（两张子表同属登记表，口径必须一致）
from .sku_alias import (DEPRECATED_NOTE, SKU_RE, cell_str, pick_col,
                        resolve_legacy_path, resolve_registry_path)

HOLD_SHEET = "人工登记"
HDR_SKU_KEYS = ("SKU", "编码")
HDR_REASON_KEYS = ("说明", "备注", "原因", "理由")
HDR_DATE_KEYS = ("登记", "日期", "时间")
MAX_HDR_SCAN = 6

#: 豁免判定的**唯一**口径说明（前端规则页 / 接口 describe() 直接回显，避免各处改文案）
DEFINITION = {
    "source": "SKU登记表.xlsx · 人工登记 子表",
    "effect": "该 SKU 移出加急催办（单列「已豁免·不催办」），复盘把它的缺节点标为人工豁免而非卡点",
    "why": "业务上已决定不再走完流程（渠道取消 / 并入其它型号 / 末端环节不执行）；"
           "流程单据里没有可推导的结构化痕迹 → 只认人工登记",
}


# ---------------------------------------------------------------------------
# 纯读：xlsx → 行（离线脚本与导入器共用同一份解析逻辑，保证同源）
# ---------------------------------------------------------------------------
def read_hold_sheet(path=None) -> tuple:
    """读『人工登记』子表 → (rows, notes)。

    path 为空 → `resolve_registry_path()`（独立登记表）。
    独立文件或其子表不存在 → **过渡兼容**回退读 `商品资料.xlsx` 的同名子表，
    并在 notes 首条附 `DEPRECATED_NOTE` 废弃告警。绝不抛异常。
    """
    src = Path(path) if path else resolve_registry_path()
    rows, notes = _read_hold_file(src)
    if rows is not None:
        return rows, notes
    # 过渡兼容**只对「默认解析出的路径」生效**：调用方显式传了 path 就只认那个文件。
    if path is None:
        legacy = resolve_legacy_path()
        if legacy != src:
            lrows, lnotes = _read_hold_file(legacy)
            if lrows is not None:
                return lrows, [DEPRECATED_NOTE] + lnotes
    return [], notes


def _read_hold_file(src: Path) -> tuple:
    """打开 src 读 HOLD_SHEET → (rows|None, notes)。

    rows is None 表示「文件不存在 / 没有该子表 / 打开失败」→ 调用方决定是否回退。
    """
    from openpyxl import load_workbook

    if not src.is_file():
        return None, [f"⚠ 人工登记文件不存在：{src}"]
    try:
        wb = load_workbook(src, read_only=True, data_only=True)
    except Exception as e:              # noqa: BLE001
        return None, [f"⚠ 人工登记表读取失败：{type(e).__name__}: {e}"]
    try:
        if HOLD_SHEET not in wb.sheetnames:
            return None, [f"⚠ 『{HOLD_SHEET}』子表不存在于 {src}"
                          f"（现有子表：{wb.sheetnames}）"]
        return read_hold_from_wb(wb)
    finally:
        try:
            wb.close()
        except Exception:                # noqa: BLE001
            pass


def _scan_hold(wb) -> tuple:
    """核心扫描：已打开的 workbook → (rows, notes, state)，state ∈ {"ok","no_sheet","no_header"}。

    供导入器做「绝不清库」护栏判据；与 `read_hold_from_wb` 同一套逻辑。
    """
    notes = []
    if HOLD_SHEET not in wb.sheetnames:
        return [], [f"ℹ 文件没有『{HOLD_SHEET}』子表 → 本次人工登记为空"
                    f"（现有子表：{wb.sheetnames}）"], "no_sheet"
    ws = wb[HOLD_SHEET]
    hdr_row, ci = None, {}
    rows = []
    for i, vals in enumerate(ws.iter_rows(values_only=True), start=1):
        if hdr_row is None:
            if i > MAX_HDR_SCAN:
                break
            c_sku = pick_col(vals, HDR_SKU_KEYS)
            if not c_sku:
                continue
            # ⚠️ 换号表靠「旧SKU+新SKU **两列同时命中**」立判表头；本表只看一列，
            #    所以必须额外挡掉「标题行 / 说明行」——手工表常写成
            #    「业务上不需走完流程的 SKU 登记表」，含『SKU』『登记』字样，
            #    `_pick` 按**包含**匹配会把它误当表头（实测踩过，会吞掉全部数据）。
            #    真表头判据（两条都要）：① SKU 那格是**短标签**（≤8 字，如「SKU」「产品SKU」）；
            #                            ② 该行至少有 **2 个非空格**（标题行只有 1 格有字）。
            label = cell_str(vals[c_sku - 1]).replace(" ", "").replace("\u3000", "")
            if len(label) > 8 or sum(1 for v in vals if cell_str(v)) < 2:
                continue
            hdr_row = i
            ci = {"sku": c_sku,
                  "reason": pick_col(vals, HDR_REASON_KEYS),
                  "date": pick_col(vals, HDR_DATE_KEYS)}
            continue

        def _cell(key, _v=vals):
            idx = ci.get(key)
            return _v[idx - 1] if (idx and idx - 1 < len(_v)) else None

        sku = cell_str(_cell("sku")).upper()
        if not sku:
            continue
        rows.append({
            "sku": sku,
            "reason": cell_str(_cell("reason")),
            "registeredAt": cell_str(_cell("date")),
        })
    if hdr_row is None:
        return [], [f"⚠ 『{HOLD_SHEET}』前 {MAX_HDR_SCAN} 行未找到含『SKU』的表头行"], "no_header"
    return rows, notes, "ok"


def read_hold_from_wb(wb) -> tuple:
    """已打开的 workbook → (rows, notes)。供导入器复用，避免二次开文件。

    rows = [{"sku","reason","registeredAt"}, …]（只做**结构**清洗，不做业务校验）
    子表不存在 → ([], [提示])，不抛异常（登记表读写不该中断主流程）。
    ⚠️ 签名与返回值形状**保持不变**（现有测试依赖）；需要「定位状态」用 `scan_hold_wb`。
    """
    rows, notes, _state = _scan_hold(wb)
    return rows, notes


def scan_hold_wb(wb) -> tuple:
    """供导入器用：已打开的 workbook → (rows, notes, state)，state ∈ {"ok","no_sheet","no_header"}。"""
    return _scan_hold(wb)


# ---------------------------------------------------------------------------
# 业务校验：空值 / 格式可疑 / 重复 SKU / SKU 不在商品资料
# ---------------------------------------------------------------------------
def validate_hold_rows(rows, known_skus=None) -> tuple:
    """(clean_rows, notes)。known_skus 给了就校验 SKU 是否存在（缺了只告警不拦）。"""
    notes = []
    clean = []
    seen = set()
    known = {str(x).strip().upper() for x in (known_skus or []) if str(x).strip()}
    for r in rows:
        sku = r.get("sku", "")
        if not sku:
            notes.append(f"⚠ 人工登记行缺 SKU，已忽略：{r}")
            continue
        if not SKU_RE.match(sku):
            notes.append(f"⚠ 人工登记 SKU 格式可疑（{sku}），已保留但请核对")
        if sku in seen:
            notes.append(f"⚠ SKU {sku} 重复登记，以最后一行为准")
            clean = [x for x in clean if x["sku"] != sku]
        seen.add(sku)
        if known and sku not in known:
            notes.append(f"⚠ 人工登记 SKU {sku} 不在商品资料 SKU 清单里，请核对")
        clean.append(r)
    return clean, notes


# ---------------------------------------------------------------------------
# 读：DB（带指纹缓存）
# ---------------------------------------------------------------------------
_CACHE = {"fp": None, "out": {}}
_EMPTY_FP = "empty"


def _db_fingerprint(session) -> str:
    from sqlalchemy import func, select

    from .models import SkuHold
    n, latest = session.execute(
        select(func.count(SkuHold.sku), func.max(SkuHold.updated_at))).one()
    if not n:
        return _EMPTY_FP
    return f"{n}|{latest.strftime('%Y%m%d%H%M%S') if latest else '-'}"


def load_holds(force: bool = False) -> dict:
    """{SKU: {reason, registeredAt, updatedAt}}。DB 不可用时返回 {}（绝不抛）。"""
    try:
        from .db import SessionLocal
        from .models import SkuHold

        with SessionLocal() as s:
            fp = _db_fingerprint(s)
            if not force and _CACHE["fp"] == fp:
                return _CACHE["out"]
            out = {}
            for h in s.query(SkuHold).all():
                out[str(h.sku).strip().upper()] = {
                    "reason": h.reason or "",
                    "registeredAt": h.registered_at or "",
                    "updatedAt": h.updated_at.strftime("%Y-%m-%d %H:%M") if h.updated_at else "",
                }
            _CACHE.update(fp=fp, out=out)
            return out
    except Exception:                    # noqa: BLE001 — 复盘不该因登记表读不到而挂
        return _CACHE["out"] or {}


def hold_of(sku) -> dict:
    """该 SKU 的人工登记信息；没登记则 {}。"""
    k = str(sku or "").strip().upper()
    return load_holds().get(k) or {} if k else {}


def is_hold(sku) -> bool:
    return bool(hold_of(sku))


def snapshot() -> dict:
    """一次性给页面/接口用的结构。"""
    hs = load_holds()
    return {
        "count": len(hs),
        "rows": hs,
        "definition": DEFINITION,
        "sheet": HOLD_SHEET,
        "validatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def describe() -> dict:
    """口径说明（规则页/接口回显，与 DEFINITION 同源）。"""
    return dict(DEFINITION, sheet=HOLD_SHEET, columns=["SKU", "说明", "登记日期"])
