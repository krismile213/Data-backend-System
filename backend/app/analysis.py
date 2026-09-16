# -*- coding: utf-8 -*-
"""
analysis.py — 时效统计与异常识别核心（移植自现有时效分析程序 analysis.py）
====================================================================
口径说明（与现有程序一致）：
  - 解析每张审批单的「审批记录」节点，按业务关键动作链计算分段耗时；
  - 时效统计：平均 / 最大 / 最小 / 中位数 / P90 / Q3，离群上界 = Q3 + 1.5*IQR；
  - 异常识别：审批编号缺失/重复、审批记录为空、终止单据、已结束缺终节点、
    长期停滞（审批中且超过 max(3天, 该环节已结束单据全流程P90)）、
    环节耗时超长（> max(7天, Q3+1.5*IQR)）、分段耗时异常小（疑似补录）、
    流程回退/重做、节点时间乱序、业务时间列缺失。

本模块同时负责批次数据的加载与缓存（进程内字典缓存）。
"""

import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (FILE_GLOB, PART_CONFIGS, TEXT_SAFE_COLS, THRESHOLD,
                     get_batch_dir)
from .parser import (action_occurrences, get_key_action_time,
                     load_part_file, parse_approval_record)
from .seg_rules import L4 as _L4_SEG

# 渠道未发货超时判定线：采购→发货末批目标天（seg_rules.L4）
L4_TARGET_DAYS = _L4_SEG[3]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def delta_hour(t_start, t_end):
    """两个时间之间的间隔小时数；任一缺失返回 None"""
    if t_start is None or t_end is None or pd.isna(t_start) or pd.isna(t_end):
        return None
    return round((t_end - t_start).total_seconds() / 3600, 3)


def p90(series):
    """P90 分位数（小时），空序列返回 None"""
    s = series.dropna()
    if len(s) == 0:
        return None
    return round(float(s.quantile(0.9)), 3)


def jsonable(v):
    """把 pandas / datetime 值转为 JSON 可序列化值"""
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        if pd.isna(v):
            return None
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return None
    if pd.isna(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def row_to_dict(df, row):
    """DataFrame 一行 -> JSON 字典（时间转字符串、NaN 转 None）"""
    return {str(c): jsonable(row[c]) for c in df.columns}


# ---------------------------------------------------------------------------
# 单据级流程拆解
# ---------------------------------------------------------------------------


def build_flow_detail(df, part_key):
    """
    单据级流程拆解：每行一张单，含各关键动作时间、分段耗时、总耗时。
    返回 (flow_df, actions)
    """
    cfg = PART_CONFIGS[part_key]
    actions = cfg["key_actions"]
    rows = []
    for _, row in df.iterrows():
        nodes = parse_approval_record(row.get("审批记录"))
        times = {a: get_key_action_time(nodes, a) for a in actions}
        sku = row.get("新品SKU") if "新品SKU" in df.columns else row.get("产品SKU")
        rec = {
            "Part": part_key,
            "环节名称": cfg["name"],
            "审批编号": row.get("审批编号", ""),
            "新机型号": row.get("新机型号", ""),
            "SKU": sku if pd.notna(sku) else "",
            "审批状态": row.get("审批状态", ""),
            "创建时间": row.get("创建时间"),
            "完成时间": row.get("完成时间"),
        }
        for a in actions:
            rec[f"时间_{a}"] = times[a]
        for i in range(len(actions) - 1):
            a1, a2 = actions[i], actions[i + 1]
            rec[f"{a1}→{a2}(h)"] = delta_hour(times[a1], times[a2])
        rec["全流程总耗时(h)"] = delta_hour(times[actions[0]], times[actions[-1]])
        redo = [a for a in actions if action_occurrences(nodes, a) > 1]
        rec["重复动作"] = ",".join(redo)
        rows.append(rec)
    return pd.DataFrame(rows), actions


# ---------------------------------------------------------------------------
# 时效统计
# ---------------------------------------------------------------------------


def compute_statistics(flow_dict):
    """
    对每个 Part 的 flow 表统计分段时效。
    返回 (stat_df, outlier_map, med_map)：
      outlier_map[part][分段名] = 离群上界(小时)；
      med_map[part][分段名]    = 中位数(小时)（疑似补录判定参考）。
    """
    stat_rows = []
    outlier_map = {}
    med_map = {}
    for part_key, (flow, actions) in flow_dict.items():
        cfg = PART_CONFIGS[part_key]
        segs = [f"{actions[i]}→{actions[i + 1]}(h)"
                for i in range(len(actions) - 1)]
        segs.append("全流程总耗时(h)")
        outliers = {}
        medians = {}
        for seg in segs:
            s = pd.to_numeric(flow[seg], errors="coerce").dropna()
            if len(s) == 0:
                stat_rows.append({
                    "Part": part_key, "环节名称": cfg["name"], "流程环节": seg,
                    "有效单据数": 0, "平均耗时h": None, "最大耗时h": None,
                    "最小耗时h": None, "中位数耗时h": None, "P90耗时h": None,
                    "Q3耗时h": None, "离群上界h": None, "离群单据数": 0,
                })
                outliers[seg] = None
                medians[seg] = None
                continue
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            upper = q3 + 1.5 * iqr
            outliers[seg] = round(float(upper), 2)
            medians[seg] = round(float(s.median()), 2)
            long_n = int((s > max(THRESHOLD["segment_long_hours"], upper)).sum())
            stat_rows.append({
                "Part": part_key, "环节名称": cfg["name"], "流程环节": seg,
                "有效单据数": len(s),
                "平均耗时h": round(float(s.mean()), 2),
                "最大耗时h": round(float(s.max()), 2),
                "最小耗时h": round(float(s.min()), 2),
                "中位数耗时h": round(float(s.median()), 2),
                "P90耗时h": p90(s),
                "Q3耗时h": round(float(q3), 2),
                "离群上界h": round(float(upper), 2),
                "离群单据数": long_n,
            })
        outlier_map[part_key] = outliers
        med_map[part_key] = medians
    return pd.DataFrame(stat_rows), outlier_map, med_map


# ---------------------------------------------------------------------------
# 异常排查（口径与现有分析程序完全一致）
# ---------------------------------------------------------------------------


def detect_anomalies(part_key, df, actions, base_time, outlier_map, med_map,
                     p90_flow):
    """对单个 Part 做异常排查，返回异常记录 list[dict]"""
    cfg = PART_CONFIGS[part_key]
    stuck_ref = max(THRESHOLD["stuck_hours"], p90_flow or 0)
    issues = []
    seen_ids = {}

    def sku_of(row):
        sku = row.get("新品SKU") if "新品SKU" in df.columns else row.get("产品SKU")
        return sku if pd.notna(sku) else ""

    def base(row):
        return {
            "Part": part_key, "环节名称": cfg["name"],
            "审批编号": str(row.get("审批编号", "") or ""),
            "SKU": sku_of(row),
            "新机型号": row.get("新机型号", "") or "",
            "审批状态": str(row.get("审批状态", "") or ""),
        }

    for _, row in df.iterrows():
        rid = str(row.get("审批编号", "") or "")
        status = str(row.get("审批状态", "") or "")
        nodes = parse_approval_record(row.get("审批记录"))
        times = {a: get_key_action_time(nodes, a) for a in actions}
        last_action = actions[-1]

        def push(kind, note, extra=None):
            item = base(row)
            item.update({"异常类型": kind, "说明": note})
            if extra:
                item.update(extra)
            issues.append(item)

        # 1) 审批编号缺失 / 重复（登记）
        if not rid:
            push("审批编号缺失", "该单据无审批编号")
        else:
            seen_ids.setdefault(rid, 0)
            seen_ids[rid] += 1

        # 2) 审批记录为空
        if not nodes:
            push("审批记录为空", "审批记录字段无任何节点，无法分析流程")

        # 3) 终止单据（业务性提示）
        if status == "终止":
            push("终止单据", "流程被终止，未完成后续环节")

        # 4) 已结束但缺最终关键节点
        if status == "已结束" and times.get(last_action) is None:
            push("已结束但流程未到终节点",
                 f"单据已结束，但未找到最终关键动作[{last_action}]")

        # 5) 审批中且停滞
        if status == "审批中":
            create_t = row.get("创建时间")
            if isinstance(create_t, str):
                try:
                    create_t = datetime.strptime(create_t, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    create_t = None
            if create_t is not None and base_time is not None:
                wait = (base_time - create_t).total_seconds() / 3600
                if wait > stuck_ref:
                    push("长期未完成(停滞)",
                         f"审批中已 {wait:.1f} 小时，超过该环节正常完成周期参考值 "
                         f"{stuck_ref:.1f} 小时（已结束单据全流程P90），流程停滞未推进")

        # 6) 流程回退/重做
        redo = [a for a in actions if action_occurrences(nodes, a) > 1]
        if redo:
            push("流程回退/重做", "以下关键动作出现多次（退回重做）：" + ",".join(redo))

        # 7) 节点时间乱序
        seq = [(i, a, times[a]) for i, a in enumerate(actions) if times.get(a)]
        for i in range(len(seq) - 1):
            if seq[i][2] > seq[i + 1][2]:
                push("节点时间乱序",
                     f"{seq[i][1]}({seq[i][2]}) 晚于 {seq[i + 1][1]}({seq[i + 1][2]})")
                break

        # 8) 分段耗时超长（离群）
        for i in range(len(actions) - 1):
            d = delta_hour(times[actions[i]], times[actions[i + 1]])
            if d is None:
                continue
            seg = f"{actions[i]}→{actions[i + 1]}(h)"
            upper = outlier_map.get(part_key, {}).get(seg)
            limit = THRESHOLD["segment_long_hours"]
            if upper is not None:
                limit = max(limit, upper)
            if d > limit:
                push("环节耗时超长",
                     f"{actions[i]}→{actions[i + 1]} 耗时 {d:.1f} 小时，"
                     f"超过该环节离群上界 {limit:.1f} 小时")

        # 9) 分段耗时异常小（疑似补录）
        for i in range(len(actions) - 1):
            d = delta_hour(times[actions[i]], times[actions[i + 1]])
            if d is None:
                continue
            seg = f"{actions[i]}→{actions[i + 1]}(h)"
            med = med_map.get(part_key, {}).get(seg)
            if (d < THRESHOLD["segment_min_hours"]
                    and med is not None
                    and med >= THRESHOLD["segment_median_min_hours"]):
                push("分段耗时异常小(疑似补录)",
                     f"{actions[i]}→{actions[i + 1]} 耗时 {d:.2f} 小时，"
                     f"而该环节中位数 {med:.1f} 小时，疑为补录或时间戳异常")

    # 10) 审批编号重复（跨行）
    for rid, cnt in seen_ids.items():
        if cnt > 1:
            sub = df[df["审批编号"].astype(str) == rid]
            for _, row in sub.iterrows():
                item = base(row)
                item.update({"异常类型": "审批编号重复",
                             "说明": f"编号出现 {cnt} 次"})
                issues.append(item)

    # 11) 已结束单据的业务时间列缺失
    for col in PART_CONFIGS[part_key]["biz_time_cols"]:
        if col in df.columns:
            sub = df[(df["审批状态"] == "已结束") & (df[col].isna())]
            for _, row in sub.iterrows():
                item = base(row)
                item.update({"异常类型": "业务时间列缺失",
                             "说明": f"单据已结束但业务字段[{col}]为空"})
                issues.append(item)

    # 12) Part5 渠道未发货（有发往渠道仓库但无货件号/发货记录）
    #     独立异常类型：超过采购→发货末批目标(seg_rules.L4=22天)仍未建货件
    #     判为「渠道未发货(超时)」，与「采购→发货末批超时」区分
    if part_key == "Part5" and "发往渠道仓库" in df.columns:
        ship_limit_days = L4_TARGET_DAYS
        for _, row in df.iterrows():
            hub = str(row.get("发往渠道仓库", "") or "").strip()
            if not hub or hub.lower() == "nan":
                continue
            trk = str(row.get("货件号", "") or "").strip()
            if trk and trk.lower() != "nan":
                continue
            create_t = row.get("创建时间")
            if isinstance(create_t, str):
                try:
                    create_t = datetime.strptime(create_t, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    create_t = None
            note = f"渠道仓[{hub}]已立项但无货件号、无发货记录"
            kind = "渠道未发货"
            if create_t is not None and base_time is not None:
                wait_d = (base_time - create_t).days
                note += f"，建单已 {wait_d} 天"
                if wait_d > ship_limit_days:
                    kind = "渠道未发货(超时)"
                    note += f"（超过采购→发货目标 {ship_limit_days} 天仍未建货件）"
            push(kind, note)

    return issues


# ---------------------------------------------------------------------------
# 批次数据装载与缓存
# ---------------------------------------------------------------------------


def _dir_fingerprint(d: Path):
    """批次目录指纹：文件名 + 修改时间 + 大小。文件有任何变化即视为批次更新。"""
    items = []
    for f in sorted(d.glob(FILE_GLOB)):
        try:
            st = f.stat()
            items.append((f.name, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(items)


class BatchData:
    """一个批次的完整分析结果（进程内缓存）"""

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.fingerprint = _dir_fingerprint(path)
        self.base_time = None
        self.raw = {}         # part -> 原始 DataFrame
        self.flow = {}        # part -> (flow_df, actions)
        self.stat_df = None   # 时效统计 DataFrame
        self.outlier_map = {}
        self.med_map = {}
        self.p90_flow = {}    # part -> 已结束单据全流程P90
        self.anomalies = []   # list[dict]
        self._load()

    def _load(self):
        d = self.path
        files = sorted(d.glob(FILE_GLOB))
        if not files:
            raise FileNotFoundError(f"批次目录无数据文件: {d}")
        for f in files:
            part_key = f.name.split("_")[0]
            if part_key not in PART_CONFIGS:
                continue
            df = load_part_file(f, part_key)
            self.raw[part_key] = df
            flow, actions = build_flow_detail(df, part_key)
            self.flow[part_key] = (flow, actions)
            # 基准时间：全量数据里出现的最晚时间（模拟"现在"）
            for col in ("创建时间", "更新时间", "完成时间"):
                if col in df.columns:
                    t = pd.to_datetime(df[col], errors="coerce").max()
                    if pd.notna(t) and (self.base_time is None or t > self.base_time):
                        self.base_time = t
        # 时效统计
        self.stat_df, self.outlier_map, self.med_map = compute_statistics(self.flow)
        # 各 Part 已结束单据全流程耗时 P90（停滞阈值参考）
        for pk, (flow, _) in self.flow.items():
            s = pd.to_numeric(
                flow[flow["审批状态"] == "已结束"]["全流程总耗时(h)"],
                errors="coerce").dropna()
            self.p90_flow[pk] = p90(s)
        # 异常排查
        for pk, df in self.raw.items():
            flow, actions = self.flow[pk]
            self.anomalies.extend(detect_anomalies(
                pk, df, actions, self.base_time,
                self.outlier_map, self.med_map, self.p90_flow.get(pk)))

    # -- 总览 ----------------------------------------------------------------

    def overview(self):
        """总览 KPI + 各 Part 状态分布 + 状态饼图数据"""
        part_rows = []
        status_counter = {}
        total = finished = running = terminated = 0
        total_hours = []
        for pk, df in self.raw.items():
            st = df["审批状态"].astype(str).value_counts()
            done = int(st.get("已结束", 0))
            run = int(st.get("审批中", 0))
            term = int(st.get("终止", 0))
            total += len(df)
            finished += done
            running += run
            terminated += term
            for k, v in st.items():
                status_counter[k] = status_counter.get(k, 0) + int(v)
            sku_col = ("新品SKU" if "新品SKU" in df.columns
                       else ("产品SKU" if "产品SKU" in df.columns else None))
            skus = set()
            if sku_col:
                skus = {str(x).strip() for x in df[sku_col].dropna().unique()}
            skus = {s for s in skus if s and s.lower() != "nan"}
            models = set()
            if "新机型号" in df.columns:
                models = {str(x).strip() for x in df["新机型号"].dropna().unique()}
            models = {m for m in models if m and m.lower() != "nan"}
            part_rows.append({
                "part": pk, "name": PART_CONFIGS[pk]["name"],
                "total": len(df), "finished": done, "running": run,
                "terminated": term,
                "finishRate": round(done / len(df), 3) if len(df) else 0,
                "skuCount": len(skus), "skuList": sorted(skus),
                "modelCount": len(models), "modelList": sorted(models),
            })
            if pk in self.flow:
                fl = self.flow[pk][0]
                s = pd.to_numeric(
                    fl[fl["审批状态"] == "已结束"]["全流程总耗时(h)"],
                    errors="coerce").dropna()
                total_hours.extend(s.tolist())
        anomaly_types = {}
        for a in self.anomalies:
            anomaly_types[a["异常类型"]] = anomaly_types.get(a["异常类型"], 0) + 1
        anomaly_docs = {f"{a['Part']}|{a['审批编号']}|{a['异常类型']}"
                        for a in self.anomalies}
        kpi = {
            "total": total,
            "finished": finished,
            "running": running,
            "terminated": terminated,
            "finishRate": round(finished / total, 3) if total else 0,
            "avgTotalHours": round(float(np.mean(total_hours)), 2)
            if total_hours else None,
            "anomalyCount": len(self.anomalies),
            "anomalyDocCount": len(anomaly_docs),
            "anomalyTypeSummary": sorted(
                [{"type": k, "count": v}
                 for k, v in anomaly_types.items()],
                key=lambda x: -x["count"]),
        }
        return {
            "batch": self.name,
            "baseTime": self.base_time.strftime("%Y-%m-%d %H:%M:%S")
            if self.base_time is not None else None,
            "kpi": kpi,
            "partOverview": part_rows,
            "statusPie": [{"name": k, "value": v}
                          for k, v in sorted(status_counter.items(),
                                             key=lambda x: -x[1])],
        }

    # -- 时效 ----------------------------------------------------------------

    def timeliness(self):
        """各 Part 分段/全流程时效统计 + 箱线图数据 + 创建趋势"""
        part_bar = []
        box_data = []
        segment_heat = []
        for pk in sorted(self.flow.keys()):
            cfg = PART_CONFIGS[pk]
            flow, actions = self.flow[pk]
            s = pd.to_numeric(
                flow[flow["审批状态"] == "已结束"]["全流程总耗时(h)"],
                errors="coerce").dropna()
            if len(s):
                part_bar.append({
                    "part": pk, "name": cfg["name"],
                    "avg": round(float(s.mean()), 2),
                    "median": round(float(s.median()), 2),
                    "p90": p90(s),
                    "count": len(s),
                })
                box_data.append({
                    "part": pk, "name": cfg["name"],
                    "min": round(float(s.min()), 2),
                    "q1": round(float(s.quantile(0.25)), 2),
                    "median": round(float(s.median()), 2),
                    "q3": round(float(s.quantile(0.75)), 2),
                    "max": round(float(s.max()), 2),
                    "count": len(s),
                })
            else:
                part_bar.append({"part": pk, "name": cfg["name"], "avg": None,
                                 "median": None, "p90": None, "count": 0})
                box_data.append({"part": pk, "name": cfg["name"], "min": None,
                                 "q1": None, "median": None, "q3": None,
                                 "max": None, "count": 0})
            # 分段耗时（用于热力图 / 横向条形）
            for _, r in self.stat_df[self.stat_df["Part"] == pk].iterrows():
                if r["流程环节"] == "全流程总耗时(h)":
                    continue
                segment_heat.append({
                    "part": pk, "partName": cfg["name"],
                    "segment": r["流程环节"],
                    "avg": r["平均耗时h"], "median": r["中位数耗时h"],
                    "p90": r["P90耗时h"], "count": r["有效单据数"],
                })
        # 创建时间趋势（跨 Part 合计，按日）
        trend_counter = {}
        for pk, df in self.raw.items():
            if "创建时间" in df.columns:
                ds = pd.to_datetime(df["创建时间"], errors="coerce").dropna()
                for d in ds:
                    key = d.strftime("%Y-%m-%d")
                    trend_counter[key] = trend_counter.get(key, 0) + 1
        trend = [{"date": k, "count": v}
                 for k, v in sorted(trend_counter.items())]
        return {
            "batch": self.name,
            "partBar": part_bar,
            "boxData": box_data,
            "segmentHeat": segment_heat,
            "trend": trend,
        }

    # -- 异常 ----------------------------------------------------------------

    def anomaly_list(self, anomaly_type=None, part=None):
        items = self.anomalies
        if anomaly_type:
            items = [a for a in items if a["异常类型"] == anomaly_type]
        if part:
            items = [a for a in items if a["Part"] == part]
        type_counter = {}
        for a in self.anomalies:
            type_counter[a["异常类型"]] = type_counter.get(a["异常类型"], 0) + 1
        # NaN -> None，保证 JSON 可序列化
        clean_items = [{k: jsonable(v) for k, v in a.items()} for a in items]
        return {
            "batch": self.name,
            "total": len(items),
            "typeSummary": sorted([{"type": k, "count": v}
                                   for k, v in type_counter.items()],
                                  key=lambda x: -x["count"]),
            "items": clean_items,
        }

    # -- 明细 ----------------------------------------------------------------

    def part_keys(self):
        return sorted(self.raw.keys())

    def records(self, part, page=1, page_size=20, sort_field=None,
                sort_order="asc", approval_no=None, creator=None,
                status=None, keyword=None):
        """明细分页 + 筛选 + 排序（后端分页）"""
        if part not in self.raw:
            raise KeyError(part)
        df = self.raw[part].copy()
        # ---- 筛选（regex=False：搜索词按字面匹配，防止正则元字符报错/误匹配） ----
        if approval_no:
            df = df[df["审批编号"].astype(str).str.contains(
                approval_no, na=False, regex=False)]
        if creator and "创建人" in df.columns:
            df = df[df["创建人"].astype(str).str.contains(
                creator, na=False, regex=False)]
        if status:
            df = df[df["审批状态"].astype(str) == status]
        if keyword:
            mask = None
            for c in df.columns:
                if c in ("产品图片", "_part", "_partName") or c.startswith("_"):
                    continue
                m = df[c].astype(str).str.contains(
                    keyword, na=False, case=False, regex=False)
                mask = m if mask is None else (mask | m)
            if mask is not None:
                df = df[mask]
        total = len(df)
        # ---- 排序 ----
        if sort_field and sort_field in df.columns:
            ascending = str(sort_order).lower() != "desc"
            try:
                df = df.sort_values(by=sort_field, ascending=ascending,
                                    na_position="last", kind="stable")
            except TypeError:
                df = df.astype(str).sort_values(by=sort_field,
                                                ascending=ascending,
                                                na_position="last",
                                                kind="stable")
        else:
            df = df.sort_index(kind="stable")
        # ---- 分页 ----
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        start = (page - 1) * page_size
        page_df = df.iloc[start:start + page_size]
        # ---- 序列化（审批记录展开为节点明细） ----
        rows = []
        for _, row in page_df.iterrows():
            rec = row_to_dict(page_df, row)
            nodes = parse_approval_record(row.get("审批记录"))
            rec["_nodes"] = [{
                "operator": n["operator"] or "",
                "action": n["action"] or "(缺动作名)",
                "time": n["actionTime"].strftime("%Y-%m-%d %H:%M:%S")
                if n["actionTime"] else "",
                "remark": n["remark"] or "",
            } for n in nodes]
            rows.append(rec)
        columns = [str(c) for c in self.raw[part].columns
                   if not str(c).startswith("_")]
        status_options = sorted(
            self.raw[part]["审批状态"].astype(str).unique().tolist())
        return {
            "batch": self.name,
            "part": part,
            "partName": PART_CONFIGS[part]["name"],
            "total": total,
            "page": page,
            "pageSize": page_size,
            "columns": columns,
            "statusOptions": status_options,
            "rows": rows,
        }

    def records_filtered(self, part, approval_no=None, creator=None,
                         status=None, keyword=None):
        """按与 records 相同的筛选条件取全量（导出用）"""
        if part not in self.raw:
            raise KeyError(part)
        df = self.raw[part]
        if approval_no:
            df = df[df["审批编号"].astype(str).str.contains(
                approval_no, na=False, regex=False)]
        if creator and "创建人" in df.columns:
            df = df[df["创建人"].astype(str).str.contains(
                creator, na=False, regex=False)]
        if status:
            df = df[df["审批状态"].astype(str) == status]
        if keyword:
            mask = None
            for c in df.columns:
                if str(c).startswith("_") or c == "产品图片":
                    continue
                m = df[c].astype(str).str.contains(
                    keyword, na=False, case=False, regex=False)
                mask = m if mask is None else (mask | m)
            if mask is not None:
                df = df[mask]
        return df


# ---------------------------------------------------------------------------
# 缓存管理
# ---------------------------------------------------------------------------

_cache = {}
_cache_lock = threading.Lock()


def get_batch_data(batch_name: str) -> BatchData:
    """获取批次数据（带进程内缓存）。

    缓存失效策略：每次调用都比对批次目录内数据文件的指纹（文件名/修改时间/大小），
    钉钉重新导出覆盖文件后自动重算，无需重启后端。
    批次不存在抛 FileNotFoundError。
    """
    d = get_batch_dir(batch_name)          # 校验存在性（抛 FileNotFoundError）
    fp = _dir_fingerprint(d)
    with _cache_lock:
        cached = _cache.get(batch_name)
        if cached is not None and cached.fingerprint == fp:
            return cached
    bd = BatchData(batch_name, d)
    with _cache_lock:
        _cache[batch_name] = bd
    return bd
