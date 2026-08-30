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


# --------------------------------------------------------------- 标题相关性闸门
# 采集侧的关键词检索**命中 JD 正文**、不只是标题，而 cluster_hint 纯按检索词打上去。
# 于是「联通广东省分公司·项目经理岗」正文里提了一句车联网，就会被当成车联网系统工程师的
# 语料；实测全库 1494 个 (JD,岗位) 对里有 214 个（14.3%）是这样挂上去的，最严重的是
# 数字人训练师 46.7%、提示词工程师 45.5%、AIGC 43.8%。领域判定（import_raw.is_it_domain）
# 拦不住它们——那些 JD 正文技术词很多，会被放行。所以在标题这一层再加一道：
# **标题必须自带该岗位簇的领域词，正文命中不算。**
# 入库侧 data/import_raw.py 直接 import 本模块的 TITLE_OK/title_on_target；建图侧
# services/ingest 只复用 NON_ENG。data/repair_offtarget_r6.py 另有一份冻结副本（一次性
# 修复脚本，按当时口径回溯，故意不跟随本表变化）——改本表时不需要同步它。
_TITLE_OK_SRC: dict[str, str] = {
    "自动驾驶": r"自动驾驶|智驾|智能驾驶|辅助驾驶|无人驾驶|无人车|感知算法|点云|激光雷达|BEV|"
                r"决策规划|规控|域控|SLAM|定位与建图|端到端算法",
    "机器人算法": r"机器人|机械臂|运动控制|运控|导航算法|SLAM|具身|AGV|AMR|无人系统|多足|人形",
    "多模态": r"多模态|视觉语言|VLM|VLA|跨模态|图文|文生图|文生视频|视频生成|图像生成",
    "智能硬件": r"硬件|嵌入式|固件|电路|驱动开发|智能终端|可穿戴|整机|结构设计|BSP|单片机|MCU|FPGA",
    "车联网": r"车联网|智能网联|座舱|车载|T-?Box|AUTOSAR|域控|整车|车规|汽车电子|TSP|车云|OTA",
    "嵌入式": r"嵌入式|固件|BSP|驱动|RTOS|单片机|MCU|Linux内核",
    "AIGC": r"AIGC|生成式|文生|图像生成|视频生成|扩散|Diffusion|数字内容",
    "数据分析": r"数据分析|商业分析|BI|数据洞察|经营分析",
    "大数据平台": r"大数据|数据平台|实时计算|流计算|数据中台|Flink|Spark",
    "物联网": r"物联网|IoT|MQTT|设备接入|边缘网关|传感|终端接入",
    "计算机视觉": r"计算机视觉|机器视觉|视觉算法|视觉感知|图像算法|图像处理|CV算法|OCR|"
                  r"目标检测|图像分割|3D重建|点云|视频算法",
    "推荐算法": r"推荐|搜索|排序|召回|特征工程|广告算法",
    "数据仓库": r"数仓|数据仓库|数据治理|数据质量|ETL|数据建模",
    "AI产品": r"(?=.*(?:AI|人工智能|大模型|大语言模型|LLM|智能体|Agent|AIGC|生成式))"
              r"(?=.*(?:产品经理|产品总监|产品负责人|产品策划))",
    "运维开发": r"运维|SRE|可观测|监控|稳定性|DevOps|发布|值班",
    "自然语言处理": r"自然语言|NLP|对话|语义|文本|大语言模型|LLM",
    "深度学习": r"深度学习|神经网络|模型训练|端侧推理|模型压缩|算子|推理引擎",
    "数据开发": r"数据开发|数据研发|数据管道|数据集成|ETL|数仓开发",
    "云计算": r"云计算|云原生|云平台|Kubernetes|K8s|虚拟化|云网络|网络架构|IaaS|PaaS",
    "工业互联网": r"工业互联网|工业物联网|工业软件|MES|SCADA|PLC|OPC|工业控制|数采|产线",
    "提示词工程": r"提示词|Prompt|大模型应用|AI应用",
    "边缘计算": r"边缘计算|边缘|端侧|端边|网关",
    # 以下 10 簇原表没有，退化成「命中任一簇」等于没闸。数字人 / 数字孪生两条按
    # detach_offtarget_r5 的实测结论收紧：BIM / CAE仿真 / 语音合成算法不是这两个岗位。
    "Java开发": r"Java|后端|服务端|后台开发|微服务|Spring",
    "后端开发": r"后端|服务端|后台开发|微服务|Golang|Go开发|Java|Python开发|C\+\+开发|服务器开发",
    "机器学习": r"机器学习|深度学习|模型训练|算法工程师|特征工程|建模|MLOps|ML平台",
    "大模型算法": r"大模型|大语言模型|LLM|预训练|SFT|RLHF|对齐|AGI|GPT|生成式",
    "大模型推理优化": r"推理优化|推理加速|推理引擎|模型压缩|量化|算子|性能优化|部署优化|"
                      r"大模型推理|LLM推理|vLLM|TensorRT",
    "大模型测试": r"测试|评测|质量|QA工程师",
    "智能体开发": r"智能体|Agent|RAG|工具调用|工作流编排|MCP|Copilot|大模型应用",
    "具身智能": r"具身|Embodied|人形|机器人大模型|VLA|操作策略|运动控制",
    "数字人": r"数字人|虚拟人|虚拟数字|数字主播|虚拟形象|数字员工|AI训练师|人工智能训练师|数据标注",
    "数字孪生": r"数字孪生|Digital ?Twin|虚实融合|CIM平台|孪生体",
}
TITLE_OK: dict[str, re.Pattern] = {k: re.compile(v, re.I) for k, v in _TITLE_OK_SRC.items()}
# 任一簇命中即可——用于 --full-catalog 那类没有 cluster_hint 的行。
_TITLE_ANY = re.compile("|".join(f"(?:{v})" for v in _TITLE_OK_SRC.values()), re.I)
# 明显非研发的标题。「视觉设计 / 视觉传达 / 平面设计」必须留在这里，否则会被计算机视觉的
# 「视觉」误收；这是实测踩到过的误杀，别删。
NON_ENG = re.compile(
    r"视觉设计|视觉传达|平面设计|美育|活动设计|"
    r"产品经理|设计师|采购|销售|运营|人力|财务|法务|市场部|品牌|行政|客服|"
    r"培训|讲师|BD岗|商务|投资|战略|公关|供应链|翻译|文案|编辑|主播|摄影|"
    r"会计|出纳|审计|护士|医师|教师|司机|保安|厨师|前台|秘书|操作工|"
    r"装配|叉车|钳工|焊工|电工|质检员|库管|仓管|专员|主管|经理岗")


def title_on_target(title: str, cluster: str | None) -> bool:
    """标题是否真的属于这个岗位簇。cluster 为 None 时退化为「命中任一簇」。"""
    t = title or ""
    # AI产品的标题正则已同时要求 AI 技术限定词 + 产品角色词，可安全豁免「产品经理」；
    # 数字人不豁免，否则「数字人产品经理/销售专员」也会绕过非研发过滤。
    if cluster != "AI产品" and NON_ENG.search(t):
        return False
    if cluster:
        pattern = TITLE_OK.get(cluster)
        # 未知簇必须失败关闭：退回 _TITLE_ANY 等于「命中任一簇即放行」，
        # 一旦 queries.json 新增一个簇却忘了配 TITLE_OK，这道闸门就自动全开。
        return bool(pattern.search(t)) if pattern else False
    return bool(_TITLE_ANY.search(t))
