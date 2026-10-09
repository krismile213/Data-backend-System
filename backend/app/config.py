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

from .seg_rules_shell import PART_CONFIGS as SHELL_PART_CONFIGS

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
# 数据根目录：其下每个含 Part*.xlsx 的子目录视为一个"批次"
# 可通过环境变量 DATA_ROOT 覆盖（企业化部署时无需改代码）
#
# ⚠️ 2026-09-22 起按**品类**分根（用户决策：手机壳单开一套规则，数据也分开放）：
#    data\手机膜\  ← DATA_ROOT（环境变量，兼容既有部署）
#    data\手机壳\  ← DATA_ROOT_SHELL
#   DATA_ROOT 保持"默认品类（手机膜）的根"，因此既有引用零改动。
_DEFAULT_ROOT = Path(
    os.environ.get("DATA_ROOT", r"C:\Users\dell\Desktop\work-main\data\手机膜")
)

#: 品类 → 数据根目录。新增品类：这里加一行 + 在 category_rules 注册规则集。
DATA_ROOTS = {
    "手机膜": _DEFAULT_ROOT,
    "手机壳": Path(os.environ.get(
        "DATA_ROOT_SHELL",
        os.environ.get("DATA_ROOT_手机壳",
                       r"C:\Users\dell\Desktop\work-main\data\手机壳"))),
}

#: 默认品类（= 所有 category 缺省参数的取值）
DEFAULT_CATEGORY = "手机膜"

#: 兼容别名：历史代码里的 DATA_ROOT 一律指默认品类的根
DATA_ROOT = _DEFAULT_ROOT

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
# 品类 → Part 环节定义（用户 2026-09-22 决策：按品类分文件，互不污染）
#   PART_CONFIGS 保持为手机膜（15 处现存引用全部零改动）；
#   手机壳的定义在 seg_rules_shell.py（列名/关键动作均按真实导出实测）。
#   ⚠️ 两个品类的 9 个 Part **键名与 Part 名完全相同**（导出文件名一致），
#      差异只在 key_actions / biz_time_cols / notes —— 所以 ETL 与 parser 无需改。
# ---------------------------------------------------------------------------
PART_CONFIGS_BY_CATEGORY = {
    "手机膜": PART_CONFIGS,
    "手机壳": SHELL_PART_CONFIGS,
}


def part_configs_for(category=None):
    """取品类的 Part 环节定义；None/未知品类 → 手机膜（保持既有行为）。"""
    return PART_CONFIGS_BY_CATEGORY.get(str(category or "").strip(), PART_CONFIGS)


# ---------------------------------------------------------------------------
# 需要以文本方式处理、禁止精度丢失的列（18 位审批编号等）
# ---------------------------------------------------------------------------
TEXT_SAFE_COLS = ["审批编号", "数据id"]

# 常见时间列（读取后统一转 datetime，便于排序与筛选）
TIME_COLS = ["创建时间", "更新时间", "完成时间"]


def data_root(category=None) -> Path:
    """取品类对应的数据根目录；None/未知品类 → 默认品类（手机膜）的根。"""
    cat = (category or "").strip() or DEFAULT_CATEGORY
    return DATA_ROOTS.get(cat, _DEFAULT_ROOT)


def _iter_root(root: Path, category: str):
    """扫描一个数据根，产出批次 dict（内部用）。"""
    out = []
    pcfg = part_configs_for(category)
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        files = sorted(d.glob(FILE_GLOB))
        if not files:
            continue
        parts = []
        for f in files:
            pk = f.name.split("_")[0]
            if pk in pcfg:
                parts.append({"part": pk, "name": pcfg[pk]["name"],
                              "file": f.name})
        out.append({
            "name": d.name,
            "path": str(d),
            "fileCount": len(files),
            "parts": parts,
            "category": category,
        })
    return out


def list_batches(category=None, all_categories: bool = False):
    """扫描数据根目录，返回可用批次列表 [{name, path, fileCount, parts, category}]

    category       指定品类 → 只扫该品类的根（缺省/未知 = 默认品类手机膜）
    all_categories True      → 扫所有品类的根（ETL 全量导入用；此时忽略 category）
    """
    if all_categories:
        out = []
        for cat in DATA_ROOTS:
            out.extend(_iter_root(DATA_ROOTS[cat], cat))
        # 同一批次名跨品类重名时，后扫到的会覆盖 DB 里的 name 唯一行 —— 用
        # “品类/批次名”作为展示名兜底，避免用户在界面上分不清。
        seen = {}
        for b in out:
            seen.setdefault(b["name"], []).append(b)
        for name, group in seen.items():
            if len(group) > 1:
                for b in group:
                    b["displayName"] = f"{b['category']}/{name}"
        return out
    return _iter_root(data_root(category), (category or "").strip() or DEFAULT_CATEGORY)


def _valid_batch_name(batch_name: str) -> bool:
    """防路径穿越：必须是纯目录名（拦截 ../、分隔符、盘符绝对路径等）"""
    return bool(batch_name) and batch_name not in (".", "..") \
        and "/" not in batch_name and "\\" not in batch_name \
        and ":" not in batch_name and Path(batch_name).name == batch_name


def locate_batch(batch_name: str):
    """按批次名跨品类查找，返回 (category, Path)；找不到抛 FileNotFoundError。

    查找顺序：默认品类优先 → 其余品类按 DATA_ROOTS 声明顺序。
    """
    if not _valid_batch_name(batch_name):
        raise FileNotFoundError(batch_name)
    order = [DEFAULT_CATEGORY] + [c for c in DATA_ROOTS if c != DEFAULT_CATEGORY]
    for cat in order:
        if cat not in DATA_ROOTS:
            continue
        d = DATA_ROOTS[cat] / batch_name
        if d.is_dir() and list(d.glob(FILE_GLOB)):
            return cat, d
    raise FileNotFoundError(batch_name)


def get_batch_dir(batch_name: str, category=None) -> Path:
    """按批次名取目录并校验存在性；无效批次抛 FileNotFoundError

    category 指定时只在该品类的根下找；缺省时跨品类查找（既有调用方零改动）。
    """
    if category:
        cat = category.strip()
        if not _valid_batch_name(batch_name):
            raise FileNotFoundError(batch_name)
        d = data_root(cat) / batch_name
        if not d.is_dir() or not list(d.glob(FILE_GLOB)):
            raise FileNotFoundError(batch_name)
        return d
    return locate_batch(batch_name)[1]


# ---------------------------------------------------------------------------
# AI 助手 BFF 反向代理配置（T02）
# ---------------------------------------------------------------------------
# 平台后端把 /api/agent/* 反向代理到用户自研 Agent（server.py）的 HTTP 适配器，
# 由**服务端**注入 Authorization: Bearer <静态 token>，前端只走相对路径、永不接触 token。
# 详见 docs/AI助手嵌入实现方案.md §2.2（拓扑 B）/ §4（接口契约）/ §8.2（SSE 坑）。
#
# 约定：默认值只是「开箱即用」的兜底，企业化部署/测试一律用环境变量覆盖；
#       与 snapshot_path 同思路——读取函数每次重读 env，便于测试逐用例覆盖。
# 值的实际来源：本地 .env 的 AGENT_TOKEN / AGENT_BASE_URL / AGENT_TIMEOUT
#       （加载时机与候选顺序见 app/env_loader.py——包导入最早时机，单一真源）。
AGENT_DEFAULT_BASE_URL = "http://127.0.0.1:8765"
#: 默认 token 为空：未配置时不注入 Authorization（交给 Agent 的本机/无鉴权模式）。
AGENT_DEFAULT_TOKEN = ""
#: 转发读取超时（秒）。SSE 是长连接，必须给足；普通请求也会用到这个上限。
AGENT_DEFAULT_TIMEOUT = 300.0


def agent_base_url() -> str:
    """Agent 上游地址（去尾部斜杠）。环境变量 AGENT_BASE_URL 覆盖。"""
    return (os.environ.get("AGENT_BASE_URL") or AGENT_DEFAULT_BASE_URL).rstrip("/")


def agent_token() -> str:
    """BFF 注入的静态 Bearer token（对应 Agent config.json→auth.tokens）。空=不注入。"""
    return (os.environ.get("AGENT_TOKEN") or AGENT_DEFAULT_TOKEN).strip()


def agent_timeout_seconds() -> float:
    """转发超时（秒）；非法值回退默认，绝不让配置错误把转发变成 500。"""
    try:
        value = float(os.environ.get("AGENT_TIMEOUT", AGENT_DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return AGENT_DEFAULT_TIMEOUT
    return value if value > 0 else AGENT_DEFAULT_TIMEOUT
