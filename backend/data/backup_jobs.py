"""岗位级快照与回滚（重建前必跑）。

`graph_service.upsert_job` 是「先清空 JobSkill/Evidence 再重建」，一旦重建结果
不如预期，被删掉的交叉验证结果和证据是找不回来的。本机没有 mysqldump，
所以用一份自包含的 JSON 快照代替：只导指定岗位的行，几 MB 量级，可原样回灌。

用法：
  # 备份（默认写 data/backup/jobs_<时间戳>.json）
  uv run python -X utf8 data/backup_jobs.py --jobs "A,B,C"
  # 回滚（用快照覆盖这些岗位的 job 字段 + job_skill + evidence + job_level_skill）
  uv run python -X utf8 data/backup_jobs.py --restore data/backup/jobs_xxx.json

快照包含：job 行的全部标量字段、job_skill、evidence（经 job_skill 关联）、
job_level_skill、authority_evidence。**不含** capability_change——演化记录由
rebuild_conflict 护栏保护，重建路径根本不会碰它；真要回滚演化请走库级备份。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402

HERE = Path(__file__).resolve().parent
BACKUP_DIR = HERE / "backup"
TABLES = ["job", "job_skill", "evidence", "job_level_skill", "authority_evidence"]


def _cols(model):
    return [c.name for c in model.__table__.columns]


def _row(obj, cols):
    out = {}
    for c in cols:
        v = getattr(obj, c)
        out[c] = v.isoformat() if isinstance(v, (datetime, date)) else v
    return out


def dump(db, names: list[str]) -> dict:
    jobs = db.query(models.Job).filter(models.Job.name.in_(names)).all()
    found = {j.name for j in jobs}
    missing = [n for n in names if n not in found]
    if missing:
        print(f"  [警告] 库中找不到这些岗位（不影响其余）：{missing}")
    job_ids = [j.id for j in jobs]
    js = db.query(models.JobSkill).filter(models.JobSkill.job_id.in_(job_ids)).all()
    js_ids = [j.id for j in js]
    # skill 表不导：重建不会删 skill 行，只会重连关系。但要记下 id→name，
    # 因为回灌时 skill_id 可能已被其他重建复用，按名字重新解析更稳。
    skill_names = dict(db.query(models.Skill.id, models.Skill.name).filter(
        models.Skill.id.in_({j.skill_id for j in js})).all()) if js else {}
    ev = db.query(models.Evidence).filter(
        models.Evidence.job_skill_id.in_(js_ids)).all() if js_ids else []
    lv = db.query(models.JobLevelSkill).filter(
        models.JobLevelSkill.job_id.in_(job_ids)).all() if job_ids else []
    au = db.query(models.AuthorityEvidence).filter(
        models.AuthorityEvidence.job_id.in_(job_ids)).all() if job_ids else []
    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "db": os.getenv("DB_NAME", "(default)"),
        "jobs": [_row(j, _cols(models.Job)) for j in jobs],
        "job_skill": [_row(j, _cols(models.JobSkill)) for j in js],
        "skill_names": {str(k): v for k, v in skill_names.items()},
        "evidence": [_row(e, _cols(models.Evidence)) for e in ev],
        "job_level_skill": [_row(x, _cols(models.JobLevelSkill)) for x in lv],
        "authority_evidence": [_row(a, _cols(models.AuthorityEvidence)) for a in au],
    }


def restore(db, snap: dict) -> None:
    """按快照覆盖：删掉这些岗位现有的 job_skill/evidence/level 行再回灌。"""
    names = [j["name"] for j in snap["jobs"]]
    jobs = {j.name: j for j in db.query(models.Job).filter(models.Job.name.in_(names)).all()}
    skill_id_by_name = dict(db.query(models.Skill.name, models.Skill.id).filter(
        models.Skill.name.in_(set(snap["skill_names"].values()))).all())
    old_job_id_map = {j["id"]: jobs[j["name"]].id for j in snap["jobs"] if j["name"] in jobs}

    for jrow in snap["jobs"]:
        job = jobs.get(jrow["name"])
        if not job:
            print(f"  [跳过] 岗位已不存在：{jrow['name']}")
            continue
        for c, v in jrow.items():
            if c in ("id", "created_at", "updated_at"):
                continue
            if c in ("first_seen_date",) and isinstance(v, str):
                v = datetime.fromisoformat(v)
            setattr(job, c, v)

    live_ids = list(old_job_id_map.values())
    old_js = db.query(models.JobSkill.id).filter(models.JobSkill.job_id.in_(live_ids)).all()
    old_js_ids = [r[0] for r in old_js]
    if old_js_ids:
        db.query(models.Evidence).filter(
            models.Evidence.job_skill_id.in_(old_js_ids)).delete(synchronize_session=False)
        db.query(models.JobSkill).filter(
            models.JobSkill.id.in_(old_js_ids)).delete(synchronize_session=False)
    db.query(models.JobLevelSkill).filter(
        models.JobLevelSkill.job_id.in_(live_ids)).delete(synchronize_session=False)
    db.flush()

    js_id_map = {}
    for r in snap["job_skill"]:
        jid = old_job_id_map.get(r["job_id"])
        sname = snap["skill_names"].get(str(r["skill_id"]))
        sid = skill_id_by_name.get(sname)
        if not jid or not sid:
            continue
        row = models.JobSkill(**{k: v for k, v in r.items()
                                 if k not in ("id", "job_id", "skill_id",
                                              "first_seen", "last_seen")},
                              job_id=jid, skill_id=sid,
                              first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
        db.add(row)
        db.flush()
        js_id_map[r["id"]] = row.id
    for r in snap["evidence"]:
        nid = js_id_map.get(r["job_skill_id"])
        if nid:
            db.add(models.Evidence(**{k: v for k, v in r.items()
                                      if k not in ("id", "job_skill_id", "created_at")},
                                   job_skill_id=nid))
    for r in snap["job_level_skill"]:
        jid = old_job_id_map.get(r["job_id"])
        if jid:
            db.add(models.JobLevelSkill(**{k: v for k, v in r.items()
                                           if k not in ("id", "job_id", "created_at")},
                                        job_id=jid))
    db.commit()
    print(f"已回滚 {len(snap['jobs'])} 个岗位："
          f"{len(js_id_map)} 条能力关系 / {len(snap['evidence'])} 条证据 / "
          f"{len(snap['job_level_skill'])} 条分级画像")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default="", help="岗位名，逗号分隔")
    ap.add_argument("--out", default="", help="快照输出路径")
    ap.add_argument("--restore", default="", help="从该快照回滚")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.restore:
            snap = json.loads(Path(args.restore).read_text("utf-8"))
            print(f"从 {args.restore} 回滚（快照时间 {snap['taken_at']}，库 {snap['db']}）")
            restore(db, snap)
            return
        names = [s.strip() for s in args.jobs.split(",") if s.strip()]
        if not names:
            ap.error("--jobs 不能为空")
        snap = dump(db, names)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        out = Path(args.out) if args.out else BACKUP_DIR / (
            f"jobs_{datetime.now():%Y%m%d_%H%M%S}.json")
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), "utf-8")
        print(f"已快照 {len(snap['jobs'])} 个岗位 → {out}")
        print(f"  能力关系 {len(snap['job_skill'])} 条 / 证据 {len(snap['evidence'])} 条 / "
              f"分级画像 {len(snap['job_level_skill'])} 条 / 权威佐证 "
              f"{len(snap['authority_evidence'])} 条")
        print(f"  回滚命令： uv run python -X utf8 data/backup_jobs.py --restore {out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
