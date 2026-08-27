"""请求/响应 Pydantic 模型。"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any


class SkillItem(BaseModel):
    name: str
    importance: str = "required"
    weight: float = 0.5
    level_required: str = "familiar"
    confidence: float | None = None


class JobUpsert(BaseModel):
    name: str
    category: str = "人工智能"
    level: str = "middle"
    track: str = "unspecified"
    industry: str = "general"
    recruitment_type: str = "mixed"
    summary: str = ""
    core_responsibilities: list[str] = []
    typical_scenarios: list[str] = []
    required_skills: list[SkillItem] = []
    bonus_skills: list[SkillItem] = []
    is_new: bool = False


class DiscoverRequest(BaseModel):
    keyword: str
    save: bool = False


class DefineRequest(BaseModel):
    keyword: str
    evidence: list[dict] = []
    save: bool = True


class EvolveRequest(BaseModel):
    job_id: int
    new_jds: list[str] = []          # 新增 JD 文本，用于驱动演化
    use_web: bool = True


class MatchRequest(BaseModel):
    job_id: int | None = None
    target_job_text: str | None = Field(default=None, max_length=20000)
    seniority: str | None = None
    recruitment_type: str | None = None
    track: str | None = None
    industry: str | None = None
    skills: list[str] = []
    skill_levels: dict[str, str] = {}
    resume_text: str | None = Field(default=None, max_length=100_000)
    generate_suggestions: bool = True
    save: bool = True

    @model_validator(mode="after")
    def validate_target(self):
        if self.job_id is None and not (self.target_job_text or "").strip():
            raise ValueError("job_id 与 target_job_text 至少提供一个")
        if self.job_id is not None and (self.target_job_text or "").strip():
            raise ValueError("job_id 与 target_job_text 不能同时提供")
        return self


class ResumeTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


class ManualSkillEdit(BaseModel):
    job_id: int
    action: str                       # add / remove / update
    skill_name: str
    importance: str = "required"
    weight: float = 0.5
    level_required: str = "familiar"
    reason: str = "人工优化"


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = "user"
    organization_name: str | None = Field(default=None, max_length=128)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in {"user", "hr"}:
            raise ValueError("公开注册仅支持 user/hr")
        return value


class LoginRequest(BaseModel):
    username: str
    password: str


class DiscoveryRunRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=256)
    conditions: dict[str, Any] = Field(default_factory=dict)
    track: str | None = None
    industry: str | None = None
    seniority: str | None = None
    recruitment_type: str | None = None
    keywords: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=128)


class CandidatePatchRequest(BaseModel):
    definition: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=512)


class CandidateReviewRequest(BaseModel):
    action: str
    comment: str | None = None
    publish: bool = False

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in {"approve", "reject"}:
            raise ValueError("action 必须是 approve/reject")
        return value


class EvolutionRunRequest(BaseModel):
    job_id: int
    evidence_batch: dict[str, Any] = Field(default_factory=dict)
    proposed_snapshot: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class EvolutionProposeRequest(BaseModel):
    proposed_snapshot: dict[str, Any] = Field(default_factory=dict)
    evidence_batch: dict[str, Any] = Field(default_factory=dict)


class EvolutionReviewRequest(BaseModel):
    action: str
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in {"approve", "reject"}:
            raise ValueError("action 必须是 approve/reject")
        return value


class RecruitmentBatchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    target_job_id: int
    idempotency_key: str | None = Field(default=None, max_length=128)


class CandidateSelectRequest(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=100)
    team_id: int | None = None


class CandidateSkillsCorrectionRequest(BaseModel):
    skills: list[str] = Field(min_length=1, max_length=100)
    skill_levels: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = False
    note: str | None = Field(default=None, max_length=512)

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            skill = value.strip()
            if not skill or len(skill) > 64:
                raise ValueError("技能名不能为空且不超过 64 字符")
            if skill not in cleaned:
                cleaned.append(skill)
        return cleaned


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    target_job_id: int


class TeamMemberRequest(BaseModel):
    resume_profile_id: int
    display_name: str = Field(min_length=1, max_length=64)
    role_label: str | None = Field(default=None, max_length=64)


class FeedbackCreateRequest(BaseModel):
    target_type: str = Field(min_length=1, max_length=32)
    target_id: str | None = Field(default=None, max_length=64)
    category: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=5000)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackReviewRequest(BaseModel):
    action: str
    comment: str | None = Field(default=None, max_length=2000)
    applied_record_type: str | None = Field(default=None, max_length=64)
    applied_record_id: str | None = Field(default=None, max_length=64)

    @field_validator("action")
    @classmethod
    def validate_feedback_action(cls, value: str) -> str:
        if value not in {"triage", "approve", "reject", "apply"}:
            raise ValueError("action 必须是 triage/approve/reject/apply")
        return value
