# -*- coding: utf-8 -*-
"""简历语料入库：jsonl → 解析 → 归一化 → 脱敏 → TalentProfile（意见⑧）。

与 JD 侧 `import_raw.py` 平行，但有一条本质不同：
**JD 原文入库（raw_jd.raw_text），简历原文不入库**。TalentProfile 结构上就没有
正文/姓名/联系方式列，只落技能要素 + 出处 URL + 正文长度。

流程：读 data/resumes/{batch}/*.jsonl 与 manifest.json → 建 ResumeBatch 台账 →
逐条 parse_resume（LLM 抽取 + 词典兜底，带缓存）→ 归一化 → 映射岗位簇/岗位 →
写 TalentProfile（按 source_url 幂等）。

用法（backend/ 下，**必须显式指定生产库**）：
    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/import_resumes.py --all
    uv run python -X utf8 data/import_resumes.py --batch 2026W31-res-a
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import resume as resume_svc  # noqa: E402
from app.services.ingest import title_key  # noqa: E402

RESUMES_DIR = BACKEND / "data" / "resumes"
CACHE_FP = BACKEND / "data" / "resume_parsed_cache.json"
_CJK = re.compile(r"[一-鿿]")

DEMO_DB = "talent_graph"      # 原始演示库，数据脚本打上去很危险（见 CLAUDE.md）


def load_cache() -> dict:
    if CACHE_FP.exists():
        try:
            return json.loads(CACHE_FP.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FP.write_text(json.dumps(cache, ensure_ascii=False, indent=1), "utf-8")


def detect_language(text: str) -> str:
    cjk = len(_CJK.findall(text))
    return "zh" if cjk >= max(20, len(text) * 0.05) else "en"


def quality_score(skill_count: int, text_len: int, has_job: bool) -> float:
    return round(0.5 * min(1.0, skill_count / 15) + 0.3 * min(1.0, text_len / 3000)
                 + 0.2 * (1.0 if has_job else 0.0), 4)


def next_code(db) -> int:
    """下一个可用的化名编号序号（T001…）。"""
    codes = [c for (c,) in db.query(models.TalentProfile.code).all() if c and c.startswith("T")]
    nums = [int(c[1:]) for c in codes if c[1:].isdigit()]
    return max(nums, default=0) + 1


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def near_duplicate(skills: list[str], host: str,
                   existing: list[tuple[str, str, set[str]]]) -> str | None:
    """同一来源站点下技能集高度重合 → 视为同一个人简历的另一个页面。

    B 源实测踩到过：taccisum.github.io/resume 与 .../resume/index_advanced.html
    是同一个人简历的两个版本，URL 不同、正文不同，但技能集几乎一样。
    返回重复对象的 code，没有则 None。
    """
    s = set(skills)
    if len(s) < 3 or not host:
        return None
    for code, other_host, other_skills in existing:
        if other_host != host or len(other_skills) < 3:
            continue
        inter = len(s & other_skills)
        union = len(s | other_skills)
        if union and inter / union >= 0.8:
            return code
    return None


def upsert_batch(db, batch: str, adapter: str, meta: dict, raw_dir: Path) -> models.ResumeBatch:
    key = f"{batch}-{adapter}"
    row = db.query(models.ResumeBatch).filter_by(batch_key=key).first()
    if not row:
        row = models.ResumeBatch(batch_key=key)
        db.add(row)
    row.source_type = meta.get("source_type") or meta.get("tier") or "dataset"
    row.source_name = meta.get("source_name", adapter)
    row.source_url = meta.get("source_url", "")
    row.license = meta.get("license", "")
    row.tier = meta.get("tier", "dataset")
    row.authority = float(meta.get("authority", 0.7))
    row.method = meta.get("method", "api")
    row.robots_ok = bool(meta.get("robots_ok", True))
    row.rate_limit_s = float(meta.get("rate_limit_s", 0) or 0)
    row.collected = int(meta.get("collected", 0))
    row.raw_dir = str(raw_dir.relative_to(BACKEND)).replace("\\", "/")
    row.notes = meta.get("privacy", "")
    row.finished_at = datetime.utcnow()
    if not row.started_at:
        row.started_at = datetime.utcnow()
    db.flush()
    return row


def import_batch(db, batch: str, cache: dict, job_by_name: dict) -> tuple[int, int]:
    bdir = RESUMES_DIR / batch
    if not bdir.exists():
        print(f"[import] 跳过 {batch}：目录不存在")
        return 0, 0
    manifest = {}
    mf = bdir / "manifest.json"
    if mf.exists():
        manifest = json.loads(mf.read_text("utf-8"))
    adapters_meta = manifest.get("adapters", {})

    seen_urls = {u for (u,) in db.query(models.TalentProfile.source_url).all() if u}
    seen_hashes = {h for (h,) in db.query(models.TalentProfile.text_hash).all() if h}
    prior = [(p.code, host_of(p.source_url), set(p.skills or []))
             for p in db.query(models.TalentProfile).all()]
    seq = next_code(db)
    total_new, total_skip = 0, 0

    for fp in sorted(bdir.glob("*.jsonl")):
        if fp.stem in ("crawl_log", "collect_log"):
            continue          # 采集请求日志，不是语料
        adapter = fp.stem
        meta = adapters_meta.get(adapter, {})
        brow = upsert_batch(db, batch, adapter, meta, bdir)
        kept = 0
        for line in fp.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            url = rec.get("source_url", "")
            text = rec.get("raw_text", "") or ""
            if not url or len(text.strip()) < 200:
                # 语料记录必须有出处和正文；否则是日志行或残缺记录，直接丢弃
                total_skip += 1
                continue
            if url in seen_urls:
                total_skip += 1
                kept += 1          # 已在库，仍计入该批次入库数
                continue

            # 落库前最后一道关口：正文必须已脱敏
            if resume_svc.contains_contacts(text):
                text = resume_svc.mask_contacts(text)
                if resume_svc.contains_contacts(text):
                    sys.exit(f"[import] 中止：{url} 正文仍含联系方式，脱敏未生效")

            h = hashlib.sha1(text.encode("utf-8")).hexdigest()
            if h in seen_hashes:
                print(f"  - 跳过（正文完全重复）: {url[:70]}")
                total_skip += 1
                kept += 1
                continue
            if h in cache:
                parsed = cache[h]
            else:
                parsed = resume_svc.parse_resume(text)
                cache[h] = parsed
                save_cache(cache)

            cluster = rec.get("target_cluster") or ""
            job_name = title_key("", cluster) if cluster else ""
            job = job_by_name.get(job_name)
            skills = parsed.get("skills", []) or []

            dup = near_duplicate(skills, host_of(url), prior)
            if dup:
                print(f"  - 跳过（与 {dup} 同源近重复，疑似同一人的另一个页面）: {url[:60]}")
                total_skip += 1
                kept += 1
                continue

            tp = models.TalentProfile(
                code=f"T{seq:03d}",
                batch_id=brow.id,
                source_type=rec.get("source_type", brow.source_type),
                source_name=rec.get("source_name", brow.source_name),
                source_url=url,
                license=rec.get("license", brow.license),
                language=rec.get("language") or detect_language(text),
                target_cluster=cluster,
                matched_job_id=job.id if job else None,
                years_experience=parsed.get("years_experience", 0) or 0,
                education=(parsed.get("education") or "")[:64],
                skills=skills,
                skill_levels=parsed.get("skill_levels", {}) or {},
                raw_skill_terms=parsed.get("raw_skill_terms", []) or [],
                skill_count=len(skills),
                text_len=len(text),
                text_hash=h,
                quality_score=quality_score(len(skills), len(text), bool(job)),
            )
            db.add(tp)
            seq += 1
            kept += 1
            total_new += 1
            seen_urls.add(url)
            seen_hashes.add(h)
            prior.append((tp.code, host_of(url), set(skills)))
            print(f"  + {tp.code} [{tp.language}] {cluster or '(无簇)'} → "
                  f"{job_name or '(未映射岗位)'}  技能 {len(skills)} 项")

        brow.kept = kept
        db.commit()
    return total_new, total_skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", default=[], help="批次目录名，可重复")
    ap.add_argument("--all", action="store_true", help="导入 data/resumes 下全部批次")
    ap.add_argument("--allow-demo-db", action="store_true",
                    help=f"允许写入原始演示库 {DEMO_DB}（默认拒绝）")
    args = ap.parse_args()

    print(f"[import] 目标库 = {settings.db_name}")
    if settings.db_name == DEMO_DB and not args.allow_demo_db:
        sys.exit(f"[import] 拒绝写入原始演示库 {DEMO_DB}。生产库请先设 "
                 f"$env:DB_NAME='talent_graph_v3'（或加 --allow-demo-db 明确覆盖）")

    batches = args.batch
    if args.all or not batches:
        batches = sorted(d.name for d in RESUMES_DIR.iterdir()
                         if d.is_dir() and not d.name.startswith("_"))
    print(f"[import] 批次: {batches}")

    cache = load_cache()
    db = SessionLocal()
    try:
        job_by_name = {j.name: j for j in db.query(models.Job).all()}
        new = skip = 0
        for b in batches:
            n, s = import_batch(db, b, cache, job_by_name)
            new, skip = new + n, skip + s
        total = db.query(models.TalentProfile).count()
        print(f"\n[import] 新增 {new} 份，已存在跳过 {skip} 份，库内合计 {total} 份人才画像")
    finally:
        db.close()
        save_cache(cache)


if __name__ == "__main__":
    main()
