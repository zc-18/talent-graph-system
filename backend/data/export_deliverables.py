"""导出提交材料：测试数据（1新岗位+1既有岗位的图谱与数据源 I/O 示例）+ 综合测试报告。

输出到 docs/测试数据/。
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.services import graph_service  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "docs", "测试数据"))
os.makedirs(OUT, exist_ok=True)


def dump(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("写出:", name)


def export_existing_job(db, job_name="Java开发工程师"):
    job = db.query(models.Job).filter(models.Job.name == job_name).first()
    if not job:
        print("未找到既有岗位", job_name); return
    detail = graph_service.job_to_dict(db, job)
    # 输入数据源示例：该岗位相关的若干原始 JD
    raws = db.query(models.RawJD).filter(models.RawJD.job_title == job_name).limit(3).all()
    inputs = [{"company": r.company, "source": r.source, "publish_date": str(r.publish_date),
               "is_duplicate": r.is_duplicate, "inflation_flag": r.inflation_flag,
               "lag_days": r.lag_days, "quality_score": r.quality_score,
               "raw_text": r.raw_text} for r in raws]
    # 证据溯源
    ev = db.query(models.JobSkill).filter(models.JobSkill.job_id == job.id).all()
    evidence_sample = []
    for j in ev[:6]:
        sk = db.query(models.Skill).get(j.skill_id)
        evs = db.query(models.Evidence).filter(models.Evidence.job_skill_id == j.id).limit(3).all()
        evidence_sample.append({"skill": sk.name if sk else "", "importance": j.importance,
                                "confidence": j.confidence, "source_count": j.source_count,
                                "evidences": [{"type": e.source_type, "snippet": e.snippet} for e in evs]})
    # 能力演化示例（核心功能②：明确标注新增/删除/修改 + 更新说明 + 数据源）
    chgs = db.query(models.CapabilityChange).filter(
        models.CapabilityChange.job_id == job.id).order_by(
        models.CapabilityChange.created_at.desc()).all()
    type_cn = {"add": "新增", "delete": "删除", "modify": "修改"}
    evo_items = [{
        "变更类型": type_cn.get(c.change_type, c.change_type), "能力项": c.skill_name,
        "重要度": c.importance, "变更前": c.old_value, "变更后": c.new_value,
        "更新说明": c.reason, "数据源": c.data_source, "置信度": c.confidence,
    } for c in chgs]
    evolution_example = {
        "说明": "针对既有岗位，系统用最新招聘 JD 窗口 + 多源联网证据交叉验证，识别能力要求变化并标注增/删/改，附更新说明与数据源。",
        "当前版本": job.version,
        "变更统计": {
            "新增": sum(1 for c in chgs if c.change_type == "add"),
            "删除": sum(1 for c in chgs if c.change_type == "delete"),
            "修改": sum(1 for c in chgs if c.change_type == "modify"),
        },
        "变更明细": evo_items,
    } if evo_items else {"说明": "暂无演化记录；可在『岗位能力演化』页用最新 JD 驱动该岗位更新。"}

    out = {
        "岗位类型": "既有岗位",
        "岗位名称": job_name,
        "输入_数据源示例": inputs,
        "输出_能力图谱": {
            "岗位名称": detail["name"], "技术栈": detail["category"], "级别": detail["level"],
            "版本": detail.get("version"),
            "岗位简介": detail["summary"], "核心职责": detail["core_responsibilities"],
            "典型应用场景": detail["typical_scenarios"],
            "必备技能": [{"技能": s["name"], "权重": s["weight"], "掌握级别": s["level_required"],
                       "置信度": s["confidence"], "独立来源数": s["source_count"]}
                      for s in detail["required_skills"]],
            "加分技能": [{"技能": s["name"], "置信度": s["confidence"]} for s in detail["bonus_skills"]],
            "岗位定义置信度": detail["confidence"], "证据总数": detail["evidence_count"],
        },
        "能力演化示例_v1到v2": evolution_example,
        "反幻觉_能力项溯源示例": evidence_sample,
    }
    dump("既有岗位_Java开发工程师_图谱与数据源.json", out)


def export_new_job(db):
    job = db.query(models.Job).filter(models.Job.is_new == True).order_by(models.Job.id.desc()).first()  # noqa: E712
    if not job:
        print("未找到新岗位"); return
    detail = graph_service.job_to_dict(db, job)
    out = {
        "岗位类型": "新发现岗位",
        "输入_关键词": job.name,
        "输入_数据源": "多源联网检索（Tavily + Serper.dev）+ 大模型 RAG 接地生成",
        "数据源摘要": detail.get("source_summary", {}),
        "输出_岗位定义": {
            "岗位名称": detail["name"], "技术栈": detail["category"], "级别": detail["level"],
            "新兴度": detail["emergence_score"], "岗位简介": detail["summary"],
            "核心职责": detail["core_responsibilities"], "典型应用场景": detail["typical_scenarios"],
            "必备技能": [{"技能": s["name"], "权重": s["weight"], "置信度": s["confidence"]}
                      for s in detail["required_skills"]],
            "加分技能": [{"技能": s["name"]} for s in detail["bonus_skills"]],
            "岗位定义置信度": detail["confidence"],
        },
    }
    dump(f"新岗位_{job.name}_图谱与数据源.json", out)


def export_report(db):
    def load(n):
        p = os.path.join(HERE, n)
        return json.load(open(p, encoding="utf-8"))["summary"] if os.path.exists(p) else {}
    stats = graph_service.stats_overview(db)
    report = {
        "测试时间": datetime.now().strftime("%Y-%m-%d"),
        "数据集规模": {"岗位JD总数": stats["total_jds"], "岗位簇数": stats["total_jobs"],
                   "技能点数": stats["total_skills"], "抄袭重复检出": stats["duplicate_jds"]},
        "核心指标": {
            "JD解析准确率(F1)": load("eval_jd_result.json").get("f1"),
            "JD解析精确率": load("eval_jd_result.json").get("precision"),
            "JD解析召回率": load("eval_jd_result.json").get("recall"),
            "简历提取准确率(F1)": load("eval_resume_result.json").get("f1"),
            "简历提取召回率": load("eval_resume_result.json").get("recall"),
            "人岗匹配分类准确率": load("eval_match_result.json").get("classification_acc"),
            "人岗匹配F1": load("eval_match_result.json").get("match_f1"),
        },
        "达标判定": {
            "JD解析≥90%": (load("eval_jd_result.json").get("f1") or 0) >= 0.9,
            "简历提取≥90%": (load("eval_resume_result.json").get("f1") or 0) >= 0.9,
            "人岗匹配≥90%": (load("eval_match_result.json").get("classification_acc") or 0) >= 0.9,
        },
    }
    dump("综合测试报告.json", report)
    print(json.dumps(report["核心指标"], ensure_ascii=False, indent=2))


def main():
    db = SessionLocal()
    try:
        export_existing_job(db)
        export_new_job(db)
        export_report(db)
    finally:
        db.close()
    print("\n导出目录:", OUT)


if __name__ == "__main__":
    main()
