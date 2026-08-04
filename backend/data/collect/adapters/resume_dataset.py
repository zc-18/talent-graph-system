# -*- coding: utf-8 -*-
"""简历语料适配器 A —— HuggingFace 公开数据集（意见⑧）。

数据集：`brackozi/Resume`（**MIT 许可证**，非 gated，962 行，字段 Category + Resume 全文）。
探测依据见 `data/collect/probe_resume_report.json`（meta=200 / license=mit / gated=False）。

只取能映射到 `queries.json` 岗位簇的新一代信息技术类目，其余（HR/Sales/Arts/
Civil Engineer 等）一概不要。**落盘前正文强制脱敏**（app.services.resume.mask_contacts
+ collect.base.mask_pii 叠加），且写盘后自检，检出联系方式直接抛错。

用法（backend/ 下）：
    uv run python -X utf8 data/collect/adapters/resume_dataset.py --batch 2026W31-res-a
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.services.resume import mask_contacts, contains_contacts  # noqa: E402
from data.collect.base import mask_pii  # noqa: E402

DATASET = "brackozi/Resume"
LICENSE = "MIT"
DATASET_URL = f"https://huggingface.co/datasets/{DATASET}"
ROWS_API = "https://datasets-server.huggingface.co/rows"

RESUMES_DIR = BACKEND / "data" / "resumes"
ARCHIVE = RESUMES_DIR / "_datasets" / "hf_brackozi_Resume.jsonl"

# 数据集类目 → queries.json 岗位簇（未列出的类目一律丢弃）
CATEGORY_CLUSTER = {
    "Data Science": "机器学习",
    "Python Developer": "后端开发",
    "Java Developer": "Java开发",
    "DevOps Engineer": "运维开发",
    "Hadoop": "大数据平台",
    "ETL Developer": "数据仓库",
    "Database": "数据开发",
}
MIN_LEN = 600          # 正文过短的简历技能要素太少，学不到东西


def fetch_all_rows(c: httpx.Client) -> list[dict]:
    """分页取全量行并归档原始数据（佐证）。"""
    rows, offset = [], 0
    while True:
        r = c.get(ROWS_API, params={"dataset": DATASET, "config": "default",
                                    "split": "train", "offset": offset, "length": 100})
        r.raise_for_status()
        d = r.json()
        batch = [x["row"] for x in d.get("rows", [])]
        rows.extend(batch)
        total = d.get("num_rows_total", 0)
        print(f"  取回 {len(rows)}/{total}")
        offset += len(batch)
        if not batch or offset >= total:
            break
        time.sleep(0.5)

    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVE, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  原始数据已归档 → {ARCHIVE}")
    return rows


def select(rows: list[dict], per_cat: int, cap: int) -> list[tuple[int, dict]]:
    """过滤 → 去重 → 按正文长度选优 → 每类目封顶，保证簇多样性。"""
    pool: dict[str, list[tuple[int, dict]]] = {}
    seen: set[str] = set()
    for i, row in enumerate(rows):
        cat = (row.get("Category") or "").strip()
        if cat not in CATEGORY_CLUSTER:
            continue
        text = (row.get("Resume") or "").strip()
        if len(text) < MIN_LEN:
            continue
        h = hashlib.sha1(" ".join(text.split()).lower().encode()).hexdigest()
        if h in seen:            # 该数据集内同一份简历重复出现很多次
            continue
        seen.add(h)
        pool.setdefault(cat, []).append((i, row))

    picked: list[tuple[int, dict]] = []
    for cat, items in pool.items():
        items.sort(key=lambda t: len(t[1].get("Resume") or ""), reverse=True)
        picked.extend(items[:per_cat])
        print(f"  {cat:<20} 去重后 {len(items):>3} 条，取 {min(per_cat, len(items))}")
    picked.sort(key=lambda t: len(t[1].get("Resume") or ""), reverse=True)
    return picked[:cap]


def convert(batch: str, per_cat: int, cap: int) -> None:
    out_dir = RESUMES_DIR / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    out_fp = out_dir / "dataset_brackozi_resume.jsonl"

    with httpx.Client(timeout=60.0, follow_redirects=True) as c:
        print(f"[resume_dataset] 拉取 {DATASET} …")
        rows = fetch_all_rows(c)

    print(f"[resume_dataset] 共 {len(rows)} 行，开始筛选：")
    picked = select(rows, per_cat, cap)

    written = 0
    with open(out_fp, "w", encoding="utf-8") as w:
        for idx, row in picked:
            cat = row["Category"].strip()
            text = mask_contacts(mask_pii((row.get("Resume") or "").strip()))
            if contains_contacts(text):
                sys.exit(f"[resume_dataset] 脱敏自检失败，第 {idx} 行仍含联系方式，已中止")
            rec = {
                "source_type": "dataset",
                "source_name": DATASET,
                "source_url": f"{DATASET_URL}#row={idx}",
                "license": LICENSE,
                "language": "en",
                "target_cluster": CATEGORY_CLUSTER[cat],
                "raw_text": text,
                "collected_at": datetime.utcnow().strftime("%Y-%m-%d"),
                "extra": {"category": cat, "row": idx, "dataset": DATASET,
                          "note": "HuggingFace 公开数据集（MIT），正文已脱敏"},
            }
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    manifest = {
        "batch": batch,
        "source_type": "dataset",
        "adapters": {"dataset_brackozi_resume": {
            "source_name": DATASET, "source_url": DATASET_URL, "license": LICENSE,
            "tier": "dataset", "authority": 0.7, "method": "api", "rate_limit_s": 0.5,
            "robots_ok": True,
            "collected": len(rows), "kept": written,
            "filters": {"categories": list(CATEGORY_CLUSTER), "min_len": MIN_LEN,
                        "per_category_cap": per_cat, "total_cap": cap,
                        "dedup": "sha1(正文归一化)"},
            "privacy": "正文经 mask_pii + mask_contacts 双重脱敏后落盘，并做写盘前自检",
            "finished_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"[resume_dataset] 读取 {len(rows)} 保留 {written} → {out_fp}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="2026W31-res-a")
    ap.add_argument("--per-cat", type=int, default=2, help="每个类目最多取几份（保多样性）")
    ap.add_argument("--cap", type=int, default=12, help="本批总量上限")
    args = ap.parse_args()
    convert(args.batch, args.per_cat, args.cap)
