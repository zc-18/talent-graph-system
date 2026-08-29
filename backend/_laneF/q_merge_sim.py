# -*- coding: utf-8 -*-
"""只读模拟：把同一岗位下的「技能名碎片」合并后，有多少岗位能多出能力簇。
纯内存计算 + SELECT，不写任何东西。"""
from __future__ import annotations
import os, re, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal
from sqlalchemy import text
from app.services.taxonomy import capability_cluster, normalize_skill
from app.services.job_resolution import role_skill_conflict

MIN_EMP = 2
# 碎片归并键：去掉修饰性尾缀/前缀后同形即视为同一技能（保守，只处理明显同义）
_STRIP = re.compile(r"(经验|能力|知识|基础|技术|体系|建设|管理|使用|应用|开发|部署|优化|"
                    r"框架|工具|流程|规范|集成|运维|二次开发|贡献|作品|生态)+$")
_PUNCT = re.compile(r"[\s()（）/／\-_,，、.。]+")
def famkey(name: str) -> str:
    v = _PUNCT.sub("", (name or "").casefold())
    prev = None
    while v != prev:
        prev = v; v = _STRIP.sub("", v)
    return v or (name or "").casefold()

def main():
    db = SessionLocal()
    try:
        rel = db.execute(text("""
            SELECT js.id, js.job_id, j.name, j.track, s.name, p.name, js.status
            FROM job_skill js JOIN job j ON j.id=js.job_id
            JOIN skill s ON s.id=js.skill_id LEFT JOIN skill p ON p.id=s.parent_id
            WHERE j.status='published'
        """)).fetchall()
        emp = db.execute(text("""
            SELECT e.job_skill_id, COALESCE(em.parent_id, em.id)
            FROM evidence e JOIN raw_jd rj ON rj.id=e.raw_jd_id
            JOIN employer em ON em.id=rj.employer_id
            WHERE em.status='active'
        """)).fetchall()
        emp_of = defaultdict(set)
        for rid, eid in emp:
            emp_of[rid].add(eid)

        # 现状 vs 合并后：每个岗位的成簇数
        cur = defaultdict(set); mrg = defaultdict(set)
        jobname = {}
        fam = defaultdict(lambda: defaultdict(set))   # (job, cluster, famkey) -> employers
        famnames = defaultdict(set)
        for rid, jid, jn, track, sname, pname, st in rel:
            jobname[jid] = jn
            track = track or "software"
            n = normalize_skill(sname)
            if role_skill_conflict(jn, track, n) or (pname and role_skill_conflict(jn, track, pname)):
                continue
            cl = capability_cluster(n, pname)
            es = emp_of.get(rid, set())
            if st == "active" and len(es) >= MIN_EMP:
                cur[jid].add(cl)
            if st in ("active", "candidate"):
                k = (jid, cl, famkey(n))
                fam[jid][(cl, famkey(n))] |= es
                famnames[(jid, cl, famkey(n))].add(sname)
        for jid, d in fam.items():
            for (cl, fk), es in d.items():
                if len(es) >= MIN_EMP:
                    mrg[jid].add(cl)

        print(f"{'岗位':<26}{'现簇':>5}{'合并后':>7}{'Δ':>4}   新增的簇")
        tot_d = 0
        rows = []
        for jid, jn in jobname.items():
            c, m = len(cur[jid]), len(mrg[jid] | cur[jid])
            rows.append((m - c, c, m, jn, sorted((mrg[jid] | cur[jid]) - cur[jid])))
        for d, c, m, jn, new in sorted(rows, reverse=True):
            tot_d += d
            print(f"{jn:<26}{c:>5}{m:>7}{d:>4}   {'、'.join(new)}")
        print(f"\n合计新增 (岗位,簇) 对：{tot_d}")
        ready_before = sum(1 for d,c,m,jn,_ in rows if c >= 8)
        ready_after  = sum(1 for d,c,m,jn,_ in rows if m >= 8)
        print(f"簇数≥8 的岗位：{ready_before} → {ready_after}")
        # 示例碎片族
        print("\n=== 合并会生效的碎片族示例 ===")
        shown = 0
        for (jid, cl, fk), names in famnames.items():
            if len(names) > 1 and len(fam[jid].get((cl, fk), set())) >= MIN_EMP:
                print(f"  [{jobname[jid]}/{cl}] {sorted(names)}")
                shown += 1
                if shown >= 25: break
    finally:
        db.rollback(); db.close()

main()
