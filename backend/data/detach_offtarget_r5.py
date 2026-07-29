"""把 2026W31-r5-dt 批次里**检索词打偏**的行摘出该岗位簇（保留在语料，不建图）。

背景：数字孪生工程技术人员与人工智能数字人训练师两个岗位 JD 样本薄（23 / 14 条），
为补齐而做了一轮定向采集，把检索词从「数字孪生」「数字人」扩到了
仿真建模 / 三维建模 / BIM / CAE仿真 / 语音合成 / 动作捕捉。

结果是**扩错了**，实测：
  - 数字孪生：新采 31 条，标题命中 0 条，29 条完全不沾边——BIM 土建工程师、
    机械设计（CAE 仿真方向）、超高压碳化硅功率芯片、电力电子软件……
    这些是传统 CAE/CAD 与电力机械岗，不是数字孪生软件岗；
  - 数字人：新采 16 条，"命中"的绝大多数是腾讯语音合成算法研究员。语音合成是
    数字人的组件技术，但「语音大模型算法研究员」与人社部定义的「数字人训练师」
    （数据标注、语料整理、数字人交互训练）不是同一个岗位。

把它们算进去会让这两个岗位的能力画像变成大杂烩——这正是 docs 里已如实记录的
「岗位聚类精度」局限。宁可承认这两个岗位样本薄，也不能靠混入邻近岗位把数字做好看。

处理方式：**不删数据**（是合法采集的真实 JD，采集台账与语料计数都保留），
只把 cluster_hint 置空，让它们落进「待映射」桶、不参与这两个岗位的建图。
标题真正点名目标岗位的行保留。

用法：
  uv run python -X utf8 data/detach_offtarget_r5.py          # 只看
  uv run python -X utf8 data/detach_offtarget_r5.py --apply
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402

BATCH_PREFIX = "2026W31-r5-dt"
# 严格判据：标题必须点名目标岗位本身，不接受"组件技术"的邻近岗
ON_TARGET = {
    "数字孪生": ("数字孪生", "孪生"),
    "数字人": ("数字人", "虚拟人", "虚拟主播", "数字员工", "虚拟形象",
               "AI训练师", "人工智能训练师"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        batch_ids = [b.id for b in db.query(models.CrawlBatch).filter(
            models.CrawlBatch.batch_key.like(f"{BATCH_PREFIX}%")).all()]
        if not batch_ids:
            print(f"找不到批次 {BATCH_PREFIX}*")
            return
        rows = db.query(models.RawJD).filter(
            models.RawJD.crawl_batch_id.in_(batch_ids)).all()

        kept, detached = [], []
        for r in rows:
            kws = ON_TARGET.get(r.cluster_hint or "")
            if kws and any(k in (r.job_title or "") for k in kws):
                kept.append(r)
            elif r.cluster_hint in ON_TARGET:
                detached.append(r)

        print(f"批次 {BATCH_PREFIX}*：共 {len(rows)} 条")
        print(f"  标题点名目标岗位，保留建图：{len(kept)} 条")
        for r in kept:
            print(f"      {r.cluster_hint} | {r.job_title[:40]}")
        print(f"  检索词打偏，摘出该簇（仍留在语料）：{len(detached)} 条")
        for r in detached[:10]:
            print(f"      {r.cluster_hint} | {r.job_title[:40]}")
        if len(detached) > 10:
            print(f"      …… 另 {len(detached) - 10} 条")

        if not args.apply:
            print("\n[dry-run] 加 --apply 生效")
            return
        for r in detached:
            r.cluster_hint = None
        db.commit()
        print(f"\n已摘出 {len(detached)} 条（cluster_hint 置空，数据与采集台账均保留）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
