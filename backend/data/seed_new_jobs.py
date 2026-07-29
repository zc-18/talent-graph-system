"""新兴岗位 6 阵容落地（2026-07 整改，老师意见③）—— 取代 rediscover_new_jobs.py。

与旧脚本的区别：**定向 upsert，不全删**。
1. 降级：提示词工程师 / AI产品经理 → is_new=False（数据保留），各写一条演化叙事
   CapabilityChange（系统能识别岗位"兴起→回落"，与"复兴"构成双向演化证明）。
2. 六个新兴岗位（政策/报告全证据）：
   - AI智能体开发工程师      new     人社部2026-07公示"智能体开发员"工种
   - 具身智能工程师          new     人社部2026-07公示"具身智能机器人应用技术员"新职业
   - 生成式人工智能系统测试员 new     人社部2025-07-22第七批正式发布工种
   - 大模型推理优化工程师    new     脉脉《2025 AI人才流动报告》/翰德2025（DeepSeek开源潮）
   - 人工智能数字人训练师    revived 2021-22元宇宙热→退潮→2024-08人社部增设工种+大模型复兴
   - 数字孪生工程技术人员    new     人社部2026-07公示新职业
3. 每岗写 AuthorityEvidence 行（部委文件/报告：标题、机构、日期、URL、引用、本地快照）。

用法： uv run python -X utf8 data/seed_new_jobs.py [--no-llm 跳过重新定义只补证据/标记]
幂等：重复执行只更新标记与证据，不重复插入。

**对已在图谱中的岗位默认不再重新定义能力项**（2026-07 修订）：这 6 个岗位的能力
现在由真实 JD 语料经交叉验证建成（run_pipeline --only-jobs），而 LLM 重定义
只产出 4-6 个粗粒度大概念且会清空重建，跑一次就把语料成果抹掉。确需重定义
加 --allow-redefine。本脚本此后的常规用法就是 --no-llm：只回补
is_new / emergence_type / first_seen_date / emergence_score 与权威佐证。
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402
from app.services import discovery, graph_service  # noqa: E402

NEW_JOBS = [
    {"kw": "AI智能体开发工程师", "auth_key": "智能体开发", "etype": "new", "first_seen": "2026-07-02"},
    {"kw": "具身智能工程师", "auth_key": "具身智能", "etype": "new", "first_seen": "2026-07-02"},
    {"kw": "生成式人工智能系统测试员", "auth_key": "生成式人工智能系统测试", "etype": "new", "first_seen": "2025-07-22"},
    {"kw": "大模型推理优化工程师", "auth_key": "大模型推理优化", "etype": "new", "first_seen": "2025-02-01"},
    {"kw": "人工智能数字人训练师", "auth_key": "数字人训练", "etype": "revived", "first_seen": "2024-08-02"},
    {"kw": "数字孪生工程技术人员", "auth_key": "数字孪生", "etype": "new", "first_seen": "2026-07-02"},
]

DEMOTIONS = [
    {"name_like": "提示词工程师",
     "reason": ("提示词工程师岗位于2023年伴随ChatGPT热潮爆发，2025年起市场判定其回落为AI从业者"
                "的通用技能而非独立岗位（麦肯锡《State of AI 2025》：提示词工程、Agent设计、评估监控"
                "成为AI从业者'新三件套'）。系统据此将其调整为常规岗位，保留能力画像。"),
     "source": {"issuer": "麦肯锡", "title": "State of AI 2025", "date": "2025-06"}},
    {"name_like": "AI产品经理",
     "reason": ("AI产品经理岗位自2017年前后即已存在并持续演进，不满足新兴岗位判据"
                "（近两年新出现 或 沉寂后复兴）。2025年其需求大幅增长（智联招聘：+178%）"
                "属既有岗位的需求扩张，系统据此调整为常规岗位。"),
     "source": {"issuer": "智联招聘", "title": "2025年AI行业人才报告", "date": "2025"}},
]


def demote(db) -> None:
    for d in DEMOTIONS:
        jobs = db.query(models.Job).filter(models.Job.name.like(f"%{d['name_like']}%"),
                                           models.Job.is_new == True).all()  # noqa: E712
        for job in jobs:
            job.is_new = False
            job.emergence_type = None
            job.version = (job.version or 1) + 1
            db.add(models.CapabilityChange(
                job_id=job.id, version=job.version, change_type="modify",
                skill_name="（岗位属性）新兴标记", importance="",
                old_value={"is_new": True}, new_value={"is_new": False},
                reason=d["reason"], data_source=d["source"], confidence=0.9))
            print(f"[demote] {job.name} -> 常规岗位（演化叙事已记录）")
    db.commit()


def upsert_authority(db, job, entries: list[dict]) -> int:
    n = 0
    for e in entries:
        if not e.get("kind"):
            continue
        exists = db.query(models.AuthorityEvidence).filter(
            models.AuthorityEvidence.job_id == job.id,
            models.AuthorityEvidence.title == e["title"]).first()
        if exists:
            continue
        pd = None
        if e.get("publish_date"):
            try:
                pd = datetime.strptime(e["publish_date"][:10], "%Y-%m-%d")
            except ValueError:
                pass
        db.add(models.AuthorityEvidence(
            job_id=job.id, kind=e["kind"], title=e["title"][:250],
            issuer=(e.get("provider") or "")[:120], publish_date=pd,
            url=e.get("url", "")[:500], excerpt=e.get("content", ""),
            local_file=e.get("local_file", "")[:250]))
        n += 1
    return n


def main(no_llm: bool = False, allow_redefine: bool = False):
    init_db()
    db = SessionLocal()
    try:
        demote(db)
        for cfg in NEW_JOBS:
            kw = cfg["kw"]
            print(f"[seed] {kw} ...")
            existing = db.query(models.Job).filter(models.Job.name == kw).first()
            if no_llm and not existing:
                print(f"  跳过（--no-llm 且库中不存在）")
                continue
            # 已在图谱中的岗位默认**不再走 LLM 重定义**：define_new_job 返回的是
            # 4-6 个粗粒度大概念（其 JSON schema 里根本没有 parent 字段，见
            # discovery._DEFINE_TPL），而 upsert_job 会先清空 JobSkill/Evidence 再重建。
            # 对一个已由真实 JD 交叉验证建好的岗位跑一次，就是用弱证据物理删除强证据——
            # 与 /api/discovery/discover 那次线上事故一字不差，只差一个命令行参数。
            if existing and not no_llm and not allow_redefine:
                n_caps = db.query(models.JobSkill).filter(
                    models.JobSkill.job_id == existing.id,
                    models.JobSkill.status == "active").count()
                print(f"  已在图谱中（v{existing.version}，{n_caps} 项已验证能力）"
                      f"→ 只补标记与权威证据，不重新定义能力项"
                      f"（确需重定义加 --allow-redefine）")
            if no_llm or (existing and not allow_redefine):
                job = existing
                auth = discovery.authority_matches(cfg["auth_key"])
            else:
                cand = discovery.discover_candidates(kw)
                definition = discovery.define_new_job(kw, cand["evidence"])
                definition["emergence_score"] = max(
                    definition.get("emergence_score", 0), cand["emergence_score"])
                job = graph_service.upsert_job(
                    db, job_title=kw, category=definition["category"],
                    level=definition["level"], responsibilities=definition["core_responsibilities"],
                    scenarios=definition["typical_scenarios"], capabilities=definition["capabilities"],
                    is_new=True, summary=definition["summary"],
                    source_summary=definition["source_summary"],
                    emergence_score=definition["emergence_score"], with_embedding=False)
                auth = [e for e in cand["evidence"] if e.get("kind")]
            job.is_new = True
            job.emergence_type = cfg["etype"]
            job.first_seen_date = datetime.strptime(cfg["first_seen"], "%Y-%m-%d")
            score = job.emergence_score or 0.0
            if score < 0.9 and any(e.get("kind") == "policy" for e in auth):
                job.emergence_score = 0.9
            elif score < 0.8 and any(e.get("kind") == "report" for e in auth):
                job.emergence_score = 0.8
            n = upsert_authority(db, job, auth)
            db.commit()
            req = [s.skill.name for s in job.skills if s.importance == "required"][:8] if job.skills else []
            print(f"  -> {job.name} [{cfg['etype']}] 权威证据+{n} 必备技能={req}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="不调 LLM 重定义，仅补标记与权威证据")
    ap.add_argument("--allow-redefine", action="store_true",
                    help="允许对已存在岗位重新调 LLM 定义能力项（会清空并覆盖其现有能力与证据）")
    args = ap.parse_args()
    main(no_llm=args.no_llm, allow_redefine=args.allow_redefine)
