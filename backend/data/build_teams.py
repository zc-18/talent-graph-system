# -*- coding: utf-8 -*-
"""按岗位簇把人才画像编成演示团队（意见⑧）。

老师第⑧条要的是"团队"这个概念本身：系统要能读一批简历、把他们当成一个团队来盘点。
本轮语料是公开合规简历而非团队成员本人的简历，所以这里编的是**演示团队**；
真实团队成员随时可以通过 POST /api/talent/teams/{id}/members/upload 加进来。

成员一律用化名（成员A/B/C…），不显示也不存储任何真实姓名。

用法（backend/ 下）：
    $env:DB_NAME='talent_graph_v3'; uv run python -X utf8 data/build_teams.py [--reset]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402

TEAMS: list[tuple[str, str, set[str]]] = [
    ("AI 算法组", "机器学习/深度学习/视觉/大模型方向的人才画像",
     {"机器学习", "深度学习", "计算机视觉", "大模型算法", "自然语言处理",
      "多模态", "AIGC", "推荐算法"}),
    ("数据平台组", "大数据平台/数仓/数据开发与分析方向的人才画像",
     {"大数据平台", "数据仓库", "数据开发", "数据分析"}),
    ("工程与智能系统组", "后端/Java/云原生/运维/嵌入式/机器人方向的人才画像",
     {"后端开发", "Java开发", "运维开发", "云计算", "嵌入式", "物联网",
      "机器人算法", "自动驾驶", "边缘计算"}),
]
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def display_name(i: int) -> str:
    """成员A…成员Z、成员AA…（化名，不含真实姓名）。"""
    if i < 26:
        return f"成员{_LETTERS[i]}"
    return f"成员{_LETTERS[i // 26 - 1]}{_LETTERS[i % 26]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空既有团队与成员")
    args = ap.parse_args()

    print(f"[teams] 目标库 = {settings.db_name}")
    db = SessionLocal()
    try:
        if args.reset:
            db.query(models.TeamMember).delete()
            db.query(models.Team).delete()
            db.commit()
            print("[teams] 已清空既有团队")

        profiles = db.query(models.TalentProfile).order_by(models.TalentProfile.id).all()
        if not profiles:
            sys.exit("[teams] 库里没有人才画像，先跑 import_resumes.py")
        job_names = {j.id: j.name for j in db.query(models.Job.id, models.Job.name).all()}

        assigned: set[int] = set()
        for name, desc, clusters in TEAMS:
            team = db.query(models.Team).filter_by(name=name).first()
            if not team:
                team = models.Team(name=name, description=desc)
                db.add(team)
                db.flush()
            else:
                team.description = desc

            members = [p for p in profiles
                       if (p.target_cluster or "") in clusters and p.id not in assigned]
            existing = {m.talent_id for m in db.query(models.TeamMember).filter(
                models.TeamMember.team_id == team.id).all()}
            idx = len(existing)
            added = 0
            for p in members:
                assigned.add(p.id)
                if p.id in existing:
                    continue
                db.add(models.TeamMember(
                    team_id=team.id, talent_id=p.id, display_name=display_name(idx),
                    role_label=job_names.get(p.matched_job_id) or p.target_cluster or ""))
                idx += 1
                added += 1
            db.commit()
            print(f"[teams] {name:<18} 成员 {idx} 人（本次新增 {added}）")

        left = [p for p in profiles if p.id not in assigned]
        if left:
            print(f"[teams] 未归入任何团队 {len(left)} 份："
                  f"{[(p.code, p.target_cluster) for p in left]}")
        total = db.query(models.TeamMember).count()
        print(f"[teams] 团队 {db.query(models.Team).count()} 个，成员合计 {total} 人")
    finally:
        db.close()


if __name__ == "__main__":
    main()
