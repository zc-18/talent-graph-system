"""数据接入与图谱构建编排（pipeline）。

把多源原始 JD 经「清洗去重→解析→交叉验证聚合→落库」构建岗位能力图谱。
支持两种入口：build_graph_from_dataset（json 数据集）与 build_graph_from_rows
（已入库的真实采集 RawJD 行，2026-07 整改）。
"""
from __future__ import annotations
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
from .taxonomy import normalize_skill

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
    """全部"可建图"的规范岗位名。不在此集合内的聚类键一律进待映射清单，不建图。"""
    return frozenset(_cluster_name_map().values()) | frozenset(_INTERNAL_TITLE_MAP.values())


@lru_cache(maxsize=8192)
def title_key(title: str, cluster_hint: str | None = None) -> str:
    """岗位标题归一化为聚类键。真实数据优先用采集时的簇提示（cluster_hint）。"""
    if cluster_hint:
        mapped = _cluster_name_map().get(cluster_hint)
        if mapped:
            return mapped
    t = (title or "").strip()
    t_clean = _TITLE_STRIP.sub("", t).strip("-—_ ")
    hit = _INTERNAL_TITLE_MAP.get(t_clean.lower()) or _INTERNAL_TITLE_MAP.get(t.lower())
    if hit:
        return hit
    # 无簇提示时按关键词反推所属岗位簇（长词优先）
    low = t.lower()
    for kw, job in _keyword_cluster_map():
        if kw and kw in low:
            return job
    return t_clean or t


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

    row = models.RawJD(
        job_title=jd.get("job_title", ""), company=jd.get("company", ""),
        location=jd.get("location", ""), source=jd.get("source", ""),
        source_url=jd.get("source_url", ""), raw_text=text, publish_date=pub,
        dedup_hash=h, simhash=str(sh), is_duplicate=is_dup, duplicate_of=dup_of,
        lag_days=lag, quality_score=cleaning.quality_score(text, lag, is_dup))
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
        key = title_key(jd.get("job_title", ""))
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


def _aggregate_clusters(db: Session, clusters: dict, parsed_cache: dict[int, dict],
                        skip_existing: bool = False, only_jobs: set[str] | None = None,
                        force: bool = False) -> tuple[list[dict], list[dict]]:
    """通胀检测 + 交叉验证聚合 + 落库（build_graph_from_dataset / _rows 共用主体）。

    返回 (results, skipped)。

    skip_existing=True 时跳过库里已存在的岗位，只补建缺失的岗位。
    用于"分时间切片建图"：先用历史切片建 v1 基线并跑完演化链，最后再补建
    历史切片里根本不存在的新岗位——此时不能让全量聚合覆盖已演化岗位的能力项
    （upsert_job 是整体重建能力关系，会把演化结果冲掉）。

    only_jobs 给定时只重建白名单内的岗位（其余簇连聚合都不跑）。这是"只修某几个
    岗位"的正道：此前想重建单个岗位只能全量重跑，而全量重跑会冲掉所有已演化岗位。

    force=False 时对已跑过演化的岗位（rebuild_conflict）拒绝重建并记入 skipped。
    """
    results, skipped = [], []
    for key, items in clusters.items():
        if key.startswith("其他-"):
            continue  # 待映射簇不建图（清单由调用方输出）
        if only_jobs is not None and key not in only_jobs:
            continue
        if skip_existing and db.query(models.Job).filter(
                models.Job.slug == graph_service.slugify(key)).first():
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
            })
            source_meta[pl["row"].id] = {
                "platform": getattr(pl["row"], "platform", None) or pl["row"].source,
                "authority": getattr(pl["row"], "source_authority", None) or 0.6,
            }

        agg = hallucination.aggregate_capabilities(agg_input, source_meta=source_meta)
        # 岗位元信息取置信度最高的一条解析
        rep = max(parsed_list, key=lambda x: len(x["parsed"].get("core_responsibilities", [])))
        rp = rep["parsed"]
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
            source_summary={"jd_count": len(items), **agg["stats"], **_era_stats(items)},
            with_embedding=False)
        db.commit()
        results.append({"job": key, "job_id": job.id, "stats": agg["stats"],
                        "confidence": job.confidence})
    return results, skipped


def build_graph_from_rows(db: Session, rows: list[models.RawJD], parse_fn=None,
                          progress=None, max_workers: int = 5,
                          cache_path: str | None = None,
                          skip_existing: bool = False,
                          only_jobs: set[str] | None = None,
                          force: bool = False,
                          dry_run: bool = False) -> dict:
    """从已入库的真实采集 RawJD 行构建图谱（2026-07 整改：真实数据主入口）。

    与 build_graph_from_dataset 共用聚合主体；此入口补做 SimHash 近似去重、
    时滞计算，并按 cluster_hint（采集检索簇）优先聚类。

    dry_run=True 时做完清洗/聚类就返回，不解析、不落库图谱（清洗结果本身
    是幂等的，仍会提交）。用于重建前确认"到底会动哪几个岗位"。
    """
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
        key = title_key(row.job_title or "", getattr(row, "cluster_hint", None))
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

    if dry_run:
        plan, skipped = [], []
        for key, items in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
            if key.startswith("其他-"):
                continue
            if only_jobs is not None and key not in only_jobs:
                continue
            n_dup = sum(1 for it in items if it["row"].is_duplicate)
            conflict = None if force else graph_service.rebuild_conflict(db, key)
            if conflict:
                skipped.append(conflict)
                print(f"  [跳过] {key:24s} JD={len(items):4d}  {conflict['message']}")
            else:
                plan.append({"job": key, "jds": len(items), "duplicates": n_dup,
                             **_era_stats(items)})
                print(f"  [重建] {key:24s} JD={len(items):4d} 重复={n_dup:3d} "
                      f"切片={_era_stats(items)['era_counts']}")
        missing = sorted(only_jobs - {p["job"] for p in plan} - {s["job_name"] for s in skipped}
                         ) if only_jobs else []
        if missing:
            print(f"  [警告] 白名单里这些岗位一条 JD 都没聚到：{missing}")
        return {"dry_run": True, "jobs_built": 0, "details": [], "plan": plan,
                "skipped_evolved": skipped, "missing": missing,
                "total_jds": len(rows), "unmapped_titles": dict(unmapped),
                "duplicates": sum(1 for r in rows if r.is_duplicate)}

    # 2) 解析（仅非重复，缓存复用）
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

    # 3) 聚合落库（共用主体）
    results, skipped = _aggregate_clusters(db, clusters, parsed_cache,
                                           skip_existing=skip_existing,
                                           only_jobs=only_jobs, force=force)
    db.commit()
    if unmapped:
        print("[ingest] 待映射标题（未建图，需补 title_map/cluster）：")
        for t, n in unmapped.most_common(20):
            print(f"    {t} ×{n}")
    return {"jobs_built": len(results), "details": results, "total_jds": len(rows),
            "skipped_evolved": skipped,
            "unmapped_titles": dict(unmapped),
            "duplicates": sum(1 for r in rows if r.is_duplicate)}
