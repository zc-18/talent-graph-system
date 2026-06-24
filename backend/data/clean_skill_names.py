"""一次性清洗已入库的冗长技能名（主要来自新岗位发现的大模型生成名）。

将冗长技能名清洗为简洁技能点；若清洗后与已有规范技能重名则合并（重指向 + 去重）。
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services.taxonomy import clean_skill_name, skill_category, skill_type  # noqa: E402


def main():
    db = SessionLocal()
    renamed = merged = 0
    try:
        skills = db.query(models.Skill).all()
        for sk in skills:
            new_name = clean_skill_name(sk.name)
            if not new_name or new_name == sk.name:
                continue
            target = db.query(models.Skill).filter(
                models.Skill.normalized_name == new_name, models.Skill.id != sk.id).first()
            if target:
                # 合并 sk -> target
                for js in db.query(models.JobSkill).filter(models.JobSkill.skill_id == sk.id).all():
                    dup = db.query(models.JobSkill).filter(
                        models.JobSkill.job_id == js.job_id,
                        models.JobSkill.skill_id == target.id).first()
                    if dup:
                        db.query(models.Evidence).filter(models.Evidence.job_skill_id == js.id).delete()
                        db.delete(js)
                    else:
                        js.skill_id = target.id
                db.query(models.SkillRelation).filter(models.SkillRelation.from_skill_id == sk.id).delete()
                db.query(models.SkillRelation).filter(models.SkillRelation.to_skill_id == sk.id).delete()
                db.delete(sk)
                merged += 1
                print(f"合并: {sk.name!r} -> {new_name!r}")
            else:
                sk.name = new_name
                sk.normalized_name = new_name
                sk.category = skill_category(new_name)
                sk.skill_type = skill_type(new_name)
                renamed += 1
                print(f"重命名: -> {new_name!r}")
        db.commit()
    finally:
        db.close()
    print(f"\n完成：重命名 {renamed}，合并 {merged}")


if __name__ == "__main__":
    main()
