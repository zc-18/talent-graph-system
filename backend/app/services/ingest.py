"""数据接入与图谱构建编排（pipeline）。

把多源原始 JD 经「清洗去重→解析→交叉验证聚合→落库」构建岗位能力图谱。
支持两种入口：build_graph_from_dataset（json 数据集）与 build_graph_from_rows
（已入库的真实采集 RawJD 行，2026-07 整改）。
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from statistics import median
from sqlalchemy.orm import Session
from .. import models
from . import cleaning, extraction, hallucination, graph_service
from .employer_resolution import employer_independence_key, get_or_create_employer
from .taxonomy import normalize_fine_skill, normalize_skill
from .job_resolution import (
    INDUSTRIES,
    RECRUITMENT_TYPES,
    SENIORITIES,
    TRACKS,
    NON_ENG,
    resolve_job_query,
)

# 真实标题装饰剥离：括号编号/城市/紧急标记/级别词（级别词回填给分级画像）
_TITLE_STRIP = re.compile(
    r"[（(【\[][^）)】\]]*[）)】\]]|急聘|急招|热招|高薪|双休|"
    r"(北京|上海|深圳|广州|杭州|成都|武汉|南京|西安|苏州|长沙|重庆|天津|合肥|厦门)[市]?|"
    r"高级|资深|专家|首席|初级|中级|助理|实习|校招|应届|Senior|Junior|Staff|Principal", re.I)


@lru_cache(maxsize=1)
def _cluster_name_map() -> dict[str, str]:
    """簇名 -> 规范岗位名（只读，调用方不要就地修改返回的 dict）。"""
    p = Path(__file__).resolve().parents[2] / "data" / "collect" / "title_map.json"
    try:
        return json.loads(p.read_text("utf-8"))["cluster_job_name"]
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _cluster_category_map() -> dict[str, str]:
    """规范岗位名 -> 钉住的技术栈领域（title_map.json 里未声明的岗位不钉，走 LLM 解析）。

    岗位的技术栈领域原本取「代表 JD 解析结果里的 category」，而同一个簇里不同 JD 的
    category 会漂移（同是工业互联网岗，有的 JD 被判成人工智能、有的判成智能系统），
    取代表条目等于随机挑一个——实测新建的工业互联网/车联网/智能硬件三个岗位全部
    没落到物联网。领域归属是稳定的人工判断，属于和「簇 -> 规范岗位名」同一层的
    策展信息，声明比推断可靠。
    """
    p = Path(__file__).resolve().parents[2] / "data" / "collect" / "title_map.json"
    try:
        raw = json.loads(p.read_text("utf-8"))
        names = raw.get("cluster_job_name", {})
        # 键从「簇名」翻成「规范岗位名」，聚合阶段拿到的是后者
        return {names[k]: v for k, v in raw.get("cluster_category", {}).items()
                if k in names}
    except Exception:
        return {}


# 平台 -> 时间切片。三个切片是演化链的观测点：2018 与 2024 各为一个公开数据集，
# 2026 是四个现网来源。用平台名而不是 publish_date 判切片，因为历史数据集里
# 相当一部分行没有可用发布日期。
_ERA_BY_PLATFORM = {"dataset_51job2018": "2018", "dataset_aijob2024": "2024"}


def _era_stats(items: list[dict]) -> dict:
    """按时间切片统计该岗位簇的 JD 数，落进 job.source_summary。

    为的是让岗位详情页能如实说出「本岗位在 2018 / 2024 历史语料中检索到 0 条 JD」——
    新兴岗位没有跨切片演化记录，不是功能没做，而是历史语料里根本不存在这个岗位，
    这本身就是新兴性最硬的量化证据。预计算在这里，避免详情页每次多查一次库。
    """
    counts: Counter = Counter()
    earliest = None
    for it in items:
        row = it["row"]
        platform = getattr(row, "platform", None) or row.source or ""
        counts[_ERA_BY_PLATFORM.get(platform, "2026")] += 1
        pd = getattr(row, "publish_date", None)
        if pd and (earliest is None or pd < earliest):
            earliest = pd
    return {"era_counts": {k: counts.get(k, 0) for k in ("2018", "2024", "2026")},
            "earliest_jd": earliest.strftime("%Y-%m-%d") if earliest else None}


# 检索词表之外的同义写法：真实标题里常见、但 queries.json 里没有的说法。
# 键=标题中的关键词（小写），值=title_map.json 里的簇名。只补真正缺的，
# 不要加 "agent"/"cv" 这类过短的通用词——会把"销售Agent"之类误判进技术岗簇。
_KEYWORD_ALIASES = {
    "大语言模型": "大模型算法", "语言大模型": "大模型算法", "llm": "大模型算法",
    "生成式大模型": "大模型算法", "预训练大模型": "大模型算法",
}


@lru_cache(maxsize=1)
def _keyword_cluster_map() -> tuple[tuple[str, str], ...]:
    """检索词 -> 规范岗位名，按关键词长度倒序（长词优先，避免"数据"抢走"大数据平台"）。

    用于**没有 cluster_hint** 的来源（如按企业目录全量采集的招聘官网）：
    这类数据不是按检索词采的，只能从标题反推所属岗位簇。
    结果只依赖磁盘上的两个映射文件，进程内缓存（几千条 JD 的循环里每条都会调用）。
    """
    qp = Path(__file__).resolve().parents[2] / "data" / "collect" / "queries.json"
    names = _cluster_name_map()
    pairs: list[tuple[str, str]] = []
    try:
        for cluster, kws in json.loads(qp.read_text("utf-8"))["queries"].items():
            job = names.get(cluster)
            if not job:
                continue
            for kw in kws:
                pairs.append((kw.lower(), job))
            pairs.append((cluster.lower(), job))
    except Exception:
        return ()
    for kw, cluster in _KEYWORD_ALIASES.items():
        job = names.get(cluster)
        if job:
            pairs.append((kw.lower(), job))
    # 岗位规范名本身也是最强的匹配词
    for job in set(names.values()):
        pairs.append((job.lower(), job))
    return tuple(sorted(set(pairs), key=lambda x: -len(x[0])))


_INTERNAL_TITLE_MAP = {
    "java开发工程师": "Java开发工程师", "java工程师": "Java开发工程师",
    "机器学习工程师": "机器学习工程师", "算法工程师": "算法工程师",
    "大数据开发工程师": "大数据开发工程师", "数据工程师": "大数据开发工程师",
    "数据分析师": "数据分析师", "深度学习工程师": "深度学习工程师",
    "nlp工程师": "自然语言处理工程师", "自然语言处理工程师": "自然语言处理工程师",
    "计算机视觉工程师": "计算机视觉工程师", "cv工程师": "计算机视觉工程师",
    "物联网开发工程师": "物联网开发工程师", "嵌入式工程师": "嵌入式工程师",
    "后端开发工程师": "后端开发工程师", "python开发工程师": "Python开发工程师",
}


@lru_cache(maxsize=1)
def canonical_job_names() -> frozenset[str]:
    """发布图谱允许建图的岗位名——以策展后的 title_map 为唯一白名单。

    `job_resolution.established_job_titles()` 是**查询解析词表**，覆盖前端开发、
    Python、C++、通用算法、测试切片、ETL 等自由文本入口；它不是发布岗位清单。
    把它并进建图白名单，会让全量重建从 32 个策展岗位膨胀成 41 个岗位，新增的
    多数只有 1–14 条 JD（自动化测试只有 1 条且置信度 0）。查询解析可以认识这些
    标题，但只有 title_map 明确策展的岗位才能独立进入公开图谱。
    """
    return frozenset(_cluster_name_map().values())


@lru_cache(maxsize=8192)
def title_key(title: str, cluster_hint: str | None = None,
              track: str | None = None, industry: str | None = None) -> str:
    """岗位标题归一化为聚类键；明确别名优先，检索簇仅作回退。"""
    # Explicit, governed aliases beat a contradictory collection query.  A
    # Java title returned by an LLM query is a low-relevance hit, not an LLM JD.
    resolved = resolve_job_query(title)
    if resolved.is_established and not resolved.requires_disambiguation:
        return resolved.canonical_title
    if cluster_hint:
        mapped = _cluster_name_map().get(cluster_hint)
        # 检索词命中的是 JD 正文、不一定是标题，所以 cluster_hint 不能无条件采信：
        # 小鹏「标准芯片-采购资深经理」正文里出现「车联网」就会被打上该 hint，
        # 于是拿采购经理的 JD 给车联网岗位供能力证据。
        # 这里只挡**明显非研发**的标题（NON_ENG），不要求标题正向命中该簇的领域词——
        # import_raw --filter-title 用的那份正向白名单是对「新采集行」验证过的口径，
        # 拿到这里对全量语料重新归簇会误杀一大片：实测把大模型推理优化 2026 年语料
        # 从 49 条砍到 8 条、工业互联网从 36 条砍到 7 条，全库 avg 置信度不升反降。
        if mapped and not NON_ENG.search(title or ""):
            return mapped
    if resolved.requires_disambiguation:
        if track == "hardware":
            return "硬件系统测试工程师"
        if industry in {"automotive", "medical_device", "manufacturing"}:
            return "行业测试工程师"
        if track == "software":
            return "系统测试工程师"
    t = (title or "").strip()
    t_clean = _TITLE_STRIP.sub("", t).strip("-—_ ")
    hit = _INTERNAL_TITLE_MAP.get(t_clean.lower()) or _INTERNAL_TITLE_MAP.get(t.lower())
    if hit:
        return hit
    # 无簇提示时按关键词反推所属岗位簇（长词优先）
    low = t.lower()
    # 但明显非研发的标题不参与反推：「资深产品经理（智能座舱方向）」标题里确实有「座舱」，
    # 靠关键词会被推回车联网系统工程师，等于绕过了上面刚做的 cluster_hint 校验。
    # 既定岗位名在前面就已返回，走到这里还命中 NON_ENG 的，宁可让它自成一键。
    if NON_ENG.search(t):
        return t_clean or t
    for kw, job in _keyword_cluster_map():
        if kw and kw in low:
            return job
    return t_clean or t


def _dimension(value: str | None, allowed: tuple[str, ...], fallback: str) -> str:
    """Accept only governed dimension values from imported records."""
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in allowed else fallback


def _resolved_dimensions(jd: dict, resolution) -> dict[str, str | None]:
    """Resolve import metadata without allowing a title/track contradiction."""
    raw_track = str(jd.get("track") or "").strip().casefold()
    if resolution.requires_disambiguation:
        track = raw_track if raw_track in TRACKS else None
    else:
        supplied_track = _dimension(raw_track, TRACKS, resolution.track)
        track = resolution.track if resolution.is_established else supplied_track
    industry = _dimension(jd.get("industry"), INDUSTRIES, resolution.industry)
    recruitment = _dimension(
        jd.get("recruitment_type"), RECRUITMENT_TYPES, resolution.recruitment_type)
    seniority = _dimension(
        jd.get("inferred_level"), SENIORITIES, resolution.seniority)
    return {
        "track": track,
        "industry": industry,
        "recruitment_type": recruitment,
        "inferred_level": None if seniority == "unspecified" else seniority,
    }


def ingest_one(db: Session, jd: dict, dedup_pool: list[dict]) -> models.RawJD:
    """单条 JD 入库 + 去重/抄袭/时滞检测。dedup_pool 累积已入库的 (id, simhash, hash)。"""
    text = jd.get("raw_text", "")
    h = cleaning.exact_hash(text)
    sh = cleaning.simhash(text)
    pub = jd.get("publish_date")
    if isinstance(pub, str):
        try:
            pub = datetime.fromisoformat(pub)
        except ValueError:
            pub = None
    lag = cleaning.lag_days(pub)

    is_dup, dup_of = False, None
    for prev in dedup_pool:
        if prev["hash"] == h or cleaning.is_near_duplicate(sh, prev["simhash"], threshold=2):
            is_dup, dup_of = True, prev["id"]
            break

    resolution = resolve_job_query(jd.get("job_title", ""))
    dimensions = _resolved_dimensions(jd, resolution)
    employer = get_or_create_employer(db, jd.get("company"))
    kwargs = dict(
        job_title=jd.get("job_title", ""), company=jd.get("company", ""),
        location=jd.get("location", ""), source=jd.get("source", ""),
        source_url=jd.get("source_url", ""), raw_text=text, publish_date=pub,
        dedup_hash=h, simhash=str(sh), is_duplicate=is_dup, duplicate_of=dup_of,
        lag_days=lag, quality_score=cleaning.quality_score(text, lag, is_dup))
    optional = {
        "track": dimensions["track"],
        "industry": dimensions["industry"],
        "recruitment_type": dimensions["recruitment_type"],
        "employer_id": getattr(employer, "id", None),
        "inferred_level": dimensions["inferred_level"],
    }
    for field, value in optional.items():
        if hasattr(models.RawJD, field):
            kwargs[field] = value
    row = models.RawJD(**kwargs)
    db.add(row)
    db.flush()
    dedup_pool.append({"id": row.id, "hash": h, "simhash": sh})
    return row


def build_graph_from_dataset(db: Session, dataset: list[dict], parse_fn=None,
                             progress=None, max_workers: int = 5,
                             cache_path: str | None = None,
                             only_jobs: set[str] | None = None,
                             force: bool = False) -> dict:
    """完整 pipeline：入库清洗 → 解析 → 聚类聚合 → 落库岗位图谱。

    dataset: [{job_title, company, raw_text, source, source_url, publish_date, ...}]
    parse_fn: JD 解析函数（默认大模型；测试可注入规则解析）。
    max_workers: 解析并发数（解析不触库，可并发以缩短时长）。
    cache_path: 若提供，将解析结果按文本 hash 落盘，供评测复用。
    """
    parse_fn = parse_fn or extraction.parse_jd
    dedup_pool: list[dict] = []
    clusters: dict[str, list[dict]] = defaultdict(list)

    # 1) 入库 + 清洗
    for i, jd in enumerate(dataset):
        row = ingest_one(db, jd, dedup_pool)
        resolution = resolve_job_query(jd.get("job_title", ""))
        key = title_key(
            jd.get("job_title", ""), jd.get("cluster_hint"),
            getattr(row, "track", None), getattr(row, "industry", None))
        if resolution.requires_disambiguation and not (
                jd.get("cluster_hint") or jd.get("track") or jd.get("industry")):
            key = f"其他-待消歧-{resolution.canonical_title}"
        clusters[key].append({"row": row, "jd": jd})
        if progress:
            progress("ingest", i + 1, len(dataset))
    db.commit()

    # 2) 解析（仅非重复 JD，并发执行以缩短时长）
    to_parse = [it["row"] for items in clusters.values() for it in items if not it["row"].is_duplicate]
    parsed_cache: dict[int, dict] = {}
    text_cache: dict[str, dict] = {}
    done = [0]

    def _do(row):
        p = parse_fn(row.raw_text)
        done[0] += 1
        if progress:
            progress("parse", done[0], len(to_parse))
        return row.id, row.raw_text, p

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for rid, text, p in ex.map(_do, to_parse):
            parsed_cache[rid] = p
            text_cache[cleaning.exact_hash(text)] = p

    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(text_cache, f, ensure_ascii=False)

    # 3) 每个岗位聚类：通胀检测 + 交叉验证聚合 + 落库
    results, skipped = _aggregate_clusters(db, clusters, parsed_cache,
                                           only_jobs=only_jobs, force=force)
    db.commit()
    return {"jobs_built": len(results), "details": results,
            "total_jds": len(dataset), "skipped_evolved": skipped,
            "duplicates": db.query(models.RawJD).filter(models.RawJD.is_duplicate == True).count()}  # noqa: E712


def _row_snapshot(row, model) -> str:
    """Stable full-row serialization used by the evidence-only write guard."""
    values = {column.name: getattr(row, column.name) for column in model.__table__.columns}
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":"))


def _query_snapshot(query, model) -> dict:
    rows = query.order_by(model.id).all()
    serialized = {row.id: _row_snapshot(row, model) for row in rows}
    digest = hashlib.sha256("\n".join(
        f"{row_id}:{serialized[row_id]}" for row_id in sorted(serialized)
    ).encode("utf-8")).hexdigest()
    return {"count": len(rows), "checksum": digest, "rows": serialized}


def _evidence_refresh_immutable_snapshot(db: Session, job_id: int) -> dict:
    """Snapshot every graph/version/level fact that evidence refresh must not touch.

    ``Skill`` is guarded globally rather than only through this job's current relations:
    evidence-only mode must never create or edit a skill anywhere.  The other tables are
    scoped to the refreshed job so the checksum also covers v1 jobs with no evolution rows.
    """
    versions = db.query(models.JobVersion).filter(models.JobVersion.job_id == job_id).all()
    version_ids = [row.id for row in versions]
    version_skill_query = db.query(models.JobVersionSkill)
    if version_ids:
        version_skill_query = version_skill_query.filter(
            models.JobVersionSkill.job_version_id.in_(version_ids))
    else:
        version_skill_query = version_skill_query.filter(models.JobVersionSkill.id < 0)
    return {
        "job": _query_snapshot(db.query(models.Job).filter(models.Job.id == job_id), models.Job),
        "skill": _query_snapshot(db.query(models.Skill), models.Skill),
        "job_skill": _query_snapshot(db.query(models.JobSkill).filter(
            models.JobSkill.job_id == job_id), models.JobSkill),
        # Global by design: the all-job release mode promises the complete level-profile
        # projection (not merely this job's rows) has an exact count/checksum match.
        "job_level_skill": _query_snapshot(db.query(models.JobLevelSkill),
                                             models.JobLevelSkill),
        "capability_change": _query_snapshot(db.query(models.CapabilityChange).filter(
            models.CapabilityChange.job_id == job_id), models.CapabilityChange),
        "job_version": _query_snapshot(db.query(models.JobVersion).filter(
            models.JobVersion.job_id == job_id), models.JobVersion),
        "job_version_skill": _query_snapshot(version_skill_query, models.JobVersionSkill),
        "evolution_run": _query_snapshot(db.query(models.EvolutionRun).filter(
            models.EvolutionRun.job_id == job_id), models.EvolutionRun),
    }


def _assert_immutable_snapshot(before: dict, after: dict) -> None:
    for table, expected in before.items():
        actual = after[table]
        if (actual["count"], actual["checksum"]) != (
                expected["count"], expected["checksum"]):
            raise AssertionError(
                f"evidence-only refresh mutated {table}: "
                f"{expected['count']}/{expected['checksum']} -> "
                f"{actual['count']}/{actual['checksum']}")


def _parsed_mentions(parsed: dict, key: str, normalizer) -> dict[str, str]:
    mentions: dict[str, str] = {}
    for value in parsed.get(key, []) or []:
        item = {"name": value} if isinstance(value, str) else (value or {})
        name = normalizer(str(item.get("name") or ""))
        if name:
            mentions.setdefault(name, str(item.get("raw") or item.get("name") or name))
    return mentions


def _observed_at(row: models.RawJD):
    return row.publish_date or row.collected_at


def _date_rank(value) -> tuple[int, int, int, int, int, int]:
    if not value:
        return (0, 0, 0, 0, 0, 0)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second)


def _select_diverse_evidence(candidates: list[dict], existing_rows: list[tuple],
                             employer_parents: dict[int, int | None], slots: int) -> list[dict]:
    """Fill evidence slots by unseen year, then unseen employer, then recency/quality."""
    if slots <= 0:
        return []
    covered_years: set[int] = set()
    covered_employers: set[str] = set()
    seen_raw_ids: set[int] = set()
    for evidence, raw in existing_rows:
        if evidence.raw_jd_id is not None:
            seen_raw_ids.add(evidence.raw_jd_id)
        if raw is None or raw.is_duplicate or raw.duplicate_of is not None:
            continue
        observed = _observed_at(raw)
        if observed:
            covered_years.add(observed.year)
        employer = employer_independence_key({
            "employer_id": raw.employer_id,
            "employer_parent_id": employer_parents.get(raw.employer_id),
            "company": raw.company,
        })
        if employer:
            covered_employers.add(employer)

    pool = {candidate["raw_jd_id"]: candidate for candidate in candidates
            if candidate["raw_jd_id"] not in seen_raw_ids}
    selected = []
    while pool and len(selected) < slots:
        def rank(candidate):
            year = candidate["year"]
            employer = candidate["employer"]
            return (
                int(year is not None and year not in covered_years),
                int(bool(employer) and employer not in covered_employers),
                _date_rank(candidate["observed_at"]),
                candidate["quality"], candidate["authority"],
                -candidate["raw_jd_id"],
            )

        chosen = max(pool.values(), key=rank)
        pool.pop(chosen["raw_jd_id"])
        selected.append(chosen)
        if chosen["year"] is not None:
            covered_years.add(chosen["year"])
        if chosen["employer"]:
            covered_employers.add(chosen["employer"])
    return selected


def _plan_job_evidence_refresh(db: Session, job: models.Job, items: list[dict],
                               parsed_cache: dict[int, dict]) -> tuple[list[dict], dict]:
    relations = db.query(models.JobSkill, models.Skill).join(
        models.Skill, models.Skill.id == models.JobSkill.skill_id).filter(
        models.JobSkill.job_id == job.id).order_by(models.JobSkill.id).all()
    relation_ids = [relation.id for relation, _ in relations]
    evidence_rows: dict[int, list[tuple]] = defaultdict(list)
    if relation_ids:
        for evidence, raw in db.query(models.Evidence, models.RawJD).outerjoin(
                models.RawJD, models.RawJD.id == models.Evidence.raw_jd_id).filter(
                models.Evidence.job_skill_id.in_(relation_ids)).all():
            evidence_rows[evidence.job_skill_id].append((evidence, raw))

    rows = []
    coarse_mentions: dict[int, dict[str, str]] = {}
    fine_mentions: dict[int, dict[str, str]] = {}
    for item in items:
        row = item["row"]
        parsed = parsed_cache.get(row.id)
        if not parsed or row.is_duplicate or row.duplicate_of is not None:
            continue
        coarse = _parsed_mentions(parsed, "required_skills", normalize_skill)
        for name, raw_name in _parsed_mentions(
                parsed, "bonus_skills", normalize_skill).items():
            coarse.setdefault(name, raw_name)
        fine = _parsed_mentions(parsed, "fine_skills", normalize_fine_skill)
        coarse_mentions[row.id] = coarse
        fine_mentions[row.id] = fine
        rows.append(row)

    all_raws = rows + [raw for values in evidence_rows.values()
                       for _, raw in values if raw is not None]
    employer_ids = {raw.employer_id for raw in all_raws if raw.employer_id}
    employer_parents = dict(db.query(models.Employer.id, models.Employer.parent_id).filter(
        models.Employer.id.in_(employer_ids)).all()) if employer_ids else {}

    plans = []
    matched_raw_ids: set[int] = set()
    at_cap = 0
    for relation, skill in relations:
        normalizer = normalize_fine_skill if skill.parent_id else normalize_skill
        target = normalizer(skill.normalized_name or skill.name or "")
        candidates = []
        for row in rows:
            mentions = fine_mentions[row.id] if skill.parent_id else coarse_mentions[row.id]
            if target not in mentions:
                continue
            matched_raw_ids.add(row.id)
            observed = _observed_at(row)
            employer = employer_independence_key({
                "employer_id": row.employer_id,
                "employer_parent_id": employer_parents.get(row.employer_id),
                "company": row.company,
            })
            candidates.append({
                "raw_jd_id": row.id,
                "source_type": "jd",
                "source": row.platform or row.source or "unknown",
                "snippet": mentions[target],
                "observed_at": observed,
                "year": observed.year if observed else None,
                "employer": employer,
                "quality": float(row.quality_score or 0.0),
                "authority": float(row.source_authority or 0.0),
            })
        existing = evidence_rows.get(relation.id, [])
        remaining = max(0, graph_service.MAX_EVIDENCE_PER_SKILL - len(existing))
        if not remaining:
            at_cap += 1
        selected = _select_diverse_evidence(
            candidates, existing, employer_parents, remaining)
        if selected:
            plans.append({"job_skill_id": relation.id, "skill_name": skill.name,
                          "selected": selected, "remaining": remaining})

    year_counts = Counter(candidate["year"] for plan in plans for candidate in plan["selected"])
    summary = {
        "relations_considered": len(relations),
        "relations_with_new_evidence": len(plans),
        "matching_jds": len(matched_raw_ids),
        "at_cap_relations": at_cap,
        "estimated_new_evidence": sum(len(plan["selected"]) for plan in plans),
        "selected_years": {str(year) if year is not None else "unknown": count
                           for year, count in sorted(year_counts.items(),
                                                     key=lambda item: (item[0] is None,
                                                                       item[0] or 0))},
    }
    return plans, summary


def refresh_job_evidence(db: Session, job: models.Job, items: list[dict],
                         parsed_cache: dict[int, dict], dry_run: bool = False) -> dict:
    """Append exact-match JD evidence without changing any existing graph facts.

    The job only needs to exist; v1/non-evolved jobs receive the same protection as evolved
    jobs.  This deliberately does not invoke confidence_batch or leveling because either can
    update governed Job/JobSkill/JobLevelSkill state outside the insert-only boundary.
    """
    persisted = db.get(models.Job, job.id) if job.id is not None else None
    if persisted is None or persisted.name != job.name:
        raise ValueError("evidence-only refresh requires an existing persisted job")

    plans, summary = _plan_job_evidence_refresh(db, persisted, items, parsed_cache)
    result = {
        "job_id": job.id, "job_name": job.name, "dry_run": dry_run,
        **summary, "added_evidence": 0,
    }
    if dry_run:
        return result

    # Flush caller-owned cleaning fields before taking the immutable baseline. They are
    # outside this function's graph write, and are explicitly allowed by pipeline semantics.
    db.flush()
    before = _evidence_refresh_immutable_snapshot(db, job.id)
    relation_ids = [row[0] for row in db.query(models.JobSkill.id).filter(
        models.JobSkill.job_id == job.id).all()]
    evidence_query = db.query(models.Evidence)
    evidence_query = (evidence_query.filter(models.Evidence.job_skill_id.in_(relation_ids))
                      if relation_ids else evidence_query.filter(models.Evidence.id < 0))
    before_evidence = _query_snapshot(evidence_query, models.Evidence)

    savepoint = db.begin_nested()
    try:
        added = 0
        for plan in plans:
            payload = [{key: value for key, value in candidate.items()
                        if key in {"raw_jd_id", "source_type", "source", "snippet"}}
                       for candidate in plan["selected"]]
            added += graph_service.write_evidence(
                db, plan["job_skill_id"], payload, cap=plan["remaining"])
        db.flush()
        for plan in plans:
            evidence_count = db.query(models.Evidence).filter(
                models.Evidence.job_skill_id == plan["job_skill_id"]).count()
            if evidence_count > graph_service.MAX_EVIDENCE_PER_SKILL:
                raise AssertionError(
                    f"evidence cap exceeded for JobSkill {plan['job_skill_id']}: "
                    f"{evidence_count} > {graph_service.MAX_EVIDENCE_PER_SKILL}")
        after = _evidence_refresh_immutable_snapshot(db, job.id)
        _assert_immutable_snapshot(before, after)
        after_evidence = _query_snapshot(evidence_query, models.Evidence)
        if after_evidence["count"] != before_evidence["count"] + added:
            raise AssertionError("evidence-only refresh did not perform insert-only writes")
        for evidence_id, serialized in before_evidence["rows"].items():
            if after_evidence["rows"].get(evidence_id) != serialized:
                raise AssertionError(f"existing Evidence {evidence_id} was mutated")
        if added != summary["estimated_new_evidence"]:
            raise AssertionError(
                f"evidence refresh estimate/write mismatch: "
                f"{summary['estimated_new_evidence']} != {added}")
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        raise

    result["added_evidence"] = added
    result["safety"] = {
        table: {"count": snapshot["count"], "checksum": snapshot["checksum"]}
        for table, snapshot in before.items()
    }
    return result


def _aggregate_clusters(db: Session, clusters: dict, parsed_cache: dict[int, dict],
                        skip_existing: bool = False, only_jobs: set[str] | None = None,
                        force: bool = False, refresh_evidence_only: bool = False,
                        refresh_evolved_evidence: bool = False
                        ) -> tuple[list[dict], list[dict]]:
    """通胀检测 + 交叉验证聚合 + 落库（build_graph_from_dataset / _rows 共用主体）。

    返回 (results, skipped)。

    skip_existing=True 时跳过库里已存在的岗位，只补建缺失的岗位。
    用于"分时间切片建图"：先用历史切片建 v1 基线并跑完演化链，最后再补建
    历史切片里根本不存在的新岗位——此时不能让全量聚合覆盖已演化岗位的能力项
    （upsert_job 是整体重建能力关系，会把演化结果冲掉）。

    only_jobs 给定时只重建白名单内的岗位（其余簇连聚合都不跑）。这是"只修某几个
    岗位"的正道：此前想重建单个岗位只能全量重跑，而全量重跑会冲掉所有已演化岗位。

    force=False 时对已跑过演化的岗位（rebuild_conflict）拒绝重建并记入 skipped。
    refresh_evidence_only=True 是更严格的发布模式：所有既有岗位（包括 v1）都只追加
    精确匹配 Evidence，缺失岗位只报告、不创建；绝不会到达 upsert_job。
    refresh_evolved_evidence 是其兼容别名，在服务层同样采用全岗位安全语义。
    """
    evidence_only = refresh_evidence_only or refresh_evolved_evidence
    results, skipped = [], []
    employer_ids = {getattr(item["row"], "employer_id", None)
                    for items in clusters.values() for item in items
                    if getattr(item["row"], "employer_id", None)}
    employer_parents = dict(db.query(models.Employer.id, models.Employer.parent_id).filter(
        models.Employer.id.in_(employer_ids)).all()) if employer_ids else {}
    for key, items in clusters.items():
        if key.startswith("其他-"):
            continue  # 待映射簇不建图（清单由调用方输出）
        if only_jobs is not None and key not in only_jobs:
            continue
        existing = db.query(models.Job).filter(
            models.Job.slug == graph_service.slugify(key)).first()
        if evidence_only:
            if existing is None:
                skipped.append({
                    "reason": "missing_existing_job", "job_name": key,
                    "message": (f"「{key}」不在现有岗位库中；证据-only 模式已跳过，"
                                "不会静默创建岗位。"),
                })
                print(f"[ingest] 缺失岗位：{key}；证据-only 模式跳过且不创建")
            else:
                refresh = refresh_job_evidence(db, existing, items, parsed_cache)
                conflict = graph_service.rebuild_conflict(db, key)
                record = {
                    "reason": "existing_job_evidence_only",
                    "job_id": existing.id, "job_name": existing.name,
                    "version": existing.version,
                    "changes": conflict["changes"] if conflict else 0,
                    "active_capabilities": db.query(models.JobSkill).filter(
                        models.JobSkill.job_id == existing.id,
                        models.JobSkill.status == "active").count(),
                    "message": (f"「{existing.name}」仅追加精确匹配证据；"
                                "岗位及所有关系保持原样。"),
                    "evidence_refresh": refresh,
                }
                skipped.append(record)
                print(f"[ingest] 证据刷新：{key} 新增 {refresh['added_evidence']} 条；"
                      "岗位/能力/级别/版本关系均未重建")
            continue
        if skip_existing and existing:
            continue
        if not force:
            conflict = graph_service.rebuild_conflict(db, key)
            if conflict:
                print(f"[ingest] 跳过重建：{conflict['message']}")
                skipped.append(conflict)
                continue
        parsed_list = []
        skill_counts, all_skill_names = [], []
        for it in items:
            row = it["row"]
            p = parsed_cache.get(row.id)
            if not p:
                continue
            names = [s["name"] for s in p.get("required_skills", []) + p.get("bonus_skills", [])]
            skill_counts.append(len(names))
            all_skill_names.extend(names)
            parsed_list.append({"row": row, "parsed": p, "names": names})

        if not parsed_list:
            continue
        med = median(skill_counts) if skill_counts else 0
        freq = Counter(all_skill_names)
        cluster_size = len(parsed_list)
        rare_cut = max(1, cluster_size * 0.2)   # 出现在<20%簇内JD的技能视为"非共识/冷门"

        agg_input, source_meta = [], {}
        for pl in parsed_list:
            names = pl["names"]
            rare = sum(1 for n in names if freq[n] <= rare_cut)
            rare_ratio = rare / max(1, len(names))
            inflation = cleaning.detect_inflation(len(names), med, rare_ratio)
            pl["row"].inflation_flag = inflation
            agg_input.append({
                "required_skills": pl["parsed"].get("required_skills", []),
                "bonus_skills": pl["parsed"].get("bonus_skills", []),
                "fine_skills": pl["parsed"].get("fine_skills", []),
                "lag_days": pl["row"].lag_days, "is_duplicate": pl["row"].is_duplicate,
                "raw_jd_id": pl["row"].id, "source": pl["row"].source,
                "company": pl["row"].company,
            })
            source_meta[pl["row"].id] = {
                "platform": getattr(pl["row"], "platform", None) or pl["row"].source,
                "authority": getattr(pl["row"], "source_authority", None) or 0.6,
                "employer_id": getattr(pl["row"], "employer_id", None),
                "employer_parent_id": employer_parents.get(
                    getattr(pl["row"], "employer_id", None)),
                "company": pl["row"].company,
            }

        agg = hallucination.aggregate_capabilities(agg_input, source_meta=source_meta)
        rep = _representative_parse(parsed_list, key)
        rp = rep["parsed"]
        slices = _slice_summary(items)
        recruitment_values = [
            value for value in slices["recruitment_type_distribution"]
            if value != "unspecified"]
        recruitment_type = recruitment_values[0] if len(recruitment_values) == 1 else "mixed"
        job = graph_service.upsert_job(
            db, job_title=key,
            category=_cluster_category_map().get(key) or rp.get("category", "人工智能"),
            level=rp.get("level", "middle"),
            responsibilities=rp.get("core_responsibilities", []),
            scenarios=rp.get("typical_scenarios", []),
            capabilities=agg["capabilities"],
            # None = 保留库中现值：新兴岗位的 is_new/emergence_score 由 seed_new_jobs.py
            # 依据人社部文件单独标注，一律传 False 会把这份策展信息抹掉。
            is_new=None, emergence_score=None,
            summary=rp.get("summary", f"{key}（基于{agg['stats']['valid_jds']}条有效JD交叉验证构建）"),
            source_summary={"jd_count": len(items), **agg["stats"], **_era_stats(items),
                            **slices},
            track=slices["track"], industry=slices["industry"],
            recruitment_type=recruitment_type,
            with_embedding=False)
        db.commit()
        results.append({"job": key, "job_id": job.id, "stats": agg["stats"],
                        "confidence": job.confidence})
    return results, skipped


# Role-type nouns, longest first: a JD may sit in the right domain yet describe a different
# kind of work (a 项目管理 posting speaking for 自动驾驶算法工程师). Matching the job's own
# role noun keeps the representative both on-domain and on-role.
_ROLE_NOUNS = ("算法工程师", "开发工程师", "系统工程师", "测试工程师", "运维工程师",
               "数据工程师", "产品经理", "训练师", "架构师", "研究员", "工程师")


def _role_noun(name: str) -> str:
    for noun in _ROLE_NOUNS:
        if noun in name:
            return noun
    return ""


def _representative_parse(parsed_list: list[dict], cluster_key: str) -> dict:
    """Pick the JD whose own title actually belongs to this cluster, then the richest one.

    A job's summary / responsibilities / scenarios are copied verbatim from ONE parsed JD, so
    that choice defines the whole job page. Ranking by bullet count alone let a single verbose
    off-target JD define the job -- 自动驾驶算法工程师 ended up describing 金融信贷风控 and
    评分卡模型, 车联网系统工程师 described semiconductor procurement.

    The title is resolved WITHOUT ``cluster_hint`` on purpose. The hint records the search query
    a JD was collected under, and a query matching body text rather than the title is exactly how
    a 金融算法工程师 posting lands in the 自动驾驶 cluster; honouring it here would re-admit the
    very rows this guard exists to exclude (measured: 39 of 40, hint-trusting vs 1, title-only).
    Membership in the cluster is unchanged -- the JD still contributes its capabilities and
    evidence. This only decides which single JD gets to speak for the job. Bullet count breaks
    ties among titles that already resolve here, and the old behaviour is the fallback so a
    cluster is never left without metadata.
    """
    def bullets(item: dict) -> int:
        return len(item["parsed"].get("core_responsibilities", []))

    on_title = []
    for item in parsed_list:
        row = item.get("row")
        title = getattr(row, "job_title", None) or item.get("job_title") or ""
        if not title:
            continue
        try:
            if title_key(title) == cluster_key:
                on_title.append(item)
        except Exception:                      # never let title parsing break ingest
            continue
    pool = on_title or parsed_list
    noun = _role_noun(cluster_key)
    if noun:
        same_role = [item for item in pool
                     if noun in (getattr(item.get("row"), "job_title", None)
                                 or item.get("job_title") or "")]
        pool = same_role or pool
    return max(pool, key=bullets)


def _slice_summary(items: list[dict]) -> dict:
    """Expose the structured dimensions used by the cluster for audit/UI."""
    fields = ("track", "industry", "recruitment_type", "inferred_level")
    out = {}
    for field in fields:
        values = []
        for item in items:
            row = item["row"]
            value = getattr(row, field, None)
            if not value:
                resolution = resolve_job_query(getattr(row, "job_title", "") or "")
                value = {
                    "track": resolution.track,
                    "industry": resolution.industry,
                    "recruitment_type": resolution.recruitment_type,
                    "inferred_level": resolution.seniority,
                }[field]
            allowed = {
                "track": TRACKS,
                "industry": INDUSTRIES,
                "recruitment_type": RECRUITMENT_TYPES,
                "inferred_level": SENIORITIES,
            }[field]
            fallback = "general" if field == "industry" else "unspecified"
            values.append(_dimension(value, allowed, fallback))
        counts = Counter(values)
        out[f"{field}_distribution"] = dict(counts)
        out[field] = counts.most_common(1)[0][0] if counts else "unspecified"
    return out


def build_graph_from_rows(db: Session, rows: list[models.RawJD], parse_fn=None,
                          progress=None, max_workers: int = 5,
                          cache_path: str | None = None,
                          skip_existing: bool = False,
                          only_jobs: set[str] | None = None,
                          force: bool = False,
                          dry_run: bool = False,
                          refresh_evidence_only: bool = False,
                          refresh_evolved_evidence: bool = False) -> dict:
    """从已入库的真实采集 RawJD 行构建图谱（2026-07 整改：真实数据主入口）。

    与 build_graph_from_dataset 共用聚合主体；此入口补做 SimHash 近似去重、
    时滞计算，并按 cluster_hint（采集检索簇）优先聚类。

    dry_run=True 时默认做完清洗/聚类就返回；若启用 evidence-only（包括旧别名），
    会继续解析所有相关规范岗位簇以准确估算新增证据，但仍不写图谱。清洗结果本身幂等，
    两种 dry-run 都沿用既有语义提交这些字段。
    """
    evidence_only = refresh_evidence_only or refresh_evolved_evidence
    parse_fn = parse_fn or extraction.parse_jd
    _CANONICAL = canonical_job_names()
    # 1) 清洗补全：simhash / 近似去重 / 时滞
    dedup_pool: list[dict] = []
    clusters: dict[str, list[dict]] = defaultdict(list)
    unmapped = Counter()
    for i, row in enumerate(rows):
        text = row.raw_text or ""
        h = row.dedup_hash or cleaning.exact_hash(text)
        sh = cleaning.simhash(text)
        is_dup, dup_of = False, None
        for prev in dedup_pool:
            if prev["hash"] == h or cleaning.is_near_duplicate(sh, prev["simhash"], threshold=2):
                is_dup, dup_of = True, prev["id"]
                break
        row.simhash = str(sh)
        row.is_duplicate = is_dup
        row.duplicate_of = dup_of
        row.lag_days = cleaning.lag_days(row.publish_date)
        row.quality_score = cleaning.quality_score(text, row.lag_days, is_dup)
        dedup_pool.append({"id": row.id, "hash": h, "simhash": sh})
        key = title_key(
            row.job_title or "", getattr(row, "cluster_hint", None),
            getattr(row, "track", None), getattr(row, "industry", None))
        if key not in _CANONICAL:
            # 未落到任何规范岗位 → 待映射桶（不建图，只留台账）
            # 注意：判据是"是否规范岗位名"，不能用 key == 原标题——标题装饰词被剥离后
            # 两者本就不相等，会让噪声标题被误判成已映射，图里长出几百个一次性岗位。
            unmapped[key] += 1
            key = f"其他-{key}"
        clusters[key].append({"row": row, "jd": None})
        if progress:
            progress("clean", i + 1, len(rows))
    db.commit()

    def _dry_run_plan(parsed_cache: dict[int, dict] | None = None) -> dict:
        plan, skipped, refreshes, missing_existing_jobs = [], [], [], []
        for key, items in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
            if key.startswith("其他-"):
                continue
            if only_jobs is not None and key not in only_jobs:
                continue
            n_dup = sum(1 for it in items if it["row"].is_duplicate)
            existing = db.query(models.Job).filter(
                models.Job.slug == graph_service.slugify(key)).first()
            if evidence_only:
                if existing is None:
                    missing_existing_jobs.append(key)
                    skipped.append({"reason": "missing_existing_job", "job_name": key})
                    print(f"  [缺失] {key:24s} JD={len(items):4d} "
                          "证据-only 模式跳过且不创建")
                elif parsed_cache is not None:
                    refresh = refresh_job_evidence(
                        db, existing, items, parsed_cache, dry_run=True)
                    refreshes.append(refresh)
                    skipped.append({
                        "reason": "existing_job_evidence_only",
                        "job_id": existing.id, "job_name": existing.name,
                        "evidence_refresh": refresh,
                    })
                    print(f"  [刷证据] {key:22s} JD={len(items):4d} "
                          f"预计新增={refresh['estimated_new_evidence']:3d} "
                          f"年份={refresh['selected_years']}（不重建任何关系）")
                continue
            conflict = None if force else graph_service.rebuild_conflict(db, key)
            if conflict:
                print(f"  [跳过] {key:24s} JD={len(items):4d}  {conflict['message']}")
                skipped.append(conflict)
            else:
                plan.append({"job": key, "jds": len(items), "duplicates": n_dup,
                             **_era_stats(items)})
                print(f"  [重建] {key:24s} JD={len(items):4d} 重复={n_dup:3d} "
                      f"切片={_era_stats(items)['era_counts']}")
        missing = sorted(only_jobs - {p["job"] for p in plan} - {s["job_name"] for s in skipped}
                         ) if only_jobs else []
        if missing:
            print(f"  [警告] 白名单里这些岗位一条 JD 都没聚到：{missing}")
        return {"dry_run": True, "jobs_built": 0, "jobs_refreshed": len(refreshes),
                "details": [], "plan": plan, "skipped_evolved": skipped,
                "evidence_refreshes": refreshes,
                # Deprecated result keys retained for callers of the old CLI flag.
                "evolved_jobs_to_refresh": len(refreshes),
                "estimated_new_evidence": sum(
                    item["estimated_new_evidence"] for item in refreshes),
                "missing_existing_jobs": missing_existing_jobs,
                "missing": missing, "total_jds": len(rows),
                "unmapped_titles": dict(unmapped),
                "duplicates": sum(1 for r in rows if r.is_duplicate)}

    if dry_run and not evidence_only:
        return _dry_run_plan()

    # 2) 解析（仅非重复，缓存复用）。evidence-only 仍只解析映射到规范岗位的簇，
    # 非治理簇不会进入解析或写图路径。
    cache: dict[str, dict] = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))
    # 只解析将要重建的簇：白名单模式下没必要为不建的岗位掏 LLM 钱
    to_parse = [it["row"] for k, items in clusters.items()
                if not k.startswith("其他-") and (only_jobs is None or k in only_jobs)
                for it in items if not it["row"].is_duplicate]
    parsed_cache: dict[int, dict] = {}
    done = [0]

    def _do(row):
        h = cleaning.exact_hash(row.raw_text or "")
        p = cache.get(h) or parse_fn(row.raw_text)
        done[0] += 1
        if progress:
            progress("parse", done[0], len(to_parse))
        return row.id, h, p

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for rid, h, p in ex.map(_do, to_parse):
            parsed_cache[rid] = p
            cache[h] = p
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)

    if dry_run:
        return _dry_run_plan(parsed_cache)

    # 3) 聚合落库（共用主体）
    results, skipped = _aggregate_clusters(db, clusters, parsed_cache,
                                           skip_existing=skip_existing,
                                           only_jobs=only_jobs, force=force,
                                           refresh_evidence_only=evidence_only)
    db.commit()
    if unmapped:
        print("[ingest] 待映射标题（未建图，需补 title_map/cluster）：")
        for t, n in unmapped.most_common(20):
            print(f"    {t} ×{n}")
    refreshes = [item["evidence_refresh"] for item in skipped
                 if item.get("evidence_refresh")]
    missing_existing_jobs = [item["job_name"] for item in skipped
                             if item.get("reason") == "missing_existing_job"]
    return {"jobs_built": len(results), "jobs_refreshed": len(refreshes),
            "details": results, "total_jds": len(rows),
            "skipped_evolved": skipped, "evidence_refreshes": refreshes,
            # Deprecated result key retained for callers of the old CLI flag.
            "evolved_jobs_refreshed": len(refreshes),
            "missing_existing_jobs": missing_existing_jobs,
            "new_evidence": sum(item["added_evidence"] for item in refreshes),
            "unmapped_titles": dict(unmapped),
            "duplicates": sum(1 for r in rows if r.is_duplicate)}
