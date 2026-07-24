"""把采集批次 jsonl 导入数据库：CrawlBatch + RawJD（真实数据入库入口）。

用法（backend/ 下）：
    uv run python -X utf8 data/import_raw.py --batch 2026W31-r1 [--tier official]

处理：URL 去重（同 URL 重采不算抄袭，直接跳过）、PII 复查打码、经验年限/标题推断级别、
     记录来源权威度与本地留存路径。SimHash 近似去重与通胀检测仍由后续 pipeline 完成。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402
from data.collect.base import mask_pii  # noqa: E402

TIER_AUTHORITY = {"official": 1.0, "gov": 1.0, "dataset": 0.7, "aggregator": 0.8}


def _query_cluster_map() -> dict[str, str]:
    """检索词 -> 岗位簇（queries.json 反查），用于 cluster_hint。"""
    qf = Path(__file__).parent / "collect" / "queries.json"
    out = {}
    try:
        for cluster, kws in json.loads(qf.read_text("utf-8"))["queries"].items():
            for kw in kws:
                out[kw] = cluster
    except Exception:
        pass
    return out

_SENIOR_PAT = re.compile(r"高级|资深|专家|首席|Senior|Staff|Principal|Expert", re.I)
_JUNIOR_PAT = re.compile(r"初级|助理|实习|校招|应届|Junior|Intern", re.I)


def infer_level(title: str, experience_req: str) -> str:
    """级别推断：标题关键词优先，其次经验年限，默认 middle。"""
    t = title or ""
    if _SENIOR_PAT.search(t):
        return "senior"
    if _JUNIOR_PAT.search(t):
        return "junior"
    exp = experience_req or ""
    m = re.search(r"(\d+)\s*[-~到至]\s*(\d+)\s*年", exp)
    if m:
        lo = int(m.group(1))
        return "junior" if lo < 3 else ("middle" if lo < 5 else "senior")
    m = re.search(r"(\d+)\s*年(以上)?", exp)
    if m:
        y = int(m.group(1))
        return "junior" if y < 3 else ("middle" if y < 5 else "senior")
    if re.search(r"应届|在校|不限", exp):
        return "junior" if "应届" in exp or "在校" in exp else "middle"
    return "middle"


def parse_date(s: str):
    if not s:
        return None
    s = str(s)
    for fmt in ("%Y年%m月%d日", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:12].strip(), fmt)
        except ValueError:
            continue
    if s.isdigit() and len(s) == 13:      # 毫秒时间戳（网易 updateTime）
        return datetime.fromtimestamp(int(s) / 1000)
    return None


def import_batch(batch: str, tier: str) -> None:
    init_db()
    base = Path(__file__).parent / "raw" / batch
    manifest = json.loads((base / "manifest.json").read_text("utf-8")) if (base / "manifest.json").exists() else {}
    db = SessionLocal()
    qmap = _query_cluster_map()
    try:
        existing_urls = {u for (u,) in db.query(models.RawJD.source_url).all() if u}
        total_kept = 0
        for fp in sorted(base.glob("*.jsonl")):
            if fp.name == "crawl_log.jsonl":
                continue
            platform = fp.stem
            meta = (manifest.get("adapters") or {}).get(platform, {})
            authority = float(meta.get("authority", TIER_AUTHORITY.get(tier, 0.8)))
            cb = db.query(models.CrawlBatch).filter(
                models.CrawlBatch.batch_key == f"{batch}-{platform}").first()
            if not cb:
                cb = models.CrawlBatch(batch_key=f"{batch}-{platform}", platform=platform)
                db.add(cb)
                db.flush()
            cb.tier = meta.get("tier", tier)
            cb.method = "api"
            cb.rate_limit_s = meta.get("rate_limit_s", 4.0)
            cb.raw_dir = str(base)
            cb.finished_at = parse_date(meta.get("finished_at", "")) or datetime.utcnow()

            kept = seen = 0
            for line in fp.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                seen += 1
                url = r.get("url") or ""
                if url and url in existing_urls:
                    continue
                text = mask_pii(r.get("raw_text") or "")
                if len(text) < 50:
                    continue
                dedup = hashlib.md5(text.encode("utf-8")).hexdigest()
                jd = models.RawJD(
                    job_title=(r.get("job_title") or "")[:120],
                    company=(r.get("company") or "")[:120],
                    location=(r.get("location") or "")[:60],
                    source=platform, source_url=url[:500],
                    raw_text=text,
                    publish_date=parse_date(r.get("publish_date")),
                    collected_at=parse_date(r.get("crawled_at")) or datetime.utcnow(),
                    dedup_hash=dedup,
                    platform=platform,
                    salary_range=(r.get("salary_range") or "")[:60],
                    experience_req=(r.get("experience_req") or "")[:60],
                    education_req=(r.get("education_req") or "")[:60],
                    crawl_batch_id=cb.id,
                    raw_file_path=str(fp),
                    inferred_level=infer_level(r.get("job_title") or "", r.get("experience_req") or ""),
                    cluster_hint=qmap.get(((r.get("extra") or {}).get("query")) or "", None),
                    source_authority=authority,
                )
                db.add(jd)
                if url:
                    existing_urls.add(url)
                kept += 1
            cb.collected = seen
            cb.kept = kept
            total_kept += kept
            db.commit()
            print(f"[import] {platform}: 读取 {seen} 保留 {kept} (batch={cb.batch_key})")
        print(f"[import] DONE batch={batch} 共入库 {total_kept} 条")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--tier", default="official", choices=list(TIER_AUTHORITY))
    args = ap.parse_args()
    import_batch(args.batch, args.tier)
