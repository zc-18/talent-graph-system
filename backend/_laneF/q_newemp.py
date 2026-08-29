# -*- coding: utf-8 -*-
"""只读：把某批次 raw/*.jsonl 的 company 与库里现有 employer 比对，统计新雇主。
用 employer_resolution.normalize_employer_name 走系统同一套归一化口径。"""
from __future__ import annotations
import json, os, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
from app.db import SessionLocal
from sqlalchemy import text
from app.services.employer_resolution import normalize_employer_name

BATCHES = sys.argv[1:] or ["2026R6T2"]
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

def main():
    db = SessionLocal()
    try:
        known = {n for (n,) in db.execute(text("SELECT normalized_name FROM employer")).fetchall() if n}
        known |= {normalize_employer_name(c) for (c,) in
                  db.execute(text("SELECT DISTINCT company FROM raw_jd")).fetchall() if c}
        known.discard("")
        existing_urls = {u for (u,) in db.execute(text("SELECT source_url FROM raw_jd")).fetchall() if u}
        print(f"库中已知雇主归一名 {len(known)} 个；已有 source_url {len(existing_urls)} 条\n")
        for b in BATCHES:
            d = RAW / b
            if not d.exists():
                print(f"!! 批次 {b} 不存在"); continue
            per_plat = {}
            new_emp = defaultdict(Counter)     # norm -> Counter(cluster)
            new_disp = {}
            all_emp = set()
            dup_url = 0
            rows_total = 0
            by_cluster_emp = defaultdict(lambda: defaultdict(Counter))  # query -> norm -> n
            for fp in sorted(d.glob("*.jsonl")):
                if fp.name == "crawl_log.jsonl": continue
                n_rows = 0
                with fp.open(encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip(): continue
                        r = json.loads(line); n_rows += 1; rows_total += 1
                        if (r.get("url") or "") in existing_urls: dup_url += 1
                        c = r.get("company") or ""
                        nm = normalize_employer_name(c)
                        if not nm: continue
                        all_emp.add(nm)
                        q = ((r.get("extra") or {}).get("query")) or "?"
                        by_cluster_emp[q][nm][fp.stem] += 1
                        if nm not in known:
                            new_emp[nm][q] += 1
                            new_disp.setdefault(nm, c)
                per_plat[fp.stem] = n_rows
            print(f"=== 批次 {b} ===")
            print(f"  条数: {per_plat}  合计 {rows_total}")
            print(f"  URL 与库内已有重复: {dup_url} 条")
            print(f"  出现的雇主(归一后): {len(all_emp)} 个，其中**此前未出现过的新雇主 {len(new_emp)} 个**")
            print(f"\n  --- 新雇主清单（{len(new_emp)}）---")
            for nm, cnt in sorted(new_emp.items(), key=lambda x: -sum(x[1].values())):
                print(f"    {sum(cnt.values()):>3} 条  {new_disp[nm]}   [{'、'.join(cnt)}]")
    finally:
        db.rollback(); db.close()

main()
