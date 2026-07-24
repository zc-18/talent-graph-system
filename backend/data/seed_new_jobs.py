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


def main(no_llm: bool = False):
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
            if no_llm:
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
            if job.emergence_score < 0.9 and any(e.get("kind") == "policy" for e in auth):
                job.emergence_score = 0.9
            elif job.emergence_score < 0.8 and any(e.get("kind") == "report" for e in auth):
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
    args = ap.parse_args()
    main(no_llm=args.no_llm)
