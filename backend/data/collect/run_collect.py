"""采集入口 CLI。

用法（backend/ 下）：
    uv run python -X utf8 data/collect/run_collect.py --platforms tencent,netease \
        --batch 2026W31 --per-query 12 [--only 大模型算法,智能体开发]

产物：data/raw/{batch}/{platform}.jsonl + crawl_log.jsonl + manifest.json
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

from data.collect.adapters.tencent import TencentCollector  # noqa: E402
from data.collect.adapters.netease import NeteaseCollector  # noqa: E402

ADAPTERS = {
    "tencent": TencentCollector,
    "netease": NeteaseCollector,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="tencent,netease")
    ap.add_argument("--batch", default=datetime.now().strftime("%YW%W"))
    ap.add_argument("--per-query", type=int, default=12)
    ap.add_argument("--only", default="", help="只采这些簇（逗号分隔，默认全部）")
    args = ap.parse_args()

    qfile = Path(__file__).parent / "queries.json"
    queries: dict[str, list[str]] = json.loads(qfile.read_text("utf-8"))["queries"]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        queries = {k: v for k, v in queries.items() if k in keep}

    out_dir = Path(__file__).resolve().parents[1] / "raw" / args.batch
    print(f"[collect] batch={args.batch} → {out_dir}")

    for name in [p.strip() for p in args.platforms.split(",") if p.strip()]:
        cls = ADAPTERS.get(name)
        if not cls:
            print(f"[collect] unknown platform {name}, skip")
            continue
        col = cls(out_dir)
        total = 0
        for cluster, kws in queries.items():
            for kw in kws:
                n = col.collect(kw, max_items=args.per_query)
                total += n
                print(f"[collect] {name} | {cluster} | '{kw}' -> {n} 条 (累计 {total})")
        col.write_manifest(notes=f"clusters={len(queries)}")
        col.close()
        print(f"[collect] {name} DONE: {col.stats}")


if __name__ == "__main__":
    main()
