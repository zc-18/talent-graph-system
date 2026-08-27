"""Structured job-query resolution and track conflict guards."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


TRACKS = ("software", "hardware", "algorithm", "data", "ops", "product")
INDUSTRIES = ("internet", "automotive", "medical_device", "manufacturing", "general")
SENIORITIES = ("junior", "middle", "senior", "unspecified")
RECRUITMENT_TYPES = ("campus", "social", "unspecified")


@dataclass(frozen=True)
class JobQuery:
    raw_query: str
    canonical_title: str
    track: str
    industry: str
    seniority: str
    recruitment_type: str
    keywords: list[str] = field(default_factory=list)
    requires_disambiguation: bool = False
    candidates: list[dict] = field(default_factory=list)
    matched_alias: str | None = None
    is_established: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_ALIASES: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"^(?:java)$|(?:java).*(?:开发|工程师)|(?:开发|工程师).*java", re.I), "Java开发工程师", "software"),
    (re.compile(r"^(?:前端|web前端)$|(?:前端|web前端).*(?:开发|工程师)", re.I),
     "前端开发工程师", "software"),
    (re.compile(r"(?:后端|服务端).*(?:开发|工程师)"), "后端开发工程师", "software"),
    (re.compile(r"(?:软件|接口|功能).*系统测试|系统测试.*(?:软件|接口|功能)"),
     "系统测试工程师", "software"),
    (re.compile(r"(?:软件|接口|功能).*测试|测试.*(?:软件|接口|功能)"),
     "软件测试工程师", "software"),
    (re.compile(r"自动化测试"), "自动化测试工程师", "software"),
    (re.compile(r"(?:硬件|电路|芯片|电子|可靠性).*(?:系统)?测试|(?:系统)?测试.*(?:硬件|电路|芯片|电子|可靠性)"), "硬件系统测试工程师", "hardware"),
    (re.compile(r"(?:汽车|车载|医疗器械|医疗设备|制造|工业).*测试|测试.*(?:汽车|车载|医疗器械|医疗设备|制造|工业)"),
     "行业测试工程师", "hardware"),
    (re.compile(r"云(?:计算|原生).*(?:开发|工程师)?"), "云计算工程师", "ops"),
    (re.compile(r"(?:ai\s*agent|智能体).*(?:开发|工程师)?|agent.*(?:开发|工程师)", re.I),
     "AI智能体开发工程师", "algorithm"),
    (re.compile(r"大模型.*(?:算法|训练).*(?:工程师)?"), "大模型算法工程师", "algorithm"),
    (re.compile(r"(?:提示词|prompt).*(?:工程师|开发)?", re.I), "提示词工程师", "algorithm"),
    (re.compile(r"生成式人工智能.*测试"), "生成式人工智能系统测试员", "software"),
    (re.compile(r"数据(?:开发|工程师)"), "大数据开发工程师", "data"),
    (re.compile(r"(?:etl|数据仓库).*(?:开发|工程师)?", re.I), "ETL开发工程师", "data"),
    (re.compile(r"(?:sre|运维开发|运维工程师)", re.I), "运维开发工程师(SRE)", "ops"),
)

_ESTABLISHED_TITLES = {
    "Java开发工程师", "后端开发工程师", "软件测试工程师", "自动化测试工程师",
    "硬件系统测试工程师", "云计算工程师", "AI智能体开发工程师", "大模型算法工程师",
    "提示词工程师", "生成式人工智能系统测试员", "大数据开发工程师", "运维开发工程师(SRE)",
    "前端开发工程师", "Python开发工程师", "C++开发工程师", "算法工程师",
    "系统测试工程师", "行业测试工程师", "ETL开发工程师",
    "机器学习工程师", "深度学习工程师", "自然语言处理工程师", "计算机视觉工程师",
    "数据分析师", "数据仓库工程师", "嵌入式软件工程师", "物联网开发工程师",
}

_AMBIGUOUS_TEST = re.compile(r"^(?:初级|中级|高级|资深|校招|社招|应届|实习)*\s*(?:系统)?测试工程师$")
_TEST_CANDIDATES = [
    {"canonical_title": "软件测试工程师", "track": "software", "reason": "功能、接口、自动化与软件质量"},
    {"canonical_title": "硬件系统测试工程师", "track": "hardware", "reason": "电路、芯片、可靠性与电磁兼容"},
    {"canonical_title": "系统测试工程师", "track": "software", "reason": "软件系统级联调与端到端验证"},
    {"canonical_title": "行业测试工程师", "track": "hardware", "reason": "汽车、医疗器械或制造行业专项验证"},
]


def _first(patterns: tuple[tuple[str, tuple[str, ...]], ...], text: str, default: str) -> str:
    for value, words in patterns:
        if any(word.lower() in text.lower() for word in words):
            return value
    return default


def resolve_job_query(query: str, known_jobs: set[str] | None = None) -> JobQuery:
    """Parse a free-text title into the dimensions used for JD bucketing."""
    raw = (query or "").strip()
    seniority = _first((
        ("junior", ("初级", "助理", "junior", "应届", "实习")),
        ("senior", ("高级", "资深", "专家", "首席", "senior", "staff", "principal")),
        ("middle", ("中级", "middle",)),
    ), raw, "unspecified")
    recruitment = _first((
        ("campus", ("校招", "校园招聘", "应届", "实习")),
        ("social", ("社招", "社会招聘",)),
    ), raw, "unspecified")
    industry = _first((
        ("automotive", ("汽车", "车载", "智能座舱", "车联网", "automotive")),
        ("medical_device", ("医疗器械", "医疗设备", "医械")),
        ("manufacturing", ("制造", "工业", "工厂", "mes")),
        ("internet", ("互联网", "电商", "游戏", "平台")),
    ), raw, "general")

    stripped = re.sub(
        r"初级|中级|高级|资深|专家|首席|助理|实习|校招|校园招聘|社招|社会招聘|应届|"
        r"Junior|Middle|Senior|Staff|Principal", "", raw, flags=re.I).strip(" -_—")
    if _AMBIGUOUS_TEST.fullmatch(stripped):
        return JobQuery(raw, "测试工程师", "software", industry, seniority, recruitment,
                        requires_disambiguation=True, candidates=list(_TEST_CANDIDATES))

    canonical, track, alias = stripped or raw, "software", None
    for pattern, title, title_track in _ALIASES:
        if pattern.search(stripped):
            canonical, track, alias = title, title_track, pattern.pattern
            break
    if canonical == stripped:
        track = _first((
            ("hardware", ("硬件", "嵌入式", "芯片", "电路", "电子")),
            ("algorithm", ("算法", "模型", "人工智能", "ai")),
            ("data", ("数据", "数仓", "etl")),
            ("ops", ("运维", "云", "sre", "devops")),
            ("product", ("产品", "经理")),
        ), stripped, "software")
    keywords = [part for part in re.split(r"[\s,，、;/]+", stripped) if part and part != canonical]
    established = canonical in ((known_jobs or set()) | _ESTABLISHED_TITLES)
    return JobQuery(raw, canonical, track, industry, seniority, recruitment,
                    keywords=keywords, matched_alias=alias, is_established=established)


_TRACK_CONFLICTS = {
    "software": ("电磁兼容", "emc", "电路设计", "模拟电路", "数字电路", "示波器", "焊接", "射频"),
    "hardware": ("selenium", "pytest", "cypress", "playwright", "postman", "接口自动化", "web测试"),
}


def track_conflict(track: str, skill_name: str) -> bool:
    """Whether a skill is a hard off-track conflict for a role slice."""
    value = (skill_name or "").casefold()
    return any(term.casefold() in value for term in _TRACK_CONFLICTS.get(track, ()))


_ROLE_CONFLICTS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"Java开发|后端开发", re.I),
     ("HTML", "CSS", "jQuery", "Vue", "React前端", "JavaScript DOM")),
    (re.compile(r"软件测试|自动化测试|生成式人工智能系统测试"),
     _TRACK_CONFLICTS["software"]),
    (re.compile(r"硬件.*测试"), _TRACK_CONFLICTS["hardware"]),
)


def role_skill_conflict(job_name: str, track: str, skill_name: str) -> bool:
    if track_conflict(track, skill_name):
        return True
    value = (skill_name or "").casefold()
    return any(pattern.search(job_name or "")
               and any(term.casefold() in value for term in terms)
               for pattern, terms in _ROLE_CONFLICTS)


def established_job_titles() -> frozenset[str]:
    return frozenset(_ESTABLISHED_TITLES)
