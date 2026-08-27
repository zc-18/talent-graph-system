"""Seed an isolated SQLite database for browser and mobile E2E verification.

The script refuses non-SQLite URLs. It is deterministic and idempotent enough for
local QA: delete the SQLite file to rebuild a clean run.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services import role_contract  # noqa: E402
from app.services.taxonomy import capability_cluster, skill_category  # noqa: E402


USERS = (
    ("demo-user", "DemoUser123!", "user"),
    ("demo-hr", "DemoHr123!", "hr"),
    ("demo-admin", "DemoAdmin123!", "admin"),
)

JOBS = (
    ("Java开发工程师", "云计算与工程", "software", ["Java", "Spring", "MySQL", "Redis", "微服务", "Git", "Docker", "Linux", "TCP/IP", "需求分析"]),
    ("软件测试工程师", "云计算与工程", "software", ["Python", "自动化测试", "接口测试", "性能测试", "Linux", "Git", "CI/CD", "MySQL", "TCP/IP", "需求分析"]),
    ("大数据开发工程师", "大数据", "data", ["Hadoop", "Spark", "Hive", "Kafka", "SQL", "Flink", "数据仓库", "Linux", "Python", "消息队列", "TCP/IP", "Git", "需求分析"]),
    ("自然语言处理工程师", "人工智能", "algorithm", ["Python", "自然语言处理", "深度学习", "PyTorch", "Transformer", "大语言模型", "数据仓库", "MySQL", "Docker", "Git", "API设计", "需求分析"]),
    ("AI智能体开发工程师", "人工智能", "algorithm", ["Python", "智能体", "大语言模型", "检索增强生成", "提示工程", "LangChain", "FastAPI", "MySQL", "Docker", "Git", "需求分析"]),
    ("物联网开发工程师", "物联网", "hardware", ["C++", "嵌入式开发", "MQTT", "传感器技术", "实时操作系统", "Linux", "边缘计算", "Git", "MySQL", "消息队列", "需求分析"]),
    ("DevOps工程师", "云计算与工程", "ops", ["Linux", "Docker", "Kubernetes", "CI/CD", "Git", "云原生", "Python", "消息队列", "TCP/IP", "MySQL", "FastAPI", "需求分析"]),
    ("数据分析师", "大数据", "data", ["SQL", "Python", "数据挖掘", "数据建模", "MySQL", "机器学习", "数据仓库", "Git", "Docker", "API设计", "需求分析"]),
    ("计算机视觉工程师", "人工智能", "algorithm", ["Python", "计算机视觉", "深度学习", "PyTorch", "多模态", "Docker", "MySQL", "FastAPI", "Git", "Kafka", "需求分析"]),
    ("云计算工程师", "云计算与工程", "ops", ["云平台", "Linux", "Docker", "Kubernetes", "网络", "Python", "CI/CD", "Git", "MySQL", "消息队列", "FastAPI", "需求分析"]),
    ("后端开发工程师", "云计算与工程", "software", ["Java", "Spring", "MySQL", "微服务", "TCP/IP", "Git", "Docker", "Spark"]),
    ("自动化测试工程师", "云计算与工程", "software", ["自动化测试", "接口测试", "Python", "Git", "TCP/IP", "MySQL", "Docker", "消息队列", "需求分析"]),
    ("系统测试工程师", "云计算与工程", "software", ["系统测试", "自动化测试", "TCP/IP", "Git", "Python", "需求分析", "MySQL", "Docker"]),
    ("硬件系统测试工程师", "物联网", "hardware", ["嵌入式开发", "TCP/IP", "Git", "系统测试", "Python", "需求分析", "自动化测试", "MySQL"]),
    ("大模型算法工程师", "人工智能", "algorithm", ["大语言模型", "Python", "数据仓库", "MySQL", "Git", "Docker", "检索增强生成", "Kafka", "FastAPI"]),
    ("提示词工程师", "人工智能", "algorithm", ["提示工程", "大语言模型", "Git", "需求分析", "Python", "MySQL", "FastAPI", "数据仓库"]),
    ("生成式人工智能系统测试员", "人工智能", "software", ["系统测试", "自动化测试", "大语言模型", "提示工程", "Git", "Python", "数据仓库", "Docker", "需求分析"]),
)


def _ensure_users(db):
    created = {}
    for username, password, role in USERS:
        user = db.query(models.AppUser).filter(models.AppUser.username == username).first()
        if not user:
            user = models.AppUser(username=username, password_hash=hash_password(password),
                                  role=role, status="active")
            db.add(user)
            db.flush()
        created[role] = user
    organization = db.query(models.Organization).filter(
        models.Organization.name == "智岗演示组织").first()
    if not organization:
        organization = models.Organization(name="智岗演示组织", status="active",
                                             created_by=created["admin"].id)
        db.add(organization)
        db.flush()
    member = db.query(models.OrganizationMember).filter(
        models.OrganizationMember.organization_id == organization.id,
        models.OrganizationMember.user_id == created["hr"].id).first()
    if not member:
        db.add(models.OrganizationMember(organization_id=organization.id,
                                         user_id=created["hr"].id, role="hr", status="active"))
    return created, organization


def _ensure_jobs(db, admin):
    employers = []
    for index, name in enumerate(("智岗示例科技", "北辰数字科技", "远景云计算"), 1):
        normalized_name = f"demo-employer-{index}"
        employer = db.query(models.Employer).filter(
            models.Employer.normalized_name == normalized_name).first()
        if not employer:
            employer = models.Employer(
                name=f"{name}有限公司", normalized_name=normalized_name, status="active")
            db.add(employer)
            db.flush()
        employers.append(employer)
    for index, (name, category, track, skills) in enumerate(JOBS, 1):
        job = db.query(models.Job).filter(models.Job.name == name).first()
        if job:
            continue
        job = models.Job(
            name=name, slug=f"demo-job-{index}", category=category, track=track,
            industry="internet", recruitment_type="social", level="middle",
            status="published", summary=f"{name}的版本化岗位画像与核心能力契约。",
            core_responsibilities=["负责核心业务交付", "参与架构与质量改进"],
            typical_scenarios=["互联网平台", "企业数字化"], confidence=.91,
            evidence_count=len(skills) * len(employers), version=2 if index in (1, 5) else 1,
            is_new=index == 5, emergence_score=.88 if index == 5 else .18,
            source_summary={"employer_count": 3, "track": track, "recruitment_type": "social"},
        )
        db.add(job)
        db.flush()
        required_clusters: set[str] = set()
        version_skills = []
        for skill_index, skill_name in enumerate(skills):
            skill = db.query(models.Skill).filter(models.Skill.name == skill_name).first()
            if not skill:
                skill = models.Skill(name=skill_name, normalized_name=skill_name,
                                     category=skill_category(skill_name), skill_type="hard")
                db.add(skill)
                db.flush()
            cluster_name = capability_cluster(skill_name)
            is_required = cluster_name in required_clusters or len(required_clusters) < 6
            if is_required:
                required_clusters.add(cluster_name)
            relation = models.JobSkill(
                job_id=job.id, skill_id=skill.id,
                importance="required" if is_required else "bonus",
                weight=round(.92 - skill_index * .05, 2), confidence=round(.94 - skill_index * .025, 3),
                factors={"support": .88, "diversity": 1, "freshness": .92,
                         "authority": .9, "external": .8},
                source_count=3, status="active",
                level_required="proficient" if skill_index < 4 else "familiar",
            )
            db.add(relation)
            db.flush()
            evidence_refs = []
            for source_index, employer in enumerate(employers):
                raw = models.RawJD(
                    job_title=name, company=employer.name, employer_id=employer.id,
                    location="北京", source="official", platform="company_site",
                    source_url=f"https://example.invalid/jobs/{index}/{skill_index}/{source_index}",
                    raw_text=f"{name}要求掌握{skill_name}", publish_date=datetime.utcnow(),
                    is_duplicate=False, inflation_flag=False, track=track, industry="internet",
                    recruitment_type="social", inferred_level="middle", source_authority=1.0,
                )
                db.add(raw)
                db.flush()
                db.add(models.Evidence(job_skill_id=relation.id, raw_jd_id=raw.id, source_type="jd",
                                       source_name="企业官网", source_url=raw.source_url,
                                       snippet=f"岗位要求掌握 {skill_name}", weight=.95))
                evidence_refs.append({"raw_jd_id": raw.id, "url": raw.source_url})
            version_skills.append({
                "skill_id": skill.id,
                "capability_cluster": skill.name,
                "importance": relation.importance,
                "status": relation.status,
                "weight": relation.weight,
                "confidence": relation.confidence,
                "level_required": relation.level_required,
                "factors": relation.factors,
                "evidence_refs": evidence_refs,
            })
            for level, recruitment_type, weight_delta in (
                ("junior", "campus", -.12), ("middle", "social", 0), ("senior", "social", .06)):
                db.add(models.JobLevelSkill(
                    job_id=job.id, level=level, recruitment_type=recruitment_type,
                    track=track, industry="internet", skill_id=skill.id,
                    importance=relation.importance,
                    weight=max(.2, relation.weight + weight_delta), level_required=(
                        "familiar" if level == "junior" else "expert" if level == "senior" else "proficient"),
                    confidence=relation.confidence, factors=relation.factors,
                    source_count=3, jd_count=24,
                ))
        db.flush()
        for version in range(1, (job.version or 1) + 1):
            version_row = models.JobVersion(
                job_id=job.id, version=version, status="published",
                effective_at=datetime.utcnow() - timedelta(days=120 * ((job.version or 1) - version)),
                evidence_window={"dimensions": {
                    "job_name": name,
                    "seniority": job.level,
                    "recruitment_type": job.recruitment_type,
                    "track": job.track,
                    "industry": job.industry,
                }},
                summary=f"{name} v{version} 完整快照",
                responsibilities=job.core_responsibilities,
                typical_scenarios=job.typical_scenarios,
                contract_snapshot=None,
                created_by=admin.id,
            )
            db.add(version_row)
            db.flush()
            for skill_index, snapshot in enumerate(version_skills):
                weight = snapshot["weight"]
                if version < (job.version or 1) and skill_index == 0:
                    weight = .76
                db.add(models.JobVersionSkill(
                    job_version_id=version_row.id,
                    skill_id=snapshot["skill_id"],
                    capability_cluster=snapshot["capability_cluster"],
                    importance=snapshot["importance"],
                    status=snapshot["status"],
                    weight=weight,
                    confidence=snapshot["confidence"],
                    level_required=snapshot["level_required"],
                    factors=snapshot["factors"],
                    evidence_refs=snapshot["evidence_refs"],
                ))
            db.flush()
            version_row.contract_snapshot = role_contract.build_contract_from_version(
                db, job, version_row)
        if (job.version or 1) > 1:
            db.add(models.CapabilityChange(
                job_id=job.id, version=job.version, change_type="modify", skill_name=skills[0],
                importance="required", old_value={"weight": .76}, new_value={"weight": .92},
                reason="多雇主证据支持率提升", data_source={"employer_count": 3}, confidence=.94,
            ))


def _rebuild_version_contracts(db) -> int:
    """Repair version contracts from persisted rows in an isolated demo database."""
    rebuilt = 0
    rows = (db.query(models.JobVersion, models.Job)
            .join(models.Job, models.Job.id == models.JobVersion.job_id)
            .order_by(models.JobVersion.job_id, models.JobVersion.version).all())
    for version_row, job in rows:
        version_row.contract_snapshot = role_contract.build_contract_from_version(
            db, job, version_row)
        rebuilt += 1
    return rebuilt


def main() -> None:
    if not settings.database_url.startswith("sqlite"):
        raise SystemExit("seed_local_demo refuses non-SQLite DATABASE_URL_OVERRIDE")
    init_db()
    db = SessionLocal()
    try:
        users, organization = _ensure_users(db)
        _ensure_jobs(db, users["admin"])
        rebuilt = _rebuild_version_contracts(db)
        if not db.query(models.Team).filter(models.Team.name == "智能研发演示团队").first():
            db.add(models.Team(name="智能研发演示团队", description="浏览器验收团队",
                               organization_id=organization.id))
        db.commit()
        print(f"seeded SQLite demo: jobs={db.query(models.Job).count()}, users={len(USERS)}, "
              f"version_contracts_rebuilt={rebuilt}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
