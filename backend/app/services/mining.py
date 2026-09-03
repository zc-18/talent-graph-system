"""每日动态挖掘流水线 —— 模拟聚合源（BOSS直聘）的增量观测层。

赛事方提供的聚合语料每天喂 1000 行进来，走「读取 → 结构校验 → 去重 → 岗位归一 →
规则抽取(+LLM 补缺) → 增量入图 → 日间对比 → 培训计划」十步，全过程逐阶段落台账，
前端的回放动画直接播 ``DailyMiningRun.stage_log`` 里记录的真实节奏与真实计数。

写库范围（硬约束，改动前务必读完 models.py 里三张观测表上方的注释）：

* **只 INSERT，且只碰 skill / job_skill / evidence 三张公开表。**
  绝不 UPDATE、绝不 DELETE。``job`` / ``raw_jd`` / ``crawl_batch`` /
  ``capability_change`` 以及**任何已存在的 job_skill 行**，跑完必须与跑之前完全一致。
* **job_skill.status 恒为 candidate，且永远不会自动转正。**
  这份语料没有雇主字段，``employer_resolution.employer_independence_key()`` 拿不到
  公司名就返回 None，``hallucination.aggregate_capabilities`` 的「≥2 个独立雇主」门
  会无条件把它判成 candidate。这是门禁在正常工作。**不要拿「公司领域」当雇主**——
  那是行业标签，不是来源身份，拿它冒充雇主等于凭空给全库置信度注水。
* **不进 raw_jd。** ``graph_service.stats_overview`` 对外的「JD 总数」就是
  ``count(RawJD)``，掺进 5 万条模拟语料会让所有已交付材料的头条数字失真。
  同理不写 ``crawl_batch``（那是合规实采台账）。
* **新技能点必须挂到粗粒度父概念下（两级技能树）。**
  ``graph_service.py`` 里「candidate 只落细粒度技能点」是全库口径——粗粒度候选项
  掉进前端 coarse/fine 两套分组的缝里，**一处都不渲染**。所以本模块给新技能点定
  父节点的依据是**同一行技能标签里共现的已知粗粒度概念**（语料自带的信号，零 LLM、
  不猜词形）；共现不上任何粗粒度概念的词**一律不建 Skill 节点**，只留在观测层
  （``DailyMiningItem.skills`` 与 ``skill_id=NULL`` 的 ``DailySkillDelta``）。
  建成孤立根节点只会让对外的「技能总数」虚涨、而页面看不见，得不偿失。
* **不写 capability_change。** 那是经雇主交叉验证的 v1→v2 演化审计链；
  每日候选层的增减记在 ``DailySkillDelta``。日志与事实分家在本项目出过两次事故。

置信度耦合说明（这一条决定了本模块可以放心补证据）：证据行写的是
``source_type='dataset'``，而 ``confidence_batch.calculate_job_state`` 只把
``source_type=='jd'`` 的证据计入支持率/多样性/时效性/权威度，只把
``REAL_EXTERNAL_TYPES = {web, external, authority, policy, report}`` 计入外部验证。
``dataset`` 两边都不沾，因此本模块写的证据对统一置信度公式是**结构性惰性**的，
夜间 02:30 的批算不会因为它而漂移。这不是巧合，是选这个 source_type 的原因。

用法见 ``data/run_daily_mining.py``；撤销见 ``data/rollback_mining.py``。
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from . import cleaning, ingest, matching, taxonomy
from .graph_service import MAX_EVIDENCE_PER_SKILL

# ---------------------------------------------------------------- 常量

MIN_BODY_CHARS = 50                 # 正文长度门（有技能标签的行豁免）
NEAR_DUP_THRESHOLD = 2              # SimHash 海明距离 ≤2 视为近重复
NEAR_DUP_MIN_CHARS = 60             # 短文本 SimHash 噪声大，不参与近重复判定
MAX_SKILLS_PER_ROW = 15             # 单行技能数上限，挡住标签堆砌造成的能力通胀
MAX_SKILL_CHARS = 40
# 单字符技能白名单。**不要**换回一刀切的「≤1 字全丢」：实测那样会连
# C（5326 次，全语料第 3 高频技能）和 R（120 次）一起丢掉，而其余单字 token
# 全是 `农/林/牧/渔` 这类切分碎片。命中白名单的一律转大写后再归一——
# taxonomy.normalize_skill('c') 原样返回 'c'，不转大写会和 'C' 各建一个技能节点。
SINGLE_CHAR_SKILLS = frozenset({"C", "R"})
SNIPPET_MAX = 240                   # 证据片段截断长度
EVIDENCE_SOURCE_TYPE = "dataset"    # 见模块头部「置信度耦合说明」
EVIDENCE_WEIGHT = 0.5               # 匿名聚合语料：无雇主、无 URL，权威度低于公开数据集
SUPPORT_DELTA_THRESHOLD = 2         # 支持条数变化 ≥2 才记一条 support_up/support_down
PARENT_MIN_COOC = 2                 # 新技能点与父概念至少同格出现 2 行才认（挡单次巧合）
PARENT_MIN_PMI = 0.30               # 互信息下限：低于此值视为「只是都很常见」，不认亲
NEW_SKILL_WEIGHT = 0.3              # 新建 candidate 关系的权重（未经交叉验证，压低）
PREREQ_PLAN_WEIGHT = 0.4
PREREQ_MAX_DEPTH = 2

# DeepSeek 计费（人民币元 / 百万 token）。**这两个数字必须定期对照官方定价页复核**，
# 官方调价时这里不会自动跟着变，预算闸算出来的钱数就会失真。
# 复核入口：https://api-docs.deepseek.com/quick_start/pricing
LLM_PRICE_INPUT_CNY_PER_MTOKEN = 2.0
LLM_PRICE_OUTPUT_CNY_PER_MTOKEN = 8.0
LLM_MAX_TOKENS = 1024
LLM_BODY_CHARS = 600                # 送进 LLM 的正文截断长度

STAGE_ORDER = ["read", "validate", "dedup", "map", "extract", "write"]
STAGE_LABELS = {
    "read": "读取", "validate": "结构校验", "dedup": "去重",
    "map": "岗位归一", "extract": "技能抽取", "write": "增量入图",
}


class MiningDataQualityError(RuntimeError):
    """输入或治理配置未通过发布闸；本轮不得写图谱或生成日间差异。"""

_SPLIT_RE = re.compile(r"[、，,/;；|\r\n\t]+")
# 括号必须在**整串切分之前**就归一成分隔符，不能等切完再逐 token 剥。
# 实测：`大数据处理框架(Spark、Hive)` 朴素切分得到 `大数据处理框架(Spark` + `Hive)`；
# 逐 token 剥括号只救得回后半截（Hive)→Hive，全语料 1735 处），前半截会带着
# 内部左括号活下来变成幽灵技能——`大数据处理框架(Spark` 全语料出现 852 次
# （总排名 39）、`大数据处理工具(Spark` 190 次、`图像处理库（OpenCV等` 180 次。
# 把括号当分隔符，Spark / OpenCV 这 1200 来次提及才能真正回到正确的技能名下。
# 靠黑名单封杀那些幽灵 token 是把信号一起扔掉，不是修复。
_BRACKET_RE = re.compile(r"[（）()【】\[\]]")
# 末尾的四个是零宽字符：LLM 返回的技能名里实测出现过 "Go-kit‌"，
# 不剥掉就会和正常的 "Go-kit" 各建一个 Skill 节点。
_TRIM_CHARS = " \t　{}《》<>·,，.。:：;；\"'`~!！?？*-—_​‌‍﻿"

# stopwords.json 缺失时的兜底表（Lane A 的产物没落地也要能跑）
_FALLBACK_STOPWORDS = {
    "岗位职责", "任职要求", "工作职责", "职位描述", "职位要求", "岗位要求",
    "相关经验", "工作经验", "项目经验", "从业经验", "经验", "能力", "素质",
    "本科", "硕士", "博士", "大专", "学历", "专业", "计算机", "统招",
    "优先", "加分项", "其他", "若干", "以上", "熟悉", "了解", "精通", "掌握",
    "沟通能力", "团队合作", "责任心", "抗压能力", "英语", "四六级",
}


# ---------------------------------------------------------------- 分片与词表

def shard_dir() -> Path:
    """分片目录：配置留空则用 backend/data/aggregate_source（Lane A 的产物目录）。"""
    if settings.mining_shard_dir:
        return Path(settings.mining_shard_dir)
    return Path(__file__).resolve().parents[2] / "data" / "aggregate_source"


def shard_path(index: int, directory: Path | None = None) -> Path:
    return (directory or shard_dir()) / f"boss_sim_{index:03d}.jsonl"


def shard_count(directory: Path | None = None) -> int:
    """语料的分片总数，**以 manifest.json 的声明为准**；没有 manifest 返回 0（未知）。

    刻意不拿 ``glob("boss_sim_*.jsonl")`` 兜底：目录里碰巧躺着几个分片，不代表
    语料的周期长度。用文件数当周期会让「跑满一轮绕回 0」在任何只放了子集的目录
    （测试临时目录、手工挑分片调试）里误触发，把游标一脚踢回开头。
    未知就不绕回——让 ``read_shard`` 抛一条说得清的 FileNotFoundError 更好。
    """
    d = directory or shard_dir()
    try:
        shards = json.loads((d / "manifest.json").read_text("utf-8")).get("shards")
        return len(shards) if shards else 0
    except Exception:  # noqa: BLE001
        return 0


_STOPWORD_CACHE: dict[tuple[str, float], tuple[set[str], list[re.Pattern]]] = {}


def load_stopwords(directory: Path | None = None) -> tuple[set[str], list[re.Pattern]]:
    """读取 stopwords.json（``{"exact": [...], "patterns": ["regex", ...]}``）。

    两部分都要用：``exact`` 是 token 全等即丢，``patterns`` 是**未锚定**的正则，
    用 ``re.search`` 判定（除非正则自己写了锚点）。

    文件在时**不掺内置兜底表**：那份词表是 Lane A 按 top-300 词频人工过筛出来的，
    我这边拍脑袋加词只会误杀（比如「英语」在他们的口径里是保留项）。
    只有文件缺失 / 解析失败时才回落到内置小表，并打印告警——
    挖掘作业不该因为一个词表停摆。
    """
    path = (directory or shard_dir()) / "stopwords.json"
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        print(f"[mining] 警告：未找到 {path}，使用内置兜底停用词表")
        return set(_FALLBACK_STOPWORDS), []
    if key in _STOPWORD_CACHE:
        return _STOPWORD_CACHE[key]
    try:
        raw = json.loads(path.read_text("utf-8"))
        exact = {str(x).strip() for x in raw.get("exact", []) if str(x).strip()}
        patterns = []
        for p in raw.get("patterns", []) or []:
            try:
                patterns.append(re.compile(p))
            except re.error:
                print(f"[mining] 警告：停用词正则无法编译，已跳过: {p!r}")
        if not exact and not patterns:
            raise ValueError("stopwords.json 里 exact 与 patterns 都是空的")
    except Exception as exc:  # noqa: BLE001
        print(f"[mining] 警告：{path} 解析失败（{exc}），使用内置兜底停用词表")
        exact, patterns = set(_FALLBACK_STOPWORDS), []
    _STOPWORD_CACHE[key] = (exact, patterns)
    return exact, patterns


def read_shard(index: int, limit: int | None = None,
               directory: Path | None = None) -> list[dict]:
    """读入一个分片的 JSONL；坏行跳过而不是整批失败。"""
    path = shard_path(index, directory)
    if not path.exists():
        raise FileNotFoundError(
            f"分片不存在：{path}（聚合语料分片由 data/aggregate_source 提供，"
            f"未生成时先跑生成脚本或用 --shard 指定已有分片）")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit and len(rows) >= limit:
                break
    return rows


# ---------------------------------------------------------------- 规则抽取

def split_skill_tags(raw: str, stop_exact: set[str],
                     stop_patterns: list[re.Pattern]) -> list[str]:
    """把「技能需求」原文切成归一后的技能名列表。

    顺序很重要：**先把整串里的括号换成分隔符，再切分**（见 ``_BRACKET_RE`` 上方注释），
    然后逐 token 去空白、判长度（单字符走白名单）、归一、过停用词。
    """
    if not raw:
        return []
    cell = _BRACKET_RE.sub("、", str(raw))
    out: list[str] = []
    seen: set[str] = set()
    for piece in _SPLIT_RE.split(cell):
        token = _bounded(piece.strip().strip(_TRIM_CHARS).strip())
        if not token:
            continue
        name = _bounded(
            (taxonomy.normalize_skill(token) or "").strip().strip(_TRIM_CHARS).strip())
        if not name:
            continue
        if is_stopword(name, stop_exact, stop_patterns) or is_stopword(
                token, stop_exact, stop_patterns):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= MAX_SKILLS_PER_ROW:
            break
    return out


def _bounded(token: str) -> str:
    """长度门：单字符只放行白名单里的 C / R（并转大写），上界 40 字。

    顺带剥掉结尾的「等」：括号变分隔符后 ``图像处理库（OpenCV等）`` 会切出 ``OpenCV等``
    （全语料 180 次），不剥就和 ``OpenCV`` 各成一个技能节点。
    """
    if not token:
        return ""
    while len(token) > 1 and token.endswith("等"):
        token = token[:-1].strip().strip(_TRIM_CHARS)
    if not token:
        return ""
    if len(token) == 1:
        return token.upper() if token.upper() in SINGLE_CHAR_SKILLS else ""
    return token if len(token) <= MAX_SKILL_CHARS else ""


def is_stopword(name: str, stop_exact: set[str],
                stop_patterns: list[re.Pattern]) -> bool:
    if name in stop_exact or name.lower() in stop_exact:
        return True
    return any(p.search(name) for p in stop_patterns)


# ---------------------------------------------------------------- LLM 补缺

_LLM_SYS = "你是招聘 JD 的技能点抽取器。只输出 JSON，不解释、不寒暄。"
_LLM_TMPL = """从下面每条岗位描述中抽取 3-8 个**具体**技能点（技术名词、工具、框架、方法论）。
要求：
1. 只抽原文出现或原文直接蕴含的内容，不得编造；
2. 不要输出「沟通能力」「责任心」「本科学历」这类通用素质与硬性条件；
3. 每个技能点 2-12 个字，不带「熟悉/掌握/精通」等前缀。

严格输出 JSON：{{"items":[{{"i":0,"skills":["技能A","技能B"]}}]}}

{blocks}"""

_llm_client_cache: list[Any] = []


def _llm_client():
    """挖掘专用 LLM 客户端。

    刻意不复用 ``clients._llm``：这份预算要和 chat / discovery 的用量隔开，
    密钥也可以单独配（``MINING_LLM_API_KEY``，留空回落到 ``DEEPSEEK_API_KEY``）。
    """
    if _llm_client_cache:
        return _llm_client_cache[0]
    key = settings.mining_llm_api_key or settings.deepseek_api_key
    if not key:
        _llm_client_cache.append(None)
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=settings.deepseek_base_url, timeout=90)
    except Exception as exc:  # noqa: BLE001
        print(f"[mining] 警告：LLM 客户端初始化失败（{exc}），降级为纯规则抽取")
        client = None
    _llm_client_cache.append(client)
    return client


def _cost_cny(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens / 1_000_000 * LLM_PRICE_INPUT_CNY_PER_MTOKEN
            + completion_tokens / 1_000_000 * LLM_PRICE_OUTPUT_CNY_PER_MTOKEN)


_PARSE_CACHE: list[dict] = []


def _parse_cache() -> dict:
    """只读复用主流水线的解析缓存 ``data/parsed_cache_real.json``。

    **绝不回写**——那份缓存属于 run_pipeline，键是 JD 正文 hash；这里命中就白捡一次
    抽取结果，命不中就走 LLM 或纯规则。
    """
    if _PARSE_CACHE:
        return _PARSE_CACHE[0]
    path = Path(__file__).resolve().parents[2] / "data" / "parsed_cache_real.json"
    data: dict = {}
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    _PARSE_CACHE.append(data)
    return data


def _cache_skills(raw_text: str) -> list[str] | None:
    cache = _parse_cache()
    if not cache:
        return None
    hit = cache.get(cleaning.exact_hash(raw_text))
    if not isinstance(hit, dict):
        return None
    names: list[str] = []
    for key in ("required_skills", "bonus_skills"):
        for item in hit.get(key) or []:
            name = (item or {}).get("name") if isinstance(item, dict) else item
            if name:
                names.append(str(name))
    return names or None


def _llm_batch_extract(batch: list[dict], budget: dict) -> dict[int, list[str]]:
    """一次调用抽取一批行的技能。任何异常都降级为空结果，绝不让整轮作业失败。"""
    client = _llm_client()
    if client is None:
        return {}
    blocks = "\n".join(
        f"### {i}\n岗位：{row['title']}\n描述：{row['body'][:LLM_BODY_CHARS]}\n"
        for i, row in enumerate(batch))
    prompt = _LLM_TMPL.format(blocks=blocks)

    # 预算闸：调用**之前**估价，估到会超就不打这一枪。中文约 1.5 字/token。
    est_prompt = int(len(prompt) / 1.5) + 120
    est_completion = 45 * len(batch)
    if budget["cost"] + _cost_cny(est_prompt, est_completion) > budget["limit"]:
        budget["budget_hit"] = True
        return {}
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "system", "content": _LLM_SYS},
                      {"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=LLM_MAX_TOKENS,
            response_format={"type": "json_object"})
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) or est_prompt
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) or len(content) // 2
    except Exception as exc:  # noqa: BLE001
        budget["errors"] += 1
        budget["last_error"] = str(exc)[:200]
        return {}

    budget["calls"] += 1
    budget["prompt_tokens"] += prompt_tokens
    budget["completion_tokens"] += completion_tokens
    budget["cost"] += _cost_cny(prompt_tokens, completion_tokens)
    if budget["cost"] >= budget["limit"]:
        budget["budget_hit"] = True

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        try:
            data = json.loads(content[start:end + 1]) if start >= 0 < end else {}
        except json.JSONDecodeError:
            return {}
    out: dict[int, list[str]] = {}
    for item in (data or {}).get("items", []) or []:
        try:
            idx = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        skills = [str(s) for s in (item.get("skills") or []) if str(s).strip()]
        if 0 <= idx < len(batch) and skills:
            out[idx] = skills
    return out


# ---------------------------------------------------------------- 阶段日志

def _stage_entry(key: str, order: int, started: float, *, in_count: int,
                 out_count: int, dropped: dict[str, int] | None = None,
                 samples: Iterable[str] | None = None, detail: str = "") -> dict:
    return {
        "key": key, "label": STAGE_LABELS[key], "order": order,
        "in_count": in_count, "out_count": out_count,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "dropped": {k: v for k, v in (dropped or {}).items() if v},
        "samples": [str(s)[:40] for s in list(samples or [])[:8]],
        "detail": detail,
    }


# ---------------------------------------------------------------- 主流程

def run_daily_mining(db: Session, *, run_date: str, shard_index: int | None = None,
                     dry_run: bool = True, use_llm: bool = True,
                     rows: int | None = None, force: bool = False,
                     directory: Path | None = None) -> dict:
    """跑一天的挖掘作业，返回该次运行的摘要 dict。

    ``dry_run=True``（默认）时把整条链路真跑一遍——包括建表行、算增量、生成培训计划——
    但最后 ``db.rollback()``，**一行都不落库**。计数与 stage_log 都是在 Python 里显式
    累加的，不依赖提交后回读，所以试运行的漏斗数字与正式运行完全一致。
    """
    rows_per_day = int(rows or settings.mining_rows_per_day)
    source_label = settings.mining_source_label or "BOSS直聘"
    directory = Path(directory) if directory else shard_dir()

    # ---- 幂等：同一天只跑一次
    existing = db.query(models.DailyMiningRun).filter(
        models.DailyMiningRun.run_date == run_date).first()
    if existing is not None:
        if not force:
            if existing.status == "completed":
                return summarize_run(db, existing)
            raise RuntimeError(
                f"{run_date} 已有一条 status={existing.status} 的运行记录，"
                f"用 --force 覆盖前请先确认它没有半途写入公开图谱")
        # 注意：JSON 列里存的 Python None 落库是 **JSON null 而不是 SQL NULL**，
        # 所以 `created_job_skill_ids.isnot(None)` 会命中每一行（JSON_LENGTH 还会把它算成 1）。
        # 判「有没有真的写过公开图谱」必须回到 Python 侧看列表是否非空。
        wrote_public = any(
            bool(js) for (js,) in db.query(
                models.DailyMiningItem.created_job_skill_ids).filter(
                models.DailyMiningItem.run_id == existing.id).all())
        if not existing.dry_run and wrote_public:
            raise RuntimeError(
                f"{run_date} 的运行已向公开图谱写入过行，--force 会让那些行失去回滚凭据。"
                f"请先跑 data/rollback_mining.py --run-date {run_date} --apply")
        db.query(models.DailySkillDelta).filter(
            models.DailySkillDelta.run_id == existing.id).delete(synchronize_session=False)
        db.query(models.DailyMiningItem).filter(
            models.DailyMiningItem.run_id == existing.id).delete(synchronize_session=False)
        db.delete(existing)
        db.flush()

    # ---- 分片选择：显式指定优先，否则接着上一次消费过的分片往下走
    #
    # 取**上一次的 shard_index + 1**，不要拿 cursor_end 反算行号。最后一个分片只有
    # 416 行（row_no 59001–59416），而 `59416 // 1000 == 59`，反算会让第 60 天以后
    # 每天都重新消费 059：run_date 不同所以幂等闸不拦，证据又没有内容级去重，
    # 于是同一批 (岗位, 技能) 上会一直追加 dataset 证据直到撞 12 条封顶，
    # 而漏斗和日间变化悄悄变成一潭死水。任何一个分片末行损坏也会踩同一个坑。
    if shard_index is None:
        prev = db.query(models.DailyMiningRun.shard_index).filter(
            models.DailyMiningRun.status == "completed").order_by(
            models.DailyMiningRun.run_date.desc()).first()
        total = shard_count(directory)
        shard_index = (int(prev[0]) + 1) if prev else 0
        if total and shard_index >= total:
            # 语料跑满一轮（60 个分片）后从头复采：这批的新技能点几乎必然是 0，
            # 证据也会撞 MAX_EVIDENCE_PER_SKILL 封顶，属于良性空转，但必须让它
            # 在日志里看得见——悄悄重采老数据正是本模块要避免的失真。
            print(f"[mining] 分片已跑满一轮（共 {total} 个），游标绕回 000 重新采集；"
                  f"本批预计新增技能点为 0")
            shard_index = 0

    run = models.DailyMiningRun(
        run_date=run_date, status="running", source_label=source_label,
        platform="boss_sim", shard_index=shard_index, dry_run=dry_run,
        started_at=datetime.utcnow(), stage_log=[])
    db.add(run)
    db.flush()

    try:
        summary = _pipeline(db, run, shard_index=shard_index, rows_per_day=rows_per_day,
                            use_llm=use_llm, directory=directory,
                            source_label=source_label, dry_run=dry_run)
    except MiningDataQualityError as exc:
        # 质量闸在任何公开图谱写入之前触发。正式任务保留一条 failed 台账，既让运维
        # 看见问题，也让公开页可以继续选择最近的 completed 批次；试运行仍不留痕。
        if dry_run:
            db.rollback()
        else:
            run.status = "failed"
            run.error = str(exc)[:2000]
            run.finished_at = datetime.utcnow()
            db.commit()
        raise
    except Exception:  # noqa: BLE001
        db.rollback()
        raise

    if dry_run:
        # 试运行：连观测层都不留痕，保证「跑之前 = 跑之后」是结构性成立的
        db.rollback()
    else:
        db.commit()
    return summary


def _pipeline(db: Session, run: models.DailyMiningRun, *, shard_index: int,
              rows_per_day: int, use_llm: bool, directory: Path,
              source_label: str, dry_run: bool) -> dict:
    stages: list[dict] = []
    stop_exact, stop_patterns = load_stopwords(directory)

    # ---------------- 1. 读取
    t0 = time.perf_counter()
    raw_rows = read_shard(shard_index, limit=rows_per_day, directory=directory)
    row_nos = [int((r.get("extra") or {}).get("row_no") or 0) for r in raw_rows]
    row_nos = [n for n in row_nos if n > 0]
    run.cursor_start = min(row_nos) if row_nos else shard_index * rows_per_day + 1
    run.cursor_end = max(row_nos) if row_nos else shard_index * rows_per_day + len(raw_rows)
    run.rows_read = len(raw_rows)
    stages.append(_stage_entry(
        "read", 1, t0, in_count=len(raw_rows), out_count=len(raw_rows),
        samples=[(r.get("job_title") or "") for r in raw_rows[:8]],
        detail=f"分片 {shard_index:03d}，原表行号 {run.cursor_start}–{run.cursor_end}"))

    # 每一行的中间状态；被丢弃的行也全程保留，漏斗才逐条解释得清
    items: list[dict] = []
    for rec in raw_rows:
        extra = rec.get("extra") or {}
        items.append({
            "row_no": extra.get("row_no"),
            "title": (rec.get("job_title") or "").strip(),
            "body": (rec.get("raw_text") or "").strip(),
            "tags": (extra.get("skill_tags") or "").strip(),
            "job_category": (extra.get("job_category") or "") or None,
            "company_domain": (extra.get("company_domain") or "") or None,
            "drop": None, "title_key": None, "job_id": None,
            "skills": [], "used_llm": False,
        })

    # ---------------- 2+3. 结构校验 + 正文长度门（合并为 validate 阶段）
    t0 = time.perf_counter()
    dropped_validate: dict[str, int] = defaultdict(int)
    for it in items:
        if not it["title"] and not it["tags"] and not it["body"]:
            it["drop"] = "字段全空"
        elif len(it["body"]) < MIN_BODY_CHARS and not it["tags"]:
            # 有技能标签、没正文的行仍然可用——标签本身就是抽取对象
            it["drop"] = "正文过短"
        if it["drop"]:
            dropped_validate[it["drop"]] += 1
    valid = [it for it in items if not it["drop"]]
    # 全语料有 28.2% 的行 raw_text 为空，信号全在 skill_tags + job_title 上。
    # 长度门必须「正文过短 **且** 无技能标签」才丢，否则一刀切会静默吃掉四分之一语料；
    # 这批靠标签留下来的行要在漏斗里明着报出来，不能只报丢了多少。
    kept_tag_only = sum(1 for it in valid if len(it["body"]) < MIN_BODY_CHARS)
    run.rows_valid = len(valid)
    stages.append(_stage_entry(
        "validate", 2, t0, in_count=len(items), out_count=len(valid),
        dropped=dropped_validate,
        samples=[it["title"] for it in items if it["drop"]][:8],
        detail=(f"正文 <{MIN_BODY_CHARS} 字**且**无技能标签的行才判无效；"
                f"其中 {kept_tag_only} 行无正文、仅凭技能标签保留")))

    # ---------------- 4. 去重（仅本轮内部）
    # 跨轮不做精确去重是有意的：分片按原表行号切分，互不重叠，每一行在整个语料生命周期里
    # 只会被消费一次，跨轮撞哈希在构造上不可能。而 DailyMiningItem 上没有哈希列（观测层
    # schema 已冻结，本模块不许加列），要跨轮查重就得回读全部历史行重算哈希——为一件构造上
    # 不会发生的事付全表扫描的代价，不值当。
    t0 = time.perf_counter()
    dropped_dedup: dict[str, int] = defaultdict(int)
    seen_hash: set[str] = set()
    simhashes: list[tuple[int, dict]] = []
    for it in valid:
        text = f"{it['title']}\n{it['body']}"
        h = cleaning.exact_hash(text)
        if h in seen_hash:
            it["drop"] = "重复"
            dropped_dedup["重复"] += 1
            continue
        seen_hash.add(h)
        if len(cleaning.normalize_text(text)) >= NEAR_DUP_MIN_CHARS:
            sh = cleaning.simhash(text)
            if any(cleaning.is_near_duplicate(sh, prev, NEAR_DUP_THRESHOLD)
                   for prev, _ in simhashes):
                it["drop"] = "近重复"
                dropped_dedup["近重复"] += 1
                continue
            simhashes.append((sh, it))
    deduped = [it for it in valid if not it["drop"]]
    run.rows_dedup = len(deduped)
    stages.append(_stage_entry(
        "dedup", 3, t0, in_count=len(valid), out_count=len(deduped),
        dropped=dropped_dedup,
        samples=[it["title"] for it in valid if it["drop"] in ("重复", "近重复")][:8],
        detail=f"精确哈希去重 + SimHash 海明距离 ≤{NEAR_DUP_THRESHOLD} 判近重复（仅本轮内）"))

    # ---------------- 5. 岗位归一
    t0 = time.perf_counter()
    try:
        canonical = ingest.canonical_job_names()
        # 提前加载标题关键词表；否则只在某条需要关键词回退的标题出现时才暴露漏发。
        ingest._keyword_cluster_map()
    except RuntimeError as exc:
        run.stage_log = stages
        raise MiningDataQualityError(f"岗位治理配置加载失败，已中止本轮：{exc}") from exc
    job_by_name = {name: jid for name, jid in db.query(
        models.Job.name, models.Job.id).all()}
    dropped_map: dict[str, int] = defaultdict(int)
    mapped: list[dict] = []
    for it in deduped:
        key = ingest.title_key(it["title"]) if it["title"] else ""
        it["title_key"] = key or None
        if key not in canonical:
            it["drop"] = "未命中策展岗位"
            dropped_map["未命中策展岗位"] += 1
            continue
        jid = job_by_name.get(key)
        if jid is None:
            # 岗位名在策展白名单里但库里还没有这个岗位行。本模块不许 INSERT job，
            # 所以只能如实丢弃并记账，等主流水线把岗位建起来。
            it["drop"] = "岗位未建库"
            dropped_map["岗位未建库"] += 1
            continue
        it["job_id"] = jid
        mapped.append(it)
    run.rows_mapped = len(mapped)
    run.rows_dropped = run.rows_read - run.rows_mapped
    hit_jobs = {it["title_key"] for it in mapped}
    stages.append(_stage_entry(
        "map", 4, t0, in_count=len(deduped), out_count=len(mapped),
        dropped=dropped_map, samples=sorted(hit_jobs),
        detail=f"命中 {len(canonical)} 个策展岗位中的 {len(hit_jobs)} 个"))
    if not mapped:
        run.stage_log = stages
        raise MiningDataQualityError(
            f"岗位归一结果为 0（去重后 {len(deduped)} 行、策展岗位 {len(canonical)} 个），"
            "已中止本轮，禁止把缺失输入解释为技能消失")

    # ---------------- 6+7. 规则抽取 + LLM 补缺（合并为 extract 阶段）
    t0 = time.perf_counter()
    for it in mapped:
        it["skills"] = split_skill_tags(it["tags"], stop_exact, stop_patterns)

    budget = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0,
              "limit": float(settings.mining_daily_budget_cny), "budget_hit": False,
              "errors": 0, "last_error": None, "cache_hits": 0}
    need_llm = [it for it in mapped
                if not it["skills"] and len(it["body"]) >= MIN_BODY_CHARS]

    # 先吃主流水线的解析缓存（零成本），剩下的才花钱
    still: list[dict] = []
    for it in need_llm:
        cached = _cache_skills(it["body"])
        if cached:
            it["skills"] = split_skill_tags(
                "、".join(cached), stop_exact, stop_patterns)
            budget["cache_hits"] += 1
        if not it["skills"]:
            still.append(it)

    llm_filled = 0
    if use_llm and still and _llm_client() is not None:
        size = max(1, int(settings.mining_llm_batch_size))
        for start in range(0, len(still), size):
            if budget["budget_hit"]:
                break
            batch = still[start:start + size]
            got = _llm_batch_extract(batch, budget)
            for idx, names in got.items():
                batch[idx]["skills"] = split_skill_tags(
                    "、".join(names), stop_exact, stop_patterns)
                if batch[idx]["skills"]:
                    batch[idx]["used_llm"] = True
                    llm_filled += 1
    elif still and use_llm:
        print("[mining] 未配置挖掘 LLM 密钥，本轮纯规则抽取")

    run.llm_calls = budget["calls"]
    run.llm_prompt_tokens = budget["prompt_tokens"]
    run.llm_completion_tokens = budget["completion_tokens"]
    run.llm_cost_cny = round(budget["cost"], 6)
    run.llm_budget_hit = bool(budget["budget_hit"])

    with_skills = [it for it in mapped if it["skills"]]
    all_names: list[str] = []
    for it in with_skills:
        all_names.extend(it["skills"])
    detail = (f"规则命中 {len(with_skills) - llm_filled} 行、LLM 补 {llm_filled} 行"
              f"（{budget['calls']} 次调用 ¥{budget['cost']:.4f}"
              f"{'，已撞日预算闸' if budget['budget_hit'] else ''}"
              f"{'，缓存命中 %d 行' % budget['cache_hits'] if budget['cache_hits'] else ''}）")
    if budget["errors"]:
        detail += f"；LLM 失败 {budget['errors']} 次，已降级为纯规则"
    # 「无可用技能」原本是一个大杂烩，而它是整条漏斗最大的一处损耗；回放动画要能
    # 讲清楚每一条为什么掉队，就得区分「标签全是噪声词、正文又短到没法送大模型」
    # 和「送了大模型但没抽出东西（含撞预算闸降级）」。
    lost = [it for it in mapped if not it["skills"]]
    lost_short = sum(1 for it in lost if len(it["body"]) < MIN_BODY_CHARS)
    dropped_extract: dict[str, int] = {}
    if lost_short:
        dropped_extract["标签全为噪声词且无正文"] = lost_short
    if len(lost) - lost_short:
        dropped_extract["正文未抽出技能"] = len(lost) - lost_short
    stages.append(_stage_entry(
        "extract", 5, t0, in_count=len(mapped), out_count=len(with_skills),
        dropped=dropped_extract,
        samples=list(dict.fromkeys(all_names))[:8], detail=detail))

    # ---------------- 8. 增量入图（INSERT-only 白名单）
    t0 = time.perf_counter()
    write_stats = _write_increment(db, run, with_skills, source_label=source_label)

    # ---------------- 9+10. 日间对比 + 培训计划
    deltas = _build_deltas(db, run, with_skills)

    run.jobs_touched = len({it["job_id"] for it in with_skills})
    run.skills_created = write_stats["skills_created"]
    run.job_skills_created = write_stats["job_skills_created"]
    run.evidence_created = write_stats["evidence_created"]
    run.new_skill_points = sum(1 for d in deltas if d.delta_type == "new")
    rows_written = write_stats.get("rows_written", len(with_skills))
    stages.append(_stage_entry(
        "write", 6, t0, in_count=len(with_skills), out_count=rows_written,
        dropped=({"技能全部仅观测未入图": len(with_skills) - rows_written}
                 if len(with_skills) > rows_written else None),
        samples=[f"{d.skill_name}({d.delta_type})" for d in deltas[:8]],
        detail=(f"新建技能节点 {run.skills_created}、候选能力关系 "
                f"{run.job_skills_created}、证据 {run.evidence_created}；"
                + (f"另有 {write_stats['skills_observed_only']} 个新技能词未共现到"
                   f"粗粒度概念，仅记观测层未入图；"
                   if write_stats.get("skills_observed_only") else "")
                + f"触达岗位 {run.jobs_touched} 个；"
                + f"日间变化 {len(deltas)} 条（新增 {run.new_skill_points}）"
                + ("；试运行不落库" if dry_run else ""))))

    # ---------------- 逐行台账
    for it in items:
        db.add(models.DailyMiningItem(
            run_id=run.id, source_row_no=it["row_no"], job_id=it["job_id"],
            title_raw=it["title"][:160] or None, title_key=it["title_key"],
            job_category=it["job_category"], company_domain=it["company_domain"],
            skills=it["skills"] or None, used_llm=bool(it["used_llm"]),
            drop_reason=it["drop"],
            created_skill_ids=it.get("created_skill_ids") or None,
            created_job_skill_ids=it.get("created_job_skill_ids") or None,
            created_evidence_ids=it.get("created_evidence_ids") or None))

    run.stage_log = stages
    run.status = "completed"
    run.finished_at = datetime.utcnow()
    db.flush()
    return summarize_run(db, run, deltas=deltas)


# ---------------------------------------------------------------- 入图

def _write_increment(db: Session, run: models.DailyMiningRun, rows: list[dict], *,
                     source_label: str) -> dict:
    """把当日技能观测增量写进 skill / job_skill / evidence —— 只 INSERT。

    三条铁律，靠代码结构保证而不是靠自觉：
    1. 已存在的 job_skill 行**一个字段都不动**（不刷 last_seen、不改 weight/status），
       它今天被观测到这件事只记在 DailyMiningItem / DailySkillDelta 里；
    2. 新建的 job_skill 行 status 恒为 candidate；
    3. 证据只补给「本挖掘源自己拥有」的关系（本轮新建的，或历史证据清一色是本源
       dataset 证据的 candidate 行）。别人的行——尤其是经雇主交叉验证的 active 行——
       一律不碰，免得给外部证据链掺进一份不可核验的匿名语料。
    """
    stats = {"skills_created": 0, "job_skills_created": 0, "evidence_created": 0}
    if not rows:
        return stats

    wanted = {name for it in rows for name in it["skills"]}
    skill_by_name: dict[str, int] = {}
    if wanted:
        for name, sid in db.query(models.Skill.normalized_name, models.Skill.id).filter(
                models.Skill.normalized_name.in_(wanted)).order_by(models.Skill.id).all():
            skill_by_name.setdefault(name, sid)

    # ---- 新技能点的父概念：同一行技能标签里共现的已知粗粒度节点
    #
    # 为什么必须有父节点：见模块头注释——粗粒度 candidate 行在前端一处都不渲染，
    # 建成孤立根节点等于让「技能总数」虚涨而挖掘页宣称的新增技能点在岗位详情页
    # 查无此项。父概念的判据只用语料自带的共现信号（技能需求列本身就是一行里
    # 若干技能词的并列），不调 LLM、不做词形猜测：
    #   「大语言模型、vLLM、KV Cache」 → vLLM / KV Cache 挂到 大语言模型 下
    # 判据不是裸共现次数，而是**互信息**（PMI）加最小支持度。实测裸计数会被高频
    # 节点通吃：Java / C++ / Python 出现在太多行里，于是 338 个新技能点有 33% 挂到
    # 编程语言下面，挂出「电力系统 -> 大语言模型」「营养学 -> LangChain」这种错亲。
    # 错的父概念比没有父概念更坏——分类是逐条显示在页面上的（岗位徽章、技能 chip、
    # 全景图配色），写错等于睁眼说瞎话，而「未入图」至少诚实。
    # PMI = log( c(x,y) * N / (f(x) * f(y)) )：同时出现的次数要显著高于「各自都常见」
    # 所能解释的程度。再叠一条 c >= PARENT_MIN_COOC，挡掉一行里的偶然同格。
    # 两个门都过不了的词一律不建节点，只留观测层。
    coarse: dict[str, tuple[int, str | None]] = {}
    for sid, nname, cat in db.query(
            models.Skill.id, models.Skill.normalized_name,
            models.Skill.category).filter(
            models.Skill.parent_id.is_(None)).order_by(models.Skill.id).all():
        if nname:
            coarse.setdefault(nname, (sid, cat))

    unknown = {n for n in wanted if n not in skill_by_name}
    cooc: dict[str, Counter] = defaultdict(Counter)
    row_freq: Counter = Counter()       # 每个名字出现在多少行里（PMI 的边际频次）
    n_rows = 0
    for it in rows:
        names = set(it["skills"])
        if not names:
            continue
        n_rows += 1
        row_freq.update(names)
        parents = [n for n in names if n in coarse]
        if not parents:
            continue
        for n in names:
            if n in unknown:
                cooc[n].update(parents)

    def _best_parent(name: str) -> str | None:
        """按 PMI 选父概念；没有一个过门就返回 None（该词不入图）。"""
        counts = cooc.get(name)
        if not counts or not n_rows:
            return None
        f_child = row_freq.get(name, 0)
        if not f_child:
            return None
        scored: list[tuple[float, str]] = []
        for parent, c in counts.items():
            f_parent = row_freq.get(parent, 0)
            if c < PARENT_MIN_COOC or not f_parent:
                continue
            pmi = math.log((c * n_rows) / (f_child * f_parent))
            if pmi >= PARENT_MIN_PMI:
                scored.append((pmi, parent))
        if not scored:
            return None
        # 同分按名称排序，保证同一份语料两次跑出同样的树
        return max(scored, key=lambda kv: (kv[0], [-ord(ch) for ch in kv[1]]))[1]

    created_skill_ids: dict[str, int] = {}
    observed_only: set[str] = set()
    for name in sorted(wanted):
        if name in skill_by_name:
            continue
        parent_name = _best_parent(name)
        if parent_name is None:
            # 挂不上任何粗粒度概念：不建节点。它今天被观测到这件事仍完整记在
            # DailyMiningItem.skills 里，并会产出一条 skill_id=NULL 的 DailySkillDelta。
            observed_only.add(name)
            continue
        parent_id, parent_cat = coarse[parent_name]
        skill = models.Skill(name=name, normalized_name=name,
                             # 分类随父概念走（同 hallucination.aggregate_capabilities
                             # 的口径）。taxonomy.skill_category 是「已知技能名 -> 分类」
                             # 的字典，对新词一律返回"其他"，直接用它会把"其他"这个
                             # 残余桶顶成全库最大的一类。
                             category=parent_cat or taxonomy.skill_category(name),
                             skill_type=taxonomy.skill_type(name),
                             parent_id=parent_id)
        db.add(skill)
        db.flush()
        skill_by_name[name] = skill.id
        created_skill_ids[name] = skill.id
        stats["skills_created"] += 1
    stats["skills_observed_only"] = len(observed_only)

    pairs = {(it["job_id"], skill_by_name[n])
             for it in rows for n in it["skills"] if n in skill_by_name}
    job_ids = {j for j, _ in pairs}
    existing_rel: dict[tuple[int, int], models.JobSkill] = {}
    if job_ids:
        for rel in db.query(models.JobSkill).filter(
                models.JobSkill.job_id.in_(job_ids)).all():
            existing_rel[(rel.job_id, rel.skill_id)] = rel

    # 证据现状一次取回：条数用于 MAX_EVIDENCE_PER_SKILL 封顶，来源用于判断这条关系
    # 是不是本挖掘源自己的（决定能不能补证据）
    rel_ids = [rel.id for key, rel in existing_rel.items() if key in pairs]
    ev_count: dict[int, int] = defaultdict(int)
    ev_foreign: set[int] = set()
    if rel_ids:
        for jsid, stype, sname in db.query(
                models.Evidence.job_skill_id, models.Evidence.source_type,
                models.Evidence.source_name).filter(
                models.Evidence.job_skill_id.in_(rel_ids)).all():
            ev_count[jsid] += 1
            if stype != EVIDENCE_SOURCE_TYPE or sname != source_label:
                ev_foreign.add(jsid)

    now = _run_datetime(run.run_date)
    new_rel: dict[tuple[int, int], models.JobSkill] = {}
    for it in rows:
        it.setdefault("created_skill_ids", [])
        it.setdefault("created_job_skill_ids", [])
        it.setdefault("created_evidence_ids", [])
        for name in it["skills"]:
            if name not in skill_by_name:
                continue      # 未挂上父概念，本轮不入图，只留观测层
            sid = skill_by_name[name]
            if name in created_skill_ids and created_skill_ids[name] not in it["created_skill_ids"]:
                # 该技能节点由本轮新建；记在第一条用到它的行上作为回滚凭据
                it["created_skill_ids"].append(created_skill_ids.pop(name))
            key = (it["job_id"], sid)
            rel = existing_rel.get(key) or new_rel.get(key)
            owned = key in new_rel
            if rel is None:
                rel = models.JobSkill(
                    job_id=it["job_id"], skill_id=sid, importance="bonus",
                    weight=NEW_SKILL_WEIGHT, level_required="familiar",
                    confidence=0.0, factors=None,
                    # 独立雇主数为 0：这份语料没有雇主字段，来源独立性无法证明。
                    source_count=0, status="candidate",
                    first_seen=now, last_seen=now)
                db.add(rel)
                db.flush()
                new_rel[key] = rel
                owned = True
                stats["job_skills_created"] += 1
                it["created_job_skill_ids"].append(rel.id)
            elif key in existing_rel:
                # 已存在的关系：一个字段都不改。只有它本来就是本挖掘源自己写出来的
                # candidate 行，才允许继续往上补证据。
                # 「一条证据都没有」不等于「证据清一色是本源的」：ev_foreign 只收
                # 至少有一条外源证据的关系，零证据的行会真空地通过，于是别人建的
                # 空 candidate 行也会被补上一份不可核验的匿名语料证据。要求
                # ev_count > 0，即它确实有本源证据，才算本源拥有。
                owned = (rel.status == "candidate" and rel.id not in ev_foreign
                         and ev_count[rel.id] > 0)
            if not owned:
                continue
            if ev_count[rel.id] >= MAX_EVIDENCE_PER_SKILL:
                continue
            snippet = _snippet(it, name)
            ev = models.Evidence(
                job_skill_id=rel.id, raw_jd_id=None,
                source_type=EVIDENCE_SOURCE_TYPE, source_name=source_label,
                source_url=None, snippet=snippet, weight=EVIDENCE_WEIGHT,
                created_at=now)
            db.add(ev)
            db.flush()
            ev_count[rel.id] += 1
            stats["evidence_created"] += 1
            it["created_evidence_ids"].append(ev.id)
    # write 阶段的 in/out 必须同单位：in 是「有技能的行」，out 也得是行数。
    # 原先 out 记的是 jobs_touched（岗位数），漏斗前端拿 max(0, in-out) 兜底就会
    # 凭空算出「丢弃 250」并标红——单位中途换了，等于在页面上说假话。
    stats["rows_written"] = sum(
        1 for it in rows
        if it.get("created_job_skill_ids") or it.get("created_evidence_ids"))

    return stats


def _snippet(item: dict, skill: str) -> str:
    head = f"[{item.get('company_domain') or '未标注领域'}] {item['title']}｜{skill}｜"
    body = (item["tags"] or item["body"] or "").replace("\n", " ")
    return (head + body)[:SNIPPET_MAX]


def _run_datetime(run_date: str) -> datetime:
    try:
        return datetime.strptime(run_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return datetime.utcnow()


# ---------------------------------------------------------------- 日间对比

def _observations(rows: Iterable[dict]) -> dict[int, dict[str, dict]]:
    """{job_id: {skill_name: {count, industries:set, titles:[...]}}}"""
    obs: dict[int, dict[str, dict]] = defaultdict(dict)
    for it in rows:
        jid = it.get("job_id")
        if not jid:
            continue
        for name in it.get("skills") or []:
            slot = obs[jid].setdefault(name, {"count": 0, "industries": set(), "titles": []})
            slot["count"] += 1
            if it.get("company_domain"):
                slot["industries"].add(it["company_domain"])
            if it.get("title") and len(slot["titles"]) < 3:
                slot["titles"].append(it["title"])
    return obs


def _prev_observations(db: Session, run: models.DailyMiningRun) -> dict[int, dict[str, dict]]:
    prev = db.query(models.DailyMiningRun).filter(
        models.DailyMiningRun.run_date < run.run_date,
        models.DailyMiningRun.status == "completed").order_by(
        models.DailyMiningRun.run_date.desc()).first()
    if prev is None:
        return {}
    rows = [{"job_id": jid, "skills": skills, "company_domain": domain, "title": title}
            for jid, skills, domain, title in db.query(
                models.DailyMiningItem.job_id, models.DailyMiningItem.skills,
                models.DailyMiningItem.company_domain, models.DailyMiningItem.title_raw
            ).filter(models.DailyMiningItem.run_id == prev.id,
                     models.DailyMiningItem.drop_reason.is_(None),
                     models.DailyMiningItem.job_id.isnot(None)).all()]
    return _observations(rows)


def _build_deltas(db: Session, run: models.DailyMiningRun,
                  rows: list[dict]) -> list[models.DailySkillDelta]:
    curr = _observations(rows)
    # 空输入不是「所有技能今天都消失」。即使上游质量闸被未来改动绕过，这里也必须
    # 失败关闭，绝不能拿昨日全集单边生成 vanished。
    if not curr:
        return []
    prev = _prev_observations(db, run)

    names = {n for per_job in list(curr.values()) + list(prev.values()) for n in per_job}
    skill_ids: dict[str, int] = {}
    if names:
        for name, sid in db.query(models.Skill.normalized_name, models.Skill.id).filter(
                models.Skill.normalized_name.in_(names)).order_by(models.Skill.id).all():
            skill_ids.setdefault(name, sid)

    job_ids = set(curr) | set(prev)
    rel_status: dict[tuple[int, int], str] = {}
    if job_ids:
        for jid, sid, status in db.query(
                models.JobSkill.job_id, models.JobSkill.skill_id,
                models.JobSkill.status).filter(models.JobSkill.job_id.in_(job_ids)).all():
            rel_status[(jid, sid)] = status

    deltas: list[models.DailySkillDelta] = []
    new_by_job: dict[int, list[models.DailySkillDelta]] = defaultdict(list)
    for jid in sorted(job_ids):
        today, yesterday = curr.get(jid, {}), prev.get(jid, {})
        for name in sorted(set(today) | set(yesterday)):
            c = today.get(name, {}).get("count", 0)
            p = yesterday.get(name, {}).get("count", 0)
            if name not in yesterday:
                dtype = "new"
            elif name not in today:
                dtype = "vanished"
            elif c - p >= SUPPORT_DELTA_THRESHOLD:
                dtype = "support_up"
            elif p - c >= SUPPORT_DELTA_THRESHOLD:
                dtype = "support_down"
            else:
                continue
            sid = skill_ids.get(name)
            status = rel_status.get((jid, sid)) if sid else None
            src = today.get(name) or yesterday.get(name) or {}
            delta = models.DailySkillDelta(
                run_id=run.id, job_id=jid, skill_id=sid, skill_name=name,
                delta_type=dtype, prev_support=p, curr_support=c,
                # 本模块从不修改已存在关系的 status，所以「昨日状态」除了「昨天还不存在」
                # 这一种情况以外，恒等于今日状态；不额外存历史快照。
                prev_status=None if dtype == "new" else status,
                curr_status=status,
                industry_count=len(today.get(name, {}).get("industries", ())),
                industries=sorted(today.get(name, {}).get("industries", ())) or None,
                sample_titles=list(src.get("titles") or []) or None,
                created_at=datetime.utcnow())
            deltas.append(delta)
            if dtype == "new":
                new_by_job[jid].append(delta)

    _attach_training_plans(db, new_by_job)
    for d in deltas:
        db.add(d)
    db.flush()
    return deltas


def _attach_training_plans(db: Session,
                           new_by_job: dict[int, list[models.DailySkillDelta]]) -> None:
    """给每条 delta_type='new' 生成新人培训顺序（纯拓扑排序，零 LLM）。

    口径：新增技能点 + 它的先修技能（沿 SkillRelation prerequisite 上溯 2 层），
    但**扣掉该岗位 junior 档已覆盖的技能**——新人培训计划里不该再教他入职就该会的东西。
    """
    if not new_by_job:
        return
    all_ids = {d.skill_id for ds in new_by_job.values() for d in ds if d.skill_id}
    if not all_ids:
        return

    # 先修边（from = 先修，to = 目标），沿目标向上追 PREREQ_MAX_DEPTH 层
    prereq: dict[int, set[int]] = defaultdict(set)
    frontier = set(all_ids)
    seen_targets: set[int] = set()
    for _ in range(PREREQ_MAX_DEPTH):
        frontier -= seen_targets
        if not frontier:
            break
        seen_targets |= frontier
        rows = db.query(models.SkillRelation.from_skill_id,
                        models.SkillRelation.to_skill_id).filter(
            models.SkillRelation.relation_type == "prerequisite",
            models.SkillRelation.to_skill_id.in_(frontier)).all()
        nxt: set[int] = set()
        for src, tgt in rows:
            prereq[tgt].add(src)
            nxt.add(src)
        frontier = nxt

    id_pool = set(all_ids) | {s for v in prereq.values() for s in v}
    names = {sid: name for sid, name in db.query(
        models.Skill.id, models.Skill.normalized_name).filter(
        models.Skill.id.in_(id_pool)).all()}

    junior: dict[int, set[int]] = defaultdict(set)
    for jid, sid in db.query(models.JobLevelSkill.job_id,
                             models.JobLevelSkill.skill_id).filter(
            models.JobLevelSkill.job_id.in_(list(new_by_job)),
            models.JobLevelSkill.level == "junior").all():
        junior[jid].add(sid)

    rel_names: dict[str, list[str]] = {}
    for tgt, srcs in prereq.items():
        tname = names.get(tgt)
        if tname:
            rel_names[tname] = sorted(
                {names[s] for s in srcs if s in names and names[s] != tname})

    for jid, deltas in new_by_job.items():
        covered = junior.get(jid, set())
        for d in deltas:
            if not d.skill_id:
                d.training_plan = None
                continue
            missing = [{"name": d.skill_name, "weight": 0.6}]
            for pid in sorted(prereq.get(d.skill_id, ())):
                if pid in covered or pid not in names:
                    continue
                missing.append({"name": names[pid], "weight": PREREQ_PLAN_WEIGHT})
                for gid in sorted(prereq.get(pid, ())):
                    if gid in covered or gid not in names:
                        continue
                    missing.append({"name": names[gid], "weight": PREREQ_PLAN_WEIGHT})
            seen, uniq = set(), []
            for m in missing:
                if m["name"] not in seen:
                    seen.add(m["name"])
                    uniq.append(m)
            d.training_plan = matching.build_learning_path(uniq, rel_names) or None


# ---------------------------------------------------------------- 摘要

def summarize_run(db: Session, run: models.DailyMiningRun,
                  deltas: list[models.DailySkillDelta] | None = None) -> dict:
    if deltas is None:
        deltas = db.query(models.DailySkillDelta).filter(
            models.DailySkillDelta.run_id == run.id).all()
    by_type: dict[str, int] = defaultdict(int)
    for d in deltas:
        by_type[d.delta_type] += 1
    return {
        "run_date": run.run_date, "status": run.status, "dry_run": bool(run.dry_run),
        "source_label": run.source_label, "platform": run.platform,
        "shard_index": run.shard_index,
        "cursor_start": run.cursor_start, "cursor_end": run.cursor_end,
        "rows_read": run.rows_read, "rows_valid": run.rows_valid,
        "rows_dedup": run.rows_dedup, "rows_mapped": run.rows_mapped,
        "rows_dropped": run.rows_dropped,
        "llm_calls": run.llm_calls, "llm_prompt_tokens": run.llm_prompt_tokens,
        "llm_completion_tokens": run.llm_completion_tokens,
        "llm_cost_cny": round(float(run.llm_cost_cny or 0.0), 6),
        "llm_budget_hit": bool(run.llm_budget_hit),
        "jobs_touched": run.jobs_touched, "new_skill_points": run.new_skill_points,
        "skills_created": run.skills_created,
        "job_skills_created": run.job_skills_created,
        "evidence_created": run.evidence_created,
        "delta_counts": dict(by_type), "delta_total": len(deltas),
        "stage_log": run.stage_log or [],
    }
