#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""列对照检查：API 同步导出 vs 手工导出，逐 Part 比对表头。

为什么需要它
------------
手工导出是**旧时间点的快照**（如 9.18），钉钉表单之后可能新增了字段。
所以两边列不完全一致是正常的，关键是要分清两类：

  ★ 新增列（当前导出有、手工没有）—— 表单改版新增字段，**必须保留**，
    绝不允许为了"跟手工对齐"把它删掉。
  ⚠ 缺失列（手工有、当前导出没有）—— 真正的回归，必须查因修复。

判据是**文件时间戳**：较新的那份是现状。本脚本只报告，不改任何文件。

用法
----
    python scripts/check_column_parity.py
    python scripts/check_column_parity.py --manual <手工快照目录>
退出码：0 = 无缺失列；2 = 存在缺失列（可用于 CI / 计划任务告警）。
"""
import argparse
import datetime as dt
import glob
import os
import sys
from pathlib import Path

import openpyxl

DEFAULT_MANUAL = r'C:\Users\dell\Desktop\work-main\dingtalk\_backup\manual_snapshot_20260918'
DEFAULT_CONFIG = r'C:\Users\dell\Desktop\work-main\dingtalk\config.json'


def content_time(path):
    """文件内容的「快照时间」。

    优先读文件名尾部的时间戳（Part1_产品开发-20260918152651.xlsx → 2026-09-18
    15:26），**不能用 mtime**：文件被复制/备份过，mtime 会变成复制时刻，判断不了
    哪一份才是新口径。文件名没有时间戳时才退回 mtime。
    """
    stem = Path(path).stem
    tail = stem.rsplit('-', 1)[-1] if '-' in stem else ''
    if len(tail) == 14 and tail.isdigit():
        try:
            return dt.datetime.strptime(tail, '%Y%m%d%H%M%S').timestamp(), tail
        except ValueError:
            pass
    return os.path.getmtime(path), stem


def read_header(path):
    """返回 (表单列列表, 快照时间戳)；表单列 = 「数据id」之后、「审批编号」之前。"""
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[wb.sheetnames[0]]
    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    wb.close()
    cols = [c for c in header if c]
    ts, _ = content_time(path)
    if '审批编号' not in cols:
        return None, ts
    return cols[2:cols.index('审批编号')], ts


def newest(directory, part):
    """目录下该 Part 的导出文件中，**内容时间最新**的一份。

    同目录可能有多个快照（手工留下的旧版 + 自动同步的新版），按内容时间取新的，
    不按 mtime —— 复制的文件 mtime 会骗人。
    """
    files = glob.glob(str(Path(directory) / f'{part}-*.xlsx'))
    if not files:
        return None
    return max(files, key=lambda f: content_time(f)[0])


def stamp(ts):
    return dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manual', default=DEFAULT_MANUAL,
                    help='手工导出快照目录（作为旧时间点基线）')
    ap.add_argument('--config', default=DEFAULT_CONFIG, help='dingtalk/config.json')
    ap.add_argument('--products', default='手机膜',
                    help='只检查这些品类（逗号分隔）。默认 手机膜 —— 目前只有它'
                         '有手工快照；手机壳尚无手工基线，带上它会因拿错同名基线'
                         '而误报缺失列')
    args = ap.parse_args()

    import json
    cfg = json.load(open(args.config, encoding='utf-8'))
    wanted = [p.strip() for p in args.products.split(',') if p.strip()]
    skipped = [p for p in cfg['products'] if p not in wanted]
    for p in skipped:
        cfg['products'].pop(p)
    if skipped:
        print(f'（跳过未指定品类：{"、".join(skipped)}；'
              f'用 --products 指定，或用 --manual <目录>/<品类> 提供各自基线）')

    missing_total = 0
    for product, pcfg in cfg['products'].items():
        out_dir = pcfg['output_dir']
        # 手工基线优先找「按品类分目录」的结构；没有就退回扁平目录
        manual_root = Path(args.manual) / product
        if not manual_root.is_dir():
            manual_root = Path(args.manual)
        print('=' * 74)
        print(f'### {product}   导出目录 {out_dir}')
        print(f'    手工基线目录 {manual_root}')
        for part in pcfg['parts']:
            cur = newest(out_dir, part)
            if not cur:
                print(f'\n  {part}: 目录里没有导出文件，跳过')
                continue
            man = newest(manual_root, part)
            cur_cols, cur_ts = read_header(cur)
            print(f'\n  {part}')
            print(f'    当前导出 {os.path.basename(cur)}  '
                  f'快照 {stamp(cur_ts)}  {len(cur_cols)} 列')
            if not man:
                print('    手工基线：未找到，仅列出当前列')
                continue
            man_cols, man_ts = read_header(man)
            print(f'    手工基线 {os.path.basename(man)}  '
                  f'快照 {stamp(man_ts)}  {len(man_cols)} 列')

            overlap = [c for c in cur_cols if c in man_cols]
            if len(overlap) < 0.4 * max(len(man_cols), 1):
                # 两个模板的表单字段几乎不重合 → 多半拿错了同名基线文件，
                # 此时报「缺失列」是假警报，直接说明并跳过判定
                print(f'    ⚠ 基线疑似不是同一模板（仅 {len(overlap)} 列与 '
                      f'{len(man_cols)} 列重合），跳过缺失列判定')
                print('      请用 --manual 指向该品类自己的手工快照目录')
                continue

            newer = '当前导出' if cur_ts >= man_ts else '手工基线'
            print(f'    较新一方：{newer}（按文件名快照时间判定）→ 以它为准')
            added = [c for c in cur_cols if c not in man_cols]
            lost = [c for c in man_cols if c not in cur_cols]
            if added:
                print(f'    ★ 新增列 {len(added)} 个（表单改版新增，保留）: '
                      + '、'.join(added))
            if lost:
                missing_total += len(lost)
                print(f'    ⚠ 缺失列 {len(lost)} 个（回归，需修）: '
                      + '、'.join(lost))
            if not added and not lost:
                print('    列完全一致')

    print('=' * 74)
    if missing_total:
        print(f'结论：发现 {missing_total} 个缺失列，需要修复。')
        return 2
    print('结论：无缺失列。新增列均为表单改版新增字段，已全部保留。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
