"""每日动态挖掘（模拟聚合源）的**写库范围防护**与漏斗回归测试（内存 SQLite，不碰云库）。

`services/mining.py` 每天消费一个 1000 行的离线模拟聚合语料分片，往公开图谱里补一层
增量观测。它的安全契约写在模块头部与 `models.py` 三张观测表上方，核心只有一句：

    只 INSERT，且只碰 skill / job_skill / evidence 三张公开表 —— 外加它自己的
    daily_mining_run / daily_mining_item / daily_skill_delta。

`job` / `raw_jd` / `crawl_batch` / `capability_change` 以及**任何已存在的 job_skill 行**
跑完必须与跑之前逐字节一致。这不是洁癖：

* `graph_service.stats_overview` 对外的「JD 总数」就是 `count(RawJD)`，掺进模拟语料会让
  所有已交付材料的头条数字失真；
* `confidence_batch` 只按 `status=='active'` 的行重算 `job.confidence`，所以「只建
  candidate、不动既有行」这件事在构造上就保证了夜间批算不会因本模块漂移；
* 日志与库表事实分家在本项目出过两次事故（见 tests/test_evolution_guards.py），
  所以 `capability_change` 一行都不许写。

本文件里最重要的一个用例是 `test_apply_run_touches_only_the_insert_whitelist`：它对上述
四张表 + 所有既有 job_skill/evidence/skill 行做**跑前跑后校验和比对**，任何差异即失败，
并且同时断言这一轮确实写进去了东西（否则防护会因为「什么都没干」而假性通过）。

其余用例锁住切分与门禁的具体口径：括号当分隔符（不是删掉）、单字符白名单 C/R、
停用词不误杀真实技能、精确+近重复去重、岗位归一漏斗可解释、预算闸、以及三个上限。
LLM 一律走替身客户端，**本文件不发任何网络请求**。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.config import settings
from app.db import Base
from app.routers import mining as mining_router
from app.services import cleaning, ingest, mining, taxonomy
from app.services.graph_service import MAX_EVIDENCE_PER_SKILL, slugify

REAL_SHARD_DIR = Path(mining.__file__).resolve().parents[2] / "data" / "aggregate_source"
REAL_STOPWORDS = REAL_SHARD_DIR / "stopwords.json"

# 语料里真实出现、必须活下来的技能名（`taxonomy.normalize_skill` 对这批是恒等映射）
REAL_SKILLS_THAT_MUST_SURVIVE = [
    "Python", "Java", "Spark", "Hive", "C", "R", "C++", "Go", "SQL", "Linux",
    "Docker", "Kubernetes", "Redis", "Flink", "PyTorch", "TensorFlow",
    "深度学习", "机器学习", "计算机视觉", "自然语言处理", "目标检测", "算法设计",
    "性能优化", "架构设计经验", "分布式系统", "数据仓库", "数据库设计", "模型部署",
    "单元测试", "嵌入式开发", "源码", "英语能力", "项目管理",
]
# 库里已有的**粗粒度**概念名（`Skill.parent_id IS NULL`）。新技能点只有和其中之一
# 在同一行技能标签里共现才会建节点（见 mining.py 头部「新技能点必须挂到粗粒度父概念下」），
# 所以任何「要真的入图」的 fixture 都必须把这里的名字放进技能标签里。
# 两个名字的排序关系是 tie-break 用例的构造前提："分布式系统" < "深度学习"。
COARSE_CV = "深度学习"              # taxonomy 分类：人工智能
COARSE_BACKEND = "分布式系统"        # taxonomy 分类：云计算与工程
# 语料里真实出现、必须被拦住的非技能噪声
REAL_NOISE_THAT_MUST_BE_BLOCKED = [
    "计算机相关专业", "无明显侧重", "获奖", "不接受居家办公", "五险一金", "应届生",
    "学历不限", "专业不限", "3年以上工作经验", "参加算法相关竞赛", "包吃住",
    "周末双休", "不限", "数学", "计算机",
]


# --------------------------------------------------------------------- 基础设施

def _fresh_session():
    """一个全新的内存库 —— 需要在同一个用例里比对两次独立运行时用。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def db():
    s = _fresh_session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """把本文件钉死在离线状态。

    1. `_llm_client_cache` 预置 None —— `_llm_client()` 命中缓存直接返回 None，
       连 OpenAI 客户端都不会构造，因此即使本机 .env 里有真 key 也打不出去；
       需要 LLM 的用例自己再 setattr 一个替身覆盖它。
    2. `_PARSE_CACHE` 预置空 dict —— 否则 `_cache_skills` 会去读 9MB 的
       `data/parsed_cache_real.json`，既慢又会让「该走 LLM 的行」被缓存悄悄填上。
    """
    monkeypatch.setattr(mining, "_llm_client_cache", [None])
    monkeypatch.setattr(mining, "_PARSE_CACHE", [{}])
    monkeypatch.setattr(settings, "mining_source_label", "BOSS直聘")
    monkeypatch.setattr(settings, "mining_daily_budget_cny", 10.0)
    monkeypatch.setattr(settings, "mining_llm_batch_size", 10)


class StubLLM:
    """最小 DeepSeek 客户端替身：只记调用、只吐预置 JSON，**永不联网**。"""

    def __init__(self, payloads, *, prompt_tokens=0, completion_tokens=0):
        self.payloads = list(payloads)
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        content = json.dumps(payload, ensure_ascii=False)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=self.prompt_tokens,
                                  completion_tokens=self.completion_tokens))


def _row(title="计算机视觉算法工程师", *, row_no=1, body="", tags="",
         category="人工智能", domain="智能硬件") -> dict:
    """构造一条与真实分片同形状的语料行。"""
    return {
        "platform": "boss_sim", "company": None, "job_title": title,
        "location": None, "salary_range": None, "experience_req": None,
        "education_req": None, "publish_date": None, "url": None,
        "crawled_at": "2026-09-02T13:42:09", "raw_text": body,
        "extra": {"row_no": row_no, "job_category": category,
                  "company_domain": domain, "skill_tags": tags},
    }


def _write_manifest(directory: Path, shards: int) -> Path:
    """写一份最小 manifest.json —— `shard_count` 只认它，不数目录里的文件。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(json.dumps({
        "source_label": "BOSS直聘", "platform": "boss_sim", "rows_per_shard": 1000,
        "shards": [{"index": i, "file": f"boss_sim_{i:03d}.jsonl", "rows": 1000,
                    "first_row_no": i * 1000 + 1, "last_row_no": (i + 1) * 1000}
                   for i in range(shards)],
    }, ensure_ascii=False), "utf-8")
    return path


def _write_shard(directory: Path, rows: list[dict], *, index: int = 0,
                 stopwords: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = mining.shard_path(index, directory)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    if stopwords is not None:
        (directory / "stopwords.json").write_text(
            json.dumps(stopwords, ensure_ascii=False), "utf-8")
    return path


# 长度必须 ≥ MIN_BODY_CHARS(50) 才过正文长度门；彼此差异要够大，免得被 SimHash 判近重复。
BODY_CV = ("岗位职责：负责图像分类与目标检测算法的研发落地，围绕嵌入式端侧推理做算子裁剪"
           "与量化压缩，配合业务方完成算法工程化交付与线上效果持续优化工作。")
BODY_JAVA = ("岗位职责：承担交易中台核心服务的接口设计与编码实现，梳理历史遗留模块的调用"
             "链路，推动缓存与消息队列的治理，保障大促期间的稳定性与响应时延。")
BODY_DATA = ("岗位职责：搭建经营分析看板与指标体系，对渠道投放、用户留存做专题分析，输出"
             "可执行的策略建议，并与产品运营团队一起复盘活动效果与增长假设。")
BODY_SHOP = ("岗位职责：负责门店日常排班与库存盘点，管理原物料损耗，跟进顾客投诉处理与"
             "满意度回访，组织店员培训并完成月度营业额与毛利指标的达成。")


# 短正文行：< MIN_BODY_CHARS 但带技能标签，靠标签活下来（全语料 28.2% 是这个形态）。
# 归一后长度 < NEAR_DUP_MIN_CHARS，因此天然豁免 SimHash，做多行 fixture 最省事。
def _short_body(i: int) -> str:
    return f"方向{i}。"


def _cooc_rows(parent: str, children: list[str], *, title="计算机视觉算法工程师",
               filler: str = "其它方向词", start: int = 1,
               domain: str = "智能硬件") -> list[dict]:
    """构造能让 `children` 真的挂到 `parent` 下的最小行集（3 行）。

    父概念判据不是「同一行里出现过粗粒度概念」这么松 —— 是 **PMI + 最小支持度**：

        c >= mining.PARENT_MIN_COOC        # 同格行数
        log(c * N / (f_child * f_parent)) >= mining.PARENT_MIN_PMI

    N 只数「有技能的行」。所以 2 行全同格（c=2, N=2, f=2/2）算出来 PMI 恰好是 0，
    **过不了门**；必须再来一行两者都不出现的语料把 N 撑到 3：
    PMI = log(2*3/(2*2)) = 0.405。这套下限是为了挡住高频节点通吃
    （Java/C++/Python 曾拿走 33% 的挂靠，挂出「营养学 → LangChain」这种错亲），
    所以「单行共现就认亲」在这里是**故意**不成立的。
    """
    # 自检：这套构造必须真的过得了两道门。有人调高门限时，失败信息直接指向
    # 「fixture 要多补几行填充行」，而不是散成一片「skills_created == 0」让人从头查。
    n_rows, c, f_child, f_parent = 3, 2, 2, 2
    assert c >= mining.PARENT_MIN_COOC and math.log(
        (c * n_rows) / (f_child * f_parent)) >= mining.PARENT_MIN_PMI, (
        f"_cooc_rows 的 3 行构造已经过不了父概念门"
        f"（PARENT_MIN_COOC={mining.PARENT_MIN_COOC}，"
        f"PARENT_MIN_PMI={mining.PARENT_MIN_PMI}）：多补几行填充行再试")
    tag = "、".join([parent] + children)
    return [
        _row(title, row_no=start, body=_short_body(1), tags=tag, domain=domain),
        _row(title, row_no=start + 1, body=_short_body(2), tags=tag, domain=domain),
        # 第三行：父与子都不出现，只为把 N 撑起来（filler 挂不上父概念，只留观测层）
        _row(title, row_no=start + 2, body=_short_body(3), tags=filler, domain=domain),
    ]


# --------------------------------------------------------------------- 库内小图谱

def _seed_job(db, name: str, **kw) -> models.Job:
    job = models.Job(name=name, slug=slugify(name),
                     category=kw.pop("category", "人工智能"),
                     level="middle", version=1, is_new=False,
                     confidence=kw.pop("confidence", 0.61),
                     evidence_count=kw.pop("evidence_count", 42), **kw)
    db.add(job)
    db.flush()
    return job


def _seed_skill(db, name: str, *, category="人工智能", parent_id=None) -> models.Skill:
    """默认建成**粗粒度**节点（parent_id=None）—— 它才能当新技能点的父概念。"""
    sk = models.Skill(name=name, normalized_name=name, category=category,
                      skill_type="hard", parent_id=parent_id)
    db.add(sk)
    db.flush()
    return sk


def _seed_rel(db, job, skill, *, status="candidate", evidence=(), **kw) -> models.JobSkill:
    rel = models.JobSkill(
        job_id=job.id, skill_id=skill.id,
        importance=kw.pop("importance", "required"), weight=kw.pop("weight", 0.7),
        level_required="proficient", confidence=kw.pop("confidence", 0.55),
        source_count=kw.pop("source_count", 3), status=status,
        first_seen=datetime(2026, 5, 1), last_seen=datetime(2026, 6, 1), **kw)
    db.add(rel)
    db.flush()
    for stype, sname in evidence:
        db.add(models.Evidence(
            job_skill_id=rel.id, raw_jd_id=None if stype == "dataset" else 1,
            source_type=stype, source_name=sname,
            source_url=None if stype == "dataset" else "https://example.invalid/x",
            snippet="预置证据", weight=0.5, created_at=datetime(2026, 6, 1)))
    db.flush()
    return rel


def _seed_graph(db) -> dict:
    """一个够小但覆盖四种「既有关系」形态的图谱，外加四张禁写表各若干行。"""
    cv = _seed_job(db, "计算机视觉工程师")
    java = _seed_job(db, "Java开发工程师", category="智能软件")

    s_py = _seed_skill(db, "Python")
    s_spark = _seed_skill(db, "Spark")
    s_java = _seed_skill(db, "Java")
    s_redis = _seed_skill(db, "Redis")

    # A：active + 外源 jd 证据 —— 经雇主交叉验证的行，一个字段都不许碰、也不许补证据
    rel_active = _seed_rel(db, cv, s_py, status="active", confidence=0.87,
                           source_count=5, evidence=[("jd", "tencent")])
    # B：candidate 但证据来自别人（web）—— 不属于本挖掘源，不许补证据
    rel_foreign = _seed_rel(db, cv, s_spark, status="candidate",
                            evidence=[("web", "serper")])
    # C：candidate 且证据清一色本源，但已顶到 MAX_EVIDENCE_PER_SKILL —— 不许再加
    rel_capped = _seed_rel(db, java, s_java, status="candidate",
                           evidence=[("dataset", "BOSS直聘")] * MAX_EVIDENCE_PER_SKILL)
    # D：candidate 且证据清一色本源、未满 —— 唯一允许补证据的形态（但行本身仍不许改）
    rel_owned = _seed_rel(db, java, s_redis, status="candidate",
                          evidence=[("dataset", "BOSS直聘")] * 2)

    # 四张禁写表
    batch = models.CrawlBatch(batch_key="2026WNN", platform="tencent", tier="official",
                              method="api", pages=3, collected=30, kept=28,
                              robots_ok=True, rate_limit_s=2.0)
    db.add(batch)
    db.flush()
    for i in range(3):
        db.add(models.RawJD(job_title="计算机视觉算法工程师", company=f"公司{i}",
                            source="tencent", raw_text="真实实采语料",
                            dedup_hash=f"h{i}", crawl_batch_id=batch.id,
                            collected_at=datetime(2026, 6, 1)))
    db.add(models.CapabilityChange(job_id=cv.id, version=2, change_type="add",
                                   skill_name="多模态融合", importance="required",
                                   reason="窗口内新增", confidence=0.7,
                                   created_at=datetime(2026, 7, 1)))
    db.commit()
    return {"cv": cv, "java": java, "rel_active": rel_active,
            "rel_foreign": rel_foreign, "rel_capped": rel_capped,
            "rel_owned": rel_owned}


# --------------------------------------------------------------------- 校验和

_GUARDED_TABLES = (models.Job, models.RawJD, models.CrawlBatch, models.CapabilityChange)


def _row_fingerprint(obj) -> str:
    cols = [c.name for c in type(obj).__table__.columns]
    return repr([(c, getattr(obj, c)) for c in cols])


def _table_checksum(db, model) -> str:
    rows = sorted(_row_fingerprint(o) for o in db.query(model).all())
    return f"{len(rows)}:" + hashlib.sha256(
        "|".join(rows).encode("utf-8")).hexdigest()


def _rows_checksum(db, model, ids) -> str:
    """只对给定 id 集合做校验和 —— 用于「既有行不许变」而「新增行允许出现」的表。"""
    if not ids:
        return "0:"
    rows = sorted(_row_fingerprint(o) for o in db.query(model).filter(
        model.id.in_(list(ids))).all())
    return f"{len(rows)}:" + hashlib.sha256(
        "|".join(rows).encode("utf-8")).hexdigest()


def _ids(db, model) -> set[int]:
    return {i for (i,) in db.query(model.id).all()}


# ============================================================================
# 1. 写库范围防护 —— 本文件的主用例
# ============================================================================

# 守卫用分片：8 行，覆盖两个岗位各两次观测（PMI 门要求 c>=2）+ 四种被丢弃的形态。
CV_TAGS = "Python、Spark、目标检测、语义分割"
JAVA_TAGS = "Java、Redis、Spring Boot、MyBatis"


def _guard_shard(directory: Path) -> Path:
    return _write_shard(directory, [
        # 命中 CV 岗：Python 撞 active 行、Spark 撞外源 candidate 行，另有两个新技能。
        # 观测两次是必须的 —— 单行共现过不了 PARENT_MIN_COOC，新技能点就一个都不建，
        # 于是「这一轮确实写了东西」的阳性对照会静默失效。
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV, tags=CV_TAGS),
        _row("计算机视觉算法工程师", row_no=2, body=_short_body(2), tags=CV_TAGS),
        # 命中 Java 岗：Java 撞封顶行、Redis 撞本源未满行，另有两个新技能
        _row("Java开发工程师", row_no=3, body=BODY_JAVA, domain="金融科技",
             tags=JAVA_TAGS),
        _row("Java开发工程师", row_no=4, body=_short_body(4), domain="金融科技",
             tags=JAVA_TAGS),
        # 策展白名单里有、库里没建岗 → 只记账不入图
        _row("数据分析师", row_no=5, body=BODY_DATA, domain="电商",
             tags="SQL、数据仓库"),
        # 完全不在策展白名单 → 只记账不入图
        _row("奶茶店店长", row_no=6, body=BODY_SHOP, category="其他",
             domain="零售", tags="门店管理、库存盘点"),
        # 与第 1 行完全相同 → 精确重复
        _row("计算机视觉算法工程师", row_no=7, body=BODY_CV, tags=CV_TAGS),
        # 字段全空
        _row("", row_no=8, body="", tags="", category="", domain=""),
    ], stopwords={"exact": ["门店管理", "库存盘点"], "patterns": [r"相关专业$"]})


def test_apply_run_touches_only_the_insert_whitelist(db, tmp_path):
    """正式落库跑完：job / raw_jd / crawl_batch / capability_change 与所有既有
    skill、job_skill、evidence 行必须逐字节不变；同时这一轮确实写进了东西。

    这是本模块唯一的安全承诺，也是「候选层不会让交付文档的头条数字漂移」的依据。
    """
    seeded = _seed_graph(db)
    _guard_shard(tmp_path)

    before_tables = {m.__tablename__: _table_checksum(db, m) for m in _GUARDED_TABLES}
    pre_js_ids = _ids(db, models.JobSkill)
    pre_ev_ids = _ids(db, models.Evidence)
    pre_sk_ids = _ids(db, models.Skill)
    before_js = _rows_checksum(db, models.JobSkill, pre_js_ids)
    before_ev = _rows_checksum(db, models.Evidence, pre_ev_ids)
    before_sk = _rows_checksum(db, models.Skill, pre_sk_ids)

    summary = mining.run_daily_mining(
        db, run_date="2026-09-03", shard_index=0, dry_run=False,
        use_llm=False, rows=50, directory=tmp_path)

    db.expire_all()

    # ---- 禁写表：整表校验和必须一致（含行数）
    after_tables = {m.__tablename__: _table_checksum(db, m) for m in _GUARDED_TABLES}
    assert after_tables == before_tables, (
        "每日挖掘只允许 INSERT 到 skill/job_skill/evidence，"
        f"以下禁写表发生了变化：{[k for k in before_tables if before_tables[k] != after_tables[k]]}")

    # ---- 既有行：允许新增，但既有 id 上的每个字段都不许动
    assert _rows_checksum(db, models.JobSkill, pre_js_ids) == before_js, \
        "既有 job_skill 行被改写（连 last_seen 都不许刷）"
    assert _rows_checksum(db, models.Evidence, pre_ev_ids) == before_ev
    assert _rows_checksum(db, models.Skill, pre_sk_ids) == before_sk

    # ---- 防护不能因为「这轮什么都没干」而假性通过
    assert summary["skills_created"] > 0
    assert summary["job_skills_created"] > 0
    assert summary["evidence_created"] > 0
    assert _ids(db, models.JobSkill) > pre_js_ids
    assert _ids(db, models.Skill) > pre_sk_ids

    # ---- 新增的 job_skill 一律 candidate、独立雇主数 0、无置信度因子
    new_js = db.query(models.JobSkill).filter(
        ~models.JobSkill.id.in_(list(pre_js_ids))).all()
    assert new_js
    assert {r.status for r in new_js} == {"candidate"}, "候选层不许自动转正"
    assert {r.source_count for r in new_js} == {0}, "这份语料没有雇主字段，独立来源数必须是 0"
    assert all(r.factors is None and (r.confidence or 0.0) == 0.0 for r in new_js)

    # ---- 新增的证据一律 dataset / 不挂 raw_jd（对统一置信度公式结构性惰性）
    new_ev = db.query(models.Evidence).filter(
        ~models.Evidence.id.in_(list(pre_ev_ids))).all()
    assert new_ev
    assert {e.source_type for e in new_ev} == {mining.EVIDENCE_SOURCE_TYPE} == {"dataset"}
    assert all(e.raw_jd_id is None for e in new_ev), \
        "证据挂上 raw_jd 就等于把模拟语料掺进实采台账"
    assert {e.source_name for e in new_ev} == {"BOSS直聘"}

    # ---- 三种「不属于本挖掘源」的既有关系，一条证据都不许补
    for label, rel in (("active+外源", seeded["rel_active"]),
                       ("candidate+外源", seeded["rel_foreign"])):
        added = [e for e in new_ev if e.job_skill_id == rel.id]
        assert not added, f"{label} 的关系被补了 {len(added)} 条匿名语料证据"
    # 封顶行：一条不加，且总数仍是上限值
    assert not [e for e in new_ev if e.job_skill_id == seeded["rel_capped"].id]
    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == seeded["rel_capped"].id
    ).count() == MAX_EVIDENCE_PER_SKILL

    # ---- 唯一允许补证据的形态：本源 candidate 且未满（两行观测各补一条）
    owned_added = [e for e in new_ev if e.job_skill_id == seeded["rel_owned"].id]
    assert len(owned_added) == 2
    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == seeded["rel_owned"].id).count() == 4

    # ---- 新建的技能节点一个都不许是孤立根：粗粒度 candidate 在前端一处都不渲染，
    #      建成孤儿只会让「技能总数」虚涨而挖掘页宣称的新增在岗位详情页查无此项
    new_sk = db.query(models.Skill).filter(
        ~models.Skill.id.in_(list(pre_sk_ids))).all()
    assert new_sk
    assert all(k.parent_id is not None for k in new_sk),         f"挖掘写出了孤立根技能：{[k.normalized_name for k in new_sk if k.parent_id is None]}"
    assert all(k.category and k.category != "其他" for k in new_sk),         "分类必须随父概念继承，不能落进「其他」残余桶"

    # ---- 观测层三张表确实落了痕
    assert db.query(models.DailyMiningRun).count() == 1
    assert db.query(models.DailyMiningItem).count() == 8
    assert summary["status"] == "completed" and summary["dry_run"] is False


def test_dry_run_leaves_no_trace_at_all(db, tmp_path):
    """试运行整条链路真跑，但连观测层都不留行 —— 「跑之前 = 跑之后」。"""
    _seed_graph(db)
    _guard_shard(tmp_path)

    before = {m.__tablename__: _table_checksum(db, m)
              for m in _GUARDED_TABLES + (models.Skill, models.JobSkill, models.Evidence)}

    summary = mining.run_daily_mining(
        db, run_date="2026-09-03", shard_index=0, dry_run=True,
        use_llm=False, rows=50, directory=tmp_path)

    db.expire_all()
    after = {m.__tablename__: _table_checksum(db, m)
             for m in _GUARDED_TABLES + (models.Skill, models.JobSkill, models.Evidence)}
    assert after == before
    assert db.query(models.DailyMiningRun).count() == 0
    assert db.query(models.DailyMiningItem).count() == 0
    assert db.query(models.DailySkillDelta).count() == 0
    # 但漏斗计数与试运行的入图计数照样算出来了（是 Python 侧显式累加，不依赖回读）
    assert summary["dry_run"] is True
    assert summary["rows_read"] == 8
    assert summary["job_skills_created"] > 0
    assert summary["evidence_created"] > 0


def test_run_writes_no_capability_change_row(db, tmp_path):
    """候选层的增减只记 DailySkillDelta，绝不掺进 v1→v2 演化审计链。"""
    _seed_graph(db)
    _guard_shard(tmp_path)
    before = db.query(models.CapabilityChange).count()

    mining.run_daily_mining(db, run_date="2026-09-03", shard_index=0,
                            dry_run=False, use_llm=False, rows=50, directory=tmp_path)

    assert db.query(models.CapabilityChange).count() == before
    assert db.query(models.DailySkillDelta).count() > 0


def test_run_does_not_grow_the_public_jd_headline(db, tmp_path):
    """`stats_overview` 的「JD 总数」= count(RawJD)，模拟语料一行都不许进去。"""
    _seed_graph(db)
    _guard_shard(tmp_path)
    before = db.query(models.RawJD).count()

    summary = mining.run_daily_mining(
        db, run_date="2026-09-03", shard_index=0, dry_run=False,
        use_llm=False, rows=50, directory=tmp_path)

    assert summary["rows_read"] == 8
    assert db.query(models.RawJD).count() == before == 3
    assert db.query(models.CrawlBatch).count() == 1


def test_deltas_never_claim_active_status(db, tmp_path):
    """新增技能点的日间变化行，状态只能是 candidate —— 前端要拿它解释门禁。"""
    _seed_graph(db)
    _guard_shard(tmp_path)
    mining.run_daily_mining(db, run_date="2026-09-03", shard_index=0,
                            dry_run=False, use_llm=False, rows=50, directory=tmp_path)

    new_deltas = db.query(models.DailySkillDelta).filter(
        models.DailySkillDelta.delta_type == "new").all()
    assert new_deltas
    assert all(d.prev_status is None for d in new_deltas)
    # 撞上既有 active 行的技能点（Python）会照实报 active；其余新建的一律 candidate
    fresh = [d for d in new_deltas if d.skill_name not in {"Python", "Spark"}]
    assert fresh and {d.curr_status for d in fresh} == {"candidate"}


# ============================================================================
# 2. split_skill_tags —— 括号是分隔符，不是要删掉的字符
# ============================================================================

@pytest.fixture()
def plain():
    """不带停用词的切分环境，隔离「切分」与「过滤」两件事。"""
    return (set(), [])


def test_bracket_is_a_delimiter_not_a_deletion(plain):
    """`大数据处理框架(Spark、Hive)` 必须切出独立的 Spark 与 Hive。

    回归：朴素切分会留下 `大数据处理框架(Spark`（全语料 852 次）这种幽灵技能，
    而逐 token 剥括号只救得回后半截的 `Hive)`。
    """
    out = mining.split_skill_tags("大数据处理框架(Spark、Hive)", *plain)
    assert "Spark" in out and "Hive" in out
    assert "大数据处理框架(Spark" not in out, "带内部左括号的幽灵技能又回来了"
    assert "Hive)" not in out, "token 尾巴上挂着右括号"
    assert not any("(" in n or ")" in n or "（" in n or "）" in n for n in out), out


@pytest.mark.parametrize("raw,expect_in,expect_not_in", [
    ("图像处理库（OpenCV等）", ["OpenCV"], ["OpenCV等", "图像处理库（OpenCV等"]),
    ("大数据处理工具(Spark)", ["Spark"], ["大数据处理工具(Spark"]),
    ("深度学习框架【PyTorch/TensorFlow】", ["PyTorch", "TensorFlow"],
     ["深度学习框架【PyTorch", "TensorFlow】"]),
    ("容器化[Docker,Kubernetes]", ["Docker", "Kubernetes"], ["Kubernetes]"]),
])
def test_bracket_forms_all_split(raw, expect_in, expect_not_in, plain):
    """全角/半角圆括号、方括号、中文书名号式括号都要当分隔符。"""
    out = mining.split_skill_tags(raw, *plain)
    for name in expect_in:
        assert name in out, f"{raw!r} 没切出 {name!r}：{out}"
    for name in expect_not_in:
        assert name not in out, f"{raw!r} 留下了幽灵 token {name!r}"


@pytest.mark.parametrize("raw,expect", [
    ("Java等", "Java"),
    ("OpenCV等", "OpenCV"),
    ("图像处理库（OpenCV等）", "OpenCV"),
])
def test_trailing_deng_is_stripped(raw, expect, plain):
    """结尾的「等」要剥掉，否则 `OpenCV等` 会和 `OpenCV` 各建一个技能节点。"""
    assert expect in mining.split_skill_tags(raw, *plain)


def test_single_char_whitelist_keeps_c_and_r(plain):
    """C 是全语料第 3 高频技能（5326 次）。回归：一刀切的 `len>1` 会静默删掉它。"""
    out = mining.split_skill_tags("C、Python、R", *plain)
    assert "C" in out and "R" in out and "Python" in out
    assert mining.SINGLE_CHAR_SKILLS == frozenset({"C", "R"})


def test_single_char_whitelist_is_case_normalized(plain):
    """小写 `c` 要转成 `C`：`normalize_skill('c')` 原样返回，不转大写就会分裂节点。"""
    assert mining.split_skill_tags("c、r", *plain) == ["C", "R"]
    assert mining.split_skill_tags("C、c", *plain) == ["C"], "同一技能不许出现两次"


def test_single_char_fragments_are_dropped(plain):
    """`农/林/牧/渔` 这类切分碎片不在白名单里，必须丢掉。"""
    out = mining.split_skill_tags("云、农、林、牧、渔、大、Python", *plain)
    assert out == ["Python"], out


def test_zero_width_chars_are_trimmed(plain):
    """LLM 回过 `Go-kit‌`；不剥零宽字符就会和 `Go-kit` 各建一个节点。"""
    out = mining.split_skill_tags("Go-kit‌、Go-kit", *plain)
    assert out == ["Go-kit"]


def test_max_skills_per_row_caps_tag_stuffing(plain):
    """单行技能数上限挡住标签堆砌造成的能力通胀。"""
    raw = "、".join(f"技能项{i:02d}" for i in range(30))
    out = mining.split_skill_tags(raw, *plain)
    assert len(out) == mining.MAX_SKILLS_PER_ROW == 15


def test_max_skill_chars_rejects_overlong_token(plain):
    """超过 MAX_SKILL_CHARS 的 token 丢弃，正好等于上限的保留。"""
    exactly = "长" * mining.MAX_SKILL_CHARS
    too_long = "长" * (mining.MAX_SKILL_CHARS + 1)
    assert mining.split_skill_tags(f"{exactly}、Python", *plain) == [exactly, "Python"]
    assert mining.split_skill_tags(f"{too_long}、Python", *plain) == ["Python"]


def test_split_is_deduped_and_order_preserving(plain):
    out = mining.split_skill_tags("Python、Java、Python、Java、Spark", *plain)
    assert out == ["Python", "Java", "Spark"]


def test_split_handles_empty_and_punctuation_only(plain):
    for raw in ["", None, "、、、", "，,;；|", "  \t \n "]:
        assert mining.split_skill_tags(raw, *plain) == []


# ============================================================================
# 3. 停用词
# ============================================================================

def test_stopwords_use_both_exact_and_patterns(tmp_path):
    _write_shard(tmp_path, [], stopwords={
        "exact": ["无明显侧重", "获奖"],
        "patterns": [r"相关专业$", r"(五险|一金|带薪)"],
    })
    exact, patterns = mining.load_stopwords(tmp_path)
    assert exact == {"无明显侧重", "获奖"}
    assert len(patterns) == 2
    # exact 全等即丢；patterns 用 search（未锚定的正则命中子串即丢）
    assert mining.is_stopword("无明显侧重", exact, patterns)
    assert mining.is_stopword("计算机相关专业", exact, patterns)
    assert mining.is_stopword("五险一金齐全", exact, patterns)
    assert not mining.is_stopword("Python", exact, patterns)
    assert not mining.is_stopword("相关专业能力建设", exact, patterns), \
        "`相关专业$` 带锚点，不该命中中间出现的情况"


def test_stopwords_filter_inside_split(tmp_path):
    """噪声在切分链路里被剔掉，真实技能原样通过。"""
    _write_shard(tmp_path, [], stopwords={
        "exact": ["计算机相关专业", "无明显侧重", "获奖", "不接受居家办公"],
        "patterns": [r"学历"],
    })
    exact, patterns = mining.load_stopwords(tmp_path)
    out = mining.split_skill_tags(
        "计算机相关专业、无明显侧重、获奖、不接受居家办公、本科学历、Python、深度学习",
        exact, patterns)
    assert out == ["Python", "深度学习"]


def test_stopword_bad_regex_is_skipped_not_fatal(tmp_path):
    """一条正则编译不过不许拖垮整轮作业。"""
    _write_shard(tmp_path, [], stopwords={"exact": ["获奖"], "patterns": ["[未闭合", r"学历"]})
    exact, patterns = mining.load_stopwords(tmp_path)
    assert exact == {"获奖"} and len(patterns) == 1
    assert mining.is_stopword("本科学历", exact, patterns)


def test_missing_stopwords_falls_back_not_crashes(tmp_path):
    """词表缺失 → 回落内置兜底表并继续跑，不抛异常。"""
    empty = tmp_path / "novocab"
    empty.mkdir()
    exact, patterns = mining.load_stopwords(empty)
    assert exact == mining._FALLBACK_STOPWORDS and patterns == []


def test_empty_stopwords_file_falls_back(tmp_path):
    """exact 与 patterns 都空的词表视为损坏，回落兜底表。"""
    _write_shard(tmp_path, [], stopwords={"exact": [], "patterns": []})
    exact, patterns = mining.load_stopwords(tmp_path)
    assert exact == mining._FALLBACK_STOPWORDS and patterns == []


def test_fallback_stopwords_do_not_eat_real_skills():
    """内置兜底表也不许误杀真实技能（它比正式词表粗，更容易误杀）。"""
    exact, patterns = set(mining._FALLBACK_STOPWORDS), []
    eaten = [s for s in REAL_SKILLS_THAT_MUST_SURVIVE
             if mining.is_stopword(s, exact, patterns)]
    assert eaten == [], f"兜底停用词表吃掉了真实技能：{eaten}"


@pytest.mark.skipif(not REAL_STOPWORDS.exists(),
                    reason="stopwords.json 未生成（data/aggregate_source 已 gitignore）")
def test_real_stopword_list_blocks_noise_but_keeps_skills():
    """**回归**：正式停用词表只吃噪声，不许吃真实技能。

    这份表是按 top-300 词频人工过筛的，往里加词很容易顺手把工程能力词一起封掉
    （`英语能力` / `源码` / `架构设计经验` 都曾是候选噪声词）。
    """
    exact, patterns = mining.load_stopwords(REAL_SHARD_DIR)
    eaten = [s for s in REAL_SKILLS_THAT_MUST_SURVIVE
             if mining.is_stopword(s, exact, patterns)]
    assert eaten == [], f"停用词表吃掉了真实技能：{eaten}"

    leaked = [n for n in REAL_NOISE_THAT_MUST_BE_BLOCKED
              if not mining.is_stopword(n, exact, patterns)]
    assert leaked == [], f"非技能噪声漏了过去：{leaked}"


@pytest.mark.skipif(not REAL_STOPWORDS.exists(),
                    reason="stopwords.json 未生成（data/aggregate_source 已 gitignore）")
def test_real_corpus_tag_cell_splits_cleanly():
    """拿一条真实形状的技能需求原文过一遍全链路，结果里不许留括号残渣。"""
    exact, patterns = mining.load_stopwords(REAL_SHARD_DIR)
    out = mining.split_skill_tags(
        "CNN、机器学习算法、算法设计、大数据处理框架(Spark、Hive)、"
        "图像处理库（OpenCV等）、无明显侧重、计算机相关专业、C、Python",
        exact, patterns)
    assert {"Spark", "Hive", "OpenCV", "C", "Python"} <= set(out), out
    assert "无明显侧重" not in out and "计算机相关专业" not in out
    assert not any(re.search(r"[（）()【】\[\]]", n) for n in out), out


# ============================================================================
# 4. 去重
# ============================================================================

def _funnel_db(db):
    """漏斗用例的最小库：两个策展岗位 + 两个粗粒度概念。

    粗粒度概念不是摆设：没有它们，`_write_increment` 里的共现判据找不到父节点，
    整轮作业一个 Skill / JobSkill 都不会建，于是所有「入图」断言会静默变成空跑。
    """
    _seed_job(db, "计算机视觉工程师")
    _seed_job(db, "Java开发工程师", category="智能软件")
    _seed_skill(db, COARSE_CV, category="人工智能")
    _seed_skill(db, COARSE_BACKEND, category="云计算与工程")
    db.commit()


def _run(db, tmp_path, **kw):
    kw.setdefault("run_date", "2026-09-03")
    kw.setdefault("shard_index", 0)
    kw.setdefault("dry_run", False)
    kw.setdefault("use_llm", False)
    kw.setdefault("rows", 100)
    kw.setdefault("directory", tmp_path)
    return mining.run_daily_mining(db, **kw)


def _stage(summary: dict, key: str) -> dict:
    return next(s for s in summary["stage_log"] if s["key"] == key)


def _drops(db) -> dict[str, int]:
    out: dict[str, int] = {}
    for (reason,) in db.query(models.DailyMiningItem.drop_reason).all():
        if reason:
            out[reason] = out.get(reason, 0) + 1
    return out


def test_exact_duplicate_is_rejected(db, tmp_path):
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV, tags="Python"),
        _row("计算机视觉算法工程师", row_no=2, body=BODY_CV, tags="Python"),
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    summary = _run(db, tmp_path)
    assert summary["rows_valid"] == 2 and summary["rows_dedup"] == 1
    assert _stage(summary, "dedup")["dropped"] == {"重复": 1}
    assert _drops(db) == {"重复": 1}


def test_near_duplicate_is_rejected_by_simhash(db, tmp_path):
    """SimHash 海明距离 ≤ NEAR_DUP_THRESHOLD 判近重复。

    构造：同一段 JD 改了一个错别字（「裁剪」→「裁减」）—— 精确哈希已经不同，
    只有 SimHash 拦得住。这正是聚合源里同一条 JD 被代招改写后重发的形态。
    """
    _funnel_db(db)
    variant = BODY_CV.replace("裁剪", "裁减")
    assert variant != BODY_CV
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV, tags="Python"),
        _row("计算机视觉算法工程师", row_no=2, body=variant, tags="Python"),
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    a = "计算机视觉算法工程师\n" + BODY_CV
    b = "计算机视觉算法工程师\n" + variant
    assert cleaning.exact_hash(a) != cleaning.exact_hash(b), "这一对必须靠 SimHash 才能识别"
    assert len(cleaning.normalize_text(a)) >= mining.NEAR_DUP_MIN_CHARS, \
        "构造前提：正文要够长才参与 SimHash 判定"
    assert cleaning.is_near_duplicate(cleaning.simhash(a), cleaning.simhash(b),
                                      mining.NEAR_DUP_THRESHOLD)

    summary = _run(db, tmp_path)
    assert summary["rows_dedup"] == 1
    assert _stage(summary, "dedup")["dropped"] == {"近重复": 1}


def test_short_rows_are_exempt_from_simhash(db, tmp_path):
    """短文本 SimHash 噪声大，低于 NEAR_DUP_MIN_CHARS 的行不参与近重复判定。

    构造：两行仅差一个字（SimHash 会判它们近重复），但归一后长度 < 60，
    因此必须双双保留 —— 否则「有标签、无正文」那 28.2% 的语料会被成片误杀。
    """
    _funnel_db(db)
    short_a = "负责视觉算法研发与端侧部署优化。"
    short_b = short_a + "的"        # tokenize 会丢掉「的」→ SimHash 完全相同，精确哈希却不同
    text_a = "计算机视觉算法工程师\n" + short_a
    text_b = "计算机视觉算法工程师\n" + short_b
    assert len(cleaning.normalize_text(text_a)) < mining.NEAR_DUP_MIN_CHARS
    assert cleaning.is_near_duplicate(cleaning.simhash(text_a), cleaning.simhash(text_b),
                                      mining.NEAR_DUP_THRESHOLD), \
        "构造前提：这一对在没有长度门时会被判近重复"

    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=short_a, tags="Python"),
        _row("计算机视觉算法工程师", row_no=2, body=short_b, tags="PyTorch"),
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    summary = _run(db, tmp_path)
    assert summary["rows_valid"] == 2, "有技能标签的短正文行不该被长度门丢掉"
    assert summary["rows_dedup"] == 2, "短文本被 SimHash 误杀了"
    assert _drops(db) == {}


def test_tag_only_rows_survive_the_length_gate(db, tmp_path):
    """正文为空但有技能标签的行必须留下（全语料 28.2% 的行 raw_text 为空）。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body="", tags="Python、目标检测"),
        _row("Java开发工程师", row_no=2, body="", tags="Java、Spring Boot"),
        _row("计算机视觉算法工程师", row_no=3, body="太短了。", tags=""),
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    summary = _run(db, tmp_path)
    assert summary["rows_valid"] == 2
    assert _stage(summary, "validate")["dropped"] == {"正文过短": 1}
    assert "仅凭技能标签保留" in _stage(summary, "validate")["detail"]
    assert summary["rows_mapped"] == 2


# ============================================================================
# 5. 岗位归一 —— 漏斗必须逐条解释得清
# ============================================================================

def test_only_canonical_titles_enter_the_graph(db, tmp_path):
    """只有归一进 `ingest.canonical_job_names()` 且库里已建岗的行才入图；
    其余一律留一条带 drop_reason 的台账行。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV, tags="Python、目标检测"),
        _row("Java开发工程师", row_no=2, body=BODY_JAVA, tags="Java、Redis"),
        _row("数据分析师", row_no=3, body=BODY_DATA, tags="SQL"),        # 白名单内、库里没建
        _row("奶茶店店长", row_no=4, body=BODY_SHOP, tags="门店管理"),    # 白名单外
        _row("行政专员", row_no=5, body=BODY_SHOP + "另需协助行政采购与访客接待。",
             tags="办公软件"),                                            # 白名单外
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    summary = _run(db, tmp_path)

    assert summary["rows_mapped"] == 2
    assert summary["rows_dropped"] == 3
    assert _drops(db) == {"未命中策展岗位": 2, "岗位未建库": 1}
    assert _stage(summary, "map")["dropped"] == {"未命中策展岗位": 2, "岗位未建库": 1}

    # 每一行都留了台账，被丢的也留着标题与原表行号，漏斗才逐条解释得清
    items = db.query(models.DailyMiningItem).order_by(
        models.DailyMiningItem.source_row_no).all()
    assert [i.source_row_no for i in items] == [1, 2, 3, 4, 5]
    assert all(i.title_raw for i in items)
    dropped = [i for i in items if i.drop_reason]
    assert len(dropped) == 3
    assert all(i.job_id is None for i in dropped)
    assert all(i.drop_reason is None and i.job_id for i in items if not i.drop_reason)

    # 白名单内但未建岗的行：title_key 归一成功、只是没有 job 行可挂
    unbuilt = next(i for i in items if i.drop_reason == "岗位未建库")
    assert unbuilt.title_key == "数据分析师"
    assert unbuilt.title_key in ingest.canonical_job_names()

    # 未命中的行也保留 title_key，供人工回看归一结果
    missed = [i for i in items if i.drop_reason == "未命中策展岗位"]
    assert all(i.title_key and i.title_key not in ingest.canonical_job_names()
               for i in missed)


def test_no_job_row_is_ever_created_for_an_unbuilt_canonical_title(db, tmp_path):
    """白名单里有、库里没有 → 如实丢弃，绝不 INSERT job 补齐。"""
    _funnel_db(db)
    before = _table_checksum(db, models.Job)
    _write_shard(tmp_path, [
        _row("数据分析师", row_no=1, body=BODY_DATA, tags="SQL、数据仓库"),
        _row("提示词工程师", row_no=2, body=BODY_CV, tags="Prompt工程"),
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    with pytest.raises(mining.MiningDataQualityError, match="岗位归一结果为 0"):
        _run(db, tmp_path)
    db.expire_all()
    assert _table_checksum(db, models.Job) == before
    assert db.query(models.Job).count() == 2
    failed = db.query(models.DailyMiningRun).one()
    assert failed.status == "failed" and failed.rows_mapped == 0
    assert "禁止把缺失输入解释为技能消失" in failed.error
    assert db.query(models.DailySkillDelta).count() == 0


def test_empty_governed_catalog_fails_without_fabricating_vanished(
        db, tmp_path, monkeypatch):
    """回归 2026-09-03 事故：白名单空集必须 failed，不能把昨日全集记成消失。"""
    _funnel_db(db)
    job = db.query(models.Job).filter(models.Job.name == "计算机视觉工程师").one()
    previous = models.DailyMiningRun(
        run_date="2026-09-02", status="completed", source_label="BOSS直聘",
        platform="boss_sim", shard_index=0, dry_run=False, rows_mapped=1, stage_log=[])
    db.add(previous)
    db.flush()
    db.add(models.DailyMiningItem(
        run_id=previous.id, source_row_no=1, job_id=job.id,
        title_raw="计算机视觉算法工程师", title_key=job.name,
        skills=["Python"], used_llm=False))
    db.commit()
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1001, body=BODY_CV, tags="Python"),
    ], index=1, stopwords={"exact": [], "patterns": []})
    monkeypatch.setattr(ingest, "canonical_job_names", lambda: frozenset())

    with pytest.raises(mining.MiningDataQualityError, match="岗位归一结果为 0"):
        _run(db, tmp_path, run_date="2026-09-03", shard_index=1)

    failed = db.query(models.DailyMiningRun).filter_by(run_date="2026-09-03").one()
    assert failed.status == "failed"
    assert failed.stage_log[-1]["detail"] == "命中 0 个策展岗位中的 0 个"
    assert db.query(models.DailySkillDelta).filter_by(run_id=failed.id).count() == 0


def test_funnel_counts_are_monotonic_and_recorded(db, tmp_path):
    """六个阶段齐全、顺序固定，且漏斗计数单调不增。"""
    _funnel_db(db)
    _guard_shard(tmp_path)
    summary = _run(db, tmp_path)

    assert [s["key"] for s in summary["stage_log"]] == mining.STAGE_ORDER
    assert all(s["label"] for s in summary["stage_log"])
    assert (summary["rows_read"] >= summary["rows_valid"]
            >= summary["rows_dedup"] >= summary["rows_mapped"])
    assert summary["rows_dropped"] == summary["rows_read"] - summary["rows_mapped"]
    assert summary["cursor_start"] == 1 and summary["cursor_end"] == 8


# ============================================================================
# 6. 预算闸 —— 全程替身客户端，不发网络请求
# ============================================================================

def _llm_shard(tmp_path, count=3):
    """构造 `count` 行「命中岗位、标签全是噪声、正文够长」的行 —— 必须走 LLM。

    正文必须**彼此差异够大**：同一标题下只差一两个字会先被 SimHash 判成近重复，
    根本走不到抽取阶段（实测四段正文两两海明距离 21–25，远超阈值 2）。
    """
    bodies = [
        "岗位职责：负责图像分类与目标检测算法的研发落地，围绕嵌入式端侧推理做算子裁剪与量化压缩，配合业务方完成算法工程化交付。",
        "岗位职责：主导视频结构化系统的模型选型与训练调优，搭建自动化评测流水线，跟踪学术前沿并复现关键论文结果。",
        "岗位职责：参与三维重建与点云配准方向的技术攻关，负责标定流程设计、数据采集规范制定以及精度指标的持续改进。",
        "岗位职责：承担智能巡检产品的缺陷识别模块开发，处理长尾样本与类别不均衡问题，推动模型小型化并完成现场部署验证。",
    ]
    assert count <= len(bodies)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=i + 1, body=bodies[i], tags="无明显侧重")
        for i in range(count)
    ], stopwords={"exact": ["无明显侧重"], "patterns": [r"相关专业$"]})


def test_llm_fills_rows_that_rules_could_not(db, tmp_path, monkeypatch):
    """先确认替身接线是通的：规则抽不到的行由 LLM 补上，并标记 used_llm。"""
    _funnel_db(db)
    _llm_shard(tmp_path, 2)
    stub = StubLLM([{"items": [{"i": 0, "skills": ["PyTorch", "目标检测"]},
                               {"i": 1, "skills": ["Kubernetes", "Docker"]}]}],
                   prompt_tokens=900, completion_tokens=60)
    monkeypatch.setattr(mining, "_llm_client_cache", [stub])

    summary = _run(db, tmp_path, use_llm=True)

    assert len(stub.calls) == 1, "batch_size=10，两行应该合并成一次调用"
    assert summary["llm_calls"] == 1
    assert summary["llm_budget_hit"] is False
    assert summary["llm_prompt_tokens"] == 900 and summary["llm_completion_tokens"] == 60
    assert summary["llm_cost_cny"] == pytest.approx(
        900 / 1e6 * mining.LLM_PRICE_INPUT_CNY_PER_MTOKEN
        + 60 / 1e6 * mining.LLM_PRICE_OUTPUT_CNY_PER_MTOKEN)

    items = db.query(models.DailyMiningItem).order_by(
        models.DailyMiningItem.source_row_no).all()
    assert [i.used_llm for i in items] == [True, True]
    assert set(items[0].skills) == {"PyTorch", "目标检测"}
    assert set(items[1].skills) == {"Kubernetes", "Docker"}
    # LLM 返回的名字同样过切分/停用词/长度链路，不许绕过
    assert all(len(n) <= mining.MAX_SKILL_CHARS for i in items for n in i.skills)


def test_zero_budget_blocks_every_llm_call(db, tmp_path, monkeypatch):
    """日预算为 0 → 调用前估价就把枪按住，一次网络请求都不发，全行降级为纯规则。"""
    _funnel_db(db)
    _llm_shard(tmp_path, 3)
    stub = StubLLM([{"items": [{"i": 0, "skills": ["PyTorch"]}]}],
                   prompt_tokens=900, completion_tokens=60)
    monkeypatch.setattr(mining, "_llm_client_cache", [stub])
    monkeypatch.setattr(settings, "mining_daily_budget_cny", 0.0)
    monkeypatch.setattr(settings, "mining_llm_batch_size", 1)

    summary = _run(db, tmp_path, use_llm=True)

    assert stub.calls == [], "预算 0 还打了 LLM"
    assert summary["llm_calls"] == 0
    assert summary["llm_cost_cny"] == 0.0
    assert summary["llm_budget_hit"] is True
    # 降级不是失败：行照样留台账，只是没有技能
    items = db.query(models.DailyMiningItem).all()
    assert len(items) == 3
    assert all(not i.used_llm and not i.skills for i in items)
    # 漏斗桶已按「为什么掉队」拆开：这三行正文够长、是送不出去而不是没料可送
    assert _stage(summary, "extract")["dropped"] == {"正文未抽出技能": 3}
    assert "已撞日预算闸" in _stage(summary, "extract")["detail"]


def test_budget_stops_further_calls_once_reached(db, tmp_path, monkeypatch):
    """累计花费撞到日预算 → 后续批次不再调用 LLM，剩余行降级为纯规则。"""
    _funnel_db(db)
    _llm_shard(tmp_path, 3)
    # 单次 25 万 prompt token = ¥0.50，正好等于日预算 → 第一枪打完即撞闸
    stub = StubLLM([{"items": [{"i": 0, "skills": ["PyTorch", "目标检测"]}]}],
                   prompt_tokens=250_000, completion_tokens=0)
    monkeypatch.setattr(mining, "_llm_client_cache", [stub])
    monkeypatch.setattr(settings, "mining_daily_budget_cny", 0.50)
    monkeypatch.setattr(settings, "mining_llm_batch_size", 1)

    summary = _run(db, tmp_path, use_llm=True)

    assert len(stub.calls) == 1, f"撞闸后又打了 {len(stub.calls) - 1} 枪"
    assert summary["llm_calls"] == 1
    assert summary["llm_budget_hit"] is True
    assert summary["llm_cost_cny"] >= settings.mining_daily_budget_cny

    items = db.query(models.DailyMiningItem).order_by(
        models.DailyMiningItem.source_row_no).all()
    assert [i.used_llm for i in items] == [True, False, False]
    assert set(items[0].skills) == {"PyTorch", "目标检测"}
    assert all(not i.skills for i in items[1:]), "撞闸后的行不该凭空冒出技能"


def test_use_llm_false_never_touches_the_client(db, tmp_path, monkeypatch):
    """`--no-llm` 时连替身都不该被调用。"""
    _funnel_db(db)
    _llm_shard(tmp_path, 2)
    stub = StubLLM([{"items": [{"i": 0, "skills": ["PyTorch"]}]}], prompt_tokens=10)
    monkeypatch.setattr(mining, "_llm_client_cache", [stub])

    summary = _run(db, tmp_path, use_llm=False)
    assert stub.calls == []
    assert summary["llm_calls"] == 0 and summary["llm_budget_hit"] is False


def test_llm_failure_degrades_to_rules(db, tmp_path, monkeypatch):
    """LLM 抛异常时整轮作业不许失败，只记错误并降级。"""
    _funnel_db(db)
    _llm_shard(tmp_path, 2)

    class Boom(StubLLM):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("connection reset by peer")

    stub = Boom([{}])
    monkeypatch.setattr(mining, "_llm_client_cache", [stub])
    monkeypatch.setattr(settings, "mining_llm_batch_size", 1)

    summary = _run(db, tmp_path, use_llm=True)
    assert len(stub.calls) == 2
    assert summary["llm_calls"] == 0 and summary["status"] == "completed"
    assert "已降级为纯规则" in _stage(summary, "extract")["detail"]


def test_malformed_llm_json_is_survivable(db, tmp_path, monkeypatch):
    """LLM 回非法 JSON → 该批次当空结果，不抛异常。"""
    _funnel_db(db)
    _llm_shard(tmp_path, 1)

    class Junk(StubLLM):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="这不是 JSON"))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10))

    stub = Junk([{}])
    monkeypatch.setattr(mining, "_llm_client_cache", [stub])

    summary = _run(db, tmp_path, use_llm=True)
    assert len(stub.calls) == 1 and summary["status"] == "completed"
    assert db.query(models.DailyMiningItem).count() == 1
    assert not db.query(models.DailyMiningItem).first().skills


# ============================================================================
# 7. 上限
# ============================================================================

def test_evidence_cap_per_skill_is_respected(db, tmp_path):
    """同一 (岗位, 技能) 的证据封顶在 MAX_EVIDENCE_PER_SKILL，多轮观测不许堆叠。"""
    job = _seed_job(db, "计算机视觉工程师")
    skill = _seed_skill(db, "Python")
    rel = _seed_rel(db, job, skill, status="candidate",
                    evidence=[("dataset", "BOSS直聘")] * (MAX_EVIDENCE_PER_SKILL - 1))
    db.commit()

    # 一个分片里对同一 (岗位, 技能) 有 4 次独立观测，只有 1 条能落进去
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=i + 1, tags="Python",
             body=BODY_CV + f"方向{i}需要覆盖不同的业务场景与数据分布假设。")
        for i in range(4)
    ], stopwords={"exact": [], "patterns": [r"相关专业$"]})

    summary = _run(db, tmp_path)
    assert summary["evidence_created"] == 1
    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == rel.id).count() == MAX_EVIDENCE_PER_SKILL


def test_snippet_is_truncated(db, tmp_path):
    """证据片段截断在 SNIPPET_MAX，不把整段 JD 塞进库。"""
    _funnel_db(db)
    # 片段取的是「标签原文」而不是切分结果，所以要让标签串本身超长
    long_tags = COARSE_CV + "、" + "、".join(f"补充技能点{i:02d}" for i in range(30))
    assert len(long_tags) > mining.SNIPPET_MAX
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, tags=long_tags, body="长" * 2000),
        _row("计算机视觉算法工程师", row_no=2, tags=long_tags, body=_short_body(2)),
        _row("计算机视觉算法工程师", row_no=3, tags="其它方向词", body=_short_body(3)),
    ], stopwords={"exact": [], "patterns": []})

    _run(db, tmp_path)
    snippets = [e.snippet for e in db.query(models.Evidence).all()]
    assert snippets
    assert all(len(s) <= mining.SNIPPET_MAX for s in snippets)
    assert any(len(s) == mining.SNIPPET_MAX for s in snippets),         "构造前提：至少有一条片段确实被截断了，否则这条断言是空的"


def test_max_skills_per_row_caps_what_reaches_the_graph(db, tmp_path):
    """单行技能上限在入图侧同样生效 —— 标签堆砌不许变成 30 行候选能力。"""
    _funnel_db(db)
    stuffed = COARSE_CV + "、" + "、".join(f"能力项{i:02d}" for i in range(30))
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=_short_body(1), tags=stuffed),
        _row("计算机视觉算法工程师", row_no=2, body=_short_body(2), tags=stuffed),
        _row("计算机视觉算法工程师", row_no=3, body=_short_body(3), tags="其它方向词"),
    ], stopwords={"exact": [], "patterns": []})

    summary = _run(db, tmp_path)
    # 15 = 1 个已有粗粒度概念 + 14 个挂到它下面的新技能点；30 个标签只有前 15 个进得来
    assert summary["skills_created"] == mining.MAX_SKILLS_PER_ROW - 1 == 14
    cv = db.query(models.Job).filter(models.Job.name == "计算机视觉工程师").one()
    assert db.query(models.JobSkill).filter(
        models.JobSkill.job_id == cv.id).count() == mining.MAX_SKILLS_PER_ROW
    for item in db.query(models.DailyMiningItem).filter(
            models.DailyMiningItem.source_row_no.in_([1, 2])).all():
        assert len(item.skills) == mining.MAX_SKILLS_PER_ROW


def test_created_ids_are_recorded_for_rollback(db, tmp_path):
    """回滚凭据：本轮新建的 skill / job_skill / evidence id 必须逐行记账，
    否则 `data/rollback_mining.py` 无法精确撤销。"""
    _funnel_db(db)
    pre_sk = _ids(db, models.Skill)
    _write_shard(tmp_path, _cooc_rows(COARSE_CV, ["Python", "目标检测"]),
                 stopwords={"exact": [], "patterns": []})

    summary = _run(db, tmp_path)
    items = db.query(models.DailyMiningItem).filter(
        models.DailyMiningItem.drop_reason.is_(None)).all()

    def _ledger(field):
        out = []
        for it in items:
            out.extend(getattr(it, field) or [])
        return out

    # 逐行台账之和 == 本轮汇总数 == 库里真正多出来的 id —— 三者对不上就回滚不干净
    assert len(_ledger("created_skill_ids")) == summary["skills_created"] == 2
    assert len(_ledger("created_job_skill_ids")) == summary["job_skills_created"]
    assert len(_ledger("created_evidence_ids")) == summary["evidence_created"]
    assert set(_ledger("created_skill_ids")) == _ids(db, models.Skill) - pre_sk
    assert set(_ledger("created_job_skill_ids")) == _ids(db, models.JobSkill)
    assert set(_ledger("created_evidence_ids")) == _ids(db, models.Evidence)
    # 同一个新建节点只记一次凭据（记在第一条用到它的行上），不许跨行重复计账
    assert len(_ledger("created_skill_ids")) == len(set(_ledger("created_skill_ids")))


# ============================================================================
# 7.5 父概念共现门 —— 新技能点必须挂到粗粒度概念下
# ============================================================================
# 本项目的两级技能树里，`graph_service` 的口径是「candidate 只落细粒度技能点」：
# 粗粒度 candidate 掉进前端 coarse/fine 两套分组的缝里，**一处都不渲染**。所以本模块
# 不允许建孤立根节点 —— 挖掘页宣称某岗位今天新增了 N 个技能点，岗位详情页却一个都
# 看不见，比不入图更糟。父概念只用语料自带的共现信号（技能标签本身就是一行并列词），
# 判据是 PMI 加最小支持度，而不是裸计数：裸计数会被 Java/C++/Python 这类高频节点通吃。


def _cooc_stopwords():
    return {"exact": ["无明显侧重"], "patterns": [r"相关专业$"]}


def test_new_skill_point_inherits_parent_and_category(db, tmp_path):
    """挂上父概念的新技能点：parent_id 指向父节点，分类**继承**父概念。

    回归锚：`taxonomy.skill_category` 对没见过的词一律返回「其他」，直接拿它当分类
    会把「其他」这个残余桶顶成全库最大的一类（实测 2625 个孤儿里 99.9% 是「其他」）。
    """
    _funnel_db(db)
    _write_shard(tmp_path, _cooc_rows(COARSE_CV, ["KV缓存管理"]),
                 stopwords=_cooc_stopwords())

    summary = _run(db, tmp_path)

    assert summary["skills_created"] == 1
    child = db.query(models.Skill).filter(
        models.Skill.normalized_name == "KV缓存管理").one()
    parent = db.query(models.Skill).filter(
        models.Skill.normalized_name == COARSE_CV).one()
    assert child.parent_id == parent.id
    assert child.category == parent.category == "人工智能"
    # 分类确实是继承来的，不是 taxonomy 查出来的
    assert taxonomy.skill_category("KV缓存管理") == "其他"
    assert child.category != "其他"
    # 挂上了就该真的入图，且照旧是 candidate
    assert db.query(models.JobSkill).filter(
        models.JobSkill.skill_id == child.id).one().status == "candidate"


def test_name_without_coarse_cooccurrence_stays_observation_only(db, tmp_path):
    """共现不上任何粗粒度概念的词：不建 Skill、不建 job_skill，
    但观测层照样完整记录，并产出一条 `skill_id IS NULL` 的日间变化。"""
    _funnel_db(db)
    pre_sk = _ids(db, models.Skill)
    orphan_tags = "张量并行、算子融合"          # 库里没有的词，且无粗粒度概念同格
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=i + 1, body=_short_body(i + 1),
             tags=orphan_tags) for i in range(3)
    ], stopwords=_cooc_stopwords())

    summary = _run(db, tmp_path)

    # 一个节点、一条关系、一条证据都不许写
    assert summary["skills_created"] == 0
    assert summary["job_skills_created"] == 0
    assert summary["evidence_created"] == 0
    assert _ids(db, models.Skill) == pre_sk
    assert db.query(models.JobSkill).count() == 0

    # 但「今天观测到了」这件事完整留在观测层
    kept = db.query(models.DailyMiningItem).filter(
        models.DailyMiningItem.drop_reason.is_(None)).all()
    assert kept and all(set(i.skills) == {"张量并行", "算子融合"} for i in kept)

    deltas = db.query(models.DailySkillDelta).all()
    assert {d.skill_name for d in deltas} == {"张量并行", "算子融合"}
    assert all(d.skill_id is None and d.delta_type == "new" for d in deltas)
    assert all(d.curr_status is None for d in deltas)

    # 漏斗要说清楚这批词去哪了，不能悄悄消失
    assert "未共现到" in _stage(summary, "write")["detail"]


def test_single_row_cooccurrence_is_not_enough(db, tmp_path):
    """只在一行里同格 → 过不了 `PARENT_MIN_COOC`，不建节点。

    这是刻意的：一行里的偶然并列不足以认亲，硬认会把错的父概念写到页面上
    （分类是逐条显示的：岗位徽章、技能 chip、全景图配色）。
    """
    assert mining.PARENT_MIN_COOC >= 2, "本用例的构造前提"
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=_short_body(1),
             tags=f"{COARSE_CV}、只出现一次的技能"),
        _row("计算机视觉算法工程师", row_no=2, body=_short_body(2), tags="其它方向词"),
        _row("计算机视觉算法工程师", row_no=3, body=_short_body(3), tags="再一个方向词"),
    ], stopwords=_cooc_stopwords())

    summary = _run(db, tmp_path)
    assert summary["skills_created"] == 0
    assert db.query(models.Skill).filter(
        models.Skill.normalized_name == "只出现一次的技能").first() is None
    # 但已有的粗粒度概念本身仍可建关系 —— 门挡的是「新建节点」，不是「入图」
    assert summary["job_skills_created"] == 1


def test_ubiquitous_parent_loses_to_the_tighter_one(db, tmp_path):
    """PMI 的意义：无处不在的粗粒度概念抢不走亲子关系。

    回归 argmax 时代的错亲（「营养学 → LangChain」「电力系统 → 大语言模型」）：
    `COARSE_BACKEND` 出现在全部 6 行、`COARSE_CV` 只和子词一起出现在 2 行，
    裸计数是 2:2 平票、按名称序反而会选中前者；PMI 把「只是都很常见」扣掉，
    log(2*6/(2*6))=0 直接出局，log(2*6/(2*2))=1.099 胜出。
    """
    _funnel_db(db)
    rows = [
        # 前两行：子词与「紧」的父概念同格
        _row("计算机视觉算法工程师", row_no=1, body=_short_body(1),
             tags=f"{COARSE_CV}、{COARSE_BACKEND}、KV缓存管理"),
        _row("计算机视觉算法工程师", row_no=2, body=_short_body(2),
             tags=f"{COARSE_CV}、{COARSE_BACKEND}、KV缓存管理"),
    ] + [
        # 后四行：只有「泛」的父概念，把它的边际频次撑满
        _row("计算机视觉算法工程师", row_no=i, body=_short_body(i),
             tags=COARSE_BACKEND) for i in range(3, 7)
    ]
    _write_shard(tmp_path, rows, stopwords=_cooc_stopwords())

    _run(db, tmp_path)

    child = db.query(models.Skill).filter(
        models.Skill.normalized_name == "KV缓存管理").one()
    parent = db.query(models.Skill).filter(
        models.Skill.id == child.parent_id).one()
    assert parent.normalized_name == COARSE_CV, (
        f"高频概念 {COARSE_BACKEND} 抢走了亲子关系，PMI 门没起作用")
    assert child.category == "人工智能"


def test_parent_choice_is_reproducible_across_runs(tmp_path):
    """同一份语料跑两次必须长出同一棵树 —— 否则「今天新增了什么」讲不清。

    构造成 PMI 平票（两个父概念的边际频次、同格次数完全相同），只剩名称序能决定，
    并且两次运行**故意用相反的顺序**建粗粒度节点（技能 id 相反），确保胜出的是
    名称序而不是插入顺序。
    """
    rows = [
        _row("计算机视觉算法工程师", row_no=1, body=_short_body(1),
             tags=f"{COARSE_CV}、{COARSE_BACKEND}、张量并行"),
        _row("计算机视觉算法工程师", row_no=2, body=_short_body(2),
             tags=f"{COARSE_CV}、{COARSE_BACKEND}、张量并行"),
        # 第三行两个父概念都不出现，把 N 撑到 3
        _row("计算机视觉算法工程师", row_no=3, body=_short_body(3), tags="其它方向词"),
    ]
    _write_shard(tmp_path, rows, stopwords=_cooc_stopwords())

    def _run_once(coarse_order):
        db = _fresh_session()
        try:
            _seed_job(db, "计算机视觉工程师")
            for name, cat in coarse_order:
                _seed_skill(db, name, category=cat)
            db.commit()
            _run(db, tmp_path)
            child = db.query(models.Skill).filter(
                models.Skill.normalized_name == "张量并行").one()
            parent = db.query(models.Skill).filter(
                models.Skill.id == child.parent_id).one()
            return parent.normalized_name, child.category
        finally:
            db.close()

    forward = _run_once([(COARSE_CV, "人工智能"), (COARSE_BACKEND, "云计算与工程")])
    backward = _run_once([(COARSE_BACKEND, "云计算与工程"), (COARSE_CV, "人工智能")])

    assert forward == backward, "父概念的选择依赖了插入顺序 / 技能 id，不可复现"
    # 平票按名称序取小者："分布式系统" < "深度学习"
    assert forward[0] == min(COARSE_CV, COARSE_BACKEND) == COARSE_BACKEND


def test_mining_never_writes_an_orphan_root_skill(db, tmp_path):
    """**不变式**：挖掘写出的 Skill 行，`parent_id` 永远不为 NULL。

    这条是硬的 —— 上面那些门怎么调都行，但一旦有人放开「挂不上就建个根节点」，
    「技能总数」就会虚涨而页面看不见，必须在这里炸出来。
    """
    _funnel_db(db)
    pre_sk = _ids(db, models.Skill)
    # 一份混合语料：有的词挂得上、有的挂不上
    _write_shard(tmp_path, _cooc_rows(COARSE_CV, ["KV缓存管理", "算子融合"]) + [
        _row("Java开发工程师", row_no=11, body=_short_body(11), tags="孤立词甲、孤立词乙"),
        _row("Java开发工程师", row_no=12, body=_short_body(12), tags="孤立词甲、孤立词乙"),
        _row("Java开发工程师", row_no=13, body=_short_body(13), tags="孤立词丙"),
    ], stopwords=_cooc_stopwords())

    summary = _run(db, tmp_path)

    created = db.query(models.Skill).filter(
        ~models.Skill.id.in_(list(pre_sk))).all()
    assert created, "构造前提：这一轮要真的建过技能节点"
    orphans = [k.normalized_name for k in created if k.parent_id is None]
    assert orphans == [], f"挖掘写出了孤立根技能：{orphans}"
    # 全库层面同样成立：跑完之后除了预置的粗粒度概念，没有新的根节点
    roots = {k.normalized_name for k in db.query(models.Skill).filter(
        models.Skill.parent_id.is_(None)).all()}
    assert roots == {COARSE_CV, COARSE_BACKEND}
    # 挂不上的词确实被拦在图外，只留观测层
    assert summary["skills_created"] == 2
    for name in ("孤立词甲", "孤立词乙", "孤立词丙"):
        assert db.query(models.Skill).filter(
            models.Skill.normalized_name == name).first() is None


def test_observation_only_names_never_reach_job_skill(db, tmp_path):
    """未入图的词不许在 job_skill 上留下半条记录（`pairs` 必须跳过它们）。"""
    _funnel_db(db)
    _write_shard(tmp_path, _cooc_rows(COARSE_CV, ["KV缓存管理"]) + [
        _row("Java开发工程师", row_no=11, body=_short_body(11), tags="孤立词甲"),
        _row("Java开发工程师", row_no=12, body=_short_body(12), tags="孤立词甲"),
    ], stopwords=_cooc_stopwords())

    _run(db, tmp_path)

    named = {sk.id: sk.normalized_name for sk in db.query(models.Skill).all()}
    on_graph = {named[r.skill_id] for r in db.query(models.JobSkill).all()}
    assert "孤立词甲" not in on_graph
    assert {"KV缓存管理", COARSE_CV} <= on_graph


# ============================================================================
# 7.6 证据归属 —— 「一条证据都没有」不等于「证据清一色是本源的」
# ============================================================================

def test_candidate_row_with_no_evidence_is_not_owned(db, tmp_path):
    """零证据的既有 candidate 行不算本挖掘源拥有，不许给它补匿名语料证据。

    `ev_foreign` 只收「至少有一条外源证据」的关系，零证据的行会真空地通过判据，
    于是别人建的空 candidate 行也会被挂上一份不可核验的证据。要求 ev_count > 0。
    """
    job = _seed_job(db, "计算机视觉工程师")
    s_empty = _seed_skill(db, COARSE_CV)
    s_own = _seed_skill(db, COARSE_BACKEND, category="云计算与工程")
    rel_empty = _seed_rel(db, job, s_empty, status="candidate", evidence=())
    rel_own = _seed_rel(db, job, s_own, status="candidate",
                        evidence=[("dataset", "BOSS直聘")])
    db.commit()
    before_js = _rows_checksum(db, models.JobSkill, _ids(db, models.JobSkill))

    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=_short_body(1),
             tags=f"{COARSE_CV}、{COARSE_BACKEND}"),
    ], stopwords=_cooc_stopwords())

    summary = _run(db, tmp_path)
    db.expire_all()

    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == rel_empty.id).count() == 0, \
        "零证据的既有 candidate 行被当成本源的了"
    assert db.query(models.Evidence).filter(
        models.Evidence.job_skill_id == rel_own.id).count() == 2, \
        "证据清一色本源的 candidate 行才是唯一允许补证据的形态"
    assert summary["evidence_created"] == 1
    # 两行都是既有关系，一个字段都不许动
    assert _rows_checksum(db, models.JobSkill, _ids(db, models.JobSkill)) == before_js


# ============================================================================
# 7.7 write 阶段的 in/out 必须同单位
# ============================================================================

def test_write_stage_in_and_out_are_both_row_counts(db, tmp_path):
    """`write` 阶段 out 记的是「真的写进图的行数」，不是岗位数。

    回归：out 曾经记 jobs_touched，与 in（行数）不同单位，前端拿 max(0, in-out)
    兜底就凭空算出「丢弃 250」并标红 —— 单位中途换掉等于在页面上说假话。
    """
    _funnel_db(db)
    # 3 行命中同一个岗位：若 out 记岗位数就会是 1，与 in=3 差出两行假丢弃
    _write_shard(tmp_path, _cooc_rows(COARSE_CV, ["KV缓存管理"]),
                 stopwords=_cooc_stopwords())

    summary = _run(db, tmp_path)
    write = _stage(summary, "write")

    assert summary["jobs_touched"] == 1
    assert write["in_count"] == 3
    assert write["out_count"] == 2, "两行写了图，第三行只有挂不上的填充词"
    assert write["out_count"] <= write["in_count"]
    assert write["dropped"] == {"技能全部仅观测未入图": 1}
    assert f"触达岗位 {summary['jobs_touched']} 个" in write["detail"]


# ============================================================================
# 8. 幂等与游标
# ============================================================================

def test_same_day_completed_run_is_not_repeated(db, tmp_path):
    """同一天重复调用直接返回既有摘要，不再写第二遍。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV,
             tags=f"{COARSE_CV}、Python"),
    ], stopwords={"exact": [], "patterns": []})

    first = _run(db, tmp_path)
    js_after_first = _ids(db, models.JobSkill)
    assert js_after_first, "构造前提：第一轮要真的写了行，否则第二次调用无从「重复写」"
    second = _run(db, tmp_path)

    assert second["run_date"] == first["run_date"]
    assert db.query(models.DailyMiningRun).count() == 1
    assert _ids(db, models.JobSkill) == js_after_first, "第二次调用又写了一遍公开图谱"


def test_force_refuses_to_orphan_public_rows(db, tmp_path):
    """已向公开图谱写过行的运行，`--force` 必须拒绝（否则那些行失去回滚凭据）。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV,
             tags=f"{COARSE_CV}、Python"),
    ], stopwords={"exact": [], "patterns": []})

    first = _run(db, tmp_path)
    assert first["job_skills_created"] > 0, "构造前提：第一轮必须真的写进了公开图谱"
    with pytest.raises(RuntimeError, match="rollback_mining"):
        _run(db, tmp_path, force=True)
    db.rollback()
    assert db.query(models.DailyMiningRun).count() == 1


def test_force_replaces_a_dry_run_record(db, tmp_path):
    """试运行没写过公开图谱 → `--force` 可以覆盖它的观测行。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV,
             tags=f"{COARSE_CV}、Python"),
    ], stopwords={"exact": [], "patterns": []})

    # 先造一条 dry_run 的历史记录（直接建行，避免 dry_run 自身回滚掉）
    db.add(models.DailyMiningRun(run_date="2026-09-03", status="completed",
                                 source_label="BOSS直聘", platform="boss_sim",
                                 shard_index=0, dry_run=True, stage_log=[]))
    db.commit()

    summary = _run(db, tmp_path, force=True)
    assert summary["dry_run"] is False
    assert db.query(models.DailyMiningRun).count() == 1


def test_cursor_follows_the_previous_run(db, tmp_path):
    """不显式指定分片时，游标接着**上一次的 shard_index** 往下走。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1001, body=BODY_CV, tags="Python"),
    ], index=1, stopwords={"exact": [], "patterns": []})
    db.add(models.DailyMiningRun(run_date="2026-09-02", status="completed",
                                 source_label="BOSS直聘", platform="boss_sim",
                                 shard_index=0, dry_run=False, cursor_start=1,
                                 cursor_end=1000, stage_log=[]))
    db.commit()

    summary = _run(db, tmp_path, run_date="2026-09-03", shard_index=None, rows=1000)
    assert summary["shard_index"] == 1
    assert summary["cursor_start"] == summary["cursor_end"] == 1001


def test_cursor_ignores_failed_run_and_latest_resolves_to_completed(db, tmp_path):
    """失败批次既不能跳过分片，也不能抢占公开页 latest / 趋势。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1001, body=BODY_CV,
             tags=f"{COARSE_CV}、Python"),
    ], index=1, stopwords={"exact": [], "patterns": []})
    completed = models.DailyMiningRun(
        run_date="2026-09-02", status="completed", source_label="BOSS直聘",
        platform="boss_sim", shard_index=0, dry_run=False, rows_mapped=1,
        new_skill_points=3, stage_log=[])
    failed = models.DailyMiningRun(
        run_date="2026-09-03", status="failed", source_label="BOSS直聘",
        platform="boss_sim", shard_index=9, dry_run=False, rows_mapped=0,
        error="岗位归一结果为 0", stage_log=[])
    db.add_all([completed, failed])
    db.commit()

    assert mining_router._resolve_run(db, "latest").run_date == "2026-09-02"
    trend = mining_router.skill_trend(days=30, db=db)
    assert [item["run_date"] for item in trend["items"]] == ["2026-09-02"]

    summary = _run(db, tmp_path, run_date="2026-09-04", shard_index=None)
    assert summary["shard_index"] == 1


def test_cursor_advances_past_a_short_final_shard(db, tmp_path):
    """**回归**：末尾分片不满 1000 行时，游标不许原地打转。

    真实语料最后一个分片只有 416 行（row_no 59001–59416）。旧实现拿
    `cursor_end // rows_per_day` 反算分片号，`59416 // 1000 == 59`，于是第 60 天
    以后每天都重新消费 059：run_date 不同所以幂等闸不拦，证据也没有内容级去重，
    同一批 (岗位, 技能) 会被一路追加 dataset 证据直到撞 12 条封顶，
    而漏斗和日间变化悄悄变成一潭死水。任何分片的末行损坏都会踩同一个坑。
    """
    assert 59416 // 1000 == 59, "构造前提：旧的行号反算会把游标钉在 059 上"
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=60001, body=BODY_CV, tags="Python"),
    ], index=60, stopwords={"exact": [], "patterns": []})
    db.add(models.DailyMiningRun(run_date="2026-09-02", status="completed",
                                 source_label="BOSS直聘", platform="boss_sim",
                                 shard_index=59, dry_run=False,
                                 cursor_start=59001, cursor_end=59416, stage_log=[]))
    db.commit()

    summary = _run(db, tmp_path, run_date="2026-09-03", shard_index=None, rows=1000)
    assert summary["shard_index"] == 60, "游标又被行号反算钉回上一个分片了"


def test_cursor_wraps_only_when_the_manifest_declares_the_period(db, tmp_path):
    """跑满一轮才绕回 000，而「一轮有多长」只认 manifest。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV, tags="Python"),
    ], index=0, stopwords={"exact": [], "patterns": []})
    _write_manifest(tmp_path, 2)
    db.add(models.DailyMiningRun(run_date="2026-09-02", status="completed",
                                 source_label="BOSS直聘", platform="boss_sim",
                                 shard_index=1, dry_run=False, stage_log=[]))
    db.commit()

    summary = _run(db, tmp_path, run_date="2026-09-03", shard_index=None, rows=1000)
    assert summary["shard_index"] == 0


def test_cursor_does_not_wrap_without_a_manifest(db, tmp_path):
    """没有 manifest 就不知道周期有多长 —— 宁可往下走到不存在的分片报错，
    也不能凭目录里的文件数把游标一脚踢回开头（临时目录里只放子集是常态）。"""
    _funnel_db(db)
    _write_shard(tmp_path, [
        _row("计算机视觉算法工程师", row_no=1, body=BODY_CV, tags="Python"),
    ], index=0, stopwords={"exact": [], "patterns": []})
    assert mining.shard_count(tmp_path) == 0
    db.add(models.DailyMiningRun(run_date="2026-09-02", status="completed",
                                 source_label="BOSS直聘", platform="boss_sim",
                                 shard_index=0, dry_run=False, stage_log=[]))
    db.commit()

    # 分片 001 不存在 → 如实报错，而不是悄悄回到 000 重采一遍老数据
    with pytest.raises(FileNotFoundError, match="分片不存在"):
        _run(db, tmp_path, run_date="2026-09-03", shard_index=None, rows=1000)
    db.rollback()


def test_shard_count_reads_the_manifest_only(tmp_path):
    """`shard_count` 只认 manifest；目录里躺着几个分片不代表语料的周期长度。"""
    _write_manifest(tmp_path, 60)
    assert mining.shard_count(tmp_path) == 60

    bare = tmp_path / "bare"
    _write_shard(bare, [_row(row_no=1)], index=0)
    _write_shard(bare, [_row(row_no=2)], index=1)
    assert mining.shard_count(bare) == 0, "不许拿文件数兜底"

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{ 这不是 JSON", "utf-8")
    assert mining.shard_count(broken) == 0

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "manifest.json").write_text(json.dumps({"shards": []}), "utf-8")
    assert mining.shard_count(empty) == 0


@pytest.mark.skipif(not (REAL_SHARD_DIR / "manifest.json").exists(),
                    reason="聚合语料未生成（data/aggregate_source 已 gitignore）")
def test_real_manifest_declares_a_short_final_shard():
    """真实 manifest 里最后一个分片确实不满一整批 —— 上面那条回归的现实依据。"""
    manifest = json.loads((REAL_SHARD_DIR / "manifest.json").read_text("utf-8"))
    shards = manifest["shards"]
    assert mining.shard_count(REAL_SHARD_DIR) == len(shards)
    per = int(manifest["rows_per_shard"])
    last = shards[-1]
    assert last["rows"] < per, "末尾分片补满了？那这条回归的构造前提要重写"
    assert last["last_row_no"] // per == last["index"], (
        "旧的行号反算恰好会把游标钉在末尾分片上，这正是被修掉的那个 bug")


# ============================================================================
# 9. 读分片
# ============================================================================

def test_read_shard_skips_bad_lines(tmp_path):
    """坏行跳过而不是整批失败。"""
    path = mining.shard_path(0, tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_row(row_no=1), ensure_ascii=False) + "\n"
        + "{ 这行不是 JSON\n" + "\n"
        + json.dumps(_row(row_no=2), ensure_ascii=False) + "\n", "utf-8")

    rows = mining.read_shard(0, directory=tmp_path)
    assert [r["extra"]["row_no"] for r in rows] == [1, 2]


def test_read_shard_respects_limit(tmp_path):
    _write_shard(tmp_path, [_row(row_no=i) for i in range(1, 11)])
    assert len(mining.read_shard(0, limit=4, directory=tmp_path)) == 4


def test_missing_shard_raises_a_usable_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="分片不存在"):
        mining.read_shard(7, directory=tmp_path)


def test_shard_path_naming(tmp_path):
    assert mining.shard_path(0, tmp_path).name == "boss_sim_000.jsonl"
    assert mining.shard_path(59, tmp_path).name == "boss_sim_059.jsonl"


@pytest.mark.skipif(not (REAL_SHARD_DIR / "boss_sim_000.jsonl").exists(),
                    reason="聚合语料分片未生成（data/aggregate_source 已 gitignore）")
def test_real_shard_has_the_expected_shape():
    """真实分片的字段形状与本文件的 fixture 一致（fixture 漂了就会在这里暴露）。"""
    rows = mining.read_shard(0, limit=20, directory=REAL_SHARD_DIR)
    assert len(rows) == 20
    for r in rows:
        assert r.get("platform") == "boss_sim"
        assert not r.get("company"), "这份语料没有雇主字段——门禁判 candidate 的前提"
        extra = r.get("extra") or {}
        assert isinstance(extra.get("row_no"), int) and extra["row_no"] > 0
        assert "skill_tags" in extra and "company_domain" in extra
