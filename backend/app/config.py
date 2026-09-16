# -*- coding: utf-8 -*-
"""
config.py — 数据后台系统 后端配置中心
==========================================
移植自「手机膜时效分析/src/config.py」的业务口径：
  - 9 个 Part 环节定义、关键动作链
  - 异常判定阈值
  - 数据目录扫描

数据目录结构：
  C:\\Users\\dell\\Desktop\\work-main\\data\\手机膜\\
      ├─ 手机膜\\          （批次）
      ├─ 手机膜8.28\\      （批次，内含 Part1~Part9 的钉钉审批导出 xlsx）
      ├─ 手机膜8.31\\
      └─ 手机膜9.7\\
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
# 数据根目录：其下每个含 Part*.xlsx 的子目录视为一个"批次"
# 可通过环境变量 DATA_ROOT 覆盖（企业化部署时无需改代码）
DATA_ROOT = Path(
    os.environ.get("DATA_ROOT", r"C:\Users\dell\Desktop\work-main\data\手机膜")
)

# 批次内数据文件通配符（按文件名顺序，天然对应 Part1~Part9）
FILE_GLOB = "Part*.xlsx"

# 系统状态目录（本地写入型数据：手工填报、KPI 快照等，独立于只读的审批数据）
STATE_DIR = Path(__file__).resolve().parent.parent / "state"

# 通用列（不属于业务环节的时间/信息列）
COMMON_COLS = {
    "序号", "数据id", "产品图片", "审批编号", "创建时间", "创建人", "当前负责人",
    "审批结果", "审批状态", "更新时间", "完成时间", "创建人部门", "审批单标题",
    "历史审批人", "耗时(时:分:秒)", "审批记录",
}

# ---------------------------------------------------------------------------
# 异常判定阈值（单位：小时）—— 与现有时效分析程序口径一致
# ---------------------------------------------------------------------------
THRESHOLD = {
    # 停滞判定最低阈值：单据"审批中"且距基准时间超过
    #   max(stuck_hours, 该Part已结束单据全流程耗时P90) 才算停滞
    "stuck_hours": 24 * 3,          # 3 天
    # 分段耗时离群判定最低阈值：超过 max(segment_long_hours, Q3+1.5*IQR) 判离群
    "segment_long_hours": 24 * 7,   # 7 天
    # 分段耗时异常小（疑似补录）：耗时 < segment_min_hours 且环节中位数
    # ≥ segment_median_min_hours 时才判定（排除动作本就同刻的环节）
    "segment_min_hours": 0.1,
    "segment_median_min_hours": 1.0,
}

# ---------------------------------------------------------------------------
# Part 环节定义（与现有分析程序完全一致，按业务发生顺序排列关键动作）
# ---------------------------------------------------------------------------
PART_CONFIGS = {
    "Part1": {
        "name": "产品开发",
        "biz_desc": "新品立项与开发：产品提案确认、取得手机数据、补充产品信息，完成立项并通知各部门",
        "key_actions": ["提交申请", "提案确认", "取得手机数据", "产品信息补充", "通知_手机数据已取得"],
        "biz_time_cols": ["手机数据取得日期"],
        "notes": "通知_产品立项/通知_东南亚运营为提案确认后的并发抄送，不单独计时",
    },
    "Part2": {
        "name": "新品首单下单",
        "biz_desc": "收集各站点备货需求与特殊需求数量，完成全套样品测试后下达首单采购",
        "key_actions": ["提交申请", "站点需求收集", "特殊需求数量收集", "全套样品测试完成", "采购下单"],
        "biz_time_cols": ["全套样品测试完成时间", "首单下单时间"],
        "notes": "两条'通知全体站点负责人…'为收集动作的并发通知，不单独计时",
    },
    "Part3": {
        "name": "新品本地仓到货",
        "biz_desc": "新品到达本地仓，仓管检查到货并通知跨境店负责人",
        "key_actions": ["提交申请", "本地仓到货检查"],
        "biz_time_cols": ["本地仓到货时间"],
        "notes": "通知跨境店负责人为并发抄送",
    },
    "Part4": {
        "name": "新品站点首次上架",
        "biz_desc": "新品在亚马逊/美客多等各站点完成首次上架",
        "key_actions": ["提交申请", "新品首次上架", "新品上架完成"],
        "biz_time_cols": ["全站点首次上架完成日期"],
        "notes": "",
    },
    "Part5": {
        "name": "新品渠道仓发货",
        "biz_desc": "本地仓向各渠道仓（FBA 等）发货，逐货件号记录",
        "key_actions": ["提交申请", "发货"],
        "biz_time_cols": ["发货日期"],
        "notes": "通知广告组上架广告为发货后的并发通知（部分单据未触发，视为流程省略）",
    },
    "Part6": {
        "name": "新品素材跟进",
        "biz_desc": "寄送样机给拍摄团队、接收素材、加工营销素材并通知完成",
        "key_actions": ["提交申请", "寄送拍摄团队", "素材接收", "产品营销素材加工", "新品营销素材完成通知"],
        "biz_time_cols": ["寄送拍摄团队日期", "拍摄团队素材交付日期"],
        "notes": "",
    },
    "Part7": {
        "name": "新品站点最终上架和广告上架",
        "biz_desc": "新品最终上架到各站点并完成广告设置",
        "key_actions": ["提交申请", "上架", "广告设置"],
        "biz_time_cols": ["最终上架完成时间", "广告上架完成时间"],
        "notes": "广告设置（抄送或签审批人）为并发节点",
    },
    "Part8": {
        "name": "新品渠道仓到货",
        "biz_desc": "渠道仓到货确认，随后手机上架、促销/邀评/广告检查同步启动",
        "key_actions": ["提交申请", "到货通知", "手机上架确认", "新品促销开始"],
        "biz_time_cols": ["渠道仓到货时间", "手机上架日期"],
        "notes": "新品邀评开始/检查广告是否设置完成与新品促销开始同刻，取其一计时",
    },
    "Part9": {
        "name": "新品环节检查",
        "biz_desc": "QA 检查前序各环节完成情况（提交后待人工填写检查结果）",
        "key_actions": ["提交申请"],
        "biz_time_cols": ["检查时间"],
        "notes": "该 Part 多为流程进行中状态",
    },
}

# ---------------------------------------------------------------------------
# 需要以文本方式处理、禁止精度丢失的列（18 位审批编号等）
# ---------------------------------------------------------------------------
TEXT_SAFE_COLS = ["审批编号", "数据id"]

# 常见时间列（读取后统一转 datetime，便于排序与筛选）
TIME_COLS = ["创建时间", "更新时间", "完成时间"]


def list_batches():
    """扫描数据根目录，返回可用批次列表 [{name, path, fileCount, parts}]"""
    batches = []
    if not DATA_ROOT.exists():
        return batches
    for d in sorted(DATA_ROOT.iterdir()):
        if not d.is_dir():
            continue
        files = sorted(d.glob(FILE_GLOB))
        if not files:
            continue
        parts = []
        for f in files:
            pk = f.name.split("_")[0]
            if pk in PART_CONFIGS:
                parts.append({"part": pk, "name": PART_CONFIGS[pk]["name"],
                              "file": f.name})
        batches.append({
            "name": d.name,
            "path": str(d),
            "fileCount": len(files),
            "parts": parts,
        })
    return batches


def get_batch_dir(batch_name: str) -> Path:
    """按批次名取目录并校验存在性；无效批次抛 FileNotFoundError"""
    # 防路径穿越：必须是纯目录名（拦截 ../、分隔符、盘符绝对路径等）
    if not batch_name or batch_name in (".", "..") \
            or "/" in batch_name or "\\" in batch_name \
            or ":" in batch_name or Path(batch_name).name != batch_name:
        raise FileNotFoundError(batch_name)
    d = DATA_ROOT / batch_name
    if not d.is_dir() or not list(d.glob(FILE_GLOB)):
        raise FileNotFoundError(batch_name)
    return d
