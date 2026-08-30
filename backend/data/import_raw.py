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
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402
from app.services.employer_resolution import (  # noqa: E402
    get_or_create_employer, normalize_employer_name)
from app.services.job_resolution import (  # noqa: E402
    NON_ENG, TITLE_OK, resolve_job_query, title_on_target)
from data.collect.base import mask_pii  # noqa: E402

TIER_AUTHORITY = {"official": 1.0, "gov": 1.0, "dataset": 0.7, "aggregator": 0.8}



def _employer_unit(db, employer) -> str:
    """雇主的配额单位：母公司归一化名（无母公司则自身）。取不到实体时退化为空串。"""
    if employer is None:
        return ""
    if employer.parent_id:
        parent = db.query(models.Employer).filter(
            models.Employer.id == employer.parent_id).first()
        if parent is not None:
            return parent.normalized_name or parent.name or ""
    return employer.normalized_name or employer.name or ""


def _quota_key(db, company: str, cluster: str | None) -> tuple[str, str | None]:
    """同雇主同簇配额键：归一化雇主名（有母公司则上卷到母公司），簇。

    用归一化名而不是 ``employer.id`` 做键，是因为配额必须在 ``get_or_create_employer``
    **之前**判定（否则被拒的 JD 会留下没有任何 RawJD 的孤儿雇主实体，而雇主实体正是
    ≥2 独立雇主闸门的计数单位）；此时首次出现的公司还没有 id，只有名字是稳定的。

    公司名为空的 JD 也必须占配额而不是直接豁免：匿名语料证明不了来源独立，放开配额
    等于让一份匿名语料无上限地撑起某个簇的支持率分母。空名统一归到 ``""`` 这一个键。
    """
    normalized = normalize_employer_name(company) or ""
    if not normalized:
        return ("", cluster)
    existing = db.query(models.Employer).filter(
        models.Employer.normalized_name == normalized).first()
    return (_employer_unit(db, existing) or normalized, cluster)

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


_QUERY_TITLE_PAIRS = tuple(sorted(
    ((keyword, cluster) for keyword, cluster in _query_cluster_map().items()),
    key=lambda item: -len(item[0])))

# 多个标题白名单同时命中时的**角色优先级**。这些不是模糊猜测，而是从 R7 全目录
# 实测冲突里提炼的强信号：例如「机器人DevOps工程师」同时命中机器人域和运维角色，
# 岗位角色应归运维；「大语言模型算法」被 NLP 的「大语言模型」和大模型算法同时命中，
# 但标题明确写了算法。越具体的角色放得越前。
_TITLE_CLUSTER_PRECEDENCE: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"大模型|大语言模型|LLM", re.I), "大模型算法"),
    (re.compile(r"(?:DevOps|SRE|运维|可观测|稳定性|监控)", re.I), "运维开发"),
    (re.compile(r"机器人.*(?:行为|运动控制|运控|导航|感知|规划|SLAM)|"
                r"(?:行为|运动控制|运控|导航|感知|规划|SLAM).*机器人", re.I), "机器人算法"),
)

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


def infer_title_cluster(title: str) -> str | None:
    """从全目录标题推断岗位簇，供过滤、配额和 cluster_hint 共用。

    飞书 `--full-catalog` 没有检索词，所有行的 extra.query 都是空串。若 cluster 一直
    保持 None，原实现会把「同雇主同簇 ≤5」退化为**同公司总共 ≤5 条**，还会让通过
    闸门的历史 JD 失去 cluster_hint。先复用 queries.json 的最长关键词规则；它能让
    「机器人运动控制算法工程师」归机器人算法，而不被通用的机器学习/具身正则抢走。
    关键词没有命中时，才用更完整的标题白名单；多个白名单同时命中则不猜。
    """
    value = title or ""
    # NON_ENG 里有「产品经理」，用于挡住检索正文命中的普通产品岗；全目录里只有标题
    # 自己同时带 AI 技术限定词的产品经理才属于 AI产品，不把所有产品岗都吸进来。
    if re.search(r"产品(?:经理|总监|负责人|策划)", value) and re.search(
            r"AI|人工智能|大模型|大语言模型|LLM|智能体|Agent|AIGC|生成式", value, re.I):
        return "AI产品"
    if NON_ENG.search(value):
        return None
    low = value.casefold()
    for keyword, cluster in _QUERY_TITLE_PAIRS:
        if keyword and keyword.casefold() in low:
            return cluster
    hits = [cluster for cluster, pattern in TITLE_OK.items() if pattern.search(value)]
    if len(hits) == 1:
        return hits[0]
    for role_pattern, cluster in _TITLE_CLUSTER_PRECEDENCE:
        if cluster in hits and role_pattern.search(value):
            return cluster
    return None


def import_batch(batch: str, tier: str, filter_it: bool = False,
                 filter_title: bool = False, max_per_employer_cluster: int = 0) -> None:
    init_db()
    base = Path(__file__).parent / "raw" / batch
    manifest = json.loads((base / "manifest.json").read_text("utf-8")) if (base / "manifest.json").exists() else {}
    db = SessionLocal()
    qmap = _query_cluster_map()
    try:
        existing_urls = {u for (u,) in db.query(models.RawJD.source_url).all() if u}
        # 同雇主同簇的配额按「库里已有 + 本批已收」合计算，否则重跑一次批次就能翻倍。
        quota = Counter()
        if max_per_employer_cluster > 0:
            for emp_id, hint, n in db.query(
                    models.RawJD.employer_id, models.RawJD.cluster_hint,
                    func.count(models.RawJD.id)).group_by(
                    models.RawJD.employer_id, models.RawJD.cluster_hint).all():
                # 历史全目录行若没 cluster_hint，无法判断它属于哪个簇；不能把它们合成一个
                # None 配额，否则某公司已有 5 条任意技术岗后，所有新簇一律被挡。仅对已有
                # 明确簇的行计入配额，新入行都会在上面由 query/标题得到明确 cluster。
                if hint is None:
                    continue
                if emp_id is None:
                    # 公司名为空的历史行同样占「匿名」这一份配额，与新入行口径一致。
                    quota[("", hint)] += n
                    continue
                employer = db.get(models.Employer, emp_id)
                quota[(_employer_unit(db, employer), hint)] += n
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

            kept = seen = off_domain = off_title = over_quota = 0
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
                    cluster = qmap.get(((r.get("extra") or {}).get("query")) or "", None)
                    # 全目录没有 query：从标题推断明确簇，后续过滤、配额和落库都用同一个结果。
                    # filter-title 模式下推不出簇就拒绝；否则留下 cluster_hint=None，后续宽松
                    # title_key 会重新猜一次，等于把这道闸门绕开。
                    if cluster is None:
                        cluster = infer_title_cluster(title)
                    if filter_title and (cluster is None or not title_on_target(title, cluster)):
                        off_title += 1
                        continue
                    resolution = resolve_job_query(title)
                    if max_per_employer_cluster > 0:
                        # 配额必须在建 Employer 之前判：被配额拒掉的 JD 不入库，
                        # 若先 get_or_create 就会给它留下一个没有任何 RawJD 的孤儿雇主实体，
                        # 而雇主实体正是 ≥2 独立雇主闸门的计数单位。
                        # 配额键用「归一化名」而非 employer.id，这样首次出现（尚未建实体）
                        # 与后续出现落在同一个键上，不会各拿一份配额。
                        if quota[_quota_key(db, company, cluster)] >= max_per_employer_cluster:
                            over_quota += 1
                            continue
                        quota[_quota_key(db, company, cluster)] += 1
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
                        cluster_hint=cluster,
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
            if filter_title:
                msg += f"  ← 标题不对口剔除 {off_title} 条"
            if max_per_employer_cluster > 0:
                msg += f"  ← 同雇主同簇超配额剔除 {over_quota} 条"
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
    ap.add_argument("--filter-title", action="store_true",
                    help="标题相关性闸门：标题必须自带该簇领域词，正文命中不算（挡住检索命中正文带回的无关岗）")
    ap.add_argument("--max-per-employer-cluster", type=int, default=0,
                    help="同一雇主同一岗位簇最多保留几条，0=不限。防单一雇主刷高支持率分母")
    args = ap.parse_args()
    import_batch(args.batch, args.tier, filter_it=args.filter_it,
                 filter_title=args.filter_title,
                 max_per_employer_cluster=args.max_per_employer_cluster)
