# -*- coding: utf-8 -*-
"""
models.py — MySQL 表结构（SQLAlchemy ORM）
==========================================
三层结构：
  batch         批次（数据目录下的一个子目录）
  part_doc      审批单据（批次 × Part 文件中的一行），raw 保留全部原始列
  approval_node 审批节点（由「审批记录」字段解析，操作人|动作|时间|备注）

索引设计：
  part_doc(batch_id, part)      按批次+环节扫
  part_doc(approval_no)         按审批编号跨批次/跨 Part 关联
  part_doc(sku)                 SKU 维度复盘（第一批功能基座）
  approval_node(doc_id)         单据时间线展开
"""

from datetime import datetime

from sqlalchemy import (BIGINT, DATETIME, DECIMAL, JSON, VARCHAR, Column,
                        ForeignKey, Index, Integer, Text)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Batch(Base):
    __tablename__ = "batch"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(VARCHAR(128), unique=True, nullable=False)   # 批次名（目录名）
    path = Column(VARCHAR(512))                                # 数据目录
    fingerprint = Column(VARCHAR(1024))                        # 文件指纹（mtime+size），增量导入判断
    imported_at = Column(DATETIME, default=datetime.now)
    doc_count = Column(Integer, default=0)                     # 单据总数（导入时统计）
    # 品类（手机膜/手机壳…）。⚠️ 旧库该列为 NULL → 一律视作默认品类（见 review._cat_cond）
    category = Column(VARCHAR(32), index=True)


class PartDoc(Base):
    __tablename__ = "part_doc"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("batch.id"), nullable=False)
    part = Column(VARCHAR(8), nullable=False)                  # Part1~Part9
    row_idx = Column(Integer, nullable=False)                  # 源文件行号（0 起）

    approval_no = Column(VARCHAR(64))                          # 审批编号（文本，防精度丢失）
    data_id = Column(VARCHAR(64))                              # 数据id
    sku = Column(VARCHAR(128))                                 # 新品SKU/产品SKU
    model = Column(VARCHAR(128))                               # 新机型号
    creator = Column(VARCHAR(128))                             # 创建人
    creator_dept = Column(VARCHAR(128))                        # 创建人部门
    status = Column(VARCHAR(32))                               # 审批状态
    title = Column(VARCHAR(256))                               # 审批单标题

    create_time = Column(DATETIME)
    update_time = Column(DATETIME)
    finish_time = Column(DATETIME)

    raw = Column(JSON)                                         # 全部原始列（NaN→None，时间→字符串）

    __table_args__ = (
        Index("idx_doc_batch_part", "batch_id", "part"),
        Index("idx_doc_approval_no", "approval_no"),
        Index("idx_doc_sku", "sku"),
        Index("idx_doc_status", "status"),
    )


class ApprovalNode(Base):
    __tablename__ = "approval_node"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    doc_id = Column(BIGINT, ForeignKey("part_doc.id"), nullable=False)
    seq = Column(Integer, nullable=False)                      # 节点序号（0 起）
    operator = Column(VARCHAR(128))                            # 操作人
    action = Column(VARCHAR(128))                              # 动作名
    action_time = Column(DATETIME)                             # 动作时间
    remark = Column(Text)                                      # 备注

    __table_args__ = (
        Index("idx_node_doc", "doc_id"),
        Index("idx_node_action", "action"),
    )


class ProductCatalog(Base):
    """商品资料（评级档位 + 单价）——损益推算的输入。

    来源：`资产文件\\商品资料.xlsx` 的 `商品资料` 表（第 2 行表头、A1 为档位说明）。
    grade→(daily_min, daily_max) 由 `app/product_rules.parse_grade_ranges()` 解析 A1 得出；
    解析结果偏离内置表 >2% 时保留内置值并记警告（见该模块）。

    ⚠️ 资料表 A1 明确「手机壳产品不参与以上产品评级规则」，故手机壳行 grade 天然为空，
    损益对手机壳 SKU 会走「评级未命中」降级分支——这是口径正确的表现，不是缺数据。
    """
    __tablename__ = "product_catalog"

    sku = Column(VARCHAR(128), primary_key=True)      # 与 part_doc.sku 同文本口径（strip 后精确匹配）
    model = Column(VARCHAR(128))                      # 型号（宿主手机型号）
    grade = Column(VARCHAR(32))                       # 评级档位：A~E / XA~XE / X
    daily_min = Column(DECIMAL(10, 4))                # 日均销量区间下限（XC=1.38）
    daily_max = Column(DECIMAL(10, 4))                # 区间上限（XC=5.5；A/XA 为空=开放）
    price = Column(DECIMAL(12, 4))                    # 单价（当前=参考成本价，G506=12.0266）
    price_type = Column(VARCHAR(32))                  # '参考成本价' | '售价'
    category = Column(VARCHAR(32))                    # 类型：钢化膜/水凝膜/手机壳…
    state = Column(VARCHAR(32))                       # 状态：在售/停售/不销售
    updated_at = Column(DATETIME, default=datetime.now)


class SkuAlias(Base):
    """SKU 换号对照（旧号 → 新号）—— 复盘/预警「不催办」判据的输入。

    来源：`资产文件\\SKU登记表.xlsx` 的 **`SKU换号`** 子表（表头 `旧SKU|新SKU|换号说明|生效日期`）。
    由 `backend/etl/products_loader.py` 的 `import_registry()` 导入（全量覆盖，带「异常绝不清库」护栏）。
    历史上曾内嵌于 `商品资料.xlsx`；2026-09-30 迁出（商品资料会被机器整体替换，会连带清空本表）。

    ⚠️ 为什么必须人工维护、不能自动推导：
      ① 流程单据里**没有**任何结构化字段承载换号（实测 G493/G494 自己的单据完全不提 G510/G533，
         反向只有 G510 一处自由文本「按 G493 的发货计划和采购计划进行」）；
      ② 商品资料装配表里的「共用<旧号>」是**刀模/神器共用**，全库 125 个 SKU 都有 → 自动推导必然误伤。
      换号是业务决策，只认这张表。
    """
    __tablename__ = "sku_alias"

    old_sku = Column(VARCHAR(128), primary_key=True)   # 旧号（被替代）
    new_sku = Column(VARCHAR(128), nullable=False)     # 新号（承接）
    note = Column(Text)                                # 换号说明（业务原话）
    effective_at = Column(VARCHAR(32))                 # 生效日期（文本，业务可空）
    updated_at = Column(DATETIME, default=datetime.now)


class SkuHold(Base):
    """SKU 人工登记（免催办）—— 复盘/预警「不催办」判据的输入。

    来源：`资产文件\\SKU登记表.xlsx` 的 **`人工登记`** 子表（表头 `SKU|说明|登记日期`）。
    由 `backend/etl/products_loader.py` 的 `import_registry()` 导入（全量覆盖，带「异常绝不清库」护栏）。
    历史上曾内嵌于 `商品资料.xlsx`；2026-09-30 迁出（商品资料会被机器整体替换，会连带清空本表）。

    ⚠️ 与 SkuAlias（换号）的区别：换号是「旧号停下、**新号承接**」；
      本表是「业务上**不再走完**流程」，没有承接号 —— 两者都是业务登记，判据互相独立，
      也都与「末端终止（作废单，数据推断）」独立。
    """
    __tablename__ = "sku_hold"

    sku = Column(VARCHAR(128), primary_key=True)       # 被豁免的 SKU
    reason = Column(Text)                              # 豁免说明（业务原话）
    registered_at = Column(VARCHAR(32))                # 登记日期（文本，业务可空）
    updated_at = Column(DATETIME, default=datetime.now)
