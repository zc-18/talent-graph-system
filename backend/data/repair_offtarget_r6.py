# -*- coding: utf-8 -*-
"""把「靠检索词挂错岗位」的 (JD, 岗位) 对摘出图谱（R6 缺陷①）。

## 病灶

飞书 ATS 与国聘的关键词检索**命中 JD 正文而不只是标题**，`data/import_raw.py`
纯按检索词给每条 JD 打 `cluster_hint`、完全不看标题，`ingest.title_key()` 又对
任何「非既定标题」**无条件采信 cluster_hint**。三段串起来，就出现了：

    数据分析实习生（蔚来）              → 车联网系统工程师
    标准芯片-采购资深经理（电子元器件）（小鹏） → 车联网系统工程师
    资深GUI视觉设计师（网易游戏）         → 人工智能数字人训练师

用采购经理的 JD 给车联网岗位供能力证据，是答辩上最经不起问的一处。

## 判定规则（两条判据取并集，各自都要求「标题」而不是正文说话）

**判据① 错簇**（对应上面那条三段链路）三个条件同时成立：
  1. `cluster_hint` 指向该岗位——挂载确实是检索词造成的；
  2. `title_key(标题, None) != 岗位名`——去掉 hint 后标题本身**定位不到**该岗位，
     即 hint 是承重的；
  3. 标题不带该簇的领域词，且标题点名的是另一种职能。
     领域词表取 Lane F 人工核过的 22 簇白名单 **∪** `queries.json` 里该簇的全部检索词
     **∪** 簇名 **∪** 岗位规范名——取并集是刻意的，**越宽越保守**。

**判据② 非研发职能**：标题点名的根本不是研发职能（销售 / 视觉设计师 / 内容运营 /
讲师 / 采购…），且那不是本岗位自身的职能。这条不要求 hint 承重，因为
`_keyword_cluster_map()` 按标题关键词反查也会把「AIGC 视觉设计师」挂到 AIGC 算法工程师。
`JOB_OWN_FUNCTION` 豁免 AI产品经理——它本来就是产品岗，Lane F 报告里
「AI产品经理 100%」正是这个筛查口径的假阳性。

**`ENG_ROLE` 护栏（判据②专用，只用来放过、不用来摘）**：标题里同时出现研发角色词
（工程师 / 开发 / 算法 / 分析师 / 运维…）时一律保留——这类标题里的职能词多半是
**业务域或部门修饰语**，不是这个人的职能。实测护栏保下 20 条真数据：
`供应链数据分析师`、`JAVA软件运营维护工程师`、`大模型应用算法工程师（智能客服）`、
`NLP算法(职位编号：商业运营中心4)`、`后端开发工程师（供应链）`……
判据①不套这条护栏：那边「hint 承重 + 无领域词」已经足够说明挂错，
`数据分析实习生 → 车联网` 正是靠这一点摘掉的。

规则命中 **155 / 1494 对（10.4%）**。作为对照，Lane F 报告里的 214 是一个**筛查**口径
（`标题命中非研发词` 单条判据），其中 94 条挂载并不依赖 cluster_hint、
23 条是 AI产品经理的正常产品岗标题，Lane F 自己在报告里就点了这个假阳性。

## 处理方式（对齐 `data/detach_offtarget_r5.py`，**不删数据**）

RawJD 是合法采集的真实 JD，采集台账与语料计数全部保留，只做两件事：

1. `cluster_hint` 置空 → 落进「待映射」桶，将来重建不再挂到这个岗位；
2. **外科手术式删掉对应的 `Evidence` 行** —— 只删「该岗位 × 该 JD」这一个组合，
   同一条 JD 供给别的岗位的证据一行不动。

### 为什么不走 `run_pipeline.py --from-db --only-jobs` 重建

`cluster_hint` 置空只影响**将来**的建图，已经写进去的 `Evidence` 行还挂在那里，
不动它图谱就没真干净。而重建要走 `graph_service.upsert_job` 的**破坏性路径**——
它清空该岗位的全部 `JobSkill`/`Evidence` 再按传入结果重建。CLAUDE.md 记的两次事故
（discovery 用 10 条幻觉能力替掉 301 条交叉验证能力、apply_evolution 静默降级 313 行
而只写 0 条删除日志）都出在这条路上，`tests/test_rebuild_guards.py` 就是拦它的。
本次受影响的 28 个岗位里有 15 个跑过演化（v2/v3，621 条变更记录），重建会把
演化叙事一起冲掉。所以走 `repair_click_evidence.py` / `repair_phantom_changes.py`
那条外科手术路线：**只动被判定为错挂的那些证据行，不新增行、不删除 JobSkill、
不碰其它行的 status**。

### 删完证据之后怎么收口

删证据会让一部分能力项跌破「≥2 个独立雇主才算确认能力项」的交叉验证门槛。
判据严格限定在**因本次删除而跌破**：删前 ≥2、删后 <2 的降级为 `candidate`；
删前本来就 <2 的一行不碰（那是别的 lane 的历史状态，不归本脚本管）；
证据被删光的同样降为 `candidate` 而不是删除——删除会让证据链断掉，
留成候选项可查、可解释。

重算完全复用生产算式：`confidence_batch._job_calculation`（`services/confidence.py`
那一份唯一公式）与 `_employer_key`（已折叠 `Employer.parent_id`），不另写一套。

用法（backend/ 下）。**dry-run 才可以对着生产库跑（零写入）；`--apply` 对
`talent_graph_v3` 会被 `repair_safety.assert_shadow_apply_target` 无条件拒绝**——
先用 `data/clone_database_r6.py` 克隆出影子库，在影子库上 apply、验收，再切 DB_NAME：

    $env:DB_NAME='talent_graph_v3'
    uv run python -X utf8 data/repair_offtarget_r6.py            # dry-run，可对生产库

    $env:DB_NAME='talent_graph_v4_shadow'
    uv run python -X utf8 data/repair_offtarget_r6.py --apply         --allow-shadow --confirm-database talent_graph_v4_shadow

`--limit-report N` 只影响 dry-run 里逐条列出的样本条数。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, text  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import (  # noqa: E402
    confidence_batch, repair_safety, role_contract, state_reconcile)
from app.services.ingest import _cluster_name_map, title_key  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
BACKUP_DIR = Path(__file__).resolve().parent / "backup"
MIN_EMPLOYERS = 2          # 与 role_contract.MIN_EMPLOYERS / 交叉验证门槛一致
# 降级原因常量。收尾统计原先按字面量比对，与 mutate() 实际写入的字符串对不上，
# 于是无论降级多少条都打印「跌破门槛 0，证据清零 0」——交付材料里的假账。
REASON_BELOW_GATE = "跌破独立雇主门槛"
REASON_NO_EVIDENCE = "证据清零"

# ---------------------------------------------------------------- 判定词表
# Lane F 人工核过的「标题必须自带岗位领域词」白名单（22 簇），原样搬来。
TITLE_OK: dict[str, str] = {
    "自动驾驶": r"自动驾驶|智驾|智能驾驶|辅助驾驶|无人驾驶|无人车|感知算法|点云|激光雷达|BEV|决策规划|规控|域控|SLAM|定位与建图|端到端算法",
    "机器人算法": r"机器人|机械臂|运动控制|运控|导航算法|SLAM|具身|AGV|AMR|无人系统|多足|人形",
    "多模态": r"多模态|视觉语言|VLM|VLA|跨模态|图文|文生图|文生视频|视频生成|图像生成",
    "智能硬件": r"硬件|嵌入式|固件|电路|驱动开发|智能终端|可穿戴|整机|结构设计|BSP|单片机|MCU|FPGA",
    "车联网": r"车联网|智能网联|座舱|车载|T-?Box|AUTOSAR|域控|整车|车规|汽车电子|TSP|车云|OTA",
    "嵌入式": r"嵌入式|固件|BSP|驱动|RTOS|单片机|MCU|Linux内核",
    "AIGC": r"AIGC|生成式|文生|图像生成|视频生成|扩散|Diffusion|数字内容",
    "数据分析": r"数据分析|商业分析|BI|数据洞察|经营分析",
    "大数据平台": r"大数据|数据平台|实时计算|流计算|数据中台|Flink|Spark",
    "物联网": r"物联网|IoT|MQTT|设备接入|边缘网关|传感|终端接入",
    "计算机视觉": r"计算机视觉|机器视觉|视觉算法|视觉感知|图像算法|图像处理|CV算法|OCR|目标检测|图像分割|3D重建|点云|视频算法",
    "推荐算法": r"推荐|搜索|排序|召回|特征工程|广告算法",
    "数据仓库": r"数仓|数据仓库|数据治理|数据质量|ETL|数据建模",
    "AI产品": r"产品经理|产品总监|产品负责人|产品策划",
    "运维开发": r"运维|SRE|可观测|监控|稳定性|DevOps|发布|值班",
    "自然语言处理": r"自然语言|NLP|对话|语义|文本|大语言模型|LLM",
    "深度学习": r"深度学习|神经网络|模型训练|端侧推理|模型压缩|算子|推理引擎",
    "数据开发": r"数据开发|数据研发|数据管道|数据集成|ETL|数仓开发",
    "云计算": r"云计算|云原生|云平台|Kubernetes|K8s|虚拟化|云网络|网络架构|IaaS|PaaS",
    "工业互联网": r"工业互联网|工业物联网|工业软件|MES|SCADA|PLC|OPC|工业控制|数采|产线",
    "提示词工程": r"提示词|Prompt|大模型应用|AI应用",
    "边缘计算": r"边缘计算|边缘|端侧|端边|网关",
    # Lane F 未覆盖的 10 个簇，按该簇检索词的词根补齐（只放宽、不收紧）
    "大模型算法": r"大模型|大语言模型|LLM|基础模型|预训练|Foundation",
    "机器学习": r"机器学习|ML|算法|模型",
    "Java开发": r"Java|后端|服务端|服务器端",
    "后端开发": r"后端|服务端|服务器端|Java|Go|Python|PHP|C\+\+|架构|平台研发|应用开发|软件开发|研发工程师",
    "数字孪生": r"数字孪生|孪生|仿真|建模|BIM|三维",
    "智能体开发": r"智能体|Agent|Agentic|MCP|工作流|大模型应用|AI应用|LLM应用",
    "具身智能": r"具身|机器人|人形|机械臂|运动控制|操作策略|VLA",
    "大模型推理优化": r"推理|部署|加速|量化|蒸馏|编译|算子|性能优化|高性能|异构|CUDA|GPU|芯片|架构",
    "大模型测试": r"测试|评测|质量|QA|验证",
    "数字人": r"数字人|虚拟人|虚拟主播|数字员工|虚拟形象|语音合成|TTS|动作捕捉|AI训练师|人工智能训练师|标注",
}

# 标题点名的是「另一种职能」。刻意不含 实习生/管培生/BD ——
# 那是用工形式与缩写，不是职能（「图像算法实习生」是真算法岗证据，摘掉就是误伤）。
OFF_FUNCTION = re.compile(
    r"产品经理|产品总监|产品负责人|产品策划|产品专家|产品运营|产品实习生|"
    r"设计师|视觉设计|平面设计|交互设计|动效设计|美术|"
    r"销售|商务|渠道经理|市场部|市场分析|品牌|公关|投资|战略|"
    r"运营|人力|财务|法务|行政|客服|审计|会计|出纳|供应链|采购|"
    r"文案|编辑|主播|摄影|翻译|讲师|培训|"
    r"项目经理|品质经理|经理岗|管培生|策划")
# 判据①用的「非研发标题」口径，与 Lane F 报告 214 那个数同源
NON_ENG = re.compile(
    r"产品经理|设计师|采购|销售|运营|人力|财务|法务|市场|品牌|行政|"
    r"客服|培训|讲师|BD|商务|投资|战略|公关|供应链|品质经理|项目经理|"
    r"实习生|管培生|翻译|文案|编辑|主播|摄影")
# 岗位规范名自带的职能词——本岗位就是干这个的，不算「另一种职能」
JOB_OWN_FUNCTION = re.compile(r"产品经理|设计师|训练师")
# 研发角色词护栏（仅判据②）：出现即保留
ENG_ROLE = re.compile(
    r"工程师|开发|研发|算法|架构师|程序员|技术专家|科学家|分析师|数据分析|"
    r"运维|测试|工程技术|建模|数据挖掘|后台|前端|全栈", re.I)

_FLAT = re.compile(r"[\s\-_·/（）()\[\]【】]+")
_QUERIES = json.loads(
    (BASE / "data" / "collect" / "queries.json").read_text("utf-8"))["queries"]
_NAMES = _cluster_name_map()
_CLUSTER_OF_JOB = {v: k for k, v in _NAMES.items()}

PAIR_SQL = """
    SELECT DISTINCT j.id, j.name, rj.id, rj.job_title, rj.company, rj.platform, rj.cluster_hint
    FROM raw_jd rj
    JOIN evidence e ON e.raw_jd_id = rj.id
    JOIN job_skill js ON js.id = e.job_skill_id
    JOIN job j ON j.id = js.job_id
"""


def domain_ok(cluster: str, title: str) -> bool:
    """标题是否自带该簇的岗位领域词。三个词源取并集，越宽越保守。"""
    if cluster in TITLE_OK and re.search(TITLE_OK[cluster], title or "", re.I):
        return True
    flat = _FLAT.sub("", title or "").casefold()
    for word in list(_QUERIES.get(cluster, [])) + [cluster, _NAMES.get(cluster, "")]:
        normalized = _FLAT.sub("", word or "").casefold()
        if normalized and normalized in flat:
            return True
    return False


def wrong_cluster(job_name: str, title: str, hint: str | None) -> bool:
    """判据①：挂载靠 cluster_hint 承重，标题既无该簇领域词、又点名别的职能。"""
    if not hint or _NAMES.get(hint) != job_name:
        return False
    if title_key(title or "", None) == job_name:
        return False
    return not domain_ok(hint, title or "") and bool(NON_ENG.search(title or ""))


def off_function(job_name: str, title: str) -> bool:
    """判据②：标题点名的根本不是研发职能，且那不是本岗位自身的职能。"""
    return bool(OFF_FUNCTION.search(title or "")
                and not JOB_OWN_FUNCTION.search(job_name or "")
                and not ENG_ROLE.search(title or ""))


def verdict(job_name: str, title: str, hint: str | None) -> str | None:
    if wrong_cluster(job_name, title, hint):
        return "①错簇"
    if off_function(job_name, title):
        return "②非研发职能"
    return None


# ---------------------------------------------------------------- 计划与执行
def build_plan(db) -> dict:
    """只读地算出：要删哪些证据行、要清哪些 cluster_hint、会影响哪些能力项。"""
    pairs = db.execute(text(PAIR_SQL)).fetchall()
    offtarget = []
    for job_id, job_name, raw_id, title, company, platform, hint in pairs:
        why = verdict(job_name, title or "", hint)
        if why:
            offtarget.append({
                "job_id": job_id, "job_name": job_name, "raw_jd_id": raw_id,
                "title": title or "", "company": company or "",
                "platform": platform or "", "hint": hint, "why": why,
            })
    keys = {(row["job_id"], row["raw_jd_id"]) for row in offtarget}

    evidence_rows = (db.query(models.Evidence, models.JobSkill)
                     .join(models.JobSkill, models.JobSkill.id == models.Evidence.job_skill_id)
                     .filter(models.Evidence.raw_jd_id.in_(
                         {row["raw_jd_id"] for row in offtarget})).all()) if offtarget else []
    doomed = [(ev, js) for ev, js in evidence_rows if (js.job_id, ev.raw_jd_id) in keys]

    # cluster_hint 只在「该 hint 指向的正是判定为错挂的那个岗位」时才清空——
    # 同一条 JD 若还合法供着别的岗位，它的 hint 不动。
    hint_clear = {row["raw_jd_id"] for row in offtarget
                  if row["hint"] and _NAMES.get(row["hint"]) == row["job_name"]}

    affected_relations = {js.id: js for _ev, js in doomed}
    return {
        "pairs_total": len(pairs), "offtarget": offtarget,
        "doomed_evidence": doomed, "hint_clear": hint_clear,
        "affected_relations": affected_relations,
    }


def _employer_counts(db, relation_ids: set[int], removed: set[int],
                     as_of) -> dict[int, tuple[int, int]]:
    """每条能力项删前/删后的独立雇主数。口径与 confidence_batch 完全一致。"""
    if not relation_ids:
        return {}
    rows = db.query(models.Evidence).filter(
        models.Evidence.job_skill_id.in_(relation_ids)).all()
    raw_ids = {r.raw_jd_id for r in rows if r.raw_jd_id}
    raws = {r.id: r for r in db.query(models.RawJD).filter(
        models.RawJD.id.in_(raw_ids)).all()} if raw_ids else {}
    employer_ids = {r.employer_id for r in raws.values() if r.employer_id}
    employers = {e.id: e for e in db.query(models.Employer).filter(
        models.Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    parent_ids = {e.parent_id for e in employers.values() if e.parent_id}
    if parent_ids:
        employers.update({e.id: e for e in db.query(models.Employer).filter(
            models.Employer.id.in_(parent_ids)).all()})

    before: dict[int, set] = defaultdict(set)
    after: dict[int, set] = defaultdict(set)
    for ev in rows:
        raw = raws.get(ev.raw_jd_id)
        if ev.source_type != "jd" or not confidence_batch._is_valid_raw_jd(raw, as_of):
            continue
        key = confidence_batch._employer_key(raw, employers)
        if key is None:
            continue
        before[ev.job_skill_id].add(key)
        if ev.id not in removed:
            after[ev.job_skill_id].add(key)
    return {rid: (len(before.get(rid, ())), len(after.get(rid, ())))
            for rid in relation_ids}


def _simulate(db, as_of):
    """在同一会话里跑两遍生产算式取差，结束 rollback。

    计划在函数内部重建：第一次 rollback 会让调用方传进来的 ORM 对象过期，
    拿过期对象去 mutate 读到的是回滚前的值。原先形参 plan 就是这样被
    下一行同名赋值静默吃掉的，签名与实际行为不符。
    """
    jobs = db.query(models.Job).filter(models.Job.status == "published").order_by(
        models.Job.id).all()

    def snapshot():
        summaries = role_contract.contract_summaries_for_jobs(db, jobs)
        return {job.id: {
            "name": job.name,
            "confidence": confidence_batch._job_calculation(db, job, as_of)["confidence"],
            "employers": summaries[job.id]["employer_count"],
            "clusters": summaries[job.id]["required_count"],
            "status": summaries[job.id]["contract_status"],
        } for job in jobs}

    before = snapshot()
    db.rollback()
    mutate(db, build_plan(db), as_of)
    db.flush()
    after = snapshot()
    db.rollback()
    return before, after


def mutate(db, plan, as_of, *, run_id: str = "r6-offtarget-simulation") -> dict:
    """Delete only selected evidence/hints, then reconcile the complete graph state."""
    counts = _employer_counts(
        db, set(plan["affected_relations"]),
        {ev.id for ev, _js in plan["doomed_evidence"]}, as_of)
    before_status = {row.id: row.status for row in db.query(models.JobSkill).all()}
    for ev, _js in plan["doomed_evidence"]:
        db.delete(ev)
    if plan["hint_clear"]:
        for raw in db.query(models.RawJD).filter(
                models.RawJD.id.in_(plan["hint_clear"])).all():
            raw.cluster_hint = None
    db.flush()
    manifest = state_reconcile.reconcile_all(
        db, as_of=as_of, run_id=run_id,
        audit_action="graph.repair.offtarget_r6",
        audit_context={
            "evidence_deleted": len(plan["doomed_evidence"]),
            "cluster_hint_cleared": len(plan["hint_clear"]),
        },
        force_audit=bool(plan["doomed_evidence"] or plan["hint_clear"]))
    surviving = {row[0] for row in db.query(models.Evidence.job_skill_id).filter(
        models.Evidence.job_skill_id.in_(set(before_status))).distinct().all()
    } if before_status else set()
    demoted = [
        (row, counts.get(row.id, (row.source_count, row.source_count))[0],
         row.source_count,
         REASON_NO_EVIDENCE if row.id not in surviving else REASON_BELOW_GATE)
        for row in db.query(models.JobSkill).filter(
            models.JobSkill.status == "candidate").all()
        if before_status.get(row.id) == "active"]
    return {"demoted": demoted, "counts": counts, "reconcile": manifest}


def _verify(db) -> bool:
    ok = True
    plan = build_plan(db)
    leftover = len(plan["doomed_evidence"])
    if leftover:
        ok = False
        print(f"  [FAIL] 仍有 {leftover} 条错挂证据未清理")
    stuck = db.query(models.RawJD).filter(
        models.RawJD.id.in_(plan["hint_clear"])).count() if plan["hint_clear"] else 0
    if stuck:
        ok = False
        print(f"  [FAIL] 仍有 {stuck} 条 RawJD 的 cluster_hint 未清空")
    orphan = db.execute(text(
        "SELECT COUNT(*) FROM job_skill js WHERE js.status='active' "
        "AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.job_skill_id=js.id)")).scalar()
    untraceable = db.execute(text(
        "SELECT COUNT(*) FROM evidence WHERE raw_jd_id IS NULL")).scalar()
    jd_total = db.query(models.RawJD).count()
    batches = db.query(models.CrawlBatch).count()
    print(f"  [OK] 错挂证据已清空、cluster_hint 已置空" if ok else "  [FAIL] 见上")
    print(f"  语料与台账原样：raw_jd {jd_total} 条、crawl_batch {batches} 批（本脚本不删数据）")
    print(f"  无证据的 active 能力项：{orphan} 条；不可回溯证据（raw_jd_id 为空）：{untraceable} 条")
    if orphan:
        ok = False
        print("  [FAIL] 出现无证据的 active 能力项——降级判据没兜住")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写影子库（缺省仅 dry-run）")
    parser.add_argument("--allow-shadow", action="store_true",
                        help="显式批准非 SQLite shadow（当前生产库始终禁止）")
    parser.add_argument("--confirm-database", default=None,
                        help="必须精确填写实际连接的非生产 shadow 库名")
    parser.add_argument("--limit-report", type=int, default=30,
                        help="dry-run 里逐条列出的样本条数（默认 30）")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        as_of = confidence_batch._naive_utc(datetime.now(timezone.utc))
        plan = build_plan(db)
        offtarget = plan["offtarget"]
        by_rule = defaultdict(int)
        for row in offtarget:
            by_rule[row["why"]] += 1

        print(f"=== (JD, 岗位) 对总数 {plan['pairs_total']}；判定为错挂 {len(offtarget)} 对 "
              f"({100.0 * len(offtarget) / max(1, plan['pairs_total']):.1f}%) ===")
        for rule, count in sorted(by_rule.items()):
            print(f"    {rule}: {count} 对")
        print(f"  要删的 evidence 行：{len(plan['doomed_evidence'])}")
        print(f"  要清 cluster_hint 的 raw_jd：{len(plan['hint_clear'])} 条"
              f"（语料与采集台账保留，只是不再参与建图）")
        print(f"  受影响的能力关系：{len(plan['affected_relations'])} 条")
        if not offtarget:
            db.rollback()
            print("\n已是干净状态，无需处理（幂等）。全状态补闸请使用 reconcile_job_skill_state_r6.py。")
            return 0

        per_job = defaultdict(int)
        for row in offtarget:
            per_job[row["job_name"]] += 1
        jd_per_job = defaultdict(int)
        for _jid, jname, *_rest in db.execute(text(PAIR_SQL)).fetchall():
            jd_per_job[jname] += 1
        print(f"\n=== 按岗位（摘 / 现有 → 剩余）===")
        for name, count in sorted(per_job.items(), key=lambda x: -x[1]):
            print(f"   {name:<24} {count:>3} / {jd_per_job[name]:>3} → {jd_per_job[name] - count:>3}")

        print(f"\n=== 被摘掉的前 {args.limit_report} 条（标题 | 公司 | 原挂岗位 | 判据）===")
        for row in offtarget[:args.limit_report]:
            print(f"   raw_jd={row['raw_jd_id']:<6} {row['title'][:34]:<34} | "
                  f"{row['company'][:20]:<20} | {row['job_name']:<20} | {row['why']}")
        if len(offtarget) > args.limit_report:
            print(f"   …… 另 {len(offtarget) - args.limit_report} 条"
                  f"（--limit-report 可调，或看备份 JSON）")

        counts = _employer_counts(db, set(plan["affected_relations"]),
                                  {ev.id for ev, _ in plan["doomed_evidence"]}, as_of)
        would_demote = [(rid, was, now) for rid, (was, now) in counts.items()
                        if plan["affected_relations"][rid].status == "active"
                        and now < MIN_EMPLOYERS]
        surviving = dict(db.query(models.Evidence.job_skill_id, func.count(models.Evidence.id))
                         .filter(models.Evidence.job_skill_id.in_(
                             set(plan["affected_relations"])),
                             models.Evidence.id.notin_(
                                 {ev.id for ev, _ in plan["doomed_evidence"]}))
                         .group_by(models.Evidence.job_skill_id).all())
        emptied = [rid for rid, relation in plan["affected_relations"].items()
                   if relation.status == "active" and surviving.get(rid, 0) == 0]
        print("\n=== 删证据后的全状态一致性补闸（只降不升，不删关系）===")
        print(f"   ① 删除后低于 ≥{MIN_EMPLOYERS} 独立雇主门槛、由 active 降为 candidate："
              f"{len(would_demote)} 条（包含删前已违规的 active，不再 grandfather）")
        for rid, was, now in would_demote[:20]:
            relation = plan["affected_relations"][rid]
            skill = db.query(models.Skill.name).filter(
                models.Skill.id == relation.skill_id).scalar()
            job = db.query(models.Job.name).filter(
                models.Job.id == relation.job_id).scalar()
            print(f"      {job:<22} {skill:<20} 雇主 {was} → {now}  {relation.importance}")
        extra = [r for r in emptied if r not in {x[0] for x in would_demote}]
        print(f"   ② 证据被删光、无据可查、由 active 降为 candidate：{len(emptied)} 条"
              f"（其中 {len(extra)} 条是判据①没覆盖、由这条兜住的）")
        for rid in extra[:10]:
            relation = plan["affected_relations"][rid]
            skill = db.query(models.Skill.name).filter(
                models.Skill.id == relation.skill_id).scalar()
            job = db.query(models.Job.name).filter(
                models.Job.id == relation.job_id).scalar()
            print(f"      {job:<22} {skill:<20} 证据全部来自被摘的 JD")
        print(f"   合计降级 {len({x[0] for x in would_demote} | set(emptied))} 条；"
              f"不删任何 job_skill 行，能力项降为候选后仍可查、可解释")

        print("\n=== 对置信度/契约的影响（复用 confidence_batch 生产算式，"
              "同一 as_of 跑两遍取差，未写库）===")
        before, after = _simulate(db, as_of)
        rows = []
        for job_id, b in before.items():
            a = after[job_id]
            if (round(b["confidence"], 4) != round(a["confidence"], 4)
                    or b["clusters"] != a["clusters"] or b["employers"] != a["employers"]):
                rows.append((b["name"], b["confidence"], a["confidence"], b["employers"],
                             a["employers"], b["clusters"], a["clusters"],
                             b["status"], a["status"]))
        rows.sort(key=lambda r: r[2] - r[1])
        print(f"{'岗位':<24} {'置信度':>28} {'卡片雇主数':>12} {'契约簇':>9}  契约状态")
        for name, bc, ac, be, ae, bcl, acl, bs, as_ in rows:
            flag = f"  ← {bs} → {as_}" if bs != as_ else ""
            print(f"{name:<24} {f'{bc:.4f} → {ac:.4f} ({ac - bc:+.4f})':>28} "
                  f"{f'{be} → {ae}':>12} {f'{bcl} → {acl}':>9}  {as_}{flag}")
        n = len(before)
        avg_b = sum(v["confidence"] for v in before.values()) / n
        avg_a = sum(v["confidence"] for v in after.values()) / n
        print()
        print(f"   受影响岗位 {len(rows)}/{n}；全库 AVG(confidence) "
              f"{avg_b:.4f} → {avg_a:.4f} ({avg_a - avg_b:+.4f})")
        print(f"   契约达标(ready) 岗位数 "
              f"{sum(1 for v in before.values() if v['status'] == 'ready')} → "
              f"{sum(1 for v in after.values() if v['status'] == 'ready')}")

        if not args.apply:
            db.rollback()
            print("\n[dry-run] zero writes；影子发布需 --apply。")
            return 0

        repair_safety.assert_shadow_apply_target(
            db, allow_shadow=args.allow_shadow,
            confirm_database=args.confirm_database)
        # _simulate 结尾 rollback 过，plan 里的 ORM 对象已过期——重建一份再动手。
        plan = build_plan(db)
        run_id = f"r6-offtarget-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        backup = {
            "schema": "r6-offtarget-backup-v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": db.get_bind().url.database, "run_id": run_id,
            "rule": {"判据①错簇": by_rule.get("①错簇", 0),
                     "判据②非研发职能": by_rule.get("②非研发职能", 0)},
            "offtarget_pairs": offtarget,
            "evidence_deleted": [{
                "evidence_id": ev.id, "job_skill_id": ev.job_skill_id,
                "job_id": js.job_id, "raw_jd_id": ev.raw_jd_id,
                "source_type": ev.source_type, "source_name": ev.source_name,
                "source_url": ev.source_url, "snippet": ev.snippet, "weight": ev.weight,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            } for ev, js in plan["doomed_evidence"]],
            "cluster_hint_cleared": [
                {"raw_jd_id": raw.id, "cluster_hint": raw.cluster_hint}
                for raw in db.query(models.RawJD).filter(
                    models.RawJD.id.in_(plan["hint_clear"])).all()],
            "job_skill_before": [{
                "job_skill_id": rid, "job_id": relation.job_id,
                "skill_id": relation.skill_id, "status": relation.status,
                "source_count": relation.source_count, "confidence": relation.confidence,
                "factors": relation.factors,
            } for rid, relation in plan["affected_relations"].items()],
            "job_before": [{"job_id": jid, **{k: v for k, v in b.items()}}
                           for jid, b in before.items()],
            **state_reconcile.backup_projection(db),
        }
        path = repair_safety.backup_path(BACKUP_DIR, "offtarget_r6")
        repair_safety.write_json_exclusive(path, backup)

        try:
            before_changes = db.query(models.CapabilityChange).count()
            before_authority = db.query(models.AuthorityEvidence).count()
            before_versions = db.query(models.JobVersion).count()
            result = mutate(db, plan, as_of, run_id=run_id)
            errors = state_reconcile.verify_all(db, as_of=as_of)
            if not _verify(db):
                errors.append("offtarget verify failed")
            if db.query(models.CapabilityChange).count() != before_changes:
                errors.append("CapabilityChange count changed")
            if db.query(models.AuthorityEvidence).count() != before_authority:
                errors.append("AuthorityEvidence count changed")
            if db.query(models.JobVersion).count() != before_versions:
                errors.append("JobVersion row count changed")
            audit = db.query(models.AuditLog).filter(
                models.AuditLog.action == "graph.repair.offtarget_r6",
                models.AuditLog.target_id == run_id).one_or_none()
            if audit is None:
                errors.append("repair AuditLog missing")
            if errors:
                raise RuntimeError("commit 前验证失败：" + "; ".join(errors[:20]))
            db.commit()
        except Exception:
            db.rollback()
            raise
        print(f"\n已写库：删证据 {len(plan['doomed_evidence'])} 行、"
              f"清 cluster_hint {len(plan['hint_clear'])} 条、"
              f"降级 {len(result['demoted'])} 条能力关系"
              f"（{REASON_BELOW_GATE} "
              f"{sum(1 for r in result['demoted'] if r[3] == REASON_BELOW_GATE)}，"
              f"{REASON_NO_EVIDENCE} "
              f"{sum(1 for r in result['demoted'] if r[3] == REASON_NO_EVIDENCE)}）。")
        print(f"改动前的原值已备份到 {path}")
        print("\n=== _verify() ===")
        return 0 if _verify(db) else 1
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
