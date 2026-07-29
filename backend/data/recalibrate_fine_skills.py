"""细粒度技能点交叉验证补闸：把单来源细粒度能力项降级为候选。

背景：`hallucination.aggregate_capabilities` 早期对细粒度技能点放行 `sc >= 1` 即
active，绕过了粗粒度必须满足的「≥2 独立来源」闸门。后果是 JD 体量越大的岗位尾巴越
长——大模型算法工程师挂到 880 项能力（其中细粒度 834，709 项只有 1 个来源），而跑过
演化收敛的岗位只有 30-40 项。抽检单来源细粒度，大量是 JD 短语碎片而非技能点
（"顶会论文发表"、"智能客服项目经验"、"AI/NLP/代码相关顶会论文"）。

本脚本把该判据回填到存量数据：细粒度 + source_count < 2 + 无外部验证 → candidate。
降级而非删除，保留观察与审计链，与演化判据④「降级为候选能力项」一致。

幂等，可重复执行。用法（backend/ 下）：
    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/recalibrate_fine_skills.py [--apply]
缺省只做 dry-run 打印影响面，加 --apply 才写库。
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services.hallucination import MIN_SOURCES_REQUIRED  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # 细粒度 = Skill.parent_id 非空；判据与 hallucination 中保持一致
        q = (db.query(models.JobSkill)
             .join(models.Skill, models.Skill.id == models.JobSkill.skill_id)
             .filter(models.JobSkill.status == "active",
                     models.Skill.parent_id.isnot(None),
                     func.coalesce(models.JobSkill.source_count, 0) < MIN_SOURCES_REQUIRED))
        rows = q.all()

        before_active = (db.query(func.count(models.JobSkill.id))
                         .filter(models.JobSkill.status == "active").scalar())
        print(f"闸值：细粒度技能点需 >= {MIN_SOURCES_REQUIRED} 个独立来源")
        print(f"当前 active 能力关系 {before_active} 条")
        print(f"待降级（细粒度且单来源）{len(rows)} 条")

        by_job: dict[str, int] = {}
        for js in rows:
            by_job[js.job.name] = by_job.get(js.job.name, 0) + 1
        for name, n in sorted(by_job.items(), key=lambda x: -x[1])[:12]:
            print(f"   {name:<26} -{n}")

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return

        for js in rows:
            js.status = "candidate"
        db.commit()

        after_active = (db.query(func.count(models.JobSkill.id))
                        .filter(models.JobSkill.status == "active").scalar())
        print(f"\n已写库：active {before_active} → {after_active}（降级 {before_active - after_active}）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
