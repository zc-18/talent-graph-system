"""分类与归一化测试。"""
from app.services import taxonomy


def test_normalize_synonyms():
    assert taxonomy.normalize_skill("pytorch") == "PyTorch"
    assert taxonomy.normalize_skill("NLP") == "自然语言处理"
    assert taxonomy.normalize_skill("k8s") == "Kubernetes"
    assert taxonomy.normalize_skill("机器学习算法") == "机器学习"
    assert taxonomy.normalize_skill("大模型") == "大语言模型"


def test_normalize_suffix_stripping():
    assert taxonomy.normalize_skill("微服务架构") == "微服务"
    assert taxonomy.normalize_skill("物联网开发") == "物联网"
    # 规范多后缀名不应被错误裁剪
    assert taxonomy.normalize_skill("分布式系统") == "分布式系统"
    assert taxonomy.normalize_skill("实时操作系统") == "实时操作系统"


def test_normalize_empty_and_unknown():
    assert taxonomy.normalize_skill("") == ""
    assert taxonomy.normalize_skill("某未知技能X") == "某未知技能X"


def test_category_and_type():
    assert taxonomy.skill_category("PyTorch") == "人工智能"
    assert taxonomy.skill_category("Spark") == "大数据"
    assert taxonomy.skill_category("MQTT") == "物联网"
    assert taxonomy.skill_type("PyTorch") == "tool"
    assert taxonomy.skill_type("团队协作") == "soft"
    assert taxonomy.skill_type("机器学习") == "hard"


def test_clean_skill_name():
    # 取首个分项、去括号说明、去冗余后缀
    assert taxonomy.clean_skill_name("LangChain/LlamaIndex等LLM开发框架") == "LangChain"
    assert taxonomy.clean_skill_name("核心提示词技术（Few-shot, Chain-of-Thought）") == "核心提示词"
    assert taxonomy.clean_skill_name("机器学习与深度学习基础") == "机器学习与深度学习"
    assert taxonomy.clean_skill_name("Python") == "Python"
    assert taxonomy.clean_skill_name("") == ""
    # 超长兜底截断
    assert len(taxonomy.clean_skill_name("一二三四五六七八九十一二三四五六七八九十")) <= 16
