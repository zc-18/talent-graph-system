# -*- coding: utf-8 -*-
"""只读：批次相关性审计 —— 有多少条是靠 cluster_hint 强行挂上去的。
对每条记录算 title_key(title, hint) 与 title_key(title, None) 比较。"""
from __future__ import annotations
import json, os, re, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.services.ingest import title_key, canonical_job_names
from app.services.employer_resolution import normalize_employer_name
from data.import_raw import _query_cluster_map, is_it_domain

NON_ENG = re.compile(r"产品经理|设计师|采购|销售|运营|人力|财务|法务|市场|品牌|行政|客服|"
                     r"培训|讲师|BD|商务|投资|战略|公关|供应链|翻译|文案|编辑|主播|摄影|"
                     r"会计|出纳|审计|护士|医师|教师|司机|保安|厨师|前台|秘书|工人|操作工|"
                     r"装配|叉车|钳工|焊工|电工|质检员|库管|仓管")

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
CJ = canonical_job_names()

def main():
    qmap = _query_cluster_map()
    tmap = json.loads((Path(__file__).resolve().parents[1] / "data" / "collect" /
                       "title_map.json").read_text("utf-8"))["cluster_job_name"]
    for b in (sys.argv[1:] or ["2026R6T2"]):
        d = RAW / b
        if not d.exists(): print(f"!! {b} 不存在"); continue
        per_job = defaultdict(lambda: {"n":0,"hint_only":0,"non_eng":0,"off_it":0,
                                       "emp":set(),"samples":[],"ok_emp":set()})
        for fp in sorted(d.glob("*.jsonl")):
            if fp.name == "crawl_log.jsonl": continue
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip(): continue
                    r = json.loads(line)
                    q = ((r.get("extra") or {}).get("query")) or ""
                    hint = qmap.get(q)
                    job = tmap.get(hint or "", "(无簇)")
                    t = r.get("job_title") or ""
                    txt = r.get("raw_text") or ""
                    s = per_job[job]; s["n"] += 1
                    nm = normalize_employer_name(r.get("company") or "")
                    if nm: s["emp"].add(nm)
                    bad = False
                    if NON_ENG.search(t): s["non_eng"] += 1; bad = True
                    if not is_it_domain(t, txt): s["off_it"] += 1; bad = True
                    solo = title_key(t, None)
                    if solo != job:                     # 标题自身指向别处 / 无法判定
                        s["hint_only"] += 1
                        if len(s["samples"]) < 6 and (bad or solo not in CJ):
                            s["samples"].append(f"{r.get('company','')}|{t}")
                    if not bad and solo == job and nm:
                        s["ok_emp"].add(nm)
        print(f"\n================ 批次 {b} 相关性审计 ================")
        print(f"{'目标岗位':<24}{'条数':>5}{'雇主':>5}{'仅靠hint':>8}{'非研发':>7}{'非IT':>6}{'强相关雇主':>10}")
        for job, s in sorted(per_job.items(), key=lambda x: -x[1]["n"]):
            print(f"{job:<24}{s['n']:>5}{len(s['emp']):>5}{s['hint_only']:>8}"
                  f"{s['non_eng']:>7}{s['off_it']:>6}{len(s['ok_emp']):>10}")
        for job, s in sorted(per_job.items(), key=lambda x: -x[1]["n"]):
            if s["samples"]:
                print(f"\n  [{job}] 明显跑偏样例：")
                for x in s["samples"]: print("     ", x)

main()
