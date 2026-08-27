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
from app.services.employer_resolution import get_or_create_employer  # noqa: E402
from app.services.job_resolution import resolve_job_query  # noqa: E402
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


# 新一代信息技术领域判定（企业官网/公共平台给的是全岗位目录，需按领域收敛）。
# 标题命中即保留；标题未命中但正文出现 ≥3 个不同技术词也保留，避免漏掉「XX工程师」这类泛标题。
_IT_TITLE = re.compile(
    r"算法|模型|AI|人工智能|机器学习|深度学习|NLP|自然语言|视觉|CV|感知|数据|大数据|数仓|"
    r"数据库|BI|后端|前端|全栈|服务端|Java|Python|Golang|C\+\+|架构|开发工程师|研发工程师|"
    r"软件|嵌入式|固件|驱动|测试开发|运维|SRE|云计算|云原生|平台研发|中台|物联网|IoT|"
    r"自动驾驶|智驾|机器人|仿真|数字孪生|芯片|FPGA|信息安全|网络安全|搜索|推荐|智能|"
    r"Agent|智能体|大语言|LLM|AIGC|多模态|标注|训练师|推理|部署|MLOps|DevOps|数字化", re.I)
_IT_BODY = re.compile(
    r"Python|Java|Golang|C\+\+|PyTorch|TensorFlow|Spark|Hadoop|Flink|Hive|Kafka|"
    r"Kubernetes|K8s|Docker|MySQL|Redis|Linux|SQL|机器学习|深度学习|神经网络|大模型|"
    r"微服务|分布式|算法|模型训练|数据仓库|嵌入式|单片机|自动驾驶|计算机视觉", re.I)


def is_it_domain(title: str, text: str) -> bool:
    """是否属于新一代信息技术领域（AI / 大数据 / 智能系统 / 物联网 / 云计算）。"""
    if _IT_TITLE.search(title or ""):
        return True
    return len(set(m.lower() for m in _IT_BODY.findall(text or ""))) >= 3


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


def import_batch(batch: str, tier: str, filter_it: bool = False) -> None:
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

            kept = seen = off_domain = 0
            # 注意：用文件迭代而非 read_text().splitlines()——JD 正文里可能含 U+2028/U+2029，
            # json.dumps 不会转义它们，但 str.splitlines() 会在那里断行，导致整条记录被劈成两半。
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
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
                    if filter_it and not is_it_domain(r.get("job_title") or "", text):
                        off_domain += 1
                        continue
                    dedup = hashlib.md5(text.encode("utf-8")).hexdigest()
                    title = (r.get("job_title") or "")[:120]
                    company = (r.get("company") or "")[:120]
                    resolution = resolve_job_query(title)
                    employer = get_or_create_employer(db, company)
                    jd = models.RawJD(
                        job_title=title,
                        company=company,
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
                        inferred_level=infer_level(r.get("job_title") or "",
                                                   r.get("experience_req") or ""),
                        track=resolution.track,
                        industry=resolution.industry,
                        recruitment_type=resolution.recruitment_type,
                        employer_id=employer.id if employer else None,
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
            msg = f"[import] {platform}: 读取 {seen} 保留 {kept} (batch={cb.batch_key})"
            if filter_it:
                msg += f"  ← 领域过滤剔除 {off_domain} 条非信息技术岗"
            print(msg)
        print(f"[import] DONE batch={batch} 共入库 {total_kept} 条")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--tier", default="official", choices=list(TIER_AUTHORITY))
    ap.add_argument("--filter-it", action="store_true",
                    help="只保留新一代信息技术领域岗位（企业官网/公共平台的全量目录需要此过滤）")
    args = ap.parse_args()
    import_batch(args.batch, args.tier, filter_it=args.filter_it)
