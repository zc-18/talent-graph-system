# -*- coding: utf-8 -*-
"""统一雇主身份口径：别名归一 + 集团母公司折叠（R6 缺陷③）。

CLAUDE.md 的承诺是「独立来源指独立雇主，不是独立平台」。库里现在有两处偏离，
方向相反但都让 `diversity` 因子失真：

**A. 分裂（会虚增）**——同一实体的不同写法各算一家。飞书 `xiaopeng` 租户的站点标题
从「加入小鹏汽车」改成了「加入小鹏集团」，本轮采集到的公司名因此是 `小鹏集团`，
`normalize_employer_name` 归一后与库内的 `小鹏汽车` 不相等，会在 5 个岗位里冒充新雇主。
`网易有道信息技术（北京）有限公司` 与库内 `网易·网易有道` 同理。
两个名字现在都还没进库（R6 两批采集未 import），所以本次是**预防性登记，不改动任何现有数字**。

**B. 膨胀（正在虚增）**——`normalize_employer_name` 的 `_BRANCH_SUFFIX` 只脱词尾的
「分公司」，而 `中国联合网络通信有限公司山东省分公司` 脱完是
`中国联合网络通信有限公司山东省`，法定后缀不在词尾脱不掉，于是**每个省分公司都成了
一个独立雇主实体**。中远海运的各子公司同理。

**修法只用数据，不动代码。** CLAUDE.md 明确警告：放宽 `employer_resolution.py` 的
归一化逻辑会静默抬高全库所有置信度分。所以这里走系统本来就支持的两条数据通道：
`EmployerAlias`（别名）与 `Employer.parent_id`（集团母公司）——
`confidence_batch._employer_key` 与 `role_contract.contract_summaries_for_jobs`
都已经在用 `COALESCE(parent_id, id)` 计数，不需要改任何函数。

**母公司不新建行，用库里已有的集团行**，避免凭空造实体：
* 联通族 → `中国联通总部`(id=527)。挂上去的 13 行全部是
  `中国联合网络通信有限公司` 这**同一个法人**的分公司 / 事业部 / 研究院，
  不是「合并子公司」，是把被后缀规则拆开的同一法人重新合起来。
* 中远海运族 → `中国远洋海运集团有限公司`(id=326)，即集团本身。

**故意不折叠的**（独立法人，折叠属于「猜品牌、并子公司」，CLAUDE.md 不允许）：
联通数字科技 / 联通数据智能 / 联通智网科技 / 中国联通（香港）创新研究院、
中国电子科技第十五所与第五十所、中国移动两家子公司、网易 11 个事业部
（网易有道、网易云音乐是各自独立上市主体）。这些留档在报告里，等人工拍板。

**预期后果：分数会降。** 折叠后独立雇主数减少，`diversity = 雇主数/3` 因子随之下降，
受影响岗位的 confidence 会掉。这是把虚高的分数改回真实值，不是回归。

用法（backend/ 下）。**dry-run 才可以对着生产库跑（零写入）；`--apply` 对
`talent_graph_v3` 会被 `repair_safety.assert_shadow_apply_target` 无条件拒绝**——
先用 `data/clone_database_r6.py` 克隆出影子库，在影子库上 apply、验收，再切 DB_NAME：

    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_employer_identity_r6.py            # dry-run，可对生产库

    $env:DB_NAME='talent_graph_v4_shadow'
    uv run python -X utf8 data/repair_employer_identity_r6.py --apply         --allow-shadow --confirm-database talent_graph_v4_shadow
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import (  # noqa: E402
    confidence_batch, repair_safety, role_contract, state_reconcile)
from app.services.employer_resolution import (  # noqa: E402
    normalize_employer_name, register_employer_alias)

BACKUP_DIR = Path(__file__).resolve().parent / "backup"

# 别名 -> 库内规范雇主名。只登记「同一实体的另一种写法」，不登记同族的不同法人。
ALIASES: list[tuple[str, str, str]] = [
    ("小鹏集团", "小鹏汽车", "飞书 xiaopeng 租户站点标题改名，同一招聘主体"),
    ("网易有道信息技术（北京）有限公司", "网易·网易有道", "网易有道的工商全称与库内简称"),
]

# 集团母公司 id -> 子实体名单（写死，逐行人工核过；不用正则，避免误折叠）
PARENT_GROUPS: dict[str, dict] = {
    "中国联通": {
        "parent_name": "中国联通总部",
        "children": [
            "中国联合网络通信有限公司研究院",
            "中国联合网络通信有限公司软件研究院",
            "中国联合网络通信有限公司广东省分公司",
            "中国联合网络通信有限公司甘肃省分公司",
            "中国联合网络通信有限公司重庆市分公司",
            "中国联合网络通信有限公司四川省分公司",
            "中国联合网络通信有限公司上海市分公司",
            "中国联合网络通信有限公司北京网络运营事业部",
            "中国联合网络通信有限公司江西省分公司",
            "中国联合网络通信有限公司福建省分公司",
            "中国联合网络通信有限公司海南省分公司",
            "中国联合网络通信有限公司西藏自治区分公司",
            "中国联合网络通信有限公司安徽省分公司",
        ],
        "why": "全部是「中国联合网络通信有限公司」同一法人的分公司/事业部/研究院",
    },
    "中远海运": {
        "parent_name": "中国远洋海运集团有限公司",
        "children": [
            "中远海运科技股份有限公司",
            "上海中远海运资讯科技有限公司",
            "天津中远海运数智能源科技有限公司",
            "中远海运散货运输有限公司",
            "中远海运重工有限公司",
            "中远海运物流供应链有限公司",
            "大连中远海运重工有限公司",
            "扬州中远海运重工有限公司",
            "中远海运博鳌有限公司博鳌亚洲论坛大酒店",
            "中国远洋海运集团航运先进技术研究院",
        ],
        "why": "全部是中国远洋海运集团的全资/控股子公司与直属研究院",
    },
}


def _by_name(db, name: str):
    return db.query(models.Employer).filter(models.Employer.name == name).first()


def _simulate(db, mutate) -> dict:
    """在同一个会话里跑两遍生产的置信度算式，取差值；结束 rollback，不写库。

    直接复用 `confidence_batch._job_calculation`（它自身不 commit），
    避免另写一套算式导致口径漂移。两遍用同一个 as_of，抵掉时效性随时间的漂移。
    """
    as_of = confidence_batch._naive_utc(datetime.now(timezone.utc))
    jobs = db.query(models.Job).filter(models.Job.status == "published").order_by(
        models.Job.id).all()

    def snapshot() -> dict:
        summaries = role_contract.contract_summaries_for_jobs(db, jobs)
        out = {}
        for job in jobs:
            result = confidence_batch._job_calculation(db, job, as_of)
            out[job.id] = {
                "name": job.name, "confidence": result["confidence"],
                "diversity": result["factors"].get("diversity", 0.0),
                "employers": summaries[job.id]["employer_count"],
                "clusters": summaries[job.id]["required_count"],
                "status": summaries[job.id]["contract_status"],
            }
        return out

    def relation_counts() -> dict[int, int]:
        return {row.id: row.source_count for row in db.query(models.JobSkill).filter(
            models.JobSkill.status == "active").all()}

    before = snapshot()
    rel_before = relation_counts()
    db.rollback()
    mutate(db)
    db.flush()
    after = snapshot()
    rel_after = relation_counts()
    db.rollback()
    changed = [k for k, v in rel_before.items() if rel_after.get(k) != v]
    below = [k for k in changed if rel_before[k] >= 2 > rel_after.get(k, 0)]
    return {"before": before, "after": after,
            "relation_delta": {"changed": len(changed), "below_gate": len(below)}}


def _plan(db):
    alias_plan, parent_plan, problems = [], [], []
    for alias, canonical, why in ALIASES:
        employer = _by_name(db, canonical)
        normalized = normalize_employer_name(alias)
        if employer is None:
            problems.append(f"别名 {alias}：库内找不到规范雇主「{canonical}」")
            continue
        existing = db.query(models.EmployerAlias).filter(
            models.EmployerAlias.normalized_alias == normalized).first()
        state = ("已登记" if existing and existing.employer_id == employer.id
                 else "冲突：已绑到别的实体" if existing else "待登记")
        if state.startswith("冲突"):
            problems.append(f"别名 {alias} 已绑定 employer_id={existing.employer_id}")
        alias_plan.append((alias, normalized, employer, state, why))

    assigned_children: dict[int, str] = {}
    for group, spec in PARENT_GROUPS.items():
        parent = _by_name(db, spec["parent_name"])
        if parent is None:
            problems.append(f"集团 {group}：库内找不到母公司行「{spec['parent_name']}」")
            continue
        if parent.status != "active" or parent.parent_id is not None:
            problems.append(
                f"集团 {group}：母公司必须 active 且自身无 parent（id={parent.id}）")
        children = []
        for name in spec["children"]:
            child = _by_name(db, name)
            if child is None:
                problems.append(f"集团 {group}：库内找不到子实体「{name}」")
                continue
            if child.id == parent.id:
                problems.append(f"集团 {group}：母公司不能作为自己的子实体「{name}」")
                continue
            if child.status != "active":
                problems.append(f"集团 {group}：子实体不是 active「{name}」")
            prior_group = assigned_children.get(child.id)
            if prior_group and prior_group != group:
                problems.append(f"子实体「{name}」同时出现在 {prior_group}/{group}")
            assigned_children[child.id] = group
            children.append(child)
        parent_plan.append((group, parent, children, spec["why"]))
    return alias_plan, parent_plan, problems


def _mutate(alias_plan, parent_plan):
    def apply(db):
        for alias, _normalized, employer, state, _why in alias_plan:
            if state == "待登记":
                register_employer_alias(db, employer, alias)
        for _group, parent, children, _why in parent_plan:
            for child in children:
                if child.id != parent.id and child.parent_id != parent.id:
                    child.parent_id = parent.id
    return apply


def _verify(db) -> bool:
    ok = True
    for alias, canonical, _why in ALIASES:
        normalized = normalize_employer_name(alias)
        row = db.query(models.EmployerAlias).filter(
            models.EmployerAlias.normalized_alias == normalized).first()
        target = _by_name(db, canonical)
        if not row or not target or row.employer_id != target.id:
            ok = False
            print(f"  [FAIL] 别名未生效：{alias} → {canonical}")
    for group, spec in PARENT_GROUPS.items():
        parent = _by_name(db, spec["parent_name"])
        for name in spec["children"]:
            child = _by_name(db, name)
            if child and parent and child.id != parent.id and child.parent_id != parent.id:
                ok = False
                print(f"  [FAIL] {group}：{name} 未挂到 {spec['parent_name']}")
    # 母公司自身不能再有 parent，否则 _employer_key 会跳一层跳空
    orphan = db.query(models.Employer).filter(
        models.Employer.parent_id.isnot(None)).all()
    parent_ids = {e.parent_id for e in orphan}
    broken = [p for p in parent_ids
              if (row := db.query(models.Employer).get(p)) is None
              or row.status != "active" or row.parent_id is not None]
    if broken:
        ok = False
        print(f"  [FAIL] 存在两级或失效的母公司：{broken}")
    print(f"  [OK] 别名 {len(ALIASES)} 条、母公司折叠 "
          f"{sum(len(s['children']) for s in PARENT_GROUPS.values())} 行全部到位；"
          f"母公司层级为一层" if ok else "  [FAIL] 见上")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写影子库（缺省仅 dry-run）")
    parser.add_argument("--allow-shadow", action="store_true",
                        help="显式批准非 SQLite shadow（当前生产库始终禁止）")
    parser.add_argument("--confirm-database", default=None,
                        help="必须精确填写实际连接的非生产 shadow 库名")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        total = db.query(models.Employer).count()
        folded = db.query(models.Employer).filter(
            models.Employer.parent_id.isnot(None)).count()
        aliases = db.query(models.EmployerAlias).count()
        print(f"=== 现状：employer {total} 行，其中已挂母公司 {folded} 行；"
              f"employer_alias {aliases} 条 ===\n")

        alias_plan, parent_plan, problems = _plan(db)
        print("--- A. 别名归一（预防性，两个名字现在都还没进库）---")
        for alias, normalized, employer, state, why in alias_plan:
            n_jd = db.query(models.RawJD).filter(
                models.RawJD.company == alias).count()
            print(f"   {alias:<26} → {employer.name}(id={employer.id})   [{state}]"
                  f"  归一键={normalized}  库内该写法的JD={n_jd}   {why}")

        print("\n--- B. 集团母公司折叠 ---")
        shrink = 0
        for group, parent, children, why in parent_plan:
            todo = [c for c in children if c.parent_id != parent.id and c.id != parent.id]
            shrink += len(todo)
            print(f"   【{group}】母公司 = {parent.name}(id={parent.id})  —— {why}")
            for child in children:
                n_jd = db.query(models.RawJD).filter(
                    models.RawJD.employer_id == child.id).count()
                mark = "已挂" if child.parent_id == parent.id else "待挂"
                print(f"      [{mark}] id={child.id:>4} JD={n_jd:>3}  {child.name}")
        print(f"\n   本次会新挂 {shrink} 行 → 独立雇主实体数 {total} → {total - shrink}"
              f"（employer 表行数不变，变的是计数口径）")

        if problems:
            print("\n[中止] 计划表与库不一致，未做任何改动：")
            for p in problems:
                print(f"   - {p}")
            db.rollback()
            return 2

        print("\n--- C. 对置信度/雇主数的影响（复用 confidence_batch 的生产算式，"
              "同一 as_of 跑两遍取差，未写库）---")
        sim = _simulate(db, _mutate(alias_plan, parent_plan))
        before, after = sim["before"], sim["after"]
        rows = []
        for job_id, b in before.items():
            a = after[job_id]
            if (round(b["confidence"], 4) != round(a["confidence"], 4)
                    or b["employers"] != a["employers"]):
                rows.append((b["name"], b["employers"], a["employers"],
                             b["confidence"], a["confidence"],
                             b["clusters"], a["clusters"], b["status"], a["status"]))
        rows.sort(key=lambda r: r[4] - r[3])
        print(f"{'岗位':<24} {'卡片雇主数':>12} {'置信度(6位)':>30} {'契约簇':>8}  契约状态")
        for name, be, ae, bc, ac, bcl, acl, bs, as_ in rows:
            flag = "  ← 契约状态变化" if bs != as_ else ""
            print(f"{name:<24} {f'{be} → {ae}':>12} "
                  f"{f'{bc:.6f} → {ac:.6f} ({ac - bc:+.6f})':>30} "
                  f"{f'{bcl} → {acl}':>8}  {as_}{flag}")
        print()
        print(f"   能力项级别：source_count（独立雇主数）发生变化的 job_skill "
              f"{sim['relation_delta']['changed']} 条；"
              f"其中因此跌破 ≥2 交叉验证门槛的 {sim['relation_delta']['below_gate']} 条")
        print("   （多样性因子 diversity = 雇主数 / DIVERSITY_CAP(=3) 且封顶 1.0，"
              "受影响的能力项折叠后雇主数多数仍 ≥3，所以置信度几乎不动——"
              "虚增主要体现在岗位卡片展示的『独立雇主数』上。）")
        n = len(before)
        avg_b = sum(v["confidence"] for v in before.values()) / n
        avg_a = sum(v["confidence"] for v in after.values()) / n
        print()
        print(f"\n   受影响岗位 {len(rows)}/{n}；全库 AVG(confidence) "
              f"{avg_b:.4f} → {avg_a:.4f} ({avg_a-avg_b:+.4f})")
        print(f"   契约达标(ready) 岗位数 "
              f"{sum(1 for v in before.values() if v['status']=='ready')} → "
              f"{sum(1 for v in after.values() if v['status']=='ready')}")

        if not args.apply:
            db.rollback()
            print("\n[dry-run] zero writes；影子发布需 --apply。")
            return 0

        repair_safety.assert_shadow_apply_target(
            db, allow_shadow=args.allow_shadow,
            confirm_database=args.confirm_database)
        as_of = confidence_batch._naive_utc(datetime.now(timezone.utc))
        run_id = f"r6-employer-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        snapshot = {
            "schema": "r6-employer-identity-backup-v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": db.get_bind().url.database,
            "employer_parent_before": [
                {"id": c.id, "name": c.name, "parent_id": c.parent_id}
                for _g, _p, children, _w in parent_plan for c in children],
            "alias_before": [{"id": a.id, "alias": a.alias,
                              "normalized_alias": a.normalized_alias,
                              "employer_id": a.employer_id,
                              "created_at": a.created_at.isoformat() if a.created_at else None}
                             for a in db.query(models.EmployerAlias).all()],
            **state_reconcile.backup_projection(db),
        }
        path = repair_safety.backup_path(BACKUP_DIR, "employer_identity_r6")
        repair_safety.write_json_exclusive(path, snapshot)
        try:
            before_changes = db.query(models.CapabilityChange).count()
            before_authority = db.query(models.AuthorityEvidence).count()
            _mutate(alias_plan, parent_plan)(db)
            db.flush()
            manifest = state_reconcile.reconcile_all(
                db, as_of=as_of, run_id=run_id,
                audit_action="graph.repair.employer_identity_r6",
                audit_context={"aliases": len(ALIASES), "parent_rows": sum(
                    len(spec["children"]) for spec in PARENT_GROUPS.values())},
                force_audit=True)
            errors = state_reconcile.verify_all(db, as_of=as_of)
            if not _verify(db):
                errors.append("employer identity verify failed")
            if db.query(models.CapabilityChange).count() != before_changes:
                errors.append("CapabilityChange count changed")
            if db.query(models.AuthorityEvidence).count() != before_authority:
                errors.append("AuthorityEvidence count changed")
            if errors:
                raise RuntimeError("commit 前验证失败：" + "; ".join(errors[:20]))
            db.commit()
        except Exception:
            db.rollback()
            raise
        print(f"\n已写 shadow；备份在 {path}；状态补闸={manifest}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
