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
from data.collect.adapters.feishu_ats import FeishuATSCollector  # noqa: E402
from data.collect.adapters.iguopin import IguopinCollector  # noqa: E402

ADAPTERS = {
    "tencent": TencentCollector,
    "netease": NeteaseCollector,
    # 飞书招聘 SaaS 官网（一个适配器覆盖多家企业官方招聘官网），--per-query 视为「每公司上限」
    "feishu_ats": FeishuATSCollector,
    # 国聘网（国家级公共招聘平台，tier=gov）
    "iguopin": IguopinCollector,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="tencent,netease")
    ap.add_argument("--batch", default=datetime.now().strftime("%YW%W"))
    ap.add_argument("--per-query", type=int, default=12)
    ap.add_argument("--only", default="", help="只采这些簇（逗号分隔，默认全部）")
    ap.add_argument("--full-catalog", action="store_true",
                    help="全量目录模式（仅 feishu_ats 支持）：不按检索词，直接翻完每家企业的公开职位目录")
    ap.add_argument("--max-per-tenant", type=int, default=400,
                    help="全量目录模式下每家企业最多采多少条")
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
        if args.full_catalog and hasattr(col, "collect_catalog"):
            total = col.collect_catalog(max_per_tenant=args.max_per_tenant)
            col.write_manifest(notes=f"full_catalog, max_per_tenant={args.max_per_tenant}")
        else:
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
