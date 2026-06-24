"""数据接入与图谱构建编排（pipeline）。

把多源原始 JD 经「清洗去重→解析→交叉验证聚合→落库」构建岗位能力图谱。
"""
from __future__ import annotations
import json
import os
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, Counter
from datetime import datetime
from statistics import median
from sqlalchemy.orm import Session
from .. import models
from . import cleaning, extraction, hallucination, graph_service
from .taxonomy import normalize_skill


def title_key(title: str) -> str:
    """岗位标题归一化为聚类键。"""
    t = (title or "").strip()
    mapping = {
        "java开发工程师": "Java开发工程师", "java工程师": "Java开发工程师",
        "机器学习工程师": "机器学习工程师", "算法工程师": "算法工程师",
        "大数据开发工程师": "大数据开发工程师", "数据工程师": "大数据开发工程师",
        "数据分析师": "数据分析师", "深度学习工程师": "深度学习工程师",
        "nlp工程师": "自然语言处理工程师", "自然语言处理工程师": "自然语言处理工程师",
        "计算机视觉工程师": "计算机视觉工程师", "cv工程师": "计算机视觉工程师",
        "物联网开发工程师": "物联网开发工程师", "嵌入式工程师": "嵌入式工程师",
        "后端开发工程师": "后端开发工程师", "python开发工程师": "Python开发工程师",
    }
    return mapping.get(t.lower(), t)


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
                             cache_path: str | None = None) -> dict:
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
    results = []
    for key, items in clusters.items():
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

        agg_input = []
        for pl in parsed_list:
            names = pl["names"]
            rare = sum(1 for n in names if freq[n] <= rare_cut)
            rare_ratio = rare / max(1, len(names))
            inflation = cleaning.detect_inflation(len(names), med, rare_ratio)
            pl["row"].inflation_flag = inflation
            agg_input.append({
                "required_skills": pl["parsed"].get("required_skills", []),
                "bonus_skills": pl["parsed"].get("bonus_skills", []),
                "lag_days": pl["row"].lag_days, "is_duplicate": pl["row"].is_duplicate,
                "raw_jd_id": pl["row"].id, "source": pl["row"].source,
            })

        agg = hallucination.aggregate_capabilities(agg_input)
        # 岗位元信息取置信度最高的一条解析
        rep = max(parsed_list, key=lambda x: len(x["parsed"].get("core_responsibilities", [])))
        rp = rep["parsed"]
        job = graph_service.upsert_job(
            db, job_title=key, category=rp.get("category", "人工智能"),
            level=rp.get("level", "middle"),
            responsibilities=rp.get("core_responsibilities", []),
            scenarios=rp.get("typical_scenarios", []),
            capabilities=agg["capabilities"], is_new=False,
            summary=rp.get("summary", f"{key}（基于{agg['stats']['valid_jds']}条有效JD交叉验证构建）"),
            source_summary={"jd_count": len(items), **agg["stats"]},
            with_embedding=False)
        db.commit()
        results.append({"job": key, "job_id": job.id, "stats": agg["stats"],
                        "confidence": job.confidence})

    db.commit()
    return {"jobs_built": len(results), "details": results,
            "total_jds": len(dataset),
            "duplicates": sum(1 for d in dedup_pool for _ in [0] if False) or
                          db.query(models.RawJD).filter(models.RawJD.is_duplicate == True).count()}  # noqa: E712
