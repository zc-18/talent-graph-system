"""数据库 ORM 模型 —— 岗位能力知识图谱核心 schema。

图谱节点：Job（岗位）、Skill（技能点）、TechTrend（技术趋势）
图谱关系：JobSkill（岗位-技能）、SkillRelation（技能-技能：先修/相关/驱动）
证据与溯源：RawJD（原始招聘数据）、Evidence（能力项证据，反幻觉溯源）
演化追踪：CapabilityChange（能力项变更记录）
匹配：Resume（简历）、MatchResult（匹配结果）
人才侧（意见⑧）：ResumeBatch（语料台账）、TalentProfile（脱敏人才画像）、
                  Team/TeamMember（团队盘点）、SkillAlias（从简历学到的技能表述）
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, ForeignKey, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from .db import Base


# ------------------------- 岗位 -------------------------
class Job(Base):
    __tablename__ = "job"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, index=True)          # 岗位名称
    slug = Column(String(160), unique=True, index=True)             # 唯一标识
    category = Column(String(64), index=True)                       # 技术栈：人工智能/大数据/智能系统/物联网...
    track = Column(String(32), nullable=True, index=True)           # software/hardware/algorithm/data/ops/product
    industry = Column(String(32), nullable=True, index=True)        # internet/automotive/medical_device/...
    recruitment_type = Column(String(16), default="mixed", index=True)  # campus/social/mixed
    level = Column(String(32), default="middle")                    # 级别：junior/middle/senior/expert
    is_new = Column(Boolean, default=False, index=True)             # 是否为新发现岗位
    status = Column(String(16), default="published")               # draft/published
    summary = Column(Text)                                          # 岗位简介
    core_responsibilities = Column(JSON)                            # 核心职责 [str]
    typical_scenarios = Column(JSON)                                # 典型行业应用场景 [str]
    emergence_score = Column(Float, default=0.0)                    # 新兴度（新岗位发现打分）
    emergence_type = Column(String(16), nullable=True)              # 新兴类型：new（新出现）/revived（沉寂后复兴）/NULL（非新兴）
    first_seen_date = Column(DateTime, nullable=True)               # 岗位（作为新兴岗）首次可考证出现时间
    confidence = Column(Float, default=0.0)                         # 岗位定义整体置信度（反幻觉）
    evidence_count = Column(Integer, default=0)                     # 支撑证据数
    source_summary = Column(JSON)                                   # 数据源摘要
    version = Column(Integer, default=1)                            # 当前版本号
    embedding = Column(JSON)                                        # 岗位语义向量
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    skills = relationship("JobSkill", back_populates="job", cascade="all, delete-orphan")
    changes = relationship("CapabilityChange", back_populates="job", cascade="all, delete-orphan")


# ------------------------- 技能点 -------------------------
class Skill(Base):
    __tablename__ = "skill"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, index=True)          # 技能点名称
    normalized_name = Column(String(128), index=True)              # 归一化名称（同义词合并）
    category = Column(String(64), index=True)                       # 所属技术栈
    skill_type = Column(String(32), default="hard")                # hard/soft/tool/framework/concept
    description = Column(Text)
    parent_id = Column(Integer, ForeignKey("skill.id"), nullable=True)  # 技能层级树
    heat = Column(Float, default=0.0)                              # 热度
    embedding = Column(JSON)                                        # 技能语义向量
    aliases = Column(JSON)                                          # 别名/同义词
    created_at = Column(DateTime, default=datetime.utcnow)

    children = relationship("Skill")


# ------------------------- 岗位-技能关系 -------------------------
class JobSkill(Base):
    __tablename__ = "job_skill"
    __table_args__ = (UniqueConstraint("job_id", "skill_id", name="uq_job_skill"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), index=True)
    skill_id = Column(Integer, ForeignKey("skill.id"), index=True)
    importance = Column(String(16), default="required")            # required（必备）/bonus（加分）
    weight = Column(Float, default=0.5)                            # 重要度权重 0-1
    level_required = Column(String(32), default="familiar")        # 掌握级别：familiar/proficient/expert
    confidence = Column(Float, default=0.0)                        # 该能力项置信度（反幻觉核心）
    factors = Column(JSON, nullable=True)                          # 置信度因子分解 {support,diversity,freshness,authority,external}
    source_count = Column(Integer, default=0)                      # 独立来源数
    status = Column(String(16), default="active")                 # active/deprecated（演化）
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="skills")
    skill = relationship("Skill")
    evidences = relationship("Evidence", back_populates="job_skill", cascade="all, delete-orphan")


# ------------------------- 技能-技能关系 -------------------------
class SkillRelation(Base):
    __tablename__ = "skill_relation"
    __table_args__ = (
        UniqueConstraint("from_skill_id", "to_skill_id", "relation_type", name="uq_skill_rel"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    from_skill_id = Column(Integer, ForeignKey("skill.id"), index=True)
    to_skill_id = Column(Integer, ForeignKey("skill.id"), index=True)
    relation_type = Column(String(32), default="related")          # prerequisite（先修）/related/drives（驱动）
    weight = Column(Float, default=0.5)


# ------------------------- 技术趋势 -------------------------
class TechTrend(Base):
    __tablename__ = "tech_trend"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), index=True)
    category = Column(String(64))
    heat = Column(Float, default=0.0)                              # 热度
    momentum = Column(Float, default=0.0)                          # 增长动量（A的爆发）
    related_skills = Column(JSON)                                   # 关联技能
    description = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ------------------------- 原始招聘数据（多源） -------------------------
class RawJD(Base):
    __tablename__ = "raw_jd"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_title = Column(String(128), index=True)
    company = Column(String(128))
    location = Column(String(64))
    source = Column(String(64))                                    # 数据源：招聘平台/官网/Tavily...
    source_url = Column(String(512))
    raw_text = Column(Text)
    publish_date = Column(DateTime, index=True)                    # 发布时间（用于时滞分析）
    collected_at = Column(DateTime, default=datetime.utcnow)
    dedup_hash = Column(String(64), index=True)                    # 精确去重 hash
    simhash = Column(String(64), index=True)                       # 近似去重 simhash
    is_duplicate = Column(Boolean, default=False, index=True)      # 是否抄袭/重复
    duplicate_of = Column(Integer, nullable=True)                  # 指向原始 JD
    quality_score = Column(Float, default=0.0)                     # 质量评分
    inflation_flag = Column(Boolean, default=False)                # 是否能力通胀
    lag_days = Column(Integer, default=0)                          # 时滞天数
    embedding = Column(JSON)                                        # JD 语义向量
    # ---- 真实采集溯源字段（2026-07 整改新增） ----
    platform = Column(String(64), index=True)                      # 采集平台标识：bytedance/tencent/iguopin/dataset:tianchi...
    salary_range = Column(String(64))                              # 薪资区间原文，如 "40-70K"
    experience_req = Column(String(64))                            # 经验要求原文，如 "3-5年"
    education_req = Column(String(64))                             # 学历要求原文
    crawl_batch_id = Column(Integer, ForeignKey("crawl_batch.id"), nullable=True, index=True)
    raw_file_path = Column(String(256))                            # 本地原始留存文件路径（佐证）
    inferred_level = Column(String(16), index=True)                # 推断级别：junior/middle/senior
    cluster_hint = Column(String(64), index=True)                  # 采集检索词对应的岗位簇（聚类辅助）
    source_authority = Column(Float, default=0.6)                  # 来源权威度：官网/政府1.0 数据集0.7 网络0.6
    track = Column(String(32), nullable=True, index=True)
    industry = Column(String(32), nullable=True, index=True)
    recruitment_type = Column(String(16), nullable=True, index=True)
    employer_id = Column(Integer, ForeignKey("employer.id"), nullable=True, index=True)

    Index("ix_rawjd_title_company", "job_title", "company")


# ------------------------- 能力项证据（反幻觉溯源） -------------------------
class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_skill_id = Column(Integer, ForeignKey("job_skill.id"), index=True)
    raw_jd_id = Column(Integer, ForeignKey("raw_jd.id"), nullable=True)
    source_type = Column(String(32))                               # jd/web/llm
    source_name = Column(String(128), nullable=True)               # 来源名（web 类证据的站点/报告名）
    source_url = Column(String(512))
    snippet = Column(Text)                                          # 证据原文片段
    weight = Column(Float, default=1.0)                            # 证据权重（源权威度×新鲜度）
    created_at = Column(DateTime, default=datetime.utcnow)

    job_skill = relationship("JobSkill", back_populates="evidences")


# ------------------------- 能力项变更记录（动态演化） -------------------------
class CapabilityChange(Base):
    __tablename__ = "capability_change"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), index=True)
    version = Column(Integer, default=1)
    change_type = Column(String(16))                               # add/delete/modify
    skill_name = Column(String(128))
    importance = Column(String(16))
    old_value = Column(JSON)
    new_value = Column(JSON)
    reason = Column(Text)                                          # 更新说明
    data_source = Column(JSON)                                      # 数据源
    confidence = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="changes")


# ------------------------- 采集批次台账（合规溯源） -------------------------
class CrawlBatch(Base):
    __tablename__ = "crawl_batch"
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_key = Column(String(64), unique=True, index=True)        # 如 2026W31-bytedance
    platform = Column(String(64), index=True)
    tier = Column(String(16), default="official")                  # official（官网/政府）/dataset/aggregator（主流平台，隔离层）
    method = Column(String(16), default="api")                     # api/html/manual
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    pages = Column(Integer, default=0)
    collected = Column(Integer, default=0)                         # 采到条数
    kept = Column(Integer, default=0)                              # 入库条数
    robots_ok = Column(Boolean, default=True)
    rate_limit_s = Column(Float, default=4.0)
    raw_dir = Column(String(256))                                  # 本地原始留存目录
    notes = Column(Text)


# ------------------------- 权威佐证（政策文件/头部报告） -------------------------
class AuthorityEvidence(Base):
    __tablename__ = "authority_evidence"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), nullable=True, index=True)  # NULL=库级文件
    kind = Column(String(16))                                      # policy（部委文件）/report（头部报告）/trend
    title = Column(String(256))
    issuer = Column(String(128))                                   # 发布机构：人力资源社会保障部/翰德/脉脉...
    publish_date = Column(DateTime, nullable=True)
    url = Column(String(512))
    excerpt = Column(Text)                                          # 引用原文
    local_file = Column(String(256))                               # 本地归档文件路径
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------------- 岗位分级能力画像（初/中/高） -------------------------
class JobLevelSkill(Base):
    __tablename__ = "job_level_skill"
    __table_args__ = (UniqueConstraint(
        "job_id", "level", "recruitment_type", "track", "industry", "skill_id",
        name="uq_job_level_skill_slice"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), index=True)
    level = Column(String(16), index=True)                         # junior/middle/senior
    recruitment_type = Column(String(16), nullable=False, default="unspecified", index=True)
    track = Column(String(32), nullable=False, default="unspecified", index=True)
    industry = Column(String(32), nullable=False, default="general", index=True)
    skill_id = Column(Integer, ForeignKey("skill.id"), index=True)
    importance = Column(String(16), default="required")
    weight = Column(Float, default=0.5)
    level_required = Column(String(32), default="familiar")
    confidence = Column(Float, default=0.0)
    factors = Column(JSON, nullable=True)
    source_count = Column(Integer, default=0)
    jd_count = Column(Integer, default=0)                          # 该级别桶内有效 JD 数
    created_at = Column(DateTime, default=datetime.utcnow)

    skill = relationship("Skill")


# ------------------------- 简历 -------------------------
class Resume(Base):
    __tablename__ = "resume"
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(256))
    candidate_name = Column(String(64))
    raw_text = Column(Text)
    extracted = Column(JSON)                                        # 结构化抽取结果
    skills = Column(JSON)                                           # 技能列表
    years_experience = Column(Float, default=0.0)
    embedding = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------------- 匹配结果 -------------------------
class MatchResult(Base):
    __tablename__ = "match_result"
    id = Column(Integer, primary_key=True, autoincrement=True)
    resume_id = Column(Integer, ForeignKey("resume.id"), index=True)
    job_id = Column(Integer, ForeignKey("job.id"), index=True)
    overall_score = Column(Float, default=0.0)                     # 综合匹配度
    dimension_scores = Column(JSON)                                # 多维度分数
    matched_skills = Column(JSON)
    missing_required = Column(JSON)                                # 缺失必备技能
    missing_bonus = Column(JSON)
    suggestions = Column(JSON)                                      # 改进建议
    learning_path = Column(JSON)                                    # 学习路径
    created_at = Column(DateTime, default=datetime.utcnow)


# ======================= 人才侧图层（2026-08 整改，老师意见⑧）=======================
# 简历语料 → 脱敏人才画像 → 团队盘点 → 技能别名学习。
# 与岗位侧（需求侧）解耦：不写入 job_skill.confidence，不改动置信度公式。


# ------------------------- 简历语料批次台账 -------------------------
class ResumeBatch(Base):
    """简历语料的采集台账。刻意与 CrawlBatch 分表：JD 批次数是对外口径
    （"6 平台 15 批次"），把简历批次混进去会污染那个数字。"""
    __tablename__ = "resume_batch"
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_key = Column(String(64), unique=True, index=True)        # 如 2026W31-res-dataset
    source_type = Column(String(16), index=True)                   # dataset/web/sample
    source_name = Column(String(128))                              # brackozi/Resume、应届毕业生网...
    source_url = Column(String(512))
    license = Column(String(128))                                  # MIT / Apache-2.0 / 页面公开
    tier = Column(String(16), default="dataset")                   # dataset/web/sample
    authority = Column(Float, default=0.7)                         # 来源权威度（仅用于语料排序，不入置信度公式）
    method = Column(String(16), default="api")                     # api/html
    robots_ok = Column(Boolean, default=True)
    rate_limit_s = Column(Float, default=4.0)
    collected = Column(Integer, default=0)                         # 采到条数
    kept = Column(Integer, default=0)                              # 入库条数
    raw_dir = Column(String(256))                                  # 本地归档目录（佐证）
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    notes = Column(Text)


# ------------------------- 脱敏人才画像 -------------------------
class TalentProfile(Base):
    """简历落库后的**唯一**形态：只有技能要素，没有身份。

    刻意不设 raw_text / candidate_name / 任何联系方式列 —— 不是"存了但不填"，
    而是结构上就存不下，延续 resume.redact_for_storage 的隐私最小化口径。
    """
    __tablename__ = "talent_profile"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, index=True)             # 化名编号 T001（对外展示用，不含真名）
    batch_id = Column(Integer, ForeignKey("resume_batch.id"), index=True)
    source_type = Column(String(16), index=True)                   # dataset/web/sample/upload
    source_name = Column(String(128))
    source_url = Column(String(512))                               # 可回溯出处
    license = Column(String(128))
    language = Column(String(8), default="zh", index=True)         # zh/en
    target_cluster = Column(String(64), index=True)                # 映射到 queries.json 的岗位簇
    matched_job_id = Column(Integer, ForeignKey("job.id"), nullable=True, index=True)
    years_experience = Column(Float, default=0.0)
    education = Column(String(64))
    skills = Column(JSON)                                          # 归一化技能名 [str]
    skill_levels = Column(JSON)                                    # {技能: familiar/proficient/expert}
    raw_skill_terms = Column(JSON)                                 # 归一化**前**的原始表述（别名学习的输入）
    skill_count = Column(Integer, default=0)
    text_len = Column(Integer, default=0)                          # 原文长度（只记长度，不记原文）
    text_hash = Column(String(40), index=True)                     # 正文摘要，仅用于去重（不可还原原文）
    quality_score = Column(Float, default=0.0)                     # 语料质量分
    holdout = Column(Boolean, default=False, index=True)           # 是否留出集（不参与别名学习，只做评测）
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------------- 团队与成员 -------------------------
class Team(Base):
    __tablename__ = "team"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), index=True)
    description = Column(Text)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    target_job_id = Column(Integer, ForeignKey("job.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_member"
    __table_args__ = (UniqueConstraint("team_id", "talent_id", name="uq_team_member"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("team.id"), index=True)
    talent_id = Column(Integer, ForeignKey("talent_profile.id"), nullable=True, index=True)
    resume_profile_id = Column(Integer, ForeignKey("resume_profile.id"), nullable=True, index=True)
    display_name = Column(String(64))                              # 化名（如"成员A"），不存真名
    role_label = Column(String(64))                                # 团队内角色标签
    created_at = Column(DateTime, default=datetime.utcnow)

    team = relationship("Team", back_populates="members")
    talent = relationship("TalentProfile")


class TeamEvent(Base):
    __tablename__ = "team_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("team.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    action = Column(String(32), nullable=False, index=True)
    member_id = Column(Integer, nullable=True, index=True)
    details = Column(JSON, nullable=True)
    before_snapshot = Column(JSON, nullable=True)
    after_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ------------------------- 从简历学到的技能表述 -------------------------
class SkillAlias(Base):
    """别名学习台账。status=accepted 的才会回写进归一化词典，
    候选/拒绝的也留档，说明"学了什么、拒了什么、为什么"。"""
    __tablename__ = "skill_alias"
    __table_args__ = (UniqueConstraint("alias", name="uq_skill_alias"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skill.id"), nullable=True, index=True)
    alias = Column(String(128), index=True)                        # 学到的表述
    canonical = Column(String(128))                                # 映射到的规范技能名
    source = Column(String(32), default="resume_corpus")
    talent_count = Column(Integer, default=0)                      # 出现在几份简历里
    status = Column(String(16), default="candidate", index=True)   # candidate/accepted/rejected
    reject_reason = Column(String(128))                            # 拒绝原因（三道护栏哪一道拦的）
    confidence = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


# ======================= 第四轮整改：身份、版本和业务闭环 =======================


class AppUser(Base):
    __tablename__ = "app_user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(512), nullable=False)
    role = Column(String(16), nullable=False, default="user", index=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    # 自助资料：昵称仅用于展示，登录标识仍是 username（不参与鉴权、不唯一约束）。
    nickname = Column(String(64), nullable=True)
    # 站内相对路径（预置图库 /avatars/aNN.webp 或本站上传 /avatars/uNN-<hash>.ext），
    # 刻意不存外部 URL：外链头像等于把任意第三方地址塞进每个页面。
    avatar_url = Column(String(512), nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Organization(Base):
    __tablename__ = "organization"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, unique=True, index=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OrganizationMember(Base):
    __tablename__ = "organization_member"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_org_member"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    role = Column(String(16), nullable=False, default="hr")
    status = Column(String(16), nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class UserSession(Base):
    __tablename__ = "user_session"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(64), nullable=False, index=True)
    target_id = Column(String(64), nullable=True, index=True)
    result = Column(String(16), nullable=False, default="success", index=True)
    summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class UsageEvent(Base):
    __tablename__ = "usage_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    feature = Column(String(64), nullable=False, index=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    success = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Employer(Base):
    __tablename__ = "employer"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, index=True)
    normalized_name = Column(String(128), nullable=False, unique=True, index=True)
    parent_id = Column(Integer, ForeignKey("employer.id"), nullable=True, index=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EmployerAlias(Base):
    __tablename__ = "employer_alias"
    __table_args__ = (UniqueConstraint("normalized_alias", name="uq_employer_alias_normalized"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    employer_id = Column(Integer, ForeignKey("employer.id"), nullable=False, index=True)
    alias = Column(String(128), nullable=False, index=True)
    normalized_alias = Column(String(128), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class JobVersion(Base):
    __tablename__ = "job_version"
    __table_args__ = (UniqueConstraint("job_id", "version", name="uq_job_version"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="published", index=True)
    effective_at = Column(DateTime, nullable=True)
    evidence_window = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    responsibilities = Column(JSON, nullable=True)
    typical_scenarios = Column(JSON, nullable=True)
    contract_snapshot = Column(JSON, nullable=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class JobVersionSkill(Base):
    __tablename__ = "job_version_skill"
    __table_args__ = (UniqueConstraint("job_version_id", "skill_id", name="uq_job_version_skill"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_version_id = Column(Integer, ForeignKey("job_version.id"), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey("skill.id"), nullable=False, index=True)
    capability_cluster = Column(String(128), nullable=True, index=True)
    importance = Column(String(16), nullable=False, default="required")
    status = Column(String(16), nullable=False, default="active")
    weight = Column(Float, nullable=False, default=0.5)
    confidence = Column(Float, nullable=False, default=0.0)
    level_required = Column(String(32), nullable=False, default="familiar")
    factors = Column(JSON, nullable=True)
    evidence_refs = Column(JSON, nullable=True)


class ConfidenceRun(Base):
    """One idempotent full-database confidence calculation at a factual as-of time."""
    __tablename__ = "confidence_run"
    id = Column(Integer, primary_key=True, autoincrement=True)
    as_of = Column(DateTime, nullable=False, unique=True, index=True)
    trigger = Column(String(24), nullable=False, default="scheduled", index=True)
    status = Column(String(16), nullable=False, default="running", index=True)
    formula = Column(String(512), nullable=False)
    job_count = Column(Integer, nullable=False, default=0)
    evidence_count = Column(Integer, nullable=False, default=0)
    valid_jd_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class JobConfidenceSnapshot(Base):
    """Auditable per-job result produced by a confidence run."""
    __tablename__ = "job_confidence_snapshot"
    __table_args__ = (
        UniqueConstraint("run_id", "job_id", name="uq_confidence_snapshot_run_job"),
        UniqueConstraint("job_id", "as_of", name="uq_confidence_snapshot_job_asof"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("confidence_run.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("job.id"), nullable=False, index=True)
    job_version_id = Column(Integer, ForeignKey("job_version.id"), nullable=True, index=True)
    job_version = Column(Integer, nullable=False, default=1)
    as_of = Column(DateTime, nullable=False, index=True)
    evidence_count = Column(Integer, nullable=False, default=0)
    valid_jd_count = Column(Integer, nullable=False, default=0)
    factors = Column(JSON, nullable=False)
    previous_confidence = Column(Float, nullable=False, default=0.0)
    confidence = Column(Float, nullable=False, default=0.0)
    delta = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EvolutionRun(Base):
    __tablename__ = "evolution_run"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_evolution_idem"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    from_version = Column(Integer, nullable=False)
    proposed_version = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    idempotency_key = Column(String(128), nullable=True)
    input_snapshot = Column(JSON, nullable=True)
    proposed_snapshot = Column(JSON, nullable=True)
    diff = Column(JSON, nullable=True)
    stats = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class EvolutionReview(Base):
    __tablename__ = "evolution_review"
    id = Column(Integer, primary_key=True, autoincrement=True)
    evolution_run_id = Column(Integer, ForeignKey("evolution_run.id"), nullable=False, index=True)
    action = Column(String(16), nullable=False, index=True)
    comment = Column(Text, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DiscoveryRun(Base):
    __tablename__ = "discovery_run"
    __table_args__ = (UniqueConstraint("owner_user_id", "idempotency_key", name="uq_discovery_idem"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    query = Column(String(256), nullable=False)
    conditions = Column(JSON, nullable=True)
    evidence_snapshot = Column(JSON, nullable=True)
    signal_snapshot = Column(JSON, nullable=True)
    conclusion = Column(String(32), nullable=False, index=True)
    matched_job_id = Column(Integer, ForeignKey("job.id"), nullable=True, index=True)
    # 后台化状态机：queued -> running -> completed/failed（对齐 EvolutionRun 的列定义）。
    # 默认取 completed 而不是 queued：历史行、展示种子（data/seed_showcase_records.py）与
    # 直接构造 DiscoveryRun 的测试都不传 status，它们代表的是**已经跑完**的同步任务；
    # 默认成 queued 会让这些行在列表里永远显示"排队中"。后台路径显式写 queued。
    status = Column(String(24), nullable=False, default="completed", index=True)
    error = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class JobCandidate(Base):
    __tablename__ = "job_candidate"
    id = Column(Integer, primary_key=True, autoincrement=True)
    discovery_run_id = Column(Integer, ForeignKey("discovery_run.id"), nullable=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="draft", index=True)
    current_revision = Column(Integer, nullable=False, default=1)
    published_job_id = Column(Integer, ForeignKey("job.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class JobCandidateRevision(Base):
    __tablename__ = "job_candidate_revision"
    __table_args__ = (UniqueConstraint("candidate_id", "revision", name="uq_candidate_revision"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("job_candidate.id"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    definition = Column(JSON, nullable=False)
    change_note = Column(String(512), nullable=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CandidateReview(Base):
    __tablename__ = "candidate_review"
    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("job_candidate.id"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    action = Column(String(16), nullable=False, index=True)
    comment = Column(Text, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ResumeProfile(Base):
    __tablename__ = "resume_profile"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    code = Column(String(32), nullable=False, index=True)
    source_type = Column(String(16), nullable=False, default="upload")
    skills = Column(JSON, nullable=False)
    skill_levels = Column(JSON, nullable=True)
    years_experience = Column(Float, nullable=False, default=0.0)
    education = Column(String(64), nullable=True)
    authorized = Column(Boolean, nullable=False, default=False)
    retention_expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MatchRun(Base):
    __tablename__ = "match_run"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    resume_profile_id = Column(Integer, ForeignKey("resume_profile.id"), nullable=True, index=True)
    job_id = Column(Integer, ForeignKey("job.id"), nullable=True, index=True)
    job_version_id = Column(Integer, ForeignKey("job_version.id"), nullable=True, index=True)
    job_version = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="completed", index=True)
    contract_snapshot = Column(JSON, nullable=True)
    result_snapshot = Column(JSON, nullable=False)
    learning_path = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class RecruitmentBatch(Base):
    __tablename__ = "recruitment_batch"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_recruitment_idem"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    target_job_id = Column(Integer, ForeignKey("job.id"), nullable=False, index=True)
    target_job_version_id = Column(Integer, ForeignKey("job_version.id"), nullable=True, index=True)
    target_job_version = Column(Integer, nullable=False)
    contract_snapshot = Column(JSON, nullable=True)
    status = Column(String(24), nullable=False, default="created", index=True)
    total_count = Column(Integer, nullable=False, default=0)
    processed_count = Column(Integer, nullable=False, default=0)
    succeeded_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class BatchCandidate(Base):
    __tablename__ = "batch_candidate"
    __table_args__ = (UniqueConstraint("batch_id", "file_hash", name="uq_batch_candidate_file"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("recruitment_batch.id"), nullable=False, index=True)
    resume_profile_id = Column(Integer, ForeignKey("resume_profile.id"), nullable=True, index=True)
    file_hash = Column(String(64), nullable=False)
    display_code = Column(String(32), nullable=False)
    parse_status = Column(String(24), nullable=False, default="pending", index=True)
    error_code = Column(String(32), nullable=True)
    error_detail = Column(String(256), nullable=True)
    overall_score = Column(Float, nullable=True, index=True)
    dimension_scores = Column(JSON, nullable=True)
    result_snapshot = Column(JSON, nullable=True)
    rank = Column(Integer, nullable=True)
    note = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CandidateSelection(Base):
    __tablename__ = "candidate_selection"
    __table_args__ = (UniqueConstraint("batch_candidate_id", "team_id", name="uq_candidate_selection"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_candidate_id = Column(Integer, ForeignKey("batch_candidate.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("team.id"), nullable=False, index=True)
    selected_by = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    before_coverage = Column(Float, nullable=True)
    after_coverage = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FeedbackTicket(Base):
    __tablename__ = "feedback_ticket"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    target_type = Column(String(32), nullable=False, index=True)
    target_id = Column(String(64), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="submitted", index=True)
    current_revision = Column(Integer, nullable=False, default=1)
    applied_record_type = Column(String(64), nullable=True)
    applied_record_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class FeedbackRevision(Base):
    __tablename__ = "feedback_revision"
    __table_args__ = (UniqueConstraint("ticket_id", "revision", name="uq_feedback_revision"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("feedback_ticket.id"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    category = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)
    created_by = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FeedbackEvent(Base):
    """Append-only feedback workflow event, including reviewer opinions."""
    __tablename__ = "feedback_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("feedback_ticket.id"), nullable=False, index=True)
    event_type = Column(String(24), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    revision = Column(Integer, nullable=True)
    comment = Column(Text, nullable=True)
    applied_record_type = Column(String(64), nullable=True)
    applied_record_id = Column(String(64), nullable=True)
    actor_user_id = Column(Integer, ForeignKey("app_user.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
