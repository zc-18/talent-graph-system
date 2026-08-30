"""RawJD 入库闸门：标题归簇、母公司配额、manifest tier（内存 SQLite）。"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from data import import_raw


def _record(title: str, company: str, url: str, query: str = "") -> dict:
    return {
        "job_title": title, "company": company, "url": url,
        "raw_text": f"{title}\n负责 Python Java SQL Linux 分布式系统研发与性能优化。" * 2,
        "publish_date": "2025-06-01", "crawled_at": "2026-08-30",
        "extra": {"query": query},
    }


def _run(monkeypatch, tmp_path: Path, records: list[dict], *, tier="official", cap=5):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(import_raw, "SessionLocal", Session)
    monkeypatch.setattr(import_raw, "init_db", lambda: None)
    monkeypatch.setattr(import_raw, "__file__", str(tmp_path / "import_raw.py"))
    raw = tmp_path / "raw" / "batch"
    raw.mkdir(parents=True)
    (raw / "manifest.json").write_text(json.dumps({"adapters": {"feishu_ats": {
        "tier": "official", "authority": 1.0, "finished_at": "2026-08-30"}}}), "utf-8")
    (raw / "feishu_ats.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), "utf-8")
    import_raw.import_batch("batch", tier, filter_it=True, filter_title=True,
                            max_per_employer_cluster=cap)
    return Session


def test_full_catalog_rows_receive_cluster_hint_and_ai_product_gate(monkeypatch, tmp_path):
    Session = _run(monkeypatch, tmp_path, [
        _record("智能座舱软件工程师", "甲公司", "https://x/1"),
        _record("大模型产品经理", "甲公司", "https://x/2"),
        _record("普通产品经理", "甲公司", "https://x/3"),
        _record("机器人销售专员", "甲公司", "https://x/4"),
    ])
    with Session() as db:
        rows = db.query(models.RawJD).order_by(models.RawJD.source_url).all()
        assert [(r.job_title, r.cluster_hint) for r in rows] == [
            ("智能座舱软件工程师", "车联网"), ("大模型产品经理", "AI产品")]
        assert all(r.source_authority == 1.0 for r in rows)
        assert db.query(models.CrawlBatch).one().tier == "official"


def test_existing_parent_quota_blocks_sibling_on_next_batch(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(import_raw, "SessionLocal", Session)
    monkeypatch.setattr(import_raw, "init_db", lambda: None)
    monkeypatch.setattr(import_raw, "__file__", str(tmp_path / "import_raw.py"))
    with Session() as db:
        parent = models.Employer(name="甲集团", normalized_name="甲集团")
        db.add(parent); db.flush()
        a = models.Employer(name="甲集团一分公司", normalized_name="甲集团一",
                            parent_id=parent.id)
        b = models.Employer(name="甲集团二分公司", normalized_name="甲集团二",
                            parent_id=parent.id)
        db.add_all([a, b]); db.flush()
        for i in range(4):
            db.add(models.RawJD(job_title="智能座舱软件工程师", company=a.name,
                                source_url=f"https://old/{i}", raw_text="x" * 80,
                                employer_id=a.id, cluster_hint="车联网"))
        db.commit()
        employers_before = db.query(models.Employer).count()
    raw = tmp_path / "raw" / "batch"
    raw.mkdir(parents=True)
    (raw / "manifest.json").write_text(json.dumps({"adapters": {"feishu_ats": {
        "tier": "official", "authority": 1.0}}}), "utf-8")
    (raw / "feishu_ats.jsonl").write_text("".join(
        json.dumps(_record("车联网平台工程师", "甲集团二分公司", f"https://new/{i}"),
                   ensure_ascii=False) + "\n" for i in range(3)), "utf-8")
    import_raw.import_batch("batch", "official", filter_it=True, filter_title=True,
                            max_per_employer_cluster=5)
    with Session() as db:
        assert db.query(models.RawJD).filter(models.RawJD.source_url.like("https://new/%")).count() == 1
        # 配额在建 Employer 之前判：被配额拒掉的两条不能留下没有任何 RawJD 的雇主实体。
        # 雇主实体是 ≥2 独立雇主闸门的计数单位，凭空多一个就等于把闸门放宽一格。
        assert db.query(models.Employer).count() == employers_before


def test_blank_company_jds_still_consume_one_shared_quota(monkeypatch, tmp_path):
    """公司名为空的 JD 不能豁免配额。

    匿名语料证明不了来源独立（`dataset_aijob2024` 的 48 条违规 active 就是这么来的），
    一旦豁免，一份匿名语料就能无上限地撑起某个簇的支持率分母。全部空名共享一份配额。
    """
    Session = _run(monkeypatch, tmp_path, [
        _record("智能座舱软件工程师", "", f"https://anon/{i}") for i in range(8)
    ], cap=5)
    with Session() as db:
        rows = db.query(models.RawJD).all()
        assert len(rows) == 5
        assert all(r.employer_id is None for r in rows)

