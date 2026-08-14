"""把修订后的分类口径回填到库里（岗位领域 + 技能分类）。

分类是**逐条显示在页面上**的信息：岗位卡片的领域徽章、技能行的分类 chip、全景图的
节点配色都直接读它。两处口径此前都错得肉眼可见：

1. **岗位领域**：`title_map.json` 只声明了 10 个岗位的领域，其余岗位取「代表 JD 的
   大模型解析结果」——而同一个簇里不同 JD 的判定会漂移，等于随机挑一个。实测
   Java 开发工程师、后端开发工程师被判成**大数据**，边缘计算工程师被判成**人工智能**。
   本轮把这三个补进 `title_map.json` 的 `cluster_category`（声明比推断可靠，与该文件
   既有的设计意图一致），本脚本负责把声明同步到已建好的库。

2. **技能分类**：`taxonomy._CLOUD` 原本是个杂物筐，编程语言、数据库、后端框架全塞在
   「云计算与工程」里，于是 Java / Python / MySQL / Redis 的分类徽章全写着"云计算与工程"，
   Elasticsearch 和「操作系统」则因为压根不在表里落到"其他"。taxonomy 已拆成
   编程语言 / 数据库与存储 / 云计算与工程 三类，本脚本按新表回填 `skill.category`。

只改分类字段，不动能力项、证据、置信度与演化记录。幂等：改完再跑输出 0 条待改。

用法（backend/ 下）：
    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/resync_categories.py            # dry-run
    uv run python -X utf8 data/resync_categories.py --apply
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import ingest  # noqa: E402
from app.services.taxonomy import skill_category  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅 dry-run）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # ---------- 1. 岗位领域：以 title_map.json 的声明为准 ----------
        declared = ingest._cluster_category_map()
        job_fix = [(j, declared[j.name]) for j in db.query(models.Job).all()
                   if j.name in declared and j.category != declared[j.name]]
        print(f"岗位领域：{len(declared)} 个岗位有声明，其中 {len(job_fix)} 个与库中不一致")
        for j, cat in job_fix:
            print(f"    {j.name:22s} {j.category} → {cat}")

        # ---------- 2. 技能分类：以 taxonomy 表为准 ----------
        # 只回填"表里有明确归类"的技能：表里没有的会算出"其他"，而库中现值可能是
        # 建图时大模型给的合理分类，用"其他"覆盖它是净损失。
        skill_fix = []
        for sk in db.query(models.Skill).all():
            cat = skill_category(sk.name)
            if cat != "其他" and sk.category != cat:
                skill_fix.append((sk, cat))
        print(f"\n技能分类：{len(skill_fix)} 项与新口径不一致")
        by_move: dict[tuple[str, str], list[str]] = {}
        for sk, cat in skill_fix:
            by_move.setdefault((sk.category or "(空)", cat), []).append(sk.name)
        for (old, new), names in sorted(by_move.items(), key=lambda kv: -len(kv[1])):
            shown = "、".join(names[:8]) + (f" 等 {len(names)} 项" if len(names) > 8 else "")
            print(f"    {old} → {new}：{shown}")

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return
        if not job_fix and not skill_fix:
            print("\n已是最新口径，无需改动。")
            return

        for j, cat in job_fix:
            j.category = cat
        for sk, cat in skill_fix:
            sk.category = cat
        db.commit()
        print(f"\n已写库：岗位 {len(job_fix)} 个、技能 {len(skill_fix)} 项分类已更新")

        dist = {}
        for j in db.query(models.Job).all():
            dist[j.category] = dist.get(j.category, 0) + 1
        print("岗位领域分布：" + " / ".join(f"{k} {v}" for k, v in sorted(dist.items())))
    finally:
        db.close()


if __name__ == "__main__":
    main()
