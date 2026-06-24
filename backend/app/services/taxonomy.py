"""技能/岗位分类体系与同义词归一化字典。

用于：①技能点归一化（合并同义词，降噪）②全景图谱按技术栈分类。
覆盖新一代信息技术：人工智能、大数据、智能系统、物联网、云计算/工程支撑。
"""
from __future__ import annotations
import re

# 技术栈分类
CATEGORIES = ["人工智能", "大数据", "智能系统", "物联网", "云计算与工程", "数据工程"]

# 同义词 -> 规范名
SYNONYMS: dict[str, str] = {
    # AI / ML
    "ml": "机器学习", "machine learning": "机器学习", "机器学习算法": "机器学习",
    "dl": "深度学习", "deep learning": "深度学习",
    "nlp": "自然语言处理", "natural language processing": "自然语言处理",
    "cv": "计算机视觉", "computer vision": "计算机视觉", "图像识别": "计算机视觉",
    "llm": "大语言模型", "large language model": "大语言模型", "大模型": "大语言模型",
    "gpt": "大语言模型", "transformer": "Transformer", "注意力机制": "Transformer",
    "rag": "检索增强生成", "retrieval augmented generation": "检索增强生成",
    "prompt": "提示工程", "prompt engineering": "提示工程", "提示词工程": "提示工程",
    "fine-tuning": "模型微调", "finetune": "模型微调", "微调": "模型微调", "sft": "模型微调",
    "lora": "模型微调", "rlhf": "强化学习对齐", "agent": "智能体",
    "ai agent": "智能体", "智能代理": "智能体", "multi-agent": "多智能体",
    "langchain": "LangChain", "llamaindex": "LlamaIndex", "向量数据库": "向量数据库",
    "vector database": "向量数据库", "milvus": "向量数据库", "faiss": "向量数据库",
    "pytorch": "PyTorch", "torch": "PyTorch", "tensorflow": "TensorFlow", "tf": "TensorFlow",
    "keras": "TensorFlow", "scikit-learn": "scikit-learn", "sklearn": "scikit-learn",
    "huggingface": "HuggingFace", "hugging face": "HuggingFace", "transformers库": "HuggingFace",
    "强化学习": "强化学习", "reinforcement learning": "强化学习", "rl": "强化学习",
    "知识图谱": "知识图谱", "knowledge graph": "知识图谱", "neo4j": "Neo4j",
    "推荐系统": "推荐系统", "recommendation": "推荐系统", "多模态": "多模态",
    "特征工程": "特征工程", "feature engineering": "特征工程", "特征处理": "特征工程",
    "multimodal": "多模态", "扩散模型": "扩散模型", "diffusion": "扩散模型", "aigc": "AIGC",
    "模型部署": "模型部署", "model serving": "模型部署", "triton": "模型部署",
    "vllm": "推理加速", "tensorrt": "推理加速", "onnx": "推理加速", "量化": "模型量化",
    # 大数据
    "hadoop": "Hadoop", "spark": "Spark", "pyspark": "Spark", "flink": "Flink",
    "hive": "Hive", "hbase": "HBase", "kafka": "Kafka", "数据仓库": "数据仓库",
    "data warehouse": "数据仓库", "数仓": "数据仓库", "etl": "ETL", "数据湖": "数据湖",
    "data lake": "数据湖", "doris": "Doris", "clickhouse": "ClickHouse",
    "数据挖掘": "数据挖掘", "data mining": "数据挖掘", "实时计算": "实时计算",
    "流计算": "实时计算", "stream processing": "实时计算", "数据治理": "数据治理",
    "presto": "Presto", "trino": "Presto", "数据建模": "数据建模",
    # 物联网 / 智能系统
    "iot": "物联网", "internet of things": "物联网", "mqtt": "MQTT", "coap": "CoAP",
    "嵌入式": "嵌入式开发", "embedded": "嵌入式开发", "单片机": "嵌入式开发",
    "stm32": "嵌入式开发", "rtos": "实时操作系统", "freertos": "实时操作系统",
    "边缘计算": "边缘计算", "edge computing": "边缘计算", "传感器": "传感器技术",
    "5g": "5G通信", "lora通信": "LoRa通信", "zigbee": "Zigbee",
    "机器人": "机器人技术", "robotics": "机器人技术", "ros": "ROS",
    "自动驾驶": "自动驾驶", "autonomous driving": "自动驾驶", "slam": "SLAM",
    "具身智能": "具身智能", "embodied ai": "具身智能", "数字孪生": "数字孪生",
    "digital twin": "数字孪生", "控制系统": "控制系统", "plc": "PLC",
    # 云计算 / 工程
    "docker": "Docker", "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "微服务": "微服务", "microservice": "微服务", "ci/cd": "CI/CD", "devops": "DevOps",
    "linux": "Linux", "git": "Git", "云原生": "云原生", "cloud native": "云原生",
    "aws": "云平台", "阿里云": "云平台", "腾讯云": "云平台", "华为云": "云平台",
    "java": "Java", "python": "Python", "go": "Go", "golang": "Go", "c++": "C++",
    "javascript": "JavaScript", "js": "JavaScript", "scala": "Scala", "rust": "Rust",
    "sql": "SQL", "mysql": "MySQL", "redis": "Redis", "mongodb": "MongoDB",
    "spring": "Spring", "springboot": "Spring", "spring boot": "Spring",
    "分布式": "分布式系统", "distributed system": "分布式系统", "高并发": "高并发",
    "message queue": "消息队列", "消息中间件": "消息队列",
    # 软技能
    "沟通": "沟通能力", "团队协作": "团队协作", "团队合作": "团队协作",
    "项目管理": "项目管理", "学习能力": "学习能力", "问题解决": "问题解决能力",
}

# 规范技能 -> 技术栈
SKILL_CATEGORY: dict[str, str] = {}
_AI = ["机器学习", "深度学习", "自然语言处理", "计算机视觉", "大语言模型", "Transformer",
       "检索增强生成", "提示工程", "模型微调", "强化学习对齐", "智能体", "多智能体",
       "LangChain", "LlamaIndex", "向量数据库", "PyTorch", "TensorFlow", "scikit-learn",
       "HuggingFace", "强化学习", "推荐系统", "多模态", "扩散模型", "AIGC", "模型部署",
       "推理加速", "模型量化", "知识图谱", "特征工程"]
_BD = ["Hadoop", "Spark", "Flink", "Hive", "HBase", "Kafka", "数据仓库", "ETL", "数据湖",
       "Doris", "ClickHouse", "数据挖掘", "实时计算", "数据治理", "Presto", "数据建模"]
_IOT = ["物联网", "MQTT", "CoAP", "嵌入式开发", "实时操作系统", "边缘计算", "传感器技术",
        "5G通信", "LoRa通信", "Zigbee"]
_SYS = ["机器人技术", "ROS", "自动驾驶", "SLAM", "具身智能", "数字孪生", "控制系统", "PLC", "Neo4j"]
_CLOUD = ["Docker", "Kubernetes", "微服务", "CI/CD", "DevOps", "Linux", "Git", "云原生",
          "云平台", "Java", "Python", "Go", "C++", "JavaScript", "Scala", "Rust", "SQL",
          "MySQL", "Redis", "MongoDB", "Spring", "分布式系统", "高并发", "消息队列"]
for s in _AI:
    SKILL_CATEGORY[s] = "人工智能"
for s in _BD:
    SKILL_CATEGORY[s] = "大数据"
for s in _IOT:
    SKILL_CATEGORY[s] = "物联网"
for s in _SYS:
    SKILL_CATEGORY[s] = "智能系统"
for s in _CLOUD:
    SKILL_CATEGORY[s] = "云计算与工程"

# 技能类型
TOOL_SKILLS = {"PyTorch", "TensorFlow", "scikit-learn", "HuggingFace", "LangChain",
               "LlamaIndex", "Docker", "Kubernetes", "Git", "Neo4j", "Spark", "Flink",
               "Kafka", "Hadoop", "Hive", "HBase", "MySQL", "Redis", "MongoDB", "Spring",
               "ClickHouse", "Doris", "Presto", "ROS"}
SOFT_SKILLS = {"沟通能力", "团队协作", "项目管理", "学习能力", "问题解决能力"}


def normalize_skill(name: str) -> str:
    """归一化技能名称。"""
    if not name:
        return ""
    raw = name.strip()
    low = raw.lower().strip()
    low = re.sub(r"[（(].*?[)）]", "", low).strip()
    if low in SYNONYMS:
        return SYNONYMS[low]
    if raw in SKILL_CATEGORY or raw in SOFT_SKILLS:
        return raw
    # 去掉常见后缀再匹配（同义词字典 + 规范集合）
    canon = set(SKILL_CATEGORY) | set(SOFT_SKILLS)
    canon_lower = {c.lower(): c for c in canon}
    for suf in ["架构", "技术", "开发", "应用", "相关经验", "经验", "能力", "框架", "原理", "基础", "系统"]:
        if low.endswith(suf):
            stem = low[: -len(suf)]
            if stem in SYNONYMS:
                return SYNONYMS[stem]
            if stem in canon_lower:
                return canon_lower[stem]
    if low in canon_lower:
        return canon_lower[low]
    return raw


def skill_category(name: str) -> str:
    return SKILL_CATEGORY.get(name, "其他")

def skill_type(name: str) -> str:
    if name in TOOL_SKILLS:
        return "tool"
    if name in SOFT_SKILLS:
        return "soft"
    return "hard"


def clean_skill_name(name: str) -> str:
    """清洗大模型生成的冗长技能名为简洁技能点（用于新岗位发现）。

    例：'LangChain/LlamaIndex等LLM开发框架' -> 'LangChain'
        '核心提示词技术（Few-shot, Chain-of-Thought, ReAct）' -> '提示工程'
    """
    if not name:
        return ""
    n = re.sub(r"[（(].*?[)）]", "", name).strip()          # 去括号说明
    n = re.split(r"[/、，,；;:：]", n)[0].strip()             # 取首个分项
    n = re.sub(r"^(熟悉|掌握|了解|精通|具备|有)", "", n).strip()
    for suf in ["等LLM开发框架", "开发框架", "等相关技术", "相关技术", "技术栈",
                "相关经验", "经验", "背景", "基础", "能力", "框架", "技术", "等"]:
        if n.endswith(suf) and len(n) > len(suf) + 1:
            n = n[: -len(suf)].strip()
    n = normalize_skill(n) if n else name.strip()
    if len(n) > 16:                                          # 仍过长则截断兜底
        n = n[:16]
    return n

