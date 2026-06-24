"""用改进后的（简洁技能名）发现逻辑重新生成并覆盖 2 个新岗位。"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import discovery, graph_service  # noqa: E402

KEYWORDS = ["提示词工程师", "AI智能体开发工程师", "AI产品经理", "具身智能工程师"]


def main():
    db = SessionLocal()
    try:
        # 先删除全部新岗位（含此前冗长/重复版本），ORM 级联删除 job_skill/evidence
        for job in db.query(models.Job).filter(models.Job.is_new == True).all():  # noqa: E712
            db.delete(job)
        db.commit()
        print("已清除旧的新岗位记录")

        for kw in KEYWORDS:
            print(f"重新发现: {kw} ...")
            cand = discovery.discover_candidates(kw)
            definition = discovery.define_new_job(kw, cand["evidence"])
            definition["emergence_score"] = max(definition.get("emergence_score", 0), cand["emergence_score"])
            job = graph_service.upsert_job(
                db, job_title=definition["job_title"], category=definition["category"],
                level=definition["level"], responsibilities=definition["core_responsibilities"],
                scenarios=definition["typical_scenarios"], capabilities=definition["capabilities"],
                is_new=True, summary=definition["summary"],
                source_summary=definition["source_summary"],
                emergence_score=definition["emergence_score"], with_embedding=False)
            req = [c["name"] for c in definition["capabilities"] if c["importance"] == "required"]
            print(f"  -> {job.name}: 必备技能 = {req}")
        # 清理孤立技能（无任何 job_skill 关联）
        from sqlalchemy import select
        used = {r[0] for r in db.query(models.JobSkill.skill_id).distinct().all()}
        orphans = db.query(models.Skill).filter(~models.Skill.id.in_(used)).all() if used else []
        for o in orphans:
            db.delete(o)
        db.commit()
        print(f"清理孤立技能 {len(orphans)} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()
