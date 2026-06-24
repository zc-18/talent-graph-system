"""生成 100+ 条岗位 JD 测试数据集（含 ground-truth 标签）。

产出：
- data/seed_jds.json     —— 原始 JD（含元数据），用于 pipeline 构建图谱
- data/ground_truth.json —— 每条 JD 的真值技能标签，用于 JD 解析准确率评测
设计：覆盖 10 个新一代信息技术岗位簇；每条 JD 在职责措辞/技能子集/排版模板上充分变化，
      贴近真实「相似但不相同」的招聘文本；另植入「抄袭(整段复制)」与「能力通胀」对抗样本。
"""
from __future__ import annotations
import json
import random
import os
from datetime import datetime, timedelta

random.seed(2026)
HERE = os.path.dirname(os.path.abspath(__file__))

COMPANIES = ["智测云", "星河智能", "数擎科技", "云栖数据", "鲸智信息", "天工人工智能", "极元科技",
             "瀚海智算", "新元数字", "灵犀科技", "弘毅大数据", "方舟智联", "致远云", "博观智能",
             "翼飞科技", "数链科技", "曦和智能", "中科云智", "联川数据", "元启科技", "智衍科技",
             "云图数据", "九章智能", "星澜科技", "宏图智联"]
LOCATIONS = ["北京", "上海", "深圳", "杭州", "广州", "成都", "南京", "武汉", "西安", "合肥"]
SOURCES = ["招聘平台A", "招聘平台B", "企业官网", "校园招聘", "猎头渠道", "行业社区"]

# 必备技能动词模板（增加文本多样性，避免误判抄袭）
REQ_VERBS = ["熟练掌握{}", "精通{}", "具备扎实的{}基础", "熟悉{}及其原理", "能够独立使用{}解决实际问题",
             "深入理解{}", "有丰富的{}实战经验", "扎实的{}功底"]
BONUS_VERBS = ["有{}经验者优先", "熟悉{}者加分", "了解{}相关技术更佳", "具备{}背景优先考虑", "接触过{}优先"]
INTROS = ["我们正在寻找", "团队诚招", "因业务发展，现招聘", "岗位虚位以待：", "招募"]
CLOSINGS = ["具备良好的团队协作与沟通能力。", "有较强的学习能力和责任心。",
            "能够承受一定的工作压力，主动推动问题解决。", "对技术有热情，乐于钻研。",
            "良好的文档撰写与表达能力。"]

ROLES = {
    "Java开发工程师": {
        "category": "云计算与工程", "level": "middle",
        "core": ["Java", "Spring", "MySQL"],
        "ext_req": ["Redis", "分布式系统", "消息队列", "高并发"],
        "bonus": ["Kubernetes", "Docker", "微服务", "Linux"],
        "resp": ["负责核心业务系统的后端服务设计与开发", "参与高并发分布式系统架构设计与优化",
                 "编写高质量、可维护的代码并完成单元测试", "排查并解决线上性能与稳定性问题",
                 "参与技术方案评审与数据库设计", "对接前端及第三方系统完成接口联调"],
        "scenarios": ["金融交易系统", "电商订单平台", "企业级中台"],
    },
    "机器学习工程师": {
        "category": "人工智能", "level": "middle",
        "core": ["机器学习", "深度学习", "Python"],
        "ext_req": ["PyTorch", "数据挖掘", "特征工程".replace("特征工程", "数据挖掘")],
        "bonus": ["TensorFlow", "模型部署", "Spark", "推荐系统"],
        "resp": ["负责机器学习模型的设计、训练与调优", "完成特征工程与数据预处理流程",
                 "将模型部署上线并持续迭代优化", "跟踪前沿算法并落地业务场景",
                 "搭建模型评估与监控体系", "与业务团队协作定义算法目标"],
        "scenarios": ["智能风控", "个性化推荐", "智能营销"],
    },
    "大数据开发工程师": {
        "category": "大数据", "level": "middle",
        "core": ["Spark", "Hadoop", "SQL"],
        "ext_req": ["Hive", "数据仓库", "Kafka", "ETL"],
        "bonus": ["Flink", "实时计算", "数据治理", "ClickHouse"],
        "resp": ["负责离线与实时数据仓库的建设与维护", "开发ETL数据处理与调度流程",
                 "优化大规模数据计算任务性能", "保障数据质量与稳定产出",
                 "参与数据模型与指标体系设计", "支撑上层数据应用与分析需求"],
        "scenarios": ["用户行为分析", "实时数据大屏", "数据中台"],
    },
    "算法工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["机器学习", "深度学习", "Python"],
        "ext_req": ["数据挖掘", "强化学习", "推荐系统"],
        "bonus": ["计算机视觉", "自然语言处理", "模型部署", "推理加速"],
        "resp": ["设计并实现核心算法模型", "针对业务问题进行算法选型与优化",
                 "撰写技术文档与算法方案", "推动算法效果在业务中的提升",
                 "开展前沿算法调研与原型验证", "优化模型线上效果与计算效率"],
        "scenarios": ["搜索排序", "智能调度", "广告投放"],
    },
    "自然语言处理工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["自然语言处理", "深度学习", "Python"],
        "ext_req": ["PyTorch", "大语言模型", "Transformer"],
        "bonus": ["检索增强生成", "模型微调", "提示工程", "HuggingFace"],
        "resp": ["负责NLP算法研发，包括文本分类、信息抽取、问答等", "基于大语言模型构建智能应用",
                 "优化模型在垂直领域的效果", "跟进大模型与多模态前沿技术",
                 "构建语料处理与标注流程", "设计提示词与检索增强方案"],
        "scenarios": ["智能客服", "知识问答", "文档智能"],
    },
    "计算机视觉工程师": {
        "category": "人工智能", "level": "middle",
        "core": ["计算机视觉", "深度学习", "Python"],
        "ext_req": ["PyTorch", "模型部署"],
        "bonus": ["TensorFlow", "推理加速", "多模态", "边缘计算"],
        "resp": ["负责图像/视频视觉算法的研发", "完成目标检测、识别、分割等任务",
                 "优化模型推理性能并部署到边缘设备", "跟踪视觉领域最新进展",
                 "构建图像数据处理与增强流程", "推动视觉算法在产品中的落地"],
        "scenarios": ["智能安防", "工业质检", "自动驾驶感知"],
    },
    "物联网开发工程师": {
        "category": "物联网", "level": "middle",
        "core": ["物联网", "嵌入式开发", "C++"],
        "ext_req": ["MQTT", "传感器技术", "Linux"],
        "bonus": ["边缘计算", "5G通信", "实时操作系统"],
        "resp": ["负责物联网终端设备的软件开发", "实现设备与云平台的数据通信",
                 "完成传感器数据采集与边缘处理", "保障设备稳定运行与远程升级",
                 "参与通信协议与硬件选型", "排查现场设备问题"],
        "scenarios": ["智能家居", "工业互联网", "智慧城市"],
    },
    "数据分析师": {
        "category": "大数据", "level": "junior",
        "core": ["SQL", "数据挖掘", "Python"],
        "ext_req": ["数据建模"],
        "bonus": ["机器学习", "数据治理", "Spark"],
        "resp": ["负责业务数据的采集、清洗与分析", "搭建数据指标体系与可视化报表",
                 "通过数据洞察支持业务决策", "完成专题数据分析报告",
                 "监控核心指标波动并定位原因", "与产品运营协作推动数据应用"],
        "scenarios": ["运营分析", "用户增长", "经营决策"],
    },
    "后端开发工程师": {
        "category": "云计算与工程", "level": "middle",
        "core": ["Java", "MySQL", "Redis"],
        "ext_req": ["微服务", "Python", "Go", "消息队列"],
        "bonus": ["Kubernetes", "Docker", "高并发", "分布式系统"],
        "resp": ["负责后端服务的设计与开发", "构建高可用、可扩展的微服务架构",
                 "完成接口设计与系统集成", "持续优化系统性能与稳定性",
                 "参与核心模块技术选型", "保障服务的安全与可观测性"],
        "scenarios": ["SaaS平台", "在线服务", "API网关"],
    },
    "Python开发工程师": {
        "category": "云计算与工程", "level": "junior",
        "core": ["Python", "SQL", "Linux"],
        "ext_req": ["MySQL", "Redis"],
        "bonus": ["Docker", "数据挖掘", "机器学习", "分布式系统"],
        "resp": ["负责Python后端服务与数据处理脚本开发", "对接第三方接口与数据源",
                 "完成自动化任务与运维工具开发", "参与系统功能迭代与优化",
                 "编写接口文档与测试用例", "维护线上服务稳定运行"],
        "scenarios": ["数据平台", "自动化运维", "Web服务"],
    },
    "大模型应用开发工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["大语言模型", "Python", "检索增强生成"],
        "ext_req": ["提示工程", "向量数据库", "LangChain", "模型微调"],
        "bonus": ["智能体", "多模态", "HuggingFace", "模型部署"],
        "resp": ["基于大语言模型构建企业级智能应用", "设计检索增强生成(RAG)与知识库方案",
                 "进行提示词工程与模型微调优化效果", "搭建大模型评测与监控体系",
                 "推动大模型能力在业务场景落地"],
        "scenarios": ["企业知识问答", "智能客服", "AI办公助手"],
    },
    "推荐算法工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["推荐系统", "机器学习", "Python"],
        "ext_req": ["深度学习", "数据挖掘", "Spark"],
        "bonus": ["实时计算", "强化学习", "特征工程"],
        "resp": ["负责推荐系统召回、排序与重排算法研发", "构建用户画像与特征工程体系",
                 "优化推荐效果指标(CTR/转化率)", "搭建在线推荐服务与AB实验",
                 "跟踪推荐领域前沿算法"],
        "scenarios": ["电商推荐", "内容分发", "广告投放"],
    },
    "数据架构师": {
        "category": "大数据", "level": "expert",
        "core": ["数据仓库", "数据建模", "SQL"],
        "ext_req": ["Spark", "数据治理", "ETL"],
        "bonus": ["Flink", "数据湖", "ClickHouse"],
        "resp": ["规划企业级数据架构与数据中台", "设计数据仓库分层与主题模型",
                 "制定数据标准与数据治理规范", "把控数据质量与数据资产体系",
                 "支撑数据驱动的业务决策"],
        "scenarios": ["数据中台", "经营分析", "数据资产管理"],
    },
    "云原生开发工程师": {
        "category": "云计算与工程", "level": "senior",
        "core": ["Kubernetes", "Docker", "Linux"],
        "ext_req": ["CI/CD", "微服务", "云原生"],
        "bonus": ["Git", "云平台", "分布式系统", "DevOps"],
        "resp": ["负责云原生平台与容器化基础设施建设", "设计与维护CI/CD自动化流水线",
                 "推进微服务架构治理与服务网格", "保障系统高可用与可观测性",
                 "优化资源调度与弹性伸缩"],
        "scenarios": ["容器云平台", "DevOps体系", "弹性基础设施"],
    },
    "智能驾驶算法工程师": {
        "category": "智能系统", "level": "senior",
        "core": ["自动驾驶", "深度学习", "C++"],
        "ext_req": ["计算机视觉", "SLAM", "Python"],
        "bonus": ["ROS", "传感器技术", "推理加速"],
        "resp": ["负责自动驾驶感知/预测/规划算法研发", "完成多传感器融合与目标检测跟踪",
                 "优化模型在车载平台的部署与推理", "参与仿真测试与路测问题分析",
                 "跟踪自动驾驶前沿技术"],
        "scenarios": ["自动驾驶感知", "智能座舱", "车路协同"],
    },
    "机器人开发工程师": {
        "category": "智能系统", "level": "middle",
        "core": ["机器人技术", "ROS", "C++"],
        "ext_req": ["控制系统", "嵌入式开发", "SLAM"],
        "bonus": ["Python", "传感器技术", "实时操作系统"],
        "resp": ["负责机器人运动控制与导航算法开发", "基于ROS搭建机器人软件系统",
                 "实现SLAM建图与路径规划", "完成传感器集成与硬件联调",
                 "推进机器人在实际场景的应用"],
        "scenarios": ["服务机器人", "工业机器人", "具身智能"],
    },
    "数据挖掘工程师": {
        "category": "大数据", "level": "middle",
        "core": ["数据挖掘", "Python", "SQL"],
        "ext_req": ["机器学习", "数据建模", "Spark"],
        "bonus": ["深度学习", "实时计算", "数据治理"],
        "resp": ["负责海量数据的挖掘建模与价值发现", "构建预测/分类/聚类等数据挖掘模型",
                 "完成特征工程与数据预处理", "将挖掘成果转化为业务策略",
                 "搭建数据分析与建模流程"],
        "scenarios": ["精准营销", "风险预测", "用户增长"],
    },
    "大数据平台工程师": {
        "category": "大数据", "level": "senior",
        "core": ["Hadoop", "Spark", "Kafka"],
        "ext_req": ["Flink", "数据仓库", "Linux"],
        "bonus": ["ClickHouse", "实时计算", "云原生"],
        "resp": ["负责大数据平台的搭建、运维与优化", "维护Hadoop/Spark/Flink等计算引擎",
                 "保障海量数据的稳定计算与存储", "优化集群性能与资源利用率",
                 "支撑上层数据应用与实时计算需求"],
        "scenarios": ["实时计算平台", "数据湖仓", "流批一体"],
    },
    "多模态算法工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["多模态", "深度学习", "Python"],
        "ext_req": ["计算机视觉", "自然语言处理", "PyTorch"],
        "bonus": ["大语言模型", "扩散模型", "模型部署"],
        "resp": ["负责图文/音视频多模态算法研发", "构建跨模态表示与对齐模型",
                 "基于多模态大模型实现理解与生成", "优化模型在业务场景的效果",
                 "跟踪多模态前沿技术"],
        "scenarios": ["多模态搜索", "智能创作", "视频理解"],
    },
    "MLOps工程师": {
        "category": "云计算与工程", "level": "senior",
        "core": ["模型部署", "Docker", "Kubernetes"],
        "ext_req": ["Python", "CI/CD", "推理加速"],
        "bonus": ["云原生", "机器学习", "Linux"],
        "resp": ["负责机器学习模型的工程化与上线", "搭建模型训练-部署-监控全流程平台",
                 "优化模型推理性能与资源成本", "实现模型版本管理与持续交付",
                 "保障线上模型服务稳定可观测"],
        "scenarios": ["模型服务平台", "AI中台", "MLOps流水线"],
    },
    "AIGC算法工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["AIGC", "扩散模型", "深度学习"],
        "ext_req": ["Python", "PyTorch", "多模态"],
        "bonus": ["大语言模型", "模型微调", "HuggingFace"],
        "resp": ["负责AIGC生成式算法研发（文/图/视频）", "基于扩散模型实现高质量内容生成",
                 "优化生成模型的可控性与效率", "结合大模型构建创作工具",
                 "跟踪生成式AI前沿"],
        "scenarios": ["AI绘画", "营销素材生成", "数字人"],
    },
    "强化学习工程师": {
        "category": "人工智能", "level": "expert",
        "core": ["强化学习", "深度学习", "Python"],
        "ext_req": ["机器学习", "PyTorch"],
        "bonus": ["多智能体", "模型部署", "推理加速"],
        "resp": ["负责强化学习算法研发与落地", "设计奖励函数与训练环境",
                 "解决复杂决策与控制问题", "优化策略的收敛性与稳定性",
                 "推动强化学习在业务的应用"],
        "scenarios": ["智能决策", "机器人控制", "资源调度"],
    },
    "知识图谱工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["知识图谱", "Python", "Neo4j"],
        "ext_req": ["自然语言处理", "数据挖掘"],
        "bonus": ["大语言模型", "数据建模", "检索增强生成"],
        "resp": ["负责行业知识图谱的构建与应用", "完成实体识别、关系抽取与融合",
                 "设计图谱本体与存储方案", "结合大模型实现知识问答与推理",
                 "维护图谱质量与更新"],
        "scenarios": ["智能问答", "风控图谱", "知识推理"],
    },
    "实时计算工程师": {
        "category": "大数据", "level": "senior",
        "core": ["Flink", "实时计算", "Java"],
        "ext_req": ["Kafka", "Spark", "SQL"],
        "bonus": ["ClickHouse", "数据仓库", "分布式系统"],
        "resp": ["负责实时数据计算平台的研发", "基于Flink开发流式计算任务",
                 "保障实时链路的低延迟与高可用", "优化实时数仓与指标计算",
                 "支撑实时风控与监控场景"],
        "scenarios": ["实时风控", "实时大屏", "实时推荐"],
    },
    "搜索算法工程师": {
        "category": "人工智能", "level": "senior",
        "core": ["机器学习", "自然语言处理", "Python"],
        "ext_req": ["深度学习", "数据挖掘", "推荐系统"],
        "bonus": ["大语言模型", "向量数据库", "实时计算"],
        "resp": ["负责搜索召回、排序与相关性算法", "构建语义检索与向量召回体系",
                 "优化搜索效果指标与体验", "结合大模型提升搜索智能化",
                 "跟踪搜索与信息检索前沿"],
        "scenarios": ["电商搜索", "企业检索", "语义搜索"],
    },
}

LEVEL_WORD = {"familiar": "了解", "proficient": "熟练", "expert": "精通"}
INFLATION_POOL = ["区块链", "数字孪生", "量子计算", "ROS", "PLC", "Scala", "Rust", "Neo4j",
                  "扩散模型", "SLAM", "Zigbee", "CoAP", "AIGC"]


def make_jd(role_name, cfg, idx, inflation=False):
    # 必备技能：核心必选 + 扩展随机子集（保证「相似但不相同」）
    core = list(cfg["core"])
    ext = list(cfg["ext_req"])
    random.shuffle(ext)
    chosen_ext = ext[: random.randint(max(1, len(ext) - 2), len(ext))]
    req = core + chosen_ext
    # 去重保持顺序
    seen = set()
    req = [x for x in req if not (x in seen or seen.add(x))]

    bonus_pool = list(cfg["bonus"])
    random.shuffle(bonus_pool)
    bonus = bonus_pool[: random.randint(2, len(bonus_pool))]

    inflated_extra = []
    if inflation:
        inflated_extra = random.sample(INFLATION_POOL, 6)

    resp_pool = list(cfg["resp"])
    random.shuffle(resp_pool)
    resp = resp_pool[: random.randint(3, 5)]

    company = random.choice(COMPANIES)
    loc = random.choice(LOCATIONS)
    intro = random.choice(INTROS)

    req_lines = []
    for i, s in enumerate(req + inflated_extra, 1):
        verb = random.choice(REQ_VERBS).format(s)
        req_lines.append(f"{i}. {verb}；")
    bonus_lines = [f"- {random.choice(BONUS_VERBS).format(b)}" for b in bonus]

    # 两种排版模板增加结构多样性
    if random.random() < 0.5:
        body = f"""{intro}{role_name}（{company}·{loc}）
一、岗位职责
""" + "\n".join(f"{i}. {r}；" for i, r in enumerate(resp, 1)) + """
二、任职要求
""" + "\n".join(req_lines) + "\n三、加分项\n" + "\n".join(bonus_lines)
    else:
        body = f"""【{company}】招聘{role_name}（工作地点：{loc}）
岗位职责：
""" + "；\n".join(f"· {r}" for r in resp) + """。
任职资格：
""" + "\n".join(req_lines) + "\n优先条件：\n" + "\n".join(bonus_lines)

    if random.random() < 0.6:
        body += "\n其他：" + random.choice(CLOSINGS)

    pub = datetime(2026, 1, 1) + timedelta(days=random.randint(0, 160))
    return {
        "job_title": role_name, "company": company, "location": loc,
        "source": random.choice(SOURCES), "source_url": f"https://example.com/jd/{idx}",
        "publish_date": pub.isoformat(), "raw_text": body,
        "_ground_truth": {"required": req, "bonus": bonus,
                          "inflation": inflation, "inflated_extra": inflated_extra},
    }


def generate():
    jds, ground = [], []
    idx = 1
    for role_name, cfg in ROLES.items():
        n = random.randint(11, 13)
        for k in range(n):
            inflation = (k == n - 1 and random.random() < 0.6)
            jd = make_jd(role_name, cfg, idx, inflation=inflation)
            gt = jd.pop("_ground_truth")
            jds.append(jd)
            ground.append({"id": idx, "job_title": role_name, "ground_truth": gt})
            idx += 1

    # 植入抄袭：整段复制已有 JD（仅改公司/来源），应被近似去重命中
    dup_count = 8
    planted = []
    for _ in range(dup_count):
        src_i = random.randint(0, 59)
        src = jds[src_i]
        dup = json.loads(json.dumps(src))
        dup["company"] = random.choice(COMPANIES)
        dup["source"] = random.choice(SOURCES)
        dup["source_url"] = f"https://example.com/jd/{idx}"
        dup["_planted_duplicate_of"] = src_i + 1
        jds.append(dup)
        ground.append({"id": idx, "job_title": dup["job_title"],
                       "ground_truth": {"duplicate": True, "duplicate_of": src_i + 1}})
        planted.append(idx)
        idx += 1

    with open(os.path.join(HERE, "seed_jds.json"), "w", encoding="utf-8") as f:
        json.dump(jds, f, ensure_ascii=False, indent=2)
    with open(os.path.join(HERE, "ground_truth.json"), "w", encoding="utf-8") as f:
        json.dump(ground, f, ensure_ascii=False, indent=2)

    inflation_n = sum(1 for g in ground if g["ground_truth"].get("inflation"))
    print(f"生成 JD 总数: {len(jds)}")
    print(f"岗位簇: {len(ROLES)}")
    print(f"植入抄袭(整段复制)样本: {dup_count}  ids={planted}")
    print(f"植入能力通胀样本: {inflation_n}")


if __name__ == "__main__":
    generate()
