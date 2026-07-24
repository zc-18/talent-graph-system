"""数据库 ORM 模型 —— 岗位能力知识图谱核心 schema。

图谱节点：Job（岗位）、Skill（技能点）、TechTrend（技术趋势）
图谱关系：JobSkill（岗位-技能）、SkillRelation（技能-技能：先修/相关/驱动）
证据与溯源：RawJD（原始招聘数据）、Evidence（能力项证据，反幻觉溯源）
演化追踪：CapabilityChange（能力项变更记录）
匹配：Resume（简历）、MatchResult（匹配结果）
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
    __table_args__ = (UniqueConstraint("job_id", "level", "skill_id", name="uq_job_level_skill"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job.id"), index=True)
    level = Column(String(16), index=True)                         # junior/middle/senior
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
