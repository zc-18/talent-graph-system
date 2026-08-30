# -*- coding: utf-8 -*-
"""R6 交付文档数字同步器（安全版，默认 dry-run）。

用途
----
影子库验收结束、最终生产口径确定后，把 `_team/r6_final_metrics.json` 里的数字
一次性同步到 7 份 Markdown 交付稿、README 与 PPT 构建脚本。

与两个前辈脚本的关系
--------------------
- `sync_doc_numbers.py`  —— 2026-07-29 冻结快照，**无 dry-run**，跑一次就改文件。
  **任何情况下都不要运行它**（连"看看会改什么"都不行，用 `--audit` 或 `check_state.py`）。
- `sync_doc_numbers_r2.py` —— 有唯一命中断言，但 old/new 是**写死的字符串对**，
  旧 pair 已经全部应用过，今天再跑会因零命中整体退出；而且它只认 `docs/_source/`。

本脚本换了一种模型：pair 表里存的不是 old/new 字符串，而是**带占位符的句子模板**。
`old` 由 BASELINE（= 文档当前写的口径）渲染，`new` 由最终 metrics JSON 渲染。
这样最终数字换几次，pair 表都不用重写；`--apply` 成功后脚本会把 BASELINE 快照
落到 `_team/r6_doc_sync_baseline.json`，供下一轮对照。

安全保证（全部为硬失败，任一不满足则**一个文件都不写**）
--------------------------------------------------------
1. 默认 dry-run，必须显式 `--apply` 才写盘。
2. 目标文件是**显式绝对白名单**，不靠目录推断。
3. 每条 pair 的 `old` 必须在原始文件里**恰好命中 1 次**；0 次或 ≥2 次都算失败。
4. 渲染后的 `new` 若与 `old` 相同（该指标本轮没变），记为 no-op，但**仍然执行第 3 条断言**
   ——这正好当成"文档没被别的 lane 改坏"的守卫。
5. 全部替换先在内存完成；替换后再断言 `new` 在结果文本里恰好出现 1 次。
6. 写盘前重新读取文件并比对 SHA-256，与开始时读到的一致才写（防并发 lane 覆盖）。
7. metrics JSON 缺任何一个被模板引用的键 → 列出缺键后中止。
8. 一致性断言（粗+细=总、采集-重复=有效、分母不超过岗位数、加权均值自洽）不通过 → 中止。

绝不做的事
----------
- 不做裸数字替换。同一个数字在不同语境下含义不同：`166` 既是 SimHash 重复数，
  也是"Java 岗 189 条淘汰候选里的 166 条加分项"；`136` 既是粗粒度技能数，
  也是《改进说明_第三版》里的单元测试用例数。全局替换会把对的改错。
- 不碰任何被标为历史快照的句子（《改进说明_第二版》第四节表、《测试方案与报告》
  §3.6.3/§3.7.2 的事故端点等）。历史数字必须留在原地并保留历史标签。
- 不生成 docx/pptx，不连数据库。生成与对账各是各的步骤。

用法（在 backend/ 目录）
------------------------
    uv run python -X utf8 data/sync_doc_numbers_r6.py            # dry-run，打印将要改什么
    uv run python -X utf8 data/sync_doc_numbers_r6.py --diff     # dry-run + 逐条前后文
    uv run python -X utf8 data/sync_doc_numbers_r6.py --audit    # 只列各指标当前出现位置，不做替换
    uv run python -X utf8 data/sync_doc_numbers_r6.py --apply    # 写盘

配套：`_team/r6_doc_number_map.md` 是人工核对用的「数字 → 位置」全表，
包含本脚本**不覆盖**的手工项（数据源六行、PPT 版式、三个无画像岗位名单）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # talent-graph-system/
WORKSPACE = REPO.parent                             # 挑战杯/
METRICS_JSON = WORKSPACE / "_team" / "r6_final_metrics.json"
BASELINE_SNAPSHOT = WORKSPACE / "_team" / "r6_doc_sync_baseline.json"

# ---------------------------------------------------------------- 目标白名单
# 显式列出，不靠目录推断。键是模板里用的短名。
TARGETS: dict[str, Path] = {
    "方案":   REPO / "docs" / "_source" / "作品设计与实现方案.md",
    "答辩":   REPO / "docs" / "_source" / "技术答辩文档.md",
    "测试":   REPO / "docs" / "_source" / "测试方案与报告.md",
    "脚本":   REPO / "docs" / "_source" / "演示视频脚本.md",
    "部署":   REPO / "docs" / "_source" / "部署说明.md",
    "改二":   REPO / "docs" / "_source" / "改进说明_第二版.md",
    "改三":   REPO / "docs" / "_source" / "改进说明_第三版.md",
    "README": REPO / "README.md",
    "PPT":    REPO / "docs" / "ppt_build" / "build.js",
}

# ------------------------------------------------------- BASELINE（文档当前口径）
# 这是「文档当前写的数字」，不是生产库对账结果。
# 2026-08-30 已同步到影子库 talent_graph_v4_shadow 的最终口径
# （active 860→812 / candidate 3556→3604 / 分级 904→828、29→27 岗 / 均值 0.5356→0.5502）。
# --apply 成功后脚本会把最终 metrics 落成新的 baseline 快照。
BASELINE: dict[str, object] = {
    "jobs": 32, "new_jobs": 6,
    "skills": 3952, "coarse_skills": 136, "fine_skills": 3816,
    "raw_jd": 2570, "dup_jd": 166, "effective_jd": 2404, "inflation_jd": 460,
    "active": 812, "candidate": 3604, "deprecated": 1231,
    "evidence": 9128,
    "authority": 21, "authority_jobs": 19, "authority_new_rows": 8,
    "changes": 621, "change_add": 367, "change_delete": 143, "change_modify": 111,
    "level_rows": 828, "level_jobs": 27, "level_boundary_violations": 0,
    "avg_confidence": 0.5502, "existing_avg": 0.5271, "emerging_avg": 0.6507,
    "employer_identified": 2105, "contract_ready": 6, "as_of": "2026-08-30",
    "skill_relations": 57,
}

# 占位符定界符：正文里既没有 @@ 也没有 <<，用 @@key@@ 不会撞车（脚本会再断言一次）
PH = re.compile(r"@@([a-z_0-9]+)@@")


def derive(m: dict) -> dict:
    """把原始指标扩展成模板里直接可用的字符串。"""
    d = {k: str(v) for k, v in m.items()}
    d["avg4"] = f"{float(m['avg_confidence']):.4f}"
    d["existing4"] = f"{float(m['existing_avg']):.4f}"
    d["emerging4"] = f"{float(m['emerging_avg']):.4f}"   # JSON 常写 0.657，文档统一四位
    d["employer_pct"] = f"{m['employer_identified'] / m['effective_jd'] * 100:.2f}"
    d["contract_pct"] = f"{m['contract_ready'] / m['jobs'] * 100:.2f}"
    d["viol_pct"] = f"{m['level_boundary_violations'] / m['level_rows'] * 100:.1f}"
    d["existing_jobs"] = str(m["jobs"] - m["new_jobs"])
    # 置信度随「时效性」因子每天约 -0.0003 自然衰减，服务器每日 02:30 还会重算，
    # 所以均值在文档里**必须带口径日期**，否则评委隔两周打开线上站就对不上。
    d.setdefault("as_of", str(m.get("as_of", "2026-08-30")))
    return d


# ------------------------------------------------------------------ pair 表
# (目标短名, 模板)。old = 模板⊗BASELINE，new = 模板⊗FINAL。
# 只放**当前口径**的整句/整短语；历史快照句一律不入表。
TEMPLATES: list[tuple[str, str]] = [
    # ---------------- 作品设计与实现方案 ----------------
    ("方案", "当前全流程漏斗为：**采集 @@raw_jd@@ 条 → SimHash 去重后 @@effective_jd@@ 条（重复 @@dup_jd@@ 条）"
             "→ 全部解析 @@effective_jd@@ 条 → 交叉验证确认能力 @@active@@ 条 / 候选能力 @@candidate@@ 条 "
             "→ 沉淀为 @@jobs@@ 个岗位、@@skills@@ 项技能**。"),
    ("方案", "| 人社部文件 / 头部机构报告 | @@authority@@ 条 AuthorityEvidence | authority | 1.0 | "
             "覆盖 @@authority_jobs@@/@@jobs@@ 个岗位；@@new_jobs@@/@@new_jobs@@ 个新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条，"
             "原文快照归档，不计入 JD 语料 |"),
    ("方案", "生产库现有 **@@authority@@ 条 AuthorityEvidence，覆盖 @@authority_jobs@@/@@jobs@@ 个岗位；"
             "@@new_jobs@@/@@new_jobs@@ 个新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条**。"),
    ("方案", "生产库 `job_level_skill` 当前共 **@@level_rows@@ 条，覆盖 @@level_jobs@@/@@jobs@@ 个岗位**，"
             "且 **@@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**越界"),
    ("方案", "**@@jobs@@ 个岗位（新兴 @@new_jobs@@ + 既有 @@existing_jobs@@）、@@skills@@ 项技能"
             "（粗粒度 @@coarse_skills@@ + 细粒度技能点 @@fine_skills@@，两级体系，细粒度节点挂 `parent_id`）、"
             "岗位-能力关系 active @@active@@ / candidate @@candidate@@ / deprecated @@deprecated@@、"
             "@@evidence@@ 条 Evidence（均可回溯到具体 RawJD）、@@authority@@ 条 AuthorityEvidence"
             "（覆盖 @@authority_jobs@@/@@jobs@@ 岗位，其中 @@new_jobs@@/@@new_jobs@@ 新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条）、"
             "@@skill_relations@@ 条技能先修/相关/驱动关系**。"),
    ("方案", "分级画像 **@@level_rows@@ 条、覆盖 @@level_jobs@@/@@jobs@@ 岗，"
             "越界 @@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**；"
             "**@@changes@@ 条真实演化记录**贯穿 2018 → 2024 → 2026 三个时间切片。"
             "证据治理补充指标：有效非重复 JD 的雇主已识别 **@@employer_identified@@/@@effective_jd@@（@@employer_pct@@%）**；"
             "RoleContract 已评估 @@jobs@@ 岗，其中 **@@contract_ready@@ 岗 ready（@@contract_pct@@%）**。"),
    ("方案", "在 **@@raw_jd@@ 条真实生产语料**上，SimHash 查重共检出 **@@dup_jd@@ 条重复**"
             "（去重后有效 JD @@effective_jd@@ 条），能力通胀检测标记 **@@inflation_jd@@ 条** JD。"),
    ("方案", "岗位-能力关系共 **@@active@@ 条 active、@@candidate@@ 条 candidate、@@deprecated@@ 条 deprecated**。"),
    ("方案", "全库岗位置信度均值 **@@avg4@@**（@@as_of@@ 口径）；其中 **@@existing_jobs@@ 个既有岗位均值 @@existing4@@、"
             "@@new_jobs@@ 个新兴岗位均值 @@emerging4@@**（同一口径日期）。"),
    ("方案", "最终为 **@@avg4@@**（@@as_of@@ 口径）（既有岗位 **@@existing4@@** / 新兴岗位 **@@emerging4@@**）。"),
    ("方案", "| 测试 JD 规模 | ≥100 | **对抗基准 379 条 + 真实语料 @@raw_jd@@ 条** | ✅ |"),

    # ---------------- 技术答辩文档 ----------------
    ("答辩", "当前生产库共有 **@@authority@@ 条 AuthorityEvidence，覆盖 @@authority_jobs@@/@@jobs@@ 个岗位；"
             "@@new_jobs@@/@@new_jobs@@ 新兴岗位全部覆盖，其中新兴岗位挂 @@authority_new_rows@@ 条**。"),
    ("答辩", "当前 R6 最终生产均值为 **@@avg4@@**（@@as_of@@ 口径）；@@existing_jobs@@ 个既有岗位均值 **@@existing4@@**，"
             "@@new_jobs@@ 个新兴岗位均值 **@@emerging4@@**（同一口径日期）。"),
    ("答辩", "有效非重复 JD 中雇主已识别 **@@employer_identified@@/@@effective_jd@@（@@employer_pct@@%）**；"
             "RoleContract 已评估 @@jobs@@ 岗，**@@contract_ready@@ 岗 ready（@@contract_pct@@%）**。"),
    ("答辩", "R6 最终 `job_level_skill` 共 **@@level_rows@@ 条，覆盖 @@level_jobs@@/@@jobs@@ 岗**，"
             "越界 **@@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**。"),
    ("答辩", "演化页\"级别演化\"模式对有画像的 @@level_jobs@@ 岗展示初→中→高阶梯视图。"),

    # ---------------- 测试方案与报告 ----------------
    ("测试", "SimHash 查重在该语料上检出 **@@dup_jd@@ 条重复**（去重后有效 @@effective_jd@@ 条），"
             "能力通胀检测标记 **@@inflation_jd@@ 条** JD。"),
    ("测试", "技能 @@skills@@ 项（粗粒度 @@coarse_skills@@ + 细粒度技能点 @@fine_skills@@）、"
             "岗位-能力关系 **active @@active@@ / candidate @@candidate@@ / deprecated @@deprecated@@**、"
             "**@@evidence@@ 条 Evidence（@@evidence@@ 条均可回溯到 RawJD）**，"
             "另有 **@@authority@@ 条 AuthorityEvidence，覆盖 @@authority_jobs@@/@@jobs@@ 岗位；"
             "@@new_jobs@@/@@new_jobs@@ 新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条**。"),
    ("测试", "| 真实语料查重（@@raw_jd@@ 条生产语料） | — | SimHash 检出 @@dup_jd@@ 条重复"
             "（去重后 @@effective_jd@@ 条） | 与人工抽检一致 |"),
    ("测试", "| 真实语料通胀标记（@@raw_jd@@ 条生产语料） | — | @@inflation_jd@@ 条 JD 标记 `inflation_flag` |"),
    ("测试", "| 有效 JD 雇主识别 | @@effective_jd@@ 条有效非重复 JD | "
             "**@@employer_identified@@/@@effective_jd@@（@@employer_pct@@%）**"),
    ("测试", "| RoleContract 治理投影 | @@jobs@@ 岗均评估 | **@@contract_ready@@/@@jobs@@ ready（@@contract_pct@@%）**"),
    ("测试", "R6 最终当前值见 §2：active **@@active@@**、Evidence **@@evidence@@**；"),
    ("测试", "R6 最终当前 Evidence 为 **@@evidence@@ 条，且 @@evidence@@ 条均带 RawJD**。"),
    ("测试", "- **R6 最终结果**：全库岗位置信度均值 **@@avg4@@**（@@as_of@@ 口径），其中既有 @@existing_jobs@@ 岗 **@@existing4@@**、"
             "新兴 @@new_jobs@@ 岗 **@@emerging4@@**（同一口径日期）；"),
    ("测试", "R6 最终当前关系为 **@@active@@ active / @@candidate@@ candidate / @@deprecated@@ deprecated**，"),
    ("测试", "候选总量以 R6 当前 **@@candidate@@ 条 candidate 关系**为准；"),
    ("测试", "R6 最终当前均值见 3.7：全库 @@avg4@@ / 既有 @@existing4@@ / 新兴 @@emerging4@@。"),
    ("测试", "`job_level_skill` 共 **@@level_rows@@ 条，覆盖 @@level_jobs@@/@@jobs@@ 个岗位**；"),
    ("测试", "- **一致性（R6 最终）**：越界 **@@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**，"
             "@@level_rows@@ 条均能在同一岗位 active 能力集中找到对应。"),
    ("测试", "并在 **@@raw_jd@@ 条真实生产语料**上稳定运行（SimHash 检出 @@dup_jd@@ 条重复、"
             "去重后 @@effective_jd@@ 条，通胀标记 @@inflation_jd@@ 条）"),
    ("测试", "R6 最终图谱为 active/candidate/deprecated **@@active@@/@@candidate@@/@@deprecated@@**、"
             "Evidence **@@evidence@@**、AuthorityEvidence **@@authority@@**，"
             "分级画像 **@@level_rows@@ 条覆盖 @@level_jobs@@ 岗且 @@level_boundary_violations@@/@@level_rows@@ 越界**，"
             "全库置信度 **@@avg4@@**。"),
    ("测试", "| 测试 JD 规模 | ≥100 条 | **对抗基准 379 条 + 生产真实语料 @@raw_jd@@ 条** | ✅ 达标 |"),

    # ---------------- 演示视频脚本 ----------------
    ("脚本", "鼠标划过 KPI 卡（岗位 @@jobs@@、技能 @@skills@@、真实 JD @@raw_jd@@、Evidence @@evidence@@）；"),
    ("脚本", "采集 @@raw_jd@@ → SimHash 查重剔 @@dup_jd@@ → 解析 @@effective_jd@@ → "
             "交叉验证确认 @@active@@ / 候选 @@candidate@@ → 入图谱"),
    ("脚本", "补充全库 **@@authority@@ 条 AuthorityEvidence 覆盖 @@authority_jobs@@/@@jobs@@ 岗，"
             "@@new_jobs@@/@@new_jobs@@ 新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条**"),
    ("脚本", "主动说明生产库现有 **@@level_rows@@ 条分级画像、覆盖 @@level_jobs@@/@@jobs@@ 岗，"
             "越界 @@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**；"),
    ("脚本", "@@raw_jd@@ 条真实 JD、去重后有效 @@effective_jd@@ 条，@@jobs@@ 个岗位、@@skills@@ 项技能、"
             "@@evidence@@ 条可回溯 RawJD 的 Evidence、@@authority@@ 条 AuthorityEvidence"
             "（覆盖 @@authority_jobs@@ 个岗位）、@@changes@@ 条真实演化记录；"),
    ("脚本", "R6 最终全库岗位置信度均值 @@avg4@@（@@as_of@@ 口径；既有 @@existing4@@ / 新兴 @@emerging4@@）"),
    ("脚本", "有效 JD 雇主识别 @@employer_identified@@/@@effective_jd@@（@@employer_pct@@%），"
             "RoleContract @@contract_ready@@/@@jobs@@ ready（@@contract_pct@@%）。"),
    ("脚本", "分级画像修到 @@level_rows@@ 条覆盖 @@level_jobs@@ 岗且 @@level_boundary_violations@@/@@level_rows@@ 越界。"),
    ("脚本", "- 关键数字（@@raw_jd@@/@@effective_jd@@/@@jobs@@/@@skills@@/@@active@@/@@candidate@@/@@deprecated@@/"
             "@@evidence@@/@@authority@@/@@changes@@/@@level_rows@@/@@avg4@@/"
             "@@employer_identified@@÷@@effective_jd@@/@@contract_ready@@÷@@jobs@@/98.25% 等）用字幕强调；"),
    ("脚本", "两级技能体系——@@coarse_skills@@ 项粗粒度能力再细分到 @@fine_skills@@ 个**技能点**。"),

    # ---------------- 部署说明 ----------------
    ("部署", "# 初/中/高分级画像（R6 最终：@@level_rows@@ 条，覆盖 @@level_jobs@@/@@jobs@@ 岗，"
             "越界 @@level_boundary_violations@@/@@level_rows@@）"),

    # ---------------- 改进说明_第二版：只改「表前 R6 对照说明句」，表内历史数字不动 ----
    ("改二", "R6 最终当前关系为 **@@active@@/@@candidate@@/@@deprecated@@**，Evidence **@@evidence@@**，"
             "AuthorityEvidence **@@authority@@**（覆盖 @@authority_jobs@@ 岗；"
             "@@new_jobs@@/@@new_jobs@@ 新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条），"
             "分级画像 **@@level_rows@@ 行覆盖 @@level_jobs@@ 岗、"
             "越界 @@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**，"
             "全库均值 **@@avg4@@**（既有 @@existing4@@ / 新兴 @@emerging4@@）。"),
    ("改二", "R6 当前关系为 active @@active@@ / candidate @@candidate@@ / deprecated @@deprecated@@。"),

    # ---------------- 改进说明_第三版 ----------------
    ("改三", "图谱里**已存在**的 @@skills@@ 个技能节点"),

    # ---------------- README ----------------
    ("README", "- **数据规模**：@@jobs@@ 岗位（新兴 @@new_jobs@@）、@@skills@@ 技能"
               "（粗 @@coarse_skills@@ / 细 @@fine_skills@@）、@@raw_jd@@ 条真实 JD，"
               "去重后有效 @@effective_jd@@ 条（重复 @@dup_jd@@）。"),
    ("README", "- **能力与证据**：active/candidate/deprecated = **@@active@@/@@candidate@@/@@deprecated@@**；"
               "Evidence **@@evidence@@**（均带 RawJD）；AuthorityEvidence **@@authority@@**，"
               "覆盖 **@@authority_jobs@@/@@jobs@@ 岗**，@@new_jobs@@/@@new_jobs@@ 新兴岗位全覆盖且共挂 @@authority_new_rows@@ 条。"),
    ("README", "- **演化与分级**：演化 **@@changes@@**（新增 @@change_add@@ / 淘汰 @@change_delete@@ / 修改 @@change_modify@@）；"
               "分级画像 **@@level_rows@@ 条、覆盖 @@level_jobs@@/@@jobs@@ 岗、"
               "越界 @@level_boundary_violations@@/@@level_rows@@（@@viol_pct@@%）**。"),
    ("README", "- **置信与治理**：全库置信度 **@@avg4@@**（@@as_of@@ 口径）（既有 @@existing4@@ / 新兴 @@emerging4@@；R6 前基线 0.4783）。"),
    ("README", "| **98.25%** | **96.49%** | **100%** | **73%** | **对抗基准 379 + 真实语料 @@raw_jd@@** |"),

    # ---------------- PPT build.js ----------------
    ("PPT", "const kpis = [['@@jobs@@', '岗位（新兴@@new_jobs@@）'], ['@@skills@@', '技能（含@@fine_skills@@技能点）'], "
            "['@@raw_jd@@', '真实岗位JD'], ['@@evidence@@', 'JD溯源证据'], ['@@avg4@@', '岗位置信度均值（@@as_of@@ 口径）']]"),
    ("PPT", "'六类分发渠道累计 @@raw_jd@@ 条真实 JD · 有效 @@effective_jd@@ / 重复 @@dup_jd@@ · 原始台账全程可溯源'"),
    ("PPT", "'· SimHash 检出 @@dup_jd@@ 条重复',"),
    ("PPT", "'AuthorityEvidence @@authority@@ 条 · 覆盖 @@authority_jobs@@ / @@jobs@@ 岗 · "
            "@@new_jobs@@ / @@new_jobs@@ 新兴岗位全覆盖（@@authority_new_rows@@ 条）'"),
    ("PPT", "'雇主识别 @@employer_pct@@%（@@employer_identified@@ / @@effective_jd@@，页面显示 88%）"),
    ("PPT", "'RoleContract ready @@contract_ready@@ / @@jobs@@（@@contract_pct@@%）"),
    # PPT 由另一条 lane 同时在改文案，这里只锚定**带数字的短片段**，
    # 不锚定整句，避免对方润色一次就把同步器打脱节。
    ("PPT", "'@@level_rows@@ 行覆盖 @@level_jobs@@ / @@jobs@@ 岗 · 越界 @@level_boundary_violations@@ / @@level_rows@@"),
    ("PPT", "当前 @@level_rows@@ / @@level_rows@@ 均在边界内；"),
    ("PPT", "active @@active@@ / candidate @@candidate@@ / deprecated @@deprecated@@"),
    ("PPT", "全库均值 @@avg4@@（@@as_of@@ 口径"),
    ("PPT", "'2018 历史基线 → 2026 现网真实 JD 驱动演化 · @@changes@@ 条：新增 @@change_add@@ / "
            "删除 @@change_delete@@ / 修改 @@change_modify@@'"),
    ("PPT", "'双轨数据集（对抗基准 379 条 + 真实语料 @@raw_jd@@ 条）· 三项核心指标全部超过 90%'"),
]

# --audit 用：指标 → 精确匹配正则（带边界，避开子串误命中）
AUDIT_PATTERNS = {
    "skills":            lambda d: rf"(?<![\d.]){d['skills']}(?![\d.])",
    "coarse_skills":     lambda d: rf"(?<![\d.]){d['coarse_skills']}(?![\d.])",
    "fine_skills":       lambda d: rf"(?<![\d.]){d['fine_skills']}(?![\d.])",
    "raw_jd":            lambda d: rf"(?<![\d.]){d['raw_jd']}(?![\d.])",
    "dup_jd":            lambda d: rf"(?<![\d.]){d['dup_jd']}(?![\d.])",
    "effective_jd":      lambda d: rf"(?<![\d.]){d['effective_jd']}(?![\d.])",
    "inflation_jd":      lambda d: rf"(?<![\d.]){d['inflation_jd']}(?![\d.])",
    "active":            lambda d: rf"(?<![\d.]){d['active']}(?![\d.])",
    "candidate":         lambda d: rf"(?<![\d.]){d['candidate']}(?![\d.])",
    "deprecated":        lambda d: rf"(?<![\d.]){d['deprecated']}(?![\d.])",
    "evidence":          lambda d: rf"(?<![\d.]){d['evidence']}(?![\d.])",
    "authority":         lambda d: rf"(?<![\d.]){d['authority']}(?![\d.])\s*(?:条|/)",
    "authority_jobs":    lambda d: rf"{d['authority_jobs']}\s*/\s*{d['jobs']}",
    "level_rows":        lambda d: rf"(?<![\d.]){d['level_rows']}(?![\d.])",
    "level_jobs":        lambda d: rf"{d['level_jobs']}\s*/\s*{d['jobs']}",
    "changes":           lambda d: rf"(?<![\d.]){d['changes']}(?![\d.])",
    "avg_confidence":    lambda d: re.escape(d["avg4"]),
    "existing_avg":      lambda d: re.escape(d["existing4"]),
    "emerging_avg":      lambda d: re.escape(d["emerging4"]),
    "employer_identified": lambda d: rf"(?<![\d.]){d['employer_identified']}(?![\d.])",
    "contract_ready":    lambda d: rf"{d['contract_ready']}\s*[/÷]\s*{d['jobs']}",
}

# 同形异义地雷：这些字符串里的数字和某个指标撞号，但含义完全不同，禁止碰
KNOWN_TRAPS = [
    "条是加分项",           # Java 岗 189 条里 166 条是加分项 ≠ SimHash 重复数
    "个 / 覆盖率",          # 《改进说明_第三版》136 个用例 ≠ 粗粒度技能 136
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render(tpl: str, d: dict) -> str:
    missing: set[str] = set()

    def sub(m):
        k = m.group(1)
        if k not in d:
            missing.add(k)
            return m.group(0)
        return d[k]

    out = PH.sub(sub, tpl)
    if missing:
        raise KeyError(", ".join(sorted(missing)))
    return out


# 口径 JSON 里的别名 → 本脚本使用的键名
ALIASES = {"skills_coarse": "coarse_skills", "skills_fine": "fine_skills"}

# 这些是**语料级**指标：只有重新采集/重新清洗才会变，图谱状态重算不影响它们。
# 最终口径 JSON 若没给，就沿用 BASELINE，但必须在终端**显式列出来**让人确认，
# 不能默默带过去——静默继承正是「文档写 0.5613 而生产实测 0.4783」那类漂移的成因。
CORPUS_LEVEL_INHERITABLE = {
    "dup_jd", "effective_jd", "inflation_jd",
    "authority_jobs", "authority_new_rows",
    "change_add", "change_delete", "change_modify",
    "employer_identified", "skill_relations",
}


def load_metrics(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[中止] 找不到最终口径文件：{path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    meta = {k: v for k, v in raw.items() if k.startswith("_")}
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    for src, dst in ALIASES.items():
        if src in raw and dst not in raw:
            raw[dst] = raw.pop(src)

    needed = {k for _, t_ in TEMPLATES for k in PH.findall(t_)}
    derived_keys = {"avg4", "existing4", "emerging4", "employer_pct",
                    "contract_pct", "viol_pct", "existing_jobs"}
    base_needed = (needed - derived_keys) | {
        "avg_confidence", "existing_avg", "emerging_avg",
        "employer_identified", "effective_jd", "contract_ready", "jobs",
        "level_boundary_violations", "level_rows", "new_jobs",
    }
    missing = sorted(base_needed - set(raw))
    inherit = [k for k in missing if k in CORPUS_LEVEL_INHERITABLE]
    hard = [k for k in missing if k not in CORPUS_LEVEL_INHERITABLE]
    if hard:
        sys.exit(
            "[中止] 最终口径 JSON 缺少下列**非语料级**键，无法安全推断，未写任何文件：\n  "
            + "\n  ".join(hard)
            + "\n\n补齐方式见 `_team/r6_doc_number_map.md` 第 4 节。"
        )
    if inherit:
        print("[沿用] 下列语料级指标未出现在最终口径 JSON 中，按 BASELINE 原值带入。")
        print("       如果本轮重新采集过语料，**必须**先把它们补进 JSON 再跑：")
        for k in inherit:
            print(f"         {k} = {BASELINE[k]}")
            raw[k] = BASELINE[k]
        print()
    if "as_of" not in raw:
        measured = meta.get("_meta", {}).get("measured_at", "")
        raw["as_of"] = measured[:10] if len(measured) >= 10 else BASELINE["as_of"]
    if meta.get("_meta", {}).get("superseded"):
        print(f"[口径来源] {meta['_meta'].get('source_database', '?')} "
              f"@ {meta['_meta'].get('measured_at', '?')}")
        print(f"[已作废]   {meta['_meta']['superseded']}")
        print()
    return raw


def check_consistency(m: dict) -> list[str]:
    bad = []
    if m["coarse_skills"] + m["fine_skills"] != m["skills"]:
        bad.append(f"粗+细 != 总：{m['coarse_skills']}+{m['fine_skills']} != {m['skills']}")
    if m["raw_jd"] - m["dup_jd"] != m["effective_jd"]:
        bad.append(f"采集-重复 != 有效：{m['raw_jd']}-{m['dup_jd']} != {m['effective_jd']}")
    for k in ("authority_jobs", "level_jobs", "contract_ready", "new_jobs"):
        if m[k] > m["jobs"]:
            bad.append(f"{k}={m[k]} 超过岗位数 {m['jobs']}")
    if m["employer_identified"] > m["effective_jd"]:
        bad.append("已识别雇主 JD 数超过有效 JD 数")
    if m["level_boundary_violations"] > m["level_rows"]:
        bad.append("越界数超过分级画像总行数")
    ex, nj, j = m["jobs"] - m["new_jobs"], m["new_jobs"], m["jobs"]
    weighted = (ex * float(m["existing_avg"]) + nj * float(m["emerging_avg"])) / j
    if abs(weighted - float(m["avg_confidence"])) > 0.002:
        bad.append(
            f"加权均值不自洽：({ex}×{m['existing_avg']} + {nj}×{m['emerging_avg']})/{j} "
            f"= {weighted:.4f}，但 avg_confidence = {m['avg_confidence']}"
        )
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description="R6 交付文档数字同步器（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正写盘；不加则只预演")
    ap.add_argument("--diff", action="store_true", help="dry-run 时打印每条替换的前后文")
    ap.add_argument("--audit", action="store_true", help="只列出各指标当前出现位置，不做任何替换")
    ap.add_argument("--selftest", action="store_true",
                    help="只用 BASELINE 自校验：确认每条模板在当前文档里恰好命中 1 次，不需要 metrics 文件")
    ap.add_argument("--metrics", type=Path, default=METRICS_JSON,
                    help=f"口径 JSON 路径（默认 {METRICS_JSON}）；仅用于自检/演练，正式同步用默认值")
    args = ap.parse_args()

    for name, p in TARGETS.items():
        if not p.exists():
            sys.exit(f"[中止] 目标文件不存在：{name} -> {p}")

    if args.selftest:
        # 不读 metrics：old 与 new 都用 BASELINE 渲染，只验证「模板 ↔ 文档现状」没有脱节。
        # 别的 lane 改动交付稿措辞后跑一次，就能立刻发现同步器已经对不上，
        # 而不是等到最终数字到手、真要写盘的那一刻才炸。
        bad = []
        for name, tpl in TEMPLATES:
            s = render(tpl, derive(BASELINE))
            n = TARGETS[name].read_text(encoding="utf-8").count(s)
            if n != 1:
                bad.append(f"{name}: 命中 {n} 次（应为 1）→ {s[:78]}…")
        if bad:
            print(f"[自校验失败] {len(bad)}/{len(TEMPLATES)} 条模板与文档现状脱节：")
            for b in bad:
                print("   -", b)
            sys.exit(1)
        print(f"[自校验通过] {len(TEMPLATES)} 条模板全部在当前文档里恰好命中 1 次。")
        return

    final = load_metrics(args.metrics)
    fd = derive(final)
    bd = derive(BASELINE)

    if args.audit:
        print("=== 指标当前出现位置（只读，不改任何文件）===")
        print(f"口径来源：{METRICS_JSON}\n")
        for key, mk in AUDIT_PATTERNS.items():
            pat = re.compile(mk(fd))
            print(f"\n## {key} = {fd.get(key, final.get(key))}")
            for name, p in TARGETS.items():
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if pat.search(line):
                        trap = " ⚠同形异义?" if any(t in line for t in KNOWN_TRAPS) else ""
                        print(f"   {name}:{i}{trap}  {line.strip()[:88]}")
        print("\n提醒：标 ⚠ 的行是已知同形异义陷阱，禁止替换。")
        return

    bad = check_consistency(final)
    if bad:
        print("[中止] 最终口径内部不自洽，未写任何文件：")
        for b in bad:
            print("   -", b)
        sys.exit(1)

    # 读入全部目标，记录原始 hash
    original: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for name, p in TARGETS.items():
        t = p.read_text(encoding="utf-8")
        original[name], hashes[name] = t, sha(t)
        if "@@" in t:
            sys.exit(f"[中止] {name} 含有 @@ 定界符，会与占位符冲突")

    staged = dict(original)
    failures: list[str] = []
    changed, noop = [], 0

    for name, tpl in TEMPLATES:
        try:
            old, new = render(tpl, bd), render(tpl, fd)
        except KeyError as e:
            failures.append(f"{name}: 模板引用了未知键 {e}")
            continue
        n = original[name].count(old)
        if n != 1:
            failures.append(f"{name}: 基线句命中 {n} 次（应为 1）→ {old[:78]}…")
            continue
        if old == new:
            noop += 1
            continue
        staged[name] = staged[name].replace(old, new, 1)
        if staged[name].count(new) != 1:
            failures.append(f"{name}: 替换后新句出现 {staged[name].count(new)} 次（应为 1）")
            continue
        changed.append((name, old, new))

    print(f"pair 总数 {len(TEMPLATES)}｜本轮无变化 {noop}｜待替换 {len(changed)}｜失败 {len(failures)}")

    if failures:
        print("\n[中止] 以下条目未通过唯一命中断言，**一个文件都没写**：")
        for f in failures:
            print("   -", f)
        print("\n可能原因：(a) 最终数字尚未更新到 metrics JSON；"
              "(b) 别的 lane 改动了这些句子的措辞，需要同步更新本脚本的模板；"
              "(c) BASELINE 与文档现状已经脱节。请人工核对后再跑。")
        sys.exit(1)

    if args.diff or not args.apply:
        for name, old, new in changed:
            print(f"\n--- {name}\n- {old[:200]}\n+ {new[:200]}")

    if not args.apply:
        print("\n[dry-run] 未写盘。确认无误后加 --apply。")
        return

    # 写盘前复查文件未被并发改动
    for name, p in TARGETS.items():
        if sha(p.read_text(encoding="utf-8")) != hashes[name]:
            sys.exit(f"[中止] {name} 在本次运行期间被其他进程修改，未写任何文件")

    for name, p in TARGETS.items():
        if staged[name] != original[name]:
            p.write_text(staged[name], encoding="utf-8")
            print(f"[写入] {name}")

    BASELINE_SNAPSHOT.write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成 {len(changed)} 处替换。基线快照已写入 {BASELINE_SNAPSHOT}")
    print("下一步：把本脚本的 BASELINE 常量更新为这份快照，再重出 docx / PPT。")
    print("提醒：数据源六行、PPT 版式、三个无画像岗位名单不在本脚本范围，需人工核对"
          "（见 _team/r6_doc_number_map.md 第 2.11、6 节）。")


if __name__ == "__main__":
    main()
