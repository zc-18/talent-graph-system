# -*- coding: utf-8 -*-
"""只读：21 个 T3 岗位的能力面 —— 现有簇 / 门槛外簇 / 完全没有的簇。"""
from __future__ import annotations
import os, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal
from sqlalchemy import text
from app.services.taxonomy import capability_cluster, normalize_skill, CAPABILITY_CLUSTERS
from app.services.job_resolution import role_skill_conflict

T3 = ["嵌入式软件工程师","AIGC算法工程师","数据分析师","大数据平台工程师","物联网开发工程师",
      "计算机视觉工程师","推荐算法工程师","数据仓库工程师","AI产品经理","运维开发工程师(SRE)",
      "自然语言处理工程师","深度学习工程师","大数据开发工程师","云计算工程师","工业互联网工程师",
      "提示词工程师","边缘计算工程师",
      # 新兴岗位（本轮不扩采，仅列出供对照）
      "人工智能数字人训练师","数字孪生工程技术人员","生成式人工智能系统测试员","具身智能工程师"]

def main():
    db = SessionLocal()
    try:
        for jn in T3:
            r = db.execute(text("SELECT id,track,confidence FROM job WHERE name=:n"), {"n": jn}).fetchone()
            if not r: print(f"!! {jn} 不存在"); continue
            jid, track, conf = r
            track = track or "software"
            caps = db.execute(text("""
                SELECT s.name, p.name, js.status, js.source_count
                FROM job_skill js JOIN skill s ON s.id=js.skill_id
                LEFT JOIN skill p ON p.id=s.parent_id
                WHERE js.job_id=:j AND js.status IN ('active','candidate')
            """), {"j": jid}).fetchall()
            passed, gated, cand, conflict = Counter(), Counter(), Counter(), Counter()
            for sname, pname, st, sc in caps:
                n = normalize_skill(sname); cl = capability_cluster(n, pname)
                if role_skill_conflict(jn, track, n) or (pname and role_skill_conflict(jn, track, pname)):
                    conflict[cl] += 1; continue
                if st != "active": cand[cl] += 1; continue
                (passed if (sc or 0) >= 2 else gated)[cl] += 1
            have = set(passed); near = set(gated) - have; only_cand = set(cand) - have - near
            missing = [c for c in CAPABILITY_CLUSTERS if c not in have | near | only_cand]
            print(f"\n### {jn}  track={track} conf={conf:.3f}  active成簇={len(have)}")
            print(f"  现有簇({len(have)}): " + "、".join(f"{c}×{passed[c]}" for c in sorted(have, key=lambda x:-passed[x])))
            if near:  print(f"  仅差雇主数({len(near)}): " + "、".join(f"{c}×{gated[c]}" for c in sorted(near, key=lambda x:-gated[x])))
            if only_cand: print(f"  只在candidate里({len(only_cand)}): " + "、".join(f"{c}×{cand[c]}" for c in sorted(only_cand, key=lambda x:-cand[x])))
            if conflict: print(f"  ⚠ track_conflict 拒: " + "、".join(f"{c}×{conflict[c]}" for c in conflict))
            print(f"  一个证据都没有的簇({len(missing)}): " + "、".join(missing))
    finally:
        db.rollback(); db.close()

main()
