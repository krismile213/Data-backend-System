# -*- coding: utf-8 -*-
"""
parser.py — 审批记录解析 + xlsx 读取（移植自现有 approval_parser.py / analysis.py）
====================================================================
审批记录字段格式（钉钉审批导出）：
    节点之间以 ";" 分隔，节点内以 "|" 分隔。
    标准节点：操作人|动作|时间|备注   （备注可缺省）
    异常节点（数据中真实存在）：
      1) 操作人|时间|备注          —— 缺动作名
      2) 操作人|时间              —— 缺动作名与备注
      3) 时间|备注                —— 缺操作人与动作名（如终止流程实例）
    节点内备注可能含 "|"（如 "同意|" 尾部空段），解析时丢弃空段。
"""

import re
import warnings
from datetime import datetime

import pandas as pd

from .config import PART_CONFIGS, TEXT_SAFE_COLS, TIME_COLS

# 常见时间格式（含 ISO "T" 分隔，防上游 isoformat 复发）
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                 "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
_TIME_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")


def _parse_dt(text):
    """把字符串解析为 datetime，失败返回 None"""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(t, fmt)
        except (ValueError, TypeError):
            continue
    return None


def parse_approval_record(record_text):
    """
    解析单条审批记录字符串，返回 list[dict]，每个 dict 代表一个审批节点：
        {"operator": 操作人, "action": 动作名或None, "actionTime": datetime或None,
         "remark": 备注, "raw": 原始段文本}
    """
    if record_text is None or (not isinstance(record_text, str)
                               and pd.isna(record_text)):
        return []
    s = str(record_text)
    items = [x.strip() for x in s.replace("\r\n", "\n").split(";") if x.strip()]
    node_list = []
    for item in items:
        parts = [p.strip() for p in item.split("|") if p.strip() != ""]
        if not parts:
            continue
        node = {"operator": None, "action": None, "actionTime": None,
                "remark": "", "raw": item}
        if len(parts) >= 4:                  # 操作人|动作|时间|备注
            node["operator"] = parts[0]
            node["action"] = parts[1]
            node["actionTime"] = _parse_dt(parts[2])
            node["remark"] = parts[3]
        elif len(parts) == 3:
            if _TIME_RE.match(parts[1]):     # 操作人|时间|备注
                node["operator"] = parts[0]
                node["actionTime"] = _parse_dt(parts[1])
                node["remark"] = parts[2]
            else:                            # 操作人|动作|时间
                node["operator"] = parts[0]
                node["action"] = parts[1]
                node["actionTime"] = _parse_dt(parts[2])
        elif len(parts) == 2:
            if _TIME_RE.match(parts[1]):     # 操作人|时间
                node["operator"] = parts[0]
                node["actionTime"] = _parse_dt(parts[1])
            elif _TIME_RE.match(parts[0]):   # 时间|备注
                node["actionTime"] = _parse_dt(parts[0])
                node["remark"] = parts[1]
            else:                            # 操作人|动作
                node["operator"] = parts[0]
                node["action"] = parts[1]
        else:                                # 单段
            if _TIME_RE.match(parts[0]):
                node["actionTime"] = _parse_dt(parts[0])
            else:
                node["operator"] = parts[0]
        node_list.append(node)
    return node_list


def get_key_action_time(node_list, target_action):
    """取节点列表中指定动作的首次时间（datetime），不存在返回 None"""
    for n in node_list:
        if n["action"] == target_action and n["actionTime"] is not None:
            return n["actionTime"]
    return None


def action_occurrences(node_list, target_action):
    """指定动作在节点列表中出现的次数（用于识别退回/重做）"""
    return sum(1 for n in node_list if n["action"] == target_action)


def clean_text_id(series):
    """把 ID 类列强制转字符串并清理：去掉 NaN、'.0' 尾缀、首尾空白"""
    out = series.map(lambda x: "" if pd.isna(x) else str(x).strip())
    out = out.map(lambda s: s[:-2] if s.endswith(".0") else s)
    return out


def load_part_file(path, part_key):
    """
    读取单个 Part 文件。
    关键点：审批编号/数据id 等列强制按文本读取（dtype=str），
    严禁 18 位编号被 Excel 转为科学计数法导致精度丢失。
    """
    # 钉钉导出的部分 xlsx 缺少默认样式定义，openpyxl 会刷屏警告，这里抑制
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Workbook contains no default style")
        df = pd.read_excel(path, dtype={c: str for c in TEXT_SAFE_COLS})
    for c in TEXT_SAFE_COLS:
        if c in df.columns:
            df[c] = clean_text_id(df[c])
    # 时间列统一转 datetime（解析失败为 NaT），便于排序/筛选/序列化
    for c in TIME_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    df["_part"] = part_key
    df["_partName"] = PART_CONFIGS[part_key]["name"]
    return df
