"""公开数据集导入适配器：把下载的公开招聘数据集转成标准批次 jsonl。

当前支持数据集：51job2018（GitHub north-jewel/data_analysis，前程无忧 2018-12 采集，
5654 条 IT 岗位，含职位描述全文）。原始文件留存于 data/raw/_datasets/ 作为佐证。

用法（backend/ 下）：
    uv run python -X utf8 data/collect/adapters/dataset_import.py --batch 2018hist-r1

输出：data/raw/<batch>/dataset_51job2018.jsonl + manifest.json（tier=dataset, authority=0.7）
过滤：仅保留能映射到 data/collect/queries.json 岗位簇的新一代信息技术岗位，
     职位描述 ≥100 字，封顶 ~300 条；extra.query 写入簇检索词供 import_raw 反查 cluster_hint。
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
DATASETS_DIR = BACKEND / "data" / "raw" / "_datasets"
QUERIES = json.loads((BACKEND / "data" / "collect" / "queries.json").read_text("utf-8"))["queries"]

DATASET_NAME = "51job2018"
SOURCE_URL = ("https://github.com/north-jewel/data_analysis/blob/master/"
              "homework/%E7%8E%8B%E6%98%A5%E5%8D%87-Ezreal/51job_3.csv")
COLLECT_DATE = "2018-12-06"   # 仓库该文件提交日期（"前程无忧Python 5000+数据"）

# 标题 → 岗位簇（键必须存在于 queries.json）；按顺序首个命中生效
TITLE_CLUSTER_RULES: list[tuple[str, str]] = [
    (r"机器学习|数据挖掘", "机器学习"),
    (r"深度学习", "深度学习"),
    (r"图像|视觉|CV", "计算机视觉"),
    (r"NLP|自然语言", "自然语言处理"),
    (r"推荐", "推荐算法"),
    (r"算法", "机器学习"),          # 通用算法岗归入机器学习簇
    (r"数据仓库|数仓|ETL", "数据仓库"),
    (r"大数据", "大数据平台"),
    (r"数据分析", "数据分析"),
    (r"数据开发", "数据开发"),
    (r"Java", "Java开发"),
    (r"云计算|云原生|Kubernetes|k8s", "云计算"),
    (r"SRE|运维", "运维开发"),
    (r"嵌入式|单片机", "嵌入式"),
    (r"物联网|IoT", "物联网"),
    (r"Python|后端|服务端|Golang|Go开发|C\+\+", "后端开发"),
]
_RULES = [(re.compile(p, re.I), c) for p, c in TITLE_CLUSTER_RULES]


def map_cluster(title: str) -> str | None:
    for pat, cluster in _RULES:
        if pat.search(title or ""):
            return cluster
    return None


def clean_text(t: str) -> str:
    t = (t or "").replace("\t", " ")
    t = re.sub(r"[ 　]{2,}", " ", t)
    return t.strip()


def convert_51job2018(batch: str, cap: int = 300) -> None:
    src = DATASETS_DIR / "51job_3.csv"
    if not src.exists():
        sys.exit(f"缺少原始数据集文件：{src}（先下载留存）")
    out_dir = BACKEND / "data" / "raw" / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    out_fp = out_dir / f"dataset_{DATASET_NAME}.jsonl"

    kept, seen, per_cluster = 0, 0, {}
    with open(src, encoding="utf-8", errors="replace") as f, \
         open(out_fp, "w", encoding="utf-8") as w:
        reader = csv.reader(f)
        header = next(reader)
        for i, row in enumerate(reader):
            if kept >= cap:
                break
            d = dict(zip(header, row))
            seen += 1
            title = clean_text(d.get("skill_type", ""))
            cluster = map_cluster(title)
            if not cluster:
                continue
            text = clean_text(d.get("job_required", ""))
            if len(text) < 100:
                continue
            per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
            if per_cluster[cluster] > 30:   # 单簇封顶，保持多样性
                continue
            query_kw = QUERIES[cluster][0]  # import_raw 反查 cluster_hint 用
            rec = {
                "platform": f"dataset:{DATASET_NAME}",
                "company": clean_text(d.get("company_name", "")),
                "job_title": title,
                "location": clean_text(d.get("city", "")),
                "salary_range": clean_text(d.get("salary", "")),
                "experience_req": clean_text(d.get("experience_required", "")),
                "education_req": clean_text(d.get("edu_required", "")),
                "publish_date": COLLECT_DATE,
                "url": f"{SOURCE_URL}#row={i}",
                "crawled_at": datetime.utcnow().strftime("%Y-%m-%d"),
                "raw_text": f"{title}\n{text}",
                "extra": {"query": query_kw, "dataset": DATASET_NAME,
                          "note": "公开数据集历史基线（2018-12 前程无忧采集）"},
            }
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kept += 1

    manifest = {
        "batch": batch,
        "tier": "dataset",
        "adapters": {f"dataset_{DATASET_NAME}": {
            "tier": "dataset", "authority": 0.7, "rate_limit_s": 0,
            "source_url": SOURCE_URL,
            "license": "仓库未声明许可证；GitHub 公开仓库，仅科研评测用途，保留出处",
            "collected": seen, "kept": kept,
            "finished_at": datetime.utcnow().strftime("%Y-%m-%d"),
        }},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"[dataset_import] 读取 {seen} 保留 {kept} → {out_fp}")
    print("[dataset_import] 各簇分布:", json.dumps(
        {k: min(v, 30) for k, v in sorted(per_cluster.items())}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="2018hist-r1")
    ap.add_argument("--cap", type=int, default=300)
    args = ap.parse_args()
    convert_51job2018(args.batch, args.cap)
