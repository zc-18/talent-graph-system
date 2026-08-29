# -*- coding: utf-8 -*-
"""只读：逐岗位 dump active 能力项 + 归簇结果 + 被拒原因。纯 SELECT + 纯函数。"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal  # noqa
from sqlalchemy import text  # noqa
from app.services.taxonomy import capability_cluster, normalize_skill, skill_category  # noqa
from app.services.job_resolution import role_skill_conflict, resolve_job_query  # noqa

JOBS = sys.argv[1:] or ["车联网系统工程师", "智能硬件开发工程师"]

def main():
    db = SessionLocal()
    try:
        for jn in JOBS:
            row = db.execute(text(
                "SELECT id,name,category,track,confidence FROM job WHERE name=:n"), {"n": jn}).fetchone()
            if not row:
                print(f"!! job not found: {jn}"); continue
            jid, name, cat, track, conf = row
            track = track or resolve_job_query(name).track
            print(f"\n{'='*90}\n### {name}  (id={jid}, category={cat}, track={track!r}, conf={conf})")
            caps = db.execute(text("""
                SELECT js.id, s.name, s.parent_id, p.name AS parent_name, js.status,
                       js.source_count, js.weight, js.importance,
                       js.confidence
                FROM job_skill js
                JOIN skill s ON s.id = js.skill_id
                LEFT JOIN skill p ON p.id = s.parent_id
                WHERE js.job_id = :j AND js.status='active'
                ORDER BY js.confidence DESC
            """), {"j": jid}).fetchall()
            print(f"active 能力项 {len(caps)} 条")
            buckets = {}
            rej = {"employer_gate": [], "track_conflict": []}
            for c in caps:
                (_id, sname, pid, pname, st, sc, w, imp, cf) = c
                ec = sc
                
                n = normalize_skill(sname)
                cl = capability_cluster(n, pname)
                conflict = role_skill_conflict(name, track, n) or (pname and role_skill_conflict(name, track, pname))
                if conflict:
                    rej["track_conflict"].append((sname, pname, ec)); continue
                if (ec or 0) < 2:
                    rej["employer_gate"].append((sname, pname, ec, cl)); continue
                buckets.setdefault(cl, []).append((sname, pname, ec, imp, round(cf or 0,3)))
            print(f"\n-- 通过门槛并成簇：{len(buckets)} 个簇")
            for cl, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
                print(f"  [{cl}]  {len(items)} 项")
                for it in items:
                    print(f"       雇主{it[2]:>2}  {it[3]:<8} conf={it[4]}  {it[0]}   ← parent={it[1]}")
            print(f"\n-- 被 employer_gate(<2) 拒：{len(rej['employer_gate'])} 项（含它们本该落入的簇）")
            from collections import Counter
            cc = Counter(x[3] for x in rej["employer_gate"])
            for cl, n_ in cc.most_common():
                print(f"     [{cl}] {n_} 项: " + "、".join(x[0] for x in rej["employer_gate"] if x[3]==cl)[:200])
            print(f"\n-- 被 track_conflict 拒：{len(rej['track_conflict'])} 项")
            for x in rej["track_conflict"]:
                print(f"     {x[0]}  ← parent={x[1]}  雇主{x[2]}")
            # 该岗位全部（含 candidate）能力项的簇分布，看「能力面」原始宽度
            allc = db.execute(text("""
                SELECT s.name, p.name, js.status, js.source_count
                FROM job_skill js JOIN skill s ON s.id=js.skill_id
                LEFT JOIN skill p ON p.id=s.parent_id
                WHERE js.job_id=:j AND js.status IN ('active','candidate')
            """), {"j": jid}).fetchall()
            cc2 = Counter()
            for sname, pname, st, sc in allc:
                cc2[capability_cluster(normalize_skill(sname), pname)] += 1
            print(f"\n-- active+candidate 全量 {len(allc)} 项的簇分布（能力面原始宽度）:")
            for cl, n_ in cc2.most_common():
                print(f"     {cl}: {n_}")
    finally:
        db.rollback(); db.close()

main()
