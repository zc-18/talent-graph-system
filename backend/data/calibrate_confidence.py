"""置信度权重校准回填（2026-07，一次性执行后冻结）。

背景：真实语料重建后校准 DIVERSITY_CAP 5→3、岗位置信度改为权重加权平均。
本脚本对存量 JobSkill / JobLevelSkill 的 factors 重算 diversity 与 confidence，
并按新口径重算各岗位整体置信度。无需重新解析（factors 已落库）。

用法： $env:DB_NAME='talent_graph_v3'; uv run python -X utf8 data/calibrate_confidence.py
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import confidence as conf  # noqa: E402

OLD_CAP = 5  # 旧多样性封顶（存量 factors 用它算的）


def recalib_factors(f: dict | None) -> tuple[dict, float] | None:
    if not f or "diversity" not in f:
        return None
    nf = dict(f)
    n_platforms = round(f["diversity"] * OLD_CAP)  # 反推平台数
    nf["diversity"] = min(1.0, max(1, n_platforms) / conf.DIVERSITY_CAP)
    return nf, conf.compute(nf)


def main():
    db = SessionLocal()
    try:
        n1 = 0
        for js in db.query(models.JobSkill).all():
            r = recalib_factors(js.factors)
            if r:
                js.factors, js.confidence = r
                n1 += 1
        n2 = 0
        for jls in db.query(models.JobLevelSkill).all():
            r = recalib_factors(jls.factors)
            if r:
                jls.factors, jls.confidence = r
                n2 += 1
        db.commit()
        # 岗位整体置信度：粗粒度 active 权重加权平均
        for job in db.query(models.Job).all():
            rows = db.query(models.JobSkill).filter(
                models.JobSkill.job_id == job.id,
                models.JobSkill.status == "active").all()
            coarse = []
            for j in rows:
                sk = db.query(models.Skill).get(j.skill_id)
                if sk and not sk.parent_id:
                    coarse.append(j)
            if coarse:
                wsum = sum(max(0.05, j.weight or 0.5) for j in coarse)
                job.confidence = round(
                    sum((j.confidence or 0) * max(0.05, j.weight or 0.5) for j in coarse) / wsum, 4)
        db.commit()
        from sqlalchemy import func
        avg = db.query(func.avg(models.Job.confidence)).scalar()
        print(f"[calibrate] JobSkill 重算 {n1}、JobLevelSkill 重算 {n2}；岗位平均置信度 → {float(avg):.4f}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
