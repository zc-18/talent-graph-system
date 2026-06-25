"""为技能图谱注入「先修/相关/驱动」关系边。

用途：① 学习路径拓扑排序（prerequisite）② 全景图谱技能间关联展示
③ 技术趋势驱动（drives：技术A爆发 → 新技能需求）。
幂等：已存在的关系不会重复插入。
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402

# (from, to, type, weight)：from 是 to 的先修/相关/驱动
PREREQUISITE = [
    ("Python", "机器学习"), ("Python", "数据挖掘"), ("机器学习", "深度学习"),
    ("深度学习", "计算机视觉"), ("深度学习", "自然语言处理"), ("深度学习", "强化学习"),
    ("Transformer", "大语言模型"), ("自然语言处理", "大语言模型"),
    ("大语言模型", "检索增强生成"), ("大语言模型", "模型微调"), ("大语言模型", "提示工程"),
    ("大语言模型", "智能体"), ("检索增强生成", "向量数据库"),
    ("深度学习", "模型部署"), ("模型部署", "推理加速"),
    ("Java", "Spring"), ("Java", "微服务"), ("SQL", "数据仓库"), ("SQL", "数据建模"),
    ("Hadoop", "Hive"), ("Hadoop", "Spark"), ("Spark", "实时计算"), ("Kafka", "实时计算"),
    ("Flink", "实时计算"), ("Docker", "Kubernetes"), ("分布式系统", "微服务"),
    ("分布式系统", "高并发"), ("分布式系统", "消息队列"),
    ("嵌入式开发", "实时操作系统"), ("物联网", "MQTT"), ("物联网", "边缘计算"),
    ("C++", "嵌入式开发"), ("数据仓库", "ETL"), ("机器学习", "推荐系统"),
    # 智能系统 / 物联网 域先修关系
    ("控制系统", "机器人技术"), ("机器人技术", "SLAM"), ("SLAM", "自动驾驶"),
    ("ROS", "机器人技术"), ("传感器技术", "物联网"), ("物联网", "数字孪生"),
    ("嵌入式开发", "边缘计算"), ("传感器技术", "PLC"),
]
# 技术驱动关系：技术A爆发 → 带动B技能需求
DRIVES = [
    ("大语言模型", "提示工程"), ("大语言模型", "检索增强生成"), ("大语言模型", "向量数据库"),
    ("大语言模型", "智能体"), ("AIGC", "扩散模型"), ("边缘计算", "推理加速"),
]
RELATED = [
    ("PyTorch", "TensorFlow"), ("Spark", "Flink"), ("机器学习", "数据挖掘"),
    ("计算机视觉", "多模态"), ("自然语言处理", "多模态"), ("MySQL", "Redis"),
]


def _skill(db, name):
    return db.query(models.Skill).filter(models.Skill.normalized_name == name).first()


def _add(db, frm, to, rtype, weight):
    a, b = _skill(db, frm), _skill(db, to)
    if not a or not b:
        return False
    exists = db.query(models.SkillRelation).filter(
        models.SkillRelation.from_skill_id == a.id,
        models.SkillRelation.to_skill_id == b.id,
        models.SkillRelation.relation_type == rtype).first()
    if exists:
        return False
    db.add(models.SkillRelation(from_skill_id=a.id, to_skill_id=b.id,
                                relation_type=rtype, weight=weight))
    return True


def main():
    init_db()
    db = SessionLocal()
    n = 0
    try:
        for frm, to in PREREQUISITE:
            n += _add(db, frm, to, "prerequisite", 0.8)
        for frm, to in DRIVES:
            n += _add(db, frm, to, "drives", 0.7)
        for frm, to in RELATED:
            n += _add(db, frm, to, "related", 0.5)
            n += _add(db, to, frm, "related", 0.5)
        db.commit()
    finally:
        db.close()
    print(f"注入技能关系: {n} 条")


if __name__ == "__main__":
    main()
