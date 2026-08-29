# -*- coding: utf-8 -*-
"""只读汇总：两批次的可用条数 / 新雇主并集 / 雇主身份分裂风险 / 超配额雇主。"""
from __future__ import annotations
import json, os, re, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal
from sqlalchemy import text
from app.services.employer_resolution import normalize_employer_name
from data.import_raw import _query_cluster_map, is_it_domain
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from q_ontarget import TITLE_OK, NON_ENG  # 复用同一套白名单

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

GROUPS = ("中国联合网络通信", "中国移动通信", "中国电信", "中国石油", "中国石化",
          "中国建筑", "中国铁建", "国家电网", "中国航天科工", "中国航天科技",
          "中国电子科技", "中国兵器", "中国中车", "中远海运", "中国能源建设")
def group_key(nm: str) -> str:
    for g in GROUPS:
        if nm.startswith(g.casefold()) or nm.startswith(g):
            return g
    return nm

def main():
    qmap = _query_cluster_map()
    tmap = json.loads((RAW.parent / "collect" / "title_map.json").read_text("utf-8"))["cluster_job_name"]
    db = SessionLocal()
    known_norm = {n: nm for n, nm in db.execute(text("SELECT normalized_name,name FROM employer")).fetchall() if n}
    raw_comp = {c for (c,) in db.execute(text("SELECT DISTINCT company FROM raw_jd")).fetchall() if c}
    for c in raw_comp:
        known_norm.setdefault(normalize_employer_name(c), c)
    known_norm.pop("", None)
    urls = {u for (u,) in db.execute(text("SELECT source_url FROM raw_jd")).fetchall() if u}
    # 每个岗位现有的雇主数（active 证据口径）
    cur_emp = {}
    for jn, n in db.execute(text("""
        SELECT j.name, COUNT(DISTINCT COALESCE(em.parent_id, em.id))
        FROM job j JOIN job_skill js ON js.job_id=j.id AND js.status='active'
        JOIN evidence e ON e.job_skill_id=js.id JOIN raw_jd rj ON rj.id=e.raw_jd_id
        JOIN employer em ON em.id=rj.employer_id WHERE em.status='active' GROUP BY j.name""")).fetchall():
        cur_emp[jn] = n
    db.rollback(); db.close()

    allnew, per_job_new, over = {}, defaultdict(set), defaultdict(Counter)
    stat = defaultdict(lambda: {"n":0,"keep":0,"emp":set(),"kemp":set()})
    for b in ("2026R6T2", "2026R6T3"):
        d = RAW / b
        if not d.exists(): continue
        for fp in sorted(d.glob("*.jsonl")):
            if fp.name == "crawl_log.jsonl": continue
            for line in fp.open(encoding="utf-8"):
                if not line.strip(): continue
                r = json.loads(line)
                cl = qmap.get(((r.get("extra") or {}).get("query")) or "")
                if not cl or cl not in TITLE_OK: continue
                job = tmap.get(cl, cl)
                s = stat[(b, job)]; s["n"] += 1
                nm = normalize_employer_name(r.get("company") or "")
                if nm: s["emp"].add(nm)
                t = r.get("job_title") or ""
                if (r.get("url") or "") in urls: continue
                if not re.search(TITLE_OK[cl], t, re.I): continue
                if cl != "AI产品" and NON_ENG.search(t): continue
                if not is_it_domain(t, r.get("raw_text") or ""): continue
                s["keep"] += 1
                if nm:
                    s["kemp"].add(nm); over[job][nm] += 1
                    if nm not in known_norm:
                        allnew[nm] = r.get("company")
                        per_job_new[job].add(nm)

    print(f"{'批次':<10}{'目标岗位':<24}{'采到':>6}{'可用':>6}{'可用雇主':>9}{'新雇主':>7}{'现有雇主→预计':>14}")
    for (b, job), s in sorted(stat.items()):
        cur = cur_emp.get(job, 0)
        print(f"{b:<10}{job:<24}{s['n']:>6}{s['keep']:>6}{len(s['kemp']):>9}"
              f"{len(per_job_new[job]):>7}   {cur} → ~{cur + len(per_job_new[job])}")
    print(f"\n两批合计新雇主（去重并集）：{len(allnew)} 家")

    print("\n=== ⚠ 雇主身份分裂风险（新名与库内已有名疑似同一实体，入库前需加 EmployerAlias）===")
    import difflib
    for nm, disp in sorted(allnew.items()):
        cands = difflib.get_close_matches(nm, list(known_norm), n=2, cutoff=0.62)
        if cands:
            print(f"   新: {disp!r:<44} 归一={nm!r}")
            for c in cands: print(f"       ↔ 库内已有: {known_norm[c]!r}  归一={c!r}")

    print("\n=== ⚠ 超过「同雇主同岗位簇 ≤5 条」纪律的雇主（按可用条数）===")
    any_over = False
    for job, cnt in over.items():
        for nm, v in cnt.most_common():
            if v > 5:
                any_over = True
                print(f"   [{job}] {nm}: {v} 条  → 建议截到 5 条")
    if not any_over: print("   （无）")

    print("\n=== 全部新雇主清单 ===")
    for job in sorted(per_job_new):
        print(f"  [{job}] {len(per_job_new[job])} 家: " + "、".join(allnew[n] or n for n in sorted(per_job_new[job])))
main()
