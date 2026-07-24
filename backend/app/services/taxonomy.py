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


# ==================== 细粒度技能层（2026-07 整改：老师意见⑤） ====================
# 设计：两级共存。粗粒度规范名（上面的 SYNONYMS/SKILL_CATEGORY）继续驱动
# required/bonus 判定、人岗匹配与评测；细粒度技能点作为子节点挂在粗粒度父技能下
# （Skill.parent_id），从真实 JD 原文抽取，带独立置信度与证据。

# 细粒度别名规整（大小写/中英写法 -> 规范细粒度名）
FINE_SYNONYMS: dict[str, str] = {
    "vllm": "vLLM推理部署", "vllm部署": "vLLM推理部署", "sglang": "SGLang推理部署",
    "tensorrt": "TensorRT加速", "tensorrt-llm": "TensorRT加速", "onnx": "ONNX模型转换",
    "triton": "Triton推理服务", "triton inference server": "Triton推理服务",
    "lora": "LoRA微调", "lora微调": "LoRA微调", "qlora": "LoRA微调",
    "sft": "SFT指令微调", "指令微调": "SFT指令微调", "全参微调": "全参数微调",
    "rlhf": "RLHF对齐训练", "dpo": "DPO偏好优化", "grpo": "GRPO强化训练",
    "模型蒸馏": "模型蒸馏", "知识蒸馏": "模型蒸馏", "模型量化": "模型量化(INT8/INT4)",
    "int8量化": "模型量化(INT8/INT4)", "awq": "模型量化(INT8/INT4)", "gptq": "模型量化(INT8/INT4)",
    "deepspeed": "DeepSpeed分布式训练", "megatron": "Megatron分布式训练",
    "fsdp": "FSDP分布式训练", "ddp": "PyTorch分布式训练(DDP)",
    "分布式训练": "PyTorch分布式训练(DDP)", "cuda": "CUDA编程优化", "cuda编程": "CUDA编程优化",
    "cutlass": "CUDA编程优化", "flash attention": "FlashAttention优化",
    "kv cache": "KV Cache优化", "kv缓存": "KV Cache优化",
    "milvus": "Milvus向量库", "faiss": "FAISS向量检索", "elasticsearch": "Elasticsearch检索",
    "es": "Elasticsearch检索", "chroma": "Chroma向量库", "qdrant": "Qdrant向量库",
    "langchain": "LangChain应用开发", "llamaindex": "LlamaIndex应用开发",
    "langgraph": "LangGraph编排", "autogen": "AutoGen多智能体", "dify": "Dify应用编排",
    "mcp": "MCP协议接入", "function calling": "Function Calling工具调用",
    "工具调用": "Function Calling工具调用", "a2a": "A2A智能体协议",
    "few-shot": "Few-shot提示设计", "cot": "CoT思维链设计", "思维链": "CoT思维链设计",
    "react": "ReAct智能体模式", "prompt调优": "Prompt调优与评估",
    "文本嵌入": "Embedding向量化", "embedding": "Embedding向量化", "bge": "Embedding向量化",
    "重排序": "Rerank重排序", "rerank": "Rerank重排序", "混合检索": "混合检索(稠密+稀疏)",
    "多路召回": "多路召回策略", "意图识别": "意图识别", "实体识别": "命名实体识别(NER)",
    "ner": "命名实体识别(NER)", "文本分类": "文本分类", "情感分析": "情感分析",
    "ocr": "OCR文字识别", "语音识别": "语音识别(ASR)", "asr": "语音识别(ASR)",
    "tts": "语音合成(TTS)", "语音合成": "语音合成(TTS)", "数字人驱动": "数字人驱动技术",
    "口型同步": "口型驱动同步", "动作捕捉": "动作捕捉", "yolo": "YOLO目标检测",
    "目标检测": "YOLO目标检测", "图像分割": "图像分割", "stable diffusion": "扩散模型生图",
    "sd": "扩散模型生图", "comfyui": "ComfyUI工作流", "controlnet": "ControlNet控制生成",
    "视频生成": "视频生成模型", "3d重建": "3D重建", "nerf": "NeRF神经渲染",
    "gaussian splatting": "3D高斯泼溅", "vla": "VLA视觉语言动作模型",
    "模仿学习": "模仿学习", "运动控制": "机器人运动控制", "运动规划": "运动规划",
    "路径规划": "路径规划", "轨迹优化": "轨迹优化", "isaac": "Isaac仿真",
    "mujoco": "MuJoCo仿真", "gazebo": "Gazebo仿真", "ros2": "ROS2开发",
    "点云处理": "点云处理", "激光雷达": "激光雷达感知", "多传感器融合": "多传感器融合",
    "bev": "BEV感知", "占用网络": "Occupancy网络",
    "spark sql": "Spark SQL调优", "spark streaming": "Spark Streaming",
    "flink sql": "Flink SQL", "flink cdc": "Flink CDC", "实时数仓": "实时数仓建设",
    "离线数仓": "离线数仓建设", "维度建模": "维度建模", "指标体系": "指标体系建设",
    "airflow": "Airflow调度", "dolphinscheduler": "DolphinScheduler调度",
    "数据血缘": "数据血缘治理", "数据质量": "数据质量监控", "ab测试": "A/B实验设计",
    "a/b测试": "A/B实验设计", "用户画像": "用户画像建模", "归因分析": "归因分析",
    "helm": "Helm部署", "istio": "Istio服务网格", "服务网格": "Istio服务网格",
    "prometheus": "Prometheus监控", "grafana": "Grafana可视化",
    "terraform": "Terraform基础设施", "jenkins": "Jenkins流水线", "gitlab ci": "GitLab CI",
    "argocd": "ArgoCD持续交付", "jvm调优": "JVM调优", "gc调优": "JVM调优",
    "mysql调优": "MySQL索引与调优", "分库分表": "分库分表", "sharding": "分库分表",
    "rocketmq": "RocketMQ", "rabbitmq": "RabbitMQ", "netty": "Netty网络编程",
    "grpc": "gRPC服务", "graphql": "GraphQL接口", "modbus": "Modbus协议",
    "opc ua": "OPC UA协议", "nb-iot": "NB-IoT接入", "lorawan": "LoRaWAN组网",
    "freertos": "FreeRTOS开发", "rt-thread": "RT-Thread开发", "linux驱动": "Linux驱动开发",
    "设备树": "Linux驱动开发", "can总线": "CAN总线通信", "autosar": "AUTOSAR架构",
}

# 细粒度技能 -> 粗粒度父技能（粗粒度必须是 SKILL_CATEGORY 中的规范名）
FINE_PARENT: dict[str, str] = {
    "vLLM推理部署": "推理加速", "SGLang推理部署": "推理加速", "TensorRT加速": "推理加速",
    "ONNX模型转换": "推理加速", "Triton推理服务": "模型部署", "FlashAttention优化": "推理加速",
    "KV Cache优化": "推理加速", "模型量化(INT8/INT4)": "模型量化", "模型蒸馏": "模型量化",
    "LoRA微调": "模型微调", "SFT指令微调": "模型微调", "全参数微调": "模型微调",
    "RLHF对齐训练": "强化学习对齐", "DPO偏好优化": "强化学习对齐", "GRPO强化训练": "强化学习对齐",
    "DeepSpeed分布式训练": "深度学习", "Megatron分布式训练": "深度学习",
    "FSDP分布式训练": "PyTorch", "PyTorch分布式训练(DDP)": "PyTorch",
    "CUDA编程优化": "推理加速", "Milvus向量库": "向量数据库", "FAISS向量检索": "向量数据库",
    "Chroma向量库": "向量数据库", "Qdrant向量库": "向量数据库", "Elasticsearch检索": "检索增强生成",
    "LangChain应用开发": "LangChain", "LlamaIndex应用开发": "LlamaIndex",
    "LangGraph编排": "智能体", "AutoGen多智能体": "多智能体", "Dify应用编排": "智能体",
    "MCP协议接入": "智能体", "Function Calling工具调用": "智能体", "A2A智能体协议": "多智能体",
    "Few-shot提示设计": "提示工程", "CoT思维链设计": "提示工程", "ReAct智能体模式": "智能体",
    "Prompt调优与评估": "提示工程", "Embedding向量化": "检索增强生成",
    "Rerank重排序": "检索增强生成", "混合检索(稠密+稀疏)": "检索增强生成",
    "多路召回策略": "推荐系统", "意图识别": "自然语言处理", "命名实体识别(NER)": "自然语言处理",
    "文本分类": "自然语言处理", "情感分析": "自然语言处理", "OCR文字识别": "计算机视觉",
    "语音识别(ASR)": "多模态", "语音合成(TTS)": "多模态", "数字人驱动技术": "多模态",
    "口型驱动同步": "多模态", "动作捕捉": "多模态", "YOLO目标检测": "计算机视觉",
    "图像分割": "计算机视觉", "扩散模型生图": "扩散模型", "ComfyUI工作流": "AIGC",
    "ControlNet控制生成": "扩散模型", "视频生成模型": "AIGC", "3D重建": "计算机视觉",
    "NeRF神经渲染": "计算机视觉", "3D高斯泼溅": "计算机视觉", "VLA视觉语言动作模型": "具身智能",
    "模仿学习": "具身智能", "机器人运动控制": "机器人技术", "运动规划": "机器人技术",
    "路径规划": "自动驾驶", "轨迹优化": "自动驾驶", "Isaac仿真": "具身智能",
    "MuJoCo仿真": "具身智能", "Gazebo仿真": "ROS", "ROS2开发": "ROS",
    "点云处理": "自动驾驶", "激光雷达感知": "自动驾驶", "多传感器融合": "自动驾驶",
    "BEV感知": "自动驾驶", "Occupancy网络": "自动驾驶",
    "Spark SQL调优": "Spark", "Spark Streaming": "Spark", "Flink SQL": "Flink",
    "Flink CDC": "Flink", "实时数仓建设": "实时计算", "离线数仓建设": "数据仓库",
    "维度建模": "数据建模", "指标体系建设": "数据治理", "Airflow调度": "ETL",
    "DolphinScheduler调度": "ETL", "数据血缘治理": "数据治理", "数据质量监控": "数据治理",
    "A/B实验设计": "数据挖掘", "用户画像建模": "数据挖掘", "归因分析": "数据挖掘",
    "Helm部署": "Kubernetes", "Istio服务网格": "云原生", "Prometheus监控": "DevOps",
    "Grafana可视化": "DevOps", "Terraform基础设施": "云原生", "Jenkins流水线": "CI/CD",
    "GitLab CI": "CI/CD", "ArgoCD持续交付": "CI/CD", "JVM调优": "Java",
    "MySQL索引与调优": "MySQL", "分库分表": "分布式系统", "RocketMQ": "消息队列",
    "RabbitMQ": "消息队列", "Netty网络编程": "高并发", "gRPC服务": "微服务",
    "GraphQL接口": "微服务", "Modbus协议": "物联网", "OPC UA协议": "物联网",
    "NB-IoT接入": "物联网", "LoRaWAN组网": "LoRa通信", "FreeRTOS开发": "实时操作系统",
    "RT-Thread开发": "实时操作系统", "Linux驱动开发": "嵌入式开发", "CAN总线通信": "嵌入式开发",
    "AUTOSAR架构": "自动驾驶",
}

_FINE_MAX_LEN = 24


def normalize_fine_skill(name: str) -> str:
    """细粒度技能名规整：别名合并、去动词前缀/括号说明，但**不**映射回粗粒度。"""
    if not name:
        return ""
    raw = name.strip()
    low = raw.lower().strip()
    if low in FINE_SYNONYMS:
        return FINE_SYNONYMS[low]
    if raw in FINE_PARENT:
        return raw
    n = re.sub(r"^(熟悉|掌握|了解|精通|具备|有)", "", raw).strip()
    n = re.split(r"[，,；;。]", n)[0].strip()
    if n.lower() in FINE_SYNONYMS:
        return FINE_SYNONYMS[n.lower()]
    if len(n) > _FINE_MAX_LEN:
        n = n[:_FINE_MAX_LEN]
    return n


def parent_of(fine_name: str, llm_parent: str | None = None, category: str | None = None) -> str | None:
    """细粒度技能的粗粒度父技能：人工映射表优先，其次 LLM 给的 parent（须为规范名），
    否则返回 None（该技能按粗粒度独立技能处理）。"""
    if fine_name in FINE_PARENT:
        return FINE_PARENT[fine_name]
    if llm_parent:
        p = normalize_skill(llm_parent)
        if p in SKILL_CATEGORY and p != fine_name:
            return p
    return None


