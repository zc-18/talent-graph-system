from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.services.graph_service import panoramic_graph


def test_panorama_capability_mode_emits_cluster_nodes_not_mixed_skills():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        job = models.Job(name="Java开发工程师", slug="java", status="published",
                         category="云计算与工程", level="middle")
        java = models.Skill(name="Java", normalized_name="Java", category="编程语言")
        spring = models.Skill(name="Spring", normalized_name="Spring", category="云计算与工程")
        mysql = models.Skill(name="MySQL", normalized_name="MySQL", category="数据库与存储")
        db.add_all([job, java, spring, mysql])
        db.flush()
        db.add_all([
            models.JobSkill(job_id=job.id, skill_id=java.id, status="active",
                            importance="required", weight=0.9, confidence=0.9),
            models.JobSkill(job_id=job.id, skill_id=spring.id, status="active",
                            importance="required", weight=0.8, confidence=0.85),
            models.JobSkill(job_id=job.id, skill_id=mysql.id, status="active",
                            importance="bonus", weight=0.5, confidence=0.8),
        ])
        db.commit()
        graph = panoramic_graph(db, granularity="cluster")
        clusters = {n["name"] for n in graph["nodes"] if n["type"] == "cluster"}
        assert clusters == {"编程与工程基础", "后端框架与服务", "数据与存储"}
        assert all(edge["target"].startswith("cluster-") for edge in graph["edges"])
        assert graph["stats"]["clusters"] == 3
    finally:
        db.close()
