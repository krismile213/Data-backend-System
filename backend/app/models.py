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

from sqlalchemy import (BIGINT, DATETIME, JSON, VARCHAR, Column, ForeignKey,
                        Index, Integer, Text)
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
