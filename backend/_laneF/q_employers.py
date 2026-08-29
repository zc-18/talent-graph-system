# -*- coding: utf-8 -*-
"""只读：导出现有雇主全集 + 5 个 T2 岗位的雇主分布。纯 SELECT，结束 rollback。"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal  # noqa
from sqlalchemy import text  # noqa

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "employers_snapshot.json")

def main():
    db = SessionLocal()
    try:
        print(f"=== DB: {os.getenv('DB_NAME','(default)')} ===")
        rows = db.execute(text("SELECT id,name,normalized_name,parent_id,status FROM employer")).fetchall()
        print("employer rows:", len(rows))
        emps = [{"id": r[0], "name": r[1], "norm": r[2], "parent_id": r[3], "status": r[4]} for r in rows]

        al = db.execute(text("SELECT employer_id,alias,normalized_alias FROM employer_alias")).fetchall()
        print("employer_alias rows:", len(al))
        aliases = [{"employer_id": a[0], "alias": a[1], "norm": a[2]} for a in al]

        # raw_jd 里出现过的所有 company 原文（另一份「已见过」的口径）
        cs = db.execute(text(
            "SELECT COALESCE(company,''), COUNT(*) FROM raw_jd GROUP BY company"
        )).fetchall()
        companies = [{"company": c[0], "n": c[1]} for c in cs]
        print("distinct raw_jd.company:", len(companies), " (含空串)")

        # 5 个 T2 岗位的雇主分布
        jobs = ["自动驾驶算法工程师", "机器人算法工程师", "智能硬件开发工程师",
                "车联网系统工程师", "多模态算法工程师"]
        per_job = {}
        for jn in jobs:
            r = db.execute(text("""
                SELECT COALESCE(rj.company,'(空)') c, COUNT(DISTINCT rj.id) n
                FROM raw_jd rj
                JOIN evidence e ON e.raw_jd_id = rj.id
                JOIN job_skill js ON js.id = e.job_skill_id
                JOIN job j ON j.id = js.job_id
                WHERE j.name = :jn
                GROUP BY rj.company ORDER BY n DESC
            """), {"jn": jn}).fetchall()
            per_job[jn] = [{"company": x[0], "n": x[1]} for x in r]
            print(f"\n--- {jn} ---  雇主原文 {len(r)} 个")
            for x in r[:30]:
                print(f"    {x[1]:>4}  {x[0]}")

        # 平台分布
        print("\n=== raw_jd.platform 分布 ===")
        for p, n in db.execute(text("SELECT platform,COUNT(*) FROM raw_jd GROUP BY platform ORDER BY 2 DESC")).fetchall():
            print(f"  {p:<24} {n}")

        json.dump({"employers": emps, "aliases": aliases, "raw_companies": companies,
                   "per_job": per_job}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n→", OUT)
    finally:
        db.rollback()
        db.close()

main()
