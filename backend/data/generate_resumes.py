"""生成简历测试集（含 ground-truth 技能），用于简历提取准确率与匹配准确率评测。

刻意使用同义词/缩写（torch、k8s、NLP、ML…）考验抽取+归一化的鲁棒性。
"""
from __future__ import annotations
import json
import os
import random

random.seed(7)
HERE = os.path.dirname(os.path.abspath(__file__))

# 技能 -> 简历中可能的表述（含同义词），归一化后应回到 canonical 名
SKILL_PHRASING = {
    "Python": ["Python", "python", "Python3"],
    "Java": ["Java", "java", "JavaEE"],
    "Go": ["Go", "Golang"],
    "C++": ["C++", "c++"],
    "机器学习": ["机器学习", "ML", "machine learning"],
    "深度学习": ["深度学习", "DL", "deep learning"],
    "PyTorch": ["PyTorch", "torch", "pytorch"],
    "TensorFlow": ["TensorFlow", "tensorflow", "TF"],
    "自然语言处理": ["自然语言处理", "NLP"],
    "计算机视觉": ["计算机视觉", "CV", "图像识别"],
    "大语言模型": ["大语言模型", "LLM", "大模型"],
    "检索增强生成": ["检索增强生成", "RAG"],
    "模型微调": ["模型微调", "fine-tuning", "LoRA微调"],
    "提示工程": ["提示工程", "prompt engineering", "提示词工程"],
    "Transformer": ["Transformer", "注意力机制"],
    "HuggingFace": ["HuggingFace", "hugging face"],
    "数据挖掘": ["数据挖掘", "data mining"],
    "推荐系统": ["推荐系统", "recommendation"],
    "强化学习": ["强化学习", "RL"],
    "模型部署": ["模型部署", "model serving"],
    "Spark": ["Spark", "PySpark"],
    "Hadoop": ["Hadoop"],
    "Hive": ["Hive"],
    "Kafka": ["Kafka"],
    "Flink": ["Flink"],
    "数据仓库": ["数据仓库", "数仓"],
    "ETL": ["ETL"],
    "实时计算": ["实时计算", "流计算"],
    "ClickHouse": ["ClickHouse"],
    "SQL": ["SQL", "sql"],
    "MySQL": ["MySQL", "mysql"],
    "Redis": ["Redis"],
    "Spring": ["Spring", "SpringBoot", "Spring Boot"],
    "微服务": ["微服务", "microservice"],
    "分布式系统": ["分布式系统", "分布式"],
    "消息队列": ["消息队列", "消息中间件"],
    "高并发": ["高并发"],
    "Kubernetes": ["Kubernetes", "k8s"],
    "Docker": ["Docker", "docker"],
    "Linux": ["Linux"],
    "物联网": ["物联网", "IoT"],
    "嵌入式开发": ["嵌入式开发", "嵌入式", "单片机"],
    "MQTT": ["MQTT"],
    "传感器技术": ["传感器", "传感器技术"],
    "边缘计算": ["边缘计算", "edge computing"],
    "5G通信": ["5G"],
    "实时操作系统": ["RTOS", "FreeRTOS"],
    "数据建模": ["数据建模"],
    "数据治理": ["数据治理"],
    "推理加速": ["TensorRT", "ONNX", "推理加速"],
    "多模态": ["多模态", "multimodal"],
}

# 每份简历：目标岗位 + 拥有的技能(canonical) + 经验年限
RESUME_SPECS = [
    ("机器学习工程师", ["Python", "机器学习", "深度学习", "PyTorch", "数据挖掘", "推荐系统"], 3),
    ("机器学习工程师", ["Python", "机器学习", "深度学习"], 2),               # 部分匹配
    ("自然语言处理工程师", ["Python", "自然语言处理", "深度学习", "PyTorch", "大语言模型", "检索增强生成", "模型微调"], 4),
    ("自然语言处理工程师", ["Python", "深度学习", "Transformer", "大语言模型"], 2),
    ("大数据开发工程师", ["Spark", "Hadoop", "Hive", "SQL", "数据仓库", "Kafka", "Flink"], 5),
    ("大数据开发工程师", ["SQL", "Hive", "数据仓库"], 1),                    # 较大差距
    ("Java开发工程师", ["Java", "Spring", "MySQL", "Redis", "分布式系统", "消息队列", "Kubernetes"], 4),
    ("Java开发工程师", ["Java", "MySQL", "Spring"], 2),
    ("算法工程师", ["Python", "机器学习", "深度学习", "数据挖掘", "强化学习", "计算机视觉"], 5),
    ("计算机视觉工程师", ["Python", "计算机视觉", "深度学习", "PyTorch", "模型部署", "推理加速"], 3),
    ("物联网开发工程师", ["物联网", "嵌入式开发", "C++", "MQTT", "传感器技术", "边缘计算"], 3),
    ("物联网开发工程师", ["C++", "Linux", "嵌入式开发"], 2),
    ("数据分析师", ["SQL", "Python", "数据挖掘", "数据建模", "机器学习"], 2),
    ("后端开发工程师", ["Java", "MySQL", "Redis", "微服务", "Go", "消息队列", "Docker"], 4),
    ("后端开发工程师", ["Python", "MySQL", "Redis"], 1),
    ("Python开发工程师", ["Python", "SQL", "MySQL", "Linux", "Docker", "数据挖掘"], 3),
    ("算法工程师", ["Python", "机器学习", "深度学习", "自然语言处理", "大语言模型", "模型部署", "推理加速"], 6),
    ("计算机视觉工程师", ["计算机视觉", "深度学习", "Python", "PyTorch", "TensorFlow", "多模态"], 4),
]

EDU = ["计算机科学与技术 硕士", "软件工程 本科", "人工智能 硕士", "数据科学 本科", "电子信息 硕士"]


def make_resume(rid, target, skills, years):
    phr = []
    for s in skills:
        phr.append(random.choice(SKILL_PHRASING.get(s, [s])))
    random.shuffle(phr)
    name = f"候选人{rid:02d}"
    skill_lines = "\n".join(f"· {p}" for p in phr)
    text = f"""姓名：{name}
求职意向：{target}
工作年限：{years}年
学历：{random.choice(EDU)}

【专业技能】
{skill_lines}

【项目经历】
1. 在实际项目中综合运用上述技术栈完成研发与上线，独立负责核心模块；
2. 参与团队协作，具备良好的工程实践与问题解决能力。

【工作经历】
某科技公司  {target}  {years}年
"""
    return {"id": rid, "target_job": target, "candidate_name": name,
            "years_experience": years, "ground_truth_skills": skills, "raw_text": text}


def generate():
    resumes = [make_resume(i + 1, t, sk, y) for i, (t, sk, y) in enumerate(RESUME_SPECS)]
    with open(os.path.join(HERE, "test_resumes.json"), "w", encoding="utf-8") as f:
        json.dump(resumes, f, ensure_ascii=False, indent=2)
    print(f"生成测试简历: {len(resumes)} 份")


if __name__ == "__main__":
    generate()
