# -*- coding: utf-8 -*-
"""从简历语料学习技能表述（意见⑧ 的"学习"落点）。

问题：岗位侧的归一化词典（taxonomy.SYNONYMS，164 条硬编码）是照着 JD 写的，
简历里的说法和 JD 不一样。实测 22 份学习集共 528 个不同表述，其中：
    60 个词典已认识 / 98 个与图谱节点同名 / 30 个只差大小写空格 /
    47 个与节点是包含关系 / 293 个图谱里根本没有（人才侧独有技能）
真正"学得到"的是中间那 77 个 —— 表述不同但指的是图谱里已有的技能。

**两级学习，爆炸半径不同：**
  Tier A 词典级：映射到 88 个粗粒度规范技能，并入 SYNONYMS。会影响全局归一化
                （JD 解析也走这条路），所以护栏最严。
  Tier B 节点级：映射到图谱里**已存在的技能节点**，只写 Skill.aliases 与
                NODE_ALIASES，**不碰 SYNONYMS**，因此 JD 主链路零风险。
  Tier C 未命中：不是别名，是"人才侧有、岗位侧没有"的技能，作为供需分析的产出记录。

三道准入护栏：
  ① 映射目标必须**已存在**（Tier A 是规范技能，Tier B 是图谱节点），不许凭空造；
  ② 键**不覆盖**硬编码 SYNONYMS 的已有条目，只做新增；
  ③ 至少 --min-count 份**不同简历**出现过，孤例只记 candidate 不启用。
留出集（holdout）不参与学习，只用于"学之前 vs 学之后"的对照评测。

用法（backend/ 下，先设 $env:DB_NAME='talent_graph_v3'）：
    uv run python -X utf8 data/learn_aliases.py --mark-holdout 8      # 划分留出集
    uv run python -X utf8 data/learn_aliases.py                        # 试算，不落库
    uv run python -X utf8 data/learn_aliases.py --apply                # 落库 + 写词典文件
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 学习阶段必须在"未加载学习结果"的干净词典上判断命中与否，否则第二次跑会自我确认
os.environ["TALENT_DISABLE_LEARNED_ALIASES"] = "1"

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services.taxonomy import (  # noqa: E402
    SYNONYMS, SKILL_CATEGORY, SOFT_SKILLS, normalize_skill,
)

OUT_FP = BACKEND / "data" / "learned_aliases.json"
_CJK = re.compile(r"[一-鿿]")
_RE_PREFIX = re.compile(r"^(熟练掌握|熟练使用|熟练运用|熟练|精通|掌握|了解|熟悉|会用|使用|具备|擅长)\s*")
_RE_SUFFIX = re.compile(r"(相关经验|实战经验|使用经验|开发经验|经验|能力|技术栈)$")
_BAD_TERM = re.compile(r"[，。；、！？,;!?]|\d{4}|年以上")
SEM_THRESHOLD = 0.90
MIN_LEN, MAX_LEN = 2, 24


def squeeze(t: str) -> str:
    return re.sub(r"[\s\-_.]", "", (t or "").lower())


def contains_token(term: str, name: str) -> bool:
    """name 是否作为**完整词**出现在 term 里。

    纯子串匹配会闹笑话：实测把 "Eclipse" 判成了图谱节点 "CLIP"（e-clip-se），
    把 "PLSQL" 判成 "SQL"。含拉丁字符的名字必须卡词边界；纯中文名不需要。
    """
    t, n = term.lower(), name.lower()
    if not n:
        return False
    if re.search(r"[a-z0-9]", n):
        return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t) is not None
    return n in t


# 机械型映射（只差大小写/空格/连字符）是可客观核验的，不是判断；
# 判断型映射（包含关系、语义相似）才需要"多份简历佐证"这道门槛。
MECHANICAL_METHODS = {"case_space", "node_case", "node_space"}


def clean_term(term: str) -> str:
    t = (term or "").strip().strip("·•-—_/\\|")
    t = _RE_PREFIX.sub("", t)
    t = _RE_SUFFIX.sub("", t)
    return t.strip()


def is_learnable(term: str) -> tuple[bool, str]:
    if not (MIN_LEN <= len(term) <= MAX_LEN):
        return False, "长度不合适"
    if _BAD_TERM.search(term):
        return False, "像句子片段而非技能名"
    if term.lower() in SYNONYMS:
        return False, "词典已有（护栏②）"
    if term in SKILL_CATEGORY or term in SOFT_SKILLS:
        return False, "本身就是规范名"
    if normalize_skill(term) != term:
        return False, "现有规则已能归一化"
    return True, ""


# ---------------- Tier A：映射到粗粒度规范技能 ----------------
_CANON = list(SKILL_CATEGORY) + list(SOFT_SKILLS)


def map_canonical(term: str) -> tuple[str, str] | None:
    sq = squeeze(term)
    for c in _CANON:                                   # 大小写/空格差异
        if squeeze(c) == sq:
            return c, "case_space"
    for k, v in SYNONYMS.items():
        if squeeze(k) == sq:
            return v, "case_space"
    hits = [c for c in _CANON if len(c) >= 3 and contains_token(term, c)]
    if hits:                                           # 整段包含规范名，取最长
        hits.sort(key=len, reverse=True)
        return hits[0], "containment"
    return None


# ---------------- Tier B：映射到图谱里已存在的技能节点 ----------------
def map_node(term: str, nodes: dict[str, str], nodes_sq: dict[str, str],
             node_names: list[str]) -> tuple[str, str] | None:
    """nodes: name.lower() -> 规范节点名；nodes_sq: squeeze(name) -> 规范节点名。"""
    low = term.lower()
    if low in nodes and nodes[low] != term:            # 只差大小写
        return nodes[low], "node_case"
    if squeeze(term) in nodes_sq and nodes_sq[squeeze(term)] != term:
        return nodes_sq[squeeze(term)], "node_space"
    hits = [n for n in node_names
            if len(n) >= 4 and n.lower() != low and contains_token(term, n)]
    if hits:
        hits.sort(key=len, reverse=True)
        return hits[0], "node_containment"
    return None


def map_by_semantic(terms: list[str]) -> dict[str, tuple[str, float]]:
    """BGE 语义对齐，**仅中文**：嵌入模型对纯英文返回退化向量（matching.py 同款坑）。"""
    from app import clients
    cjk_terms = [t for t in terms if _CJK.search(t)]
    cjk_canon = [c for c in _CANON if _CJK.search(c)]
    if not cjk_terms or not cjk_canon:
        return {}
    tvecs = clients.embed_batch([f"岗位技能：{t}" for t in cjk_terms])
    cvecs = clients.embed_batch([f"岗位技能：{c}" for c in cjk_canon])
    out: dict[str, tuple[str, float]] = {}
    for t, tv in zip(cjk_terms, tvecs):
        best, best_sim = None, 0.0
        for c, cv in zip(cjk_canon, cvecs):
            sim = clients.cosine(tv, cv)
            if sim >= 0.999 and not (c in t or t in c):
                continue
            if sim > best_sim:
                best, best_sim = c, sim
        if best and best_sim >= SEM_THRESHOLD:
            out[t] = (best, best_sim)
    return out


def mark_holdout(db, n: int) -> None:
    """确定性划分留出集：按 id 排序后等距取 n 份，尽量覆盖不同来源。"""
    rows = db.query(models.TalentProfile).order_by(models.TalentProfile.id).all()
    if not rows:
        sys.exit("[learn] 库里没有人才画像，先跑 import_resumes.py")
    for r in rows:
        r.holdout = False
    step = max(1, len(rows) // n)
    picked = rows[::step][:n]
    for r in picked:
        r.holdout = True
    db.commit()
    by_src: dict[str, int] = {}
    for r in picked:
        by_src[r.source_type] = by_src.get(r.source_type, 0) + 1
    print(f"[learn] 留出集 {len(picked)}/{len(rows)} 份：{[r.code for r in picked]}")
    print(f"[learn] 留出集来源分布：{by_src}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-holdout", type=int, default=0, help="划分留出集份数后退出")
    ap.add_argument("--min-count", type=int, default=2, help="护栏③：至少出现在几份简历里")
    ap.add_argument("--apply", action="store_true", help="真正落库并写 learned_aliases.json")
    ap.add_argument("--allow-tier-a", action="store_true",
                    help="启用 Tier A（并入全局 SYNONYMS）。默认关闭：本轮 Tier A 只有 2 条，"
                         "且含 'SQL Server→SQL' 这类信息损失映射，不值得改动 JD 共用的全局词典")
    args = ap.parse_args()

    print(f"[learn] 目标库 = {settings.db_name}")
    db = SessionLocal()
    try:
        if args.mark_holdout:
            mark_holdout(db, args.mark_holdout)
            return

        # 图谱既有技能节点（Tier B 的映射目标）
        skill_rows = db.query(models.Skill).all()
        node_names = sorted({(s.normalized_name or s.name) for s in skill_rows if (s.normalized_name or s.name)})
        nodes = {n.lower(): n for n in node_names}
        nodes_sq = {squeeze(n): n for n in node_names}
        skill_by_name: dict[str, models.Skill] = {}
        for s in skill_rows:
            skill_by_name.setdefault(s.normalized_name or s.name, s)

        profiles = db.query(models.TalentProfile).filter(
            models.TalentProfile.holdout.is_(False)).all()
        held = db.query(models.TalentProfile).filter(
            models.TalentProfile.holdout.is_(True)).count()
        print(f"[learn] 学习集 {len(profiles)} 份，留出集 {held} 份（留出集不参与学习）")
        print(f"[learn] 图谱技能节点 {len(node_names)} 个")

        # 1) 归集候选表述
        counts: dict[str, set[int]] = {}
        total_terms = 0
        for p in profiles:
            for raw in (p.raw_skill_terms or []):
                total_terms += 1
                t = clean_term(raw)
                ok, _ = is_learnable(t)
                if ok:
                    counts.setdefault(t, set()).add(p.id)
        print(f"[learn] 原始表述 {total_terms} 条 → 候选待学 {len(counts)} 个")

        frequent = {t: ids for t, ids in counts.items() if len(ids) >= args.min_count}
        print(f"[learn] 达到 {args.min_count} 份门槛的 {len(frequent)} 个；"
              f"孤例 {len(counts) - len(frequent)} 个（机械型映射仍可采纳，判断型不采纳）")

        # 2) 两级映射：**先全量映射**，再按方法类型施加护栏③
        #    机械型（大小写/空格差异）客观可核验 → 1 份即可；
        #    判断型（包含关系/语义相似）→ 必须 ≥ min-count 份不同简历佐证。
        tier_a: dict[str, dict] = {}
        tier_b: dict[str, dict] = {}
        unmapped: list[str] = []
        gated: dict[str, str] = {}
        for t in sorted(counts):
            n_prof = len(counts[t])
            hit = map_canonical(t)
            tier, target, method = "A", None, None
            if hit:
                target, method = hit
            else:
                hit = map_node(t, nodes, nodes_sq, node_names)
                if hit:
                    tier, target, method = "B", hit[0], hit[1]
            if not target:
                unmapped.append(t)
                continue
            if method not in MECHANICAL_METHODS and n_prof < args.min_count:
                gated[t] = f"护栏③：判断型映射({method})只有 {n_prof} 份佐证，需 ≥{args.min_count}"
                continue
            rec = {"canonical": target, "method": method, "count": n_prof,
                   "sim": 1.0, "tier": tier}
            (tier_a if tier == "A" else tier_b)[t] = rec

        if unmapped:
            print(f"[learn] 规则未命中 {len(unmapped)} 个，转语义对齐（仅中文）…")
            sem = map_by_semantic([t for t in unmapped if len(counts[t]) >= args.min_count])
            for t in list(unmapped):
                if t in sem:
                    c, s = sem[t]
                    tier_a[t] = {"canonical": c, "method": "semantic",
                                 "count": len(counts[t]), "sim": round(s, 4), "tier": "A"}
                    unmapped.remove(t)

        # 3) 护栏最终复核
        rejected: dict[str, str] = {t: "图谱与词典里都没有对应技能（记为人才侧独有技能）"
                                    for t in unmapped}
        rejected.update(gated)

        # 护栏④：Tier A 会改写全局归一化词典，JD 解析走同一条路，因此默认不启用。
        # 理由不是"实测掉指标"——最初确实观察到 JD F1 0.9824→0.9819，但同配置连跑
        # 两次得到 0.9825 / 0.9819，证明那是 371 条里 3 条没命中解析缓存、每次重新
        # 问大模型造成的抖动，**与别名无关**（已在 evaluate.py 里把新解析写回缓存修掉）。
        # 真正的理由是映射质量：本轮 Tier A 只有 2 条，其中 "SQL Server → SQL" 把一个
        # 具体数据库产品塌缩成一门语言，是信息损失。2 条收益换全局词典的改动，不划算。
        if not args.allow_tier_a:
            for t in list(tier_a):
                rejected[t] = ("Tier A 默认不启用：仅 2 条且含 SQL Server→SQL 这类"
                               "信息损失映射，不值得改动 JD 也在用的全局词典")
            tier_a.clear()
        for t in list(tier_a):
            c = tier_a[t]["canonical"]
            if c not in SKILL_CATEGORY and c not in SOFT_SKILLS:
                rejected[t] = "护栏①：映射目标不是规范技能"
                tier_a.pop(t)
            elif t.lower() in SYNONYMS:
                rejected[t] = "护栏②：与硬编码词典冲突"
                tier_a.pop(t)
        for t in list(tier_b):
            if tier_b[t]["canonical"] not in nodes.values():
                rejected[t] = "护栏①：映射目标不是图谱既有节点"
                tier_b.pop(t)
            elif t.lower() in SYNONYMS:
                rejected[t] = "护栏②：与硬编码词典冲突"
                tier_b.pop(t)

        print(f"\n[learn] Tier A 词典级 {len(tier_a)} 条（并入 SYNONYMS，影响全局归一化）")
        for t, d in sorted(tier_a.items(), key=lambda kv: -kv[1]["count"]):
            print(f"    {t:<26} → {d['canonical']:<18} [{d['method']}] {d['count']}份")
        print(f"[learn] Tier B 节点级 {len(tier_b)} 条（只写 Skill.aliases，不动 SYNONYMS）")
        for t, d in sorted(tier_b.items(), key=lambda kv: -kv[1]["count"])[:40]:
            print(f"    {t:<26} → {d['canonical']:<18} [{d['method']}] {d['count']}份")
        print(f"[learn] 未映射（人才侧独有技能）{len(unmapped)} 个，例: {unmapped[:10]}")

        if not args.apply:
            print("\n[learn] 试算模式，未落库。确认后加 --apply")
            return

        # 4) 落库
        # MySQL 默认排序规则大小写不敏感，'Numpy' 与 'NumPy' 在唯一键上会撞车；
        # 而别名查表本来就是 .lower() 后查的，所以入库统一按小写键归并。
        accepted = {**tier_a, **tier_b}
        merged: dict[str, dict] = {}
        for t, d in accepted.items():
            key = t.lower()
            cur = merged.get(key)
            if cur is None:
                merged[key] = {**d, "surfaces": [t]}
            else:
                cur["count"] = max(cur["count"], d["count"])
                if t not in cur["surfaces"]:
                    cur["surfaces"].append(t)
        n_new = n_upd = 0
        for key, d in merged.items():
            # 键按小写归并（避免撞唯一约束），但**存原始书写形式**：
            # 存成小写会让台账显示成 "numpy → numpy"，看不出到底学到了什么。
            surface = d["surfaces"][0]
            row = db.query(models.SkillAlias).filter_by(alias=key).first()
            if not row:
                row = models.SkillAlias(alias=surface)
                db.add(row)
                n_new += 1
            else:
                row.alias = surface
                n_upd += 1
            sk = skill_by_name.get(d["canonical"])
            row.skill_id = sk.id if sk else None
            row.canonical = d["canonical"]
            row.talent_count = d["count"]
            row.status = "accepted"
            row.reject_reason = f"tier={d['tier']} method={d['method']}"
            row.confidence = round(min(1.0, 0.6 + 0.1 * d["count"]) * d["sim"], 4)
            if sk:
                al = list(sk.aliases or [])
                for surface in d["surfaces"]:          # Skill.aliases 保留原始书写形式
                    if surface not in al:
                        al.append(surface)
                sk.aliases = al
        db.flush()
        for t, why in rejected.items():
            key = t.lower()
            if key in merged:
                continue
            row = db.query(models.SkillAlias).filter_by(alias=key).first()
            if not row:
                row = models.SkillAlias(alias=key)
                db.add(row)
            row.canonical = None
            row.talent_count = len(counts.get(t, ()))
            row.status = "rejected"
            row.reject_reason = why[:128]
            db.flush()
        db.commit()

        OUT_FP.write_text(json.dumps({
            "_note": "由 data/learn_aliases.py 从简历语料学习得到，勿手改；"
                     "taxonomy._load_learned_aliases() 会再过一遍护栏。"
                     "aliases=Tier A 并入 SYNONYMS；node_aliases=Tier B 仅供人才侧解析，不进 SYNONYMS",
            "generated_from": {"profiles": len(profiles), "holdout": held,
                               "min_count": args.min_count, "db": settings.db_name,
                               "graph_skill_nodes": len(node_names)},
            "aliases": {t: d["canonical"] for t, d in sorted(tier_a.items())},
            "node_aliases": {t: d["canonical"] for t, d in sorted(tier_b.items())},
            "talent_only_terms": sorted(unmapped),
            "detail": accepted,
        }, ensure_ascii=False, indent=2), "utf-8")
        print(f"[learn] 已落库（新增 {n_new} / 更新 {n_upd}）并写出 {OUT_FP}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
