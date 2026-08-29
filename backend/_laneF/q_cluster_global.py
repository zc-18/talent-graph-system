# -*- coding: utf-8 -*-
"""只读：全库 14 个能力簇的证据分布 —— 判断「缺簇」是语料问题还是抽取/词表问题。"""
from __future__ import annotations
import os, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal
from sqlalchemy import text
from app.services.taxonomy import capability_cluster, normalize_skill, CAPABILITY_CLUSTERS

def main():
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT s.id, s.name, p.name, js.status, js.source_count, js.job_id
            FROM job_skill js JOIN skill s ON s.id=js.skill_id
            LEFT JOIN skill p ON p.id=s.parent_id
        """)).fetchall()
        by = defaultdict(lambda: Counter())
        jobs_with_active = defaultdict(set)
        names = defaultdict(Counter)
        for sid, sname, pname, st, sc, jid in rows:
            cl = capability_cluster(normalize_skill(sname), pname)
            by[cl][st] += 1
            names[cl][sname] += 1
            if st == "active" and (sc or 0) >= 2:
                jobs_with_active[cl].add(jid)
        njobs = db.execute(text("SELECT COUNT(*) FROM job WHERE status='published'")).scalar()
        print(f"全库 job_skill 行 {len(rows)}，岗位 {njobs}\n")
        print(f"{'能力簇':<14}{'active':>7}{'cand':>7}{'deprec':>8}{'成簇岗位数':>10}   最常见技能名")
        for cl in CAPABILITY_CLUSTERS:
            c = by[cl]
            top = "、".join(k for k, _ in names[cl].most_common(6))
            print(f"{cl:<14}{c['active']:>7}{c['candidate']:>7}{c['deprecated']:>8}"
                  f"{len(jobs_with_active[cl]):>10}   {top[:80]}")
        # 关键 fundamentals 词是否作为 skill 存在
        print("\n=== 工程基本功类技能是否存在于 skill 表 ===")
        for kw in ["Git","代码审查","持续集成","持续交付","单元测试","CI/CD","Jenkins","监控",
                   "可观测","TCP/IP","HTTP","操作系统","并发编程","计算机网络","网络编程",
                   "Spring Boot","FastAPI","RESTful","接口设计","测试用例","自动化测试","pytest"]:
            r = db.execute(text("""SELECT s.name, COUNT(js.id), SUM(js.status='active')
                                   FROM skill s LEFT JOIN job_skill js ON js.skill_id=s.id
                                   WHERE s.name LIKE :k GROUP BY s.name"""),
                           {"k": f"%{kw}%"}).fetchall()
            if r:
                print(f"  {kw:<12} -> " + "; ".join(f"{a}(关系{b},active{int(c or 0)})" for a,b,c in r[:6]))
            else:
                print(f"  {kw:<12} -> ❌ skill 表中不存在")
    finally:
        db.rollback(); db.close()

main()
