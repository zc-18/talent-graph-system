"""请求/响应 Pydantic 模型。"""
from __future__ import annotations
from pydantic import BaseModel
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
    job_id: int
    skills: list[str] = []
    skill_levels: dict[str, str] = {}
    resume_text: str | None = None
    generate_suggestions: bool = True


class ManualSkillEdit(BaseModel):
    job_id: int
    action: str                       # add / remove / update
    skill_name: str
    importance: str = "required"
    weight: float = 0.5
    level_required: str = "familiar"
    reason: str = "人工优化"
