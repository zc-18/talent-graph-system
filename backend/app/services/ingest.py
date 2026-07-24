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


def _cluster_name_map() -> dict[str, str]:
    p = Path(__file__).resolve().parents[2] / "data" / "collect" / "title_map.json"
    try:
        return json.loads(p.read_text("utf-8"))["cluster_job_name"]
    except Exception:
        return {}


def title_key(title: str, cluster_hint: str | None = None) -> str:
    """岗位标题归一化为聚类键。真实数据优先用采集时的簇提示（cluster_hint）。"""
    if cluster_hint:
        mapped = _cluster_name_map().get(cluster_hint)
        if mapped:
            return mapped
    t = (title or "").strip()
    t_clean = _TITLE_STRIP.sub("", t).strip("-—_ ")
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
    return mapping.get(t_clean.lower(), mapping.get(t.lower(), t_clean or t))


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
    results = _aggregate_clusters(db, clusters, parsed_cache)
    db.commit()
    return {"jobs_built": len(results), "details": results,
            "total_jds": len(dataset),
            "duplicates": db.query(models.RawJD).filter(models.RawJD.is_duplicate == True).count()}  # noqa: E712


def _aggregate_clusters(db: Session, clusters: dict, parsed_cache: dict[int, dict]) -> list[dict]:
    """通胀检测 + 交叉验证聚合 + 落库（build_graph_from_dataset / _rows 共用主体）。"""
    results = []
    for key, items in clusters.items():
        if key.startswith("其他-"):
            continue  # 待映射簇不建图（清单由调用方输出）
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
    return results


def build_graph_from_rows(db: Session, rows: list[models.RawJD], parse_fn=None,
                          progress=None, max_workers: int = 5,
                          cache_path: str | None = None) -> dict:
    """从已入库的真实采集 RawJD 行构建图谱（2026-07 整改：真实数据主入口）。

    与 build_graph_from_dataset 共用聚合主体；此入口补做 SimHash 近似去重、
    时滞计算，并按 cluster_hint（采集检索簇）优先聚类。
    """
    parse_fn = parse_fn or extraction.parse_jd
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
        if not getattr(row, "cluster_hint", None) and key == (row.job_title or "").strip():
            # 无簇提示且标题未命中映射 → 待映射桶
            unmapped[key] += 1
            key = f"其他-{key}"
        clusters[key].append({"row": row, "jd": None})
        if progress:
            progress("clean", i + 1, len(rows))
    db.commit()

    # 2) 解析（仅非重复，缓存复用）
    cache: dict[str, dict] = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))
    to_parse = [it["row"] for k, items in clusters.items() if not k.startswith("其他-")
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
    results = _aggregate_clusters(db, clusters, parsed_cache)
    db.commit()
    if unmapped:
        print("[ingest] 待映射标题（未建图，需补 title_map/cluster）：")
        for t, n in unmapped.most_common(20):
            print(f"    {t} ×{n}")
    return {"jobs_built": len(results), "details": results, "total_jds": len(rows),
            "unmapped_titles": dict(unmapped),
            "duplicates": sum(1 for r in rows if r.is_duplicate)}
