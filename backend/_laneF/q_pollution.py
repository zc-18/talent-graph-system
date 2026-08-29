# -*- coding: utf-8 -*-
"""只读：全库扫描 cluster_hint 导致的岗位错聚。纯 SELECT。"""
from __future__ import annotations
import os, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal
from sqlalchemy import text

# 明显不是「技术研发岗」的标题特征（人工可判定的强信号）
NON_ENG = re.compile(r"产品经理|设计师|采购|销售|运营|人力|财务|法务|市场|品牌|行政|"
                     r"客服|培训|讲师|BD|商务|投资|战略|公关|供应链|品质经理|项目经理|"
                     r"实习生|管培生|翻译|文案|编辑|主播|摄影")

def main():
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT j.name, rj.job_title, rj.company, rj.platform, rj.cluster_hint, rj.id
            FROM raw_jd rj
            JOIN evidence e ON e.raw_jd_id = rj.id
            JOIN job_skill js ON js.id = e.job_skill_id
            JOIN job j ON j.id = js.job_id
            GROUP BY j.name, rj.id
        """)).fetchall()
        per_job = defaultdict(list)
        for jn, title, comp, plat, hint, rid in rows:
            per_job[jn].append((title or "", comp or "", plat or "", hint))
        print(f"{'岗位':<26} {'JD':>4} {'非研发标题':>7} {'占比':>6}   示例")
        tot_bad = tot = 0
        bad_rank = []
        for jn, items in sorted(per_job.items()):
            bad = [x for x in items if NON_ENG.search(x[0])]
            tot += len(items); tot_bad += len(bad)
            pct = 100.0*len(bad)/max(1,len(items))
            bad_rank.append((pct, len(bad), len(items), jn, bad))
        for pct, nb, n, jn, bad in sorted(bad_rank, reverse=True):
            ex = "; ".join(f"{b[1]}|{b[0]}" for b in bad[:3])
            print(f"{jn:<26} {n:>4} {nb:>7} {pct:>5.1f}%   {ex[:100]}")
        print(f"\n合计 JD-岗位对 {tot}，其中标题明显非研发岗 {tot_bad} ({100.0*tot_bad/tot:.1f}%)")

        # 按平台看
        print("\n=== 非研发标题按平台 ===")
        c = Counter()
        for jn, items in per_job.items():
            for x in items:
                if NON_ENG.search(x[0]): c[x[2]] += 1
        for k, v in c.most_common(): print(f"  {k}: {v}")
    finally:
        db.rollback(); db.close()

main()
