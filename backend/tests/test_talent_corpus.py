"""人才侧图层（意见⑧）与简历别名学习的回归测试（内存 SQLite，不碰云库）。

锁三类容易在后续改动里被悄悄破坏的东西：

1. **隐私最小化不是"填不填"而是"存不存得下"**。简历涉及真人，与 JD（企业主动
   公开的商业信息）口径不同：`talent_profile` 表结构上就没有正文与身份列，正文
   落盘前必须过 `mask_contacts`，并有 `contains_contacts` 自检关口。任何人给这张
   表加一列 `raw_text`／`candidate_name`，测试要当场报出来。

2. **别名学习的三道护栏**。学习结果是从语料里长出来的，护栏一松就会把 JD 主链路
   也污染掉：① 映射目标必须已存在，不许凭空造；② 不覆盖 164 条硬编码 SYNONYMS，
   只做新增；③ 判断型映射（包含关系/语义）必须有多份简历佐证，机械型（只差大小写
   空格）客观可核验故 1 份即可。护栏在学习脚本与 taxonomy 加载处各设一道，
   因为 learned_aliases.json 是会被手改的。

3. **人才侧只读不写**。供需缺口与团队盘点都基于既有 job_skill 计算，不回写岗位
   能力项、不参与置信度——"岗位是不是真要求这项能力"和"有没有人会"是两回事。

`corpus_overview` 的批次入库数一项另有来历：台账里的 `kept` 是采集/清洗环节写下的
数字，而跨批次近重复剔除发生在它之后（res-c-sample_ruiwen 就少了 1 份），直接展示
`kept` 会让台账各行加起来对不上总数。这里锁住"按实际画像行数现算"。
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models
from app.services import talent, taxonomy
from app.services.resume import (
    contains_contacts, mask_contacts, redact_for_storage,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _learn():
    """导入学习脚本。

    它在模块级设 TALENT_DISABLE_LEARNED_ALIASES=1（学习阶段必须在"未加载学习结果"
    的干净词典上判断命中，否则第二次跑会自我确认），这里导入后立刻恢复环境变量，
    免得泄漏给别的测试文件。本模块顶部已 import taxonomy，词典此时早已加载完毕。
    """
    prev = os.environ.get("TALENT_DISABLE_LEARNED_ALIASES")
    try:
        from data import learn_aliases
        return learn_aliases
    finally:
        if prev is None:
            os.environ.pop("TALENT_DISABLE_LEARNED_ALIASES", None)
        else:
            os.environ["TALENT_DISABLE_LEARNED_ALIASES"] = prev


# ----------------------------------------------------------------- 建行小工具

def _skill(db, name, category="人工智能"):
    s = db.query(models.Skill).filter_by(name=name).first()
    if not s:
        s = models.Skill(name=name, normalized_name=name, category=category)
        db.add(s)
        db.flush()
    return s


def _job(db, name="AI智能体开发工程师", caps=()):
    """caps: [(技能名, importance, weight, status)]"""
    j = models.Job(name=name, slug=name, category="人工智能",
                   level="middle", status="published")
    db.add(j)
    db.flush()
    for cname, importance, weight, status in caps:
        s = _skill(db, cname)
        db.add(models.JobSkill(job_id=j.id, skill_id=s.id, importance=importance,
                               weight=weight, status=status, confidence=0.8))
    db.flush()
    return j


def _profile(db, code, skills, matched_job_id=None, holdout=False, batch_id=None,
             source_type="dataset", language="zh"):
    p = models.TalentProfile(code=code, skills=list(skills), skill_count=len(skills),
                             matched_job_id=matched_job_id, holdout=holdout,
                             batch_id=batch_id, source_type=source_type,
                             language=language)
    db.add(p)
    db.flush()
    return p


def _team(db, name="AI 算法组", members=()):
    """members: [(talent_id, 化名, 角色)]"""
    t = models.Team(name=name, description="测试团队")
    db.add(t)
    db.flush()
    for tid, dn, role in members:
        db.add(models.TeamMember(team_id=t.id, talent_id=tid,
                                 display_name=dn, role_label=role))
    db.flush()
    return t


# ========================================================== 一、隐私最小化落库

def test_talent_profile_table_has_no_identity_columns():
    """结构上就存不下身份信息——不是"存了但不填"。"""
    cols = set(models.TalentProfile.__table__.columns.keys())
    forbidden = {"raw_text", "text", "content", "candidate_name", "name",
                 "phone", "mobile", "email", "wechat", "qq", "id_card", "resume_url"}
    assert not (cols & forbidden), f"talent_profile 出现身份/正文列：{cols & forbidden}"
    # 正文只留长度与摘要，且摘要不可还原原文
    assert {"text_len", "text_hash"} <= cols


def test_mask_contacts_covers_every_contact_shape():
    raw = ("张三 电话13812345678 备用 (415) 555-0132 邮箱 abc.def@example.com "
           "微信:zhangsan123 QQ：123456789 身份证 11010119900307391X "
           "主页 https://github.com/zhangsan/resume")
    out = mask_contacts(raw)
    for leak in ["13812345678", "555-0132", "abc.def@example.com", "zhangsan123",
                 "123456789", "11010119900307391X", "github.com"]:
        assert leak not in out, f"{leak} 没被脱敏：{out}"
    assert "[邮箱已脱敏]" in out and "[电话已脱敏]" in out
    assert "[证件号已脱敏]" in out and "[链接已脱敏]" in out


def test_contains_contacts_is_the_selfcheck_gate():
    raw = "联系我 13812345678 或 abc@example.com"
    assert contains_contacts(raw) is True          # 落盘前自检应当拦下
    assert contains_contacts(mask_contacts(raw)) is False
    assert contains_contacts("") is False


def test_redact_for_storage_drops_pii_keys_only():
    parsed = {"candidate_name": "张三", "projects": [{"n": "x"}], "titles": ["架构师"],
              "skills": ["Python"], "years_experience": 3.0, "education": "本科"}
    out = redact_for_storage(parsed)
    assert set(out) == {"skills", "years_experience", "education"}
    assert redact_for_storage({}) == {}


# ================================================ 二、别名学习三道护栏（加载侧）

def test_guard1_tier_a_target_must_be_canonical_skill():
    """护栏①：Tier A 会并入全局 SYNONYMS，映射目标必须是既有规范技能。"""
    assert taxonomy._load_learned_aliases(
        {"aliases": {"某新写法": "Python"}}) == {"某新写法": "Python"}
    assert taxonomy._load_learned_aliases(
        {"aliases": {"某新写法": "图谱里根本没有的技能"}}) == {}


def test_guard2_tier_a_never_overrides_hardcoded_synonym():
    """护栏②：只做新增，不覆盖 164 条硬编码词典。"""
    assert "k8s" in taxonomy.SYNONYMS
    assert taxonomy._load_learned_aliases({"aliases": {"k8s": "Docker"}}) == {}
    assert taxonomy.SYNONYMS["k8s"] == "Kubernetes"      # 原义未被改写


def test_guard2_applies_to_tier_b_node_aliases_too():
    assert taxonomy._load_node_aliases({"node_aliases": {"k8s": "K8s节点"}}) == {}
    # 不与硬编码冲突的才收，且键统一小写（别名查表本来就是 .lower() 后查的）
    assert taxonomy._load_node_aliases(
        {"node_aliases": {"NumPy": "numpy"}}) == {"numpy": "numpy"}


def test_learned_aliases_can_be_switched_off_by_env():
    """评测"学之前 vs 学之后"靠的就是这个开关，一键回滚。"""
    prev = os.environ.get("TALENT_DISABLE_LEARNED_ALIASES")
    os.environ["TALENT_DISABLE_LEARNED_ALIASES"] = "1"
    try:
        assert taxonomy._read_learned() == {}
    finally:
        if prev is None:
            os.environ.pop("TALENT_DISABLE_LEARNED_ALIASES", None)
        else:
            os.environ["TALENT_DISABLE_LEARNED_ALIASES"] = prev


def test_resolve_skill_applies_node_alias_but_normalize_skill_does_not(monkeypatch):
    """Tier B 只作用于人才侧解析，JD 主链路（normalize_skill）零影响。"""
    monkeypatch.setattr(taxonomy, "NODE_ALIASES", {"map reduce": "MapReduce"})
    assert taxonomy.resolve_skill("Map Reduce") == "MapReduce"
    assert taxonomy.normalize_skill("Map Reduce") == "Map Reduce"


# ================================================ 三、别名学习三道护栏（学习侧）

def test_guard1_tier_b_can_only_map_to_existing_graph_node():
    """护栏①：Tier B 的映射目标必须是图谱里已有的节点，不许凭空造。"""
    lz = _learn()
    node_names = ["NumPy", "Java", "MapReduce"]
    nodes = {n.lower(): n for n in node_names}
    nodes_sq = {lz.squeeze(n): n for n in node_names}

    hit = lz.map_node("Numpy", nodes, nodes_sq, node_names)
    assert hit == ("NumPy", "node_case")
    assert lz.map_node("图谱里没有的技能X", nodes, nodes_sq, node_names) is None
    # 命中结果一定来自传入的节点集合
    assert hit[0] in node_names


def test_guard3_mechanical_and_judgment_methods_are_gated_differently():
    """护栏③：机械型映射客观可核验故 1 份即可，判断型必须多份简历佐证。

    学习脚本的门槛就是 `method not in MECHANICAL_METHODS and n_prof < min_count`，
    这里锁住方法的归类——归类错了，门槛就形同虚设。
    """
    lz = _learn()
    canon, method = lz.map_canonical("PYTHON")           # 只差大小写
    assert (canon, method) == ("Python", "case_space")
    assert method in lz.MECHANICAL_METHODS               # 1 份即可采纳

    canon2, method2 = lz.map_canonical("Python爬虫")      # 整段包含规范名
    assert canon2 == "Python" and method2 == "containment"
    assert method2 not in lz.MECHANICAL_METHODS          # 需 ≥min-count 份佐证


def test_is_learnable_rejects_dictionary_hits_and_sentence_fragments():
    lz = _learn()
    assert lz.is_learnable("k8s")[0] is False            # 护栏②：词典已有
    assert lz.is_learnable("Python")[0] is False         # 本身就是规范名
    assert lz.is_learnable("三年以上后端开发经验")[0] is False  # 像句子片段
    assert lz.is_learnable("X")[0] is False              # 太短
    assert lz.is_learnable("Nest.js")[0] is True


def test_contains_token_requires_word_boundary_for_latin():
    """纯子串匹配闹过的笑话：Eclipse 判成 CLIP（e-clip-se）、PLSQL 判成 SQL。"""
    lz = _learn()
    assert lz.contains_token("Eclipse", "CLIP") is False
    assert lz.contains_token("PLSQL", "SQL") is False
    assert lz.contains_token("Core Java", "Java") is True
    assert lz.contains_token("分布式消息队列", "消息队列") is True   # 纯中文不卡词边界


def test_clean_term_strips_chinese_prefix_and_suffix():
    lz = _learn()
    assert lz.clean_term("熟练掌握Kubernetes") == "Kubernetes"
    assert lz.clean_term("Spark开发经验") == "Spark"
    assert lz.clean_term("  ·Redis ") == "Redis"


def test_mark_holdout_is_deterministic(db):
    """留出集划分必须可复现，否则"学之前 vs 学之后"的对照评测没有意义。"""
    lz = _learn()
    for i in range(10):
        _profile(db, f"T{i:03d}", ["Python"])
    db.commit()

    lz.mark_holdout(db, 3)
    first = sorted(p.code for p in db.query(models.TalentProfile)
                   .filter(models.TalentProfile.holdout.is_(True)).all())
    lz.mark_holdout(db, 3)
    second = sorted(p.code for p in db.query(models.TalentProfile)
                    .filter(models.TalentProfile.holdout.is_(True)).all())
    assert len(first) == 3 and first == second


# ================================================================ 四、供需缺口

def test_supply_demand_counts_rates_and_gap(db):
    job = _job(db, caps=[("Python", "required", 0.9, "active"),
                         ("Kubernetes", "required", 0.6, "active"),
                         ("Docker", "bonus", 0.3, "active")])
    _profile(db, "T001", ["Python"])
    _profile(db, "T002", ["Python", "Docker"])
    _profile(db, "T003", ["Java"])
    db.commit()

    out = talent.supply_demand(db, job.id)
    assert out["corpus_size"] == 3
    by = {i["skill"]: i for i in out["items"]}
    assert by["Python"]["supply_count"] == 2
    assert by["Python"]["supply_rate"] == pytest.approx(0.6667, abs=1e-4)
    assert by["Python"]["gap"] == pytest.approx(0.3, abs=1e-4)        # 0.9×(1−2/3)
    assert by["Kubernetes"]["supply_count"] == 0
    assert by["Kubernetes"]["gap"] == pytest.approx(0.6, abs=1e-4)
    # 必备覆盖只数 required，加分项不计入
    assert out["required_total"] == 2 and out["required_covered"] == 1
    assert out["coverage_rate"] == pytest.approx(0.5)
    assert out["items"][0]["skill"] == "Kubernetes"                   # 按缺口倒序


def test_supply_demand_separates_aligned_talents(db):
    """对口人才（目标岗位就是它的）单独统计一档，避免被全语料稀释。"""
    job = _job(db, caps=[("Python", "required", 0.9, "active"),
                         ("Kubernetes", "required", 0.6, "active")])
    _profile(db, "T001", ["Python"], matched_job_id=job.id)
    _profile(db, "T002", ["Python"])
    db.commit()

    out = talent.supply_demand(db, job.id)
    by = {i["skill"]: i for i in out["items"]}
    assert out["aligned_talents"] == 1
    assert by["Python"]["supply_count"] == 2
    assert by["Python"]["aligned_supply_count"] == 1
    assert by["Python"]["aligned_supply_rate"] == pytest.approx(1.0)
    assert by["Kubernetes"]["aligned_supply_rate"] == pytest.approx(0.0)


def test_supply_demand_ignores_deprecated_capabilities(db):
    """演化下线的能力项不该再出现在供需对照里。"""
    job = _job(db, caps=[("Python", "required", 0.9, "active"),
                         ("Hadoop", "required", 0.5, "deprecated")])
    _profile(db, "T001", ["Python", "Hadoop"])
    db.commit()

    out = talent.supply_demand(db, job.id)
    assert {i["skill"] for i in out["items"]} == {"Python"}


def test_supply_demand_empty_corpus_and_unknown_job(db):
    job = _job(db, caps=[("Python", "required", 0.9, "active")])
    db.commit()

    out = talent.supply_demand(db, job.id)              # 语料为空不能除零
    assert out["corpus_size"] == 0
    assert out["items"][0]["supply_rate"] == 0.0
    assert out["items"][0]["gap"] == pytest.approx(0.9)
    assert out["coverage_rate"] == 0.0
    assert talent.supply_demand(db, 999999) == {}


# ============================================================ 五、团队能力盘点

def test_team_gap_reports_who_covers_and_what_is_missing(db):
    job = _job(db, caps=[("Python", "required", 0.9, "active"),
                         ("Kubernetes", "required", 0.6, "active"),
                         ("Docker", "bonus", 0.3, "active")])
    a = _profile(db, "T001", ["Python"])
    b = _profile(db, "T002", ["Python"])
    team = _team(db, members=[(a.id, "成员A", "算法"), (b.id, "成员B", "算法")])
    db.commit()

    out = talent.team_gap(db, team.id, job.id)
    assert out["team"]["size"] == 2
    assert out["required_total"] == 2 and out["required_covered"] == 1
    assert out["coverage_rate"] == pytest.approx(0.5)
    assert out["weighted_coverage"] == pytest.approx(0.6, abs=1e-4)   # 0.9/(0.9+0.6)
    # 谁能补：Python 两个人都会
    assert [c["skill"] for c in out["covered"]] == ["Python"]
    assert len(out["covered"][0]["holders"]) == 2
    assert {h["display_name"] for h in out["covered"][0]["holders"]} == {"成员A", "成员B"}
    # 还缺谁：没人具备 Kubernetes
    assert [m["skill"] for m in out["missing"]] == ["Kubernetes"]
    assert out["missing"][0]["holders"] == []
    assert out["bonus_total"] == 1 and out["bonus_covered"] == 0


def test_team_gap_marks_irreplaceable_contribution(db):
    """uniquely_covers = 只有他会的必备能力，用来看团队的不可替代性/单点风险。"""
    job = _job(db, caps=[("Python", "required", 0.9, "active"),
                         ("Kubernetes", "required", 0.6, "active"),
                         ("Docker", "bonus", 0.3, "active")])
    a = _profile(db, "T001", ["Python", "Docker"])
    b = _profile(db, "T002", ["Kubernetes"])
    team = _team(db, members=[(a.id, "成员A", "算法"), (b.id, "成员B", "工程")])
    db.commit()

    out = talent.team_gap(db, team.id, job.id)
    assert out["required_covered"] == 2 and out["missing"] == []
    assert out["weighted_coverage"] == pytest.approx(1.0)
    assert out["bonus_covered"] == 1
    contrib = {c["display_name"]: c for c in out["contributions"]}
    assert contrib["成员A"]["uniquely_covers"] == 1
    assert contrib["成员A"]["unique_skills"] == ["Python"]
    assert contrib["成员B"]["uniquely_covers"] == 1
    assert contrib["成员B"]["unique_skills"] == ["Kubernetes"]


def test_team_gap_empty_team_puts_everything_in_missing(db):
    job = _job(db, caps=[("Python", "required", 0.9, "active"),
                         ("Kubernetes", "required", 0.6, "active")])
    team = _team(db, members=[])
    db.commit()

    out = talent.team_gap(db, team.id, job.id)
    assert out["team"]["size"] == 0
    assert out["required_covered"] == 0
    assert out["coverage_rate"] == 0.0
    assert out["weighted_coverage"] == 0.0                # 除零兜底
    assert {m["skill"] for m in out["missing"]} == {"Python", "Kubernetes"}
    assert out["contributions"] == []


def test_team_gap_unknown_ids_return_empty(db):
    job = _job(db, caps=[("Python", "required", 0.9, "active")])
    team = _team(db, members=[])
    db.commit()
    assert talent.team_gap(db, 999999, job.id) == {}
    assert talent.team_gap(db, team.id, 999999) == {}


def test_talent_skill_index_aligns_terms_to_graph_nodes(monkeypatch, db):
    """人才侧技能先经 resolve_skill 对齐，再与图谱节点比对——对不齐就等于白抽。"""
    monkeypatch.setattr(taxonomy, "NODE_ALIASES", {"map reduce": "MapReduce"})
    p = _profile(db, "T001", ["Map Reduce"])
    db.commit()

    idx = talent.talent_skill_index(db)
    assert idx.get("MapReduce") == {p.id}
    assert "Map Reduce" not in idx


# ================================================================ 六、语料台账

def _batch(db, key, kept, source_type="sample"):
    b = models.ResumeBatch(batch_key=key, source_type=source_type,
                           source_name=key, source_url="https://example.com/",
                           license="页面公开", tier=source_type, kept=kept,
                           collected=kept, robots_ok=True, raw_dir=f"data/raw/{key}")
    db.add(b)
    db.flush()
    return b


def test_corpus_overview_counts_profiles_from_rows_not_from_kept(db):
    """台账入库数按实际画像行数现算。

    `kept` 是采集/清洗环节写下的数字，跨批次近重复剔除发生在它之后（线上
    res-c-sample_ruiwen 就少了 1 份）。直接展示 kept，台账各行加起来会对不上总数。
    """
    b1 = _batch(db, "2026W31-res-c-sample_a", kept=3)
    b2 = _batch(db, "2026W31-res-c-sample_b", kept=3)      # 报 3 份，实际只入库 2 份
    for i in range(3):
        _profile(db, f"A{i}", ["Python"], batch_id=b1.id)
    for i in range(2):
        _profile(db, f"B{i}", ["Python"], batch_id=b2.id)
    db.commit()

    out = talent.corpus_overview(db)
    rows = {b["batch_key"]: b for b in out["batches"]}
    assert rows["2026W31-res-c-sample_a"]["profiles"] == 3
    assert rows["2026W31-res-c-sample_b"]["kept"] == 3
    assert rows["2026W31-res-c-sample_b"]["profiles"] == 2      # 现算，不照抄 kept
    assert sum(b["profiles"] for b in out["batches"]) == out["total_profiles"] == 5


def test_corpus_overview_tallies_holdout_sources_and_aliases(db):
    b = _batch(db, "2026W31-res-a-dataset", kept=3, source_type="dataset")
    _profile(db, "T001", ["Python", "Java"], batch_id=b.id, language="zh", holdout=True)
    _profile(db, "T002", ["Python"], batch_id=b.id, language="en",
             source_type="web")
    db.add_all([
        models.SkillAlias(alias="Numpy", canonical="numpy", status="accepted"),
        models.SkillAlias(alias="Map Reduce", canonical="MapReduce", status="accepted"),
        models.SkillAlias(alias="tomcat", canonical=None, status="rejected"),
    ])
    db.commit()

    out = talent.corpus_overview(db)
    assert out["total_profiles"] == 2
    assert out["total_skills_extracted"] == 3
    assert out["holdout"] == 1
    assert out["by_source"] == {"dataset": 1, "web": 1}
    assert out["by_language"] == {"zh": 1, "en": 1}
    assert out["alias_accepted"] == 2 and out["alias_rejected"] == 1


def test_corpus_overview_states_the_privacy_boundary(db):
    db.commit()
    out = talent.corpus_overview(db)
    assert out["total_profiles"] == 0
    assert "不入库" in out["privacy_notice"]
    assert out["batches"] == []
