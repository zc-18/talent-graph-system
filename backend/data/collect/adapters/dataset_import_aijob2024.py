"""公开数据集导入适配器（2024 中间年份切片）：HuggingFace AI_Job_DataSet_1000_list。

为什么单独写一个适配器：该数据集只有 2 列（岗位名 + "技能标签\\tJD正文"），
没有 company/city/salary/experience/education/date 等列，与 2018 的 51job CSV
（13 列结构化表）字段差异过大，无法复用 dataset_import.py 的行解析逻辑。
护栏（PII 打码、queries.json 簇映射、单簇封顶、留存出处）保持一致。

数据集：https://huggingface.co/datasets/RocXuLi/AI_Job_DataSet_1000_list
    许可证：AFL-3.0（Academic Free License 3.0，数据集卡片明确声明）
    语言：zh；规模 1000 条；格式 csv（res.csv）；可直接下载，无需登录
    时间：HF 仓库 createdAt 2024-07-18；正文内 18 条自带"截止日期"落在 2023-2024，
         逐条发布日期原数据集未提供，故统一以数据集发布日 2024-07-18 作为切片时间戳。

原始文件留存于 data/raw/_datasets/hf_aijob2024_RocXuLi_res.csv 作为佐证。

用法（backend/ 下）：
    uv run python -X utf8 data/collect/adapters/dataset_import_aijob2024.py --batch 2024hist-r1

输出：data/raw/<batch>/dataset_aijob2024.jsonl + manifest.json（tier=dataset, authority=0.7）
过滤：仅保留能映射到 queries.json 岗位簇的岗位，JD 正文 ≥100 字，单簇封顶 30、总量封顶 300
     （与 2018hist-r1 同口径，便于跨年份公平比较）。
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from data.collect.base import mask_pii  # noqa: E402

DATASETS_DIR = BACKEND / "data" / "raw" / "_datasets"
QUERIES = json.loads((BACKEND / "data" / "collect" / "queries.json").read_text("utf-8"))["queries"]

DATASET_NAME = "aijob2024"
SRC_FILE = "hf_aijob2024_RocXuLi_res.csv"
SOURCE_URL = "https://huggingface.co/datasets/RocXuLi/AI_Job_DataSet_1000_list"
RAW_FILE_URL = f"{SOURCE_URL}/blob/main/res.csv"
LICENSE = "AFL-3.0（Academic Free License 3.0，数据集卡片声明）；公开可直接下载，无需登录；保留出处"
COLLECT_DATE = "2024-07-18"   # HF 仓库创建日；原数据集无逐条发布日期

def _lat(*tokens: str) -> str:
    """拉丁缩写加词边界：规则整体是 re.I 的，不加边界会误命中单词内部
    （实测 'DSP' 命中 MindSpore 里的 'dSp'、'BEV' 命中普通单词），必须收紧。"""
    return r"(?<![A-Za-z])(?:" + "|".join(tokens) + r")(?![A-Za-z])"


# 岗位簇映射（键必须存在于 queries.json）；按顺序首个命中生效，特异规则在前。
TITLE_CLUSTER_RULES: list[tuple[str, str]] = [
    (r"大模型|预训练语言|" + _lat("LLM", "GPT"), "大模型算法"),
    (r"生成式|文生图|扩散模型|" + _lat("AIGC", "Stable ?Diffusion"), "AIGC"),
    (r"多模态", "多模态"),
    (r"数字人|虚拟人", "数字人"),
    (r"具身智能", "具身智能"),
    (r"推理优化|推理加速|模型部署|模型压缩|" + _lat("TensorRT"), "大模型推理优化"),
    (r"自动驾驶|智能驾驶|泊车|车道线|" + _lat("BEV"), "自动驾驶"),
    (r"机器人|运动控制|路径规划|轨迹规划|导航定位|" + _lat("SLAM", "AGV"), "机器人算法"),
    (r"推荐|搜索排序|广告算法|" + _lat("CTR"), "推荐算法"),
    (r"自然语言|对话系统|文本挖掘|知识图谱|" + _lat("NLP"), "自然语言处理"),
    (r"图像|视觉|机器视觉|点云|CV算法|" + _lat("OCR"), "计算机视觉"),
    (r"深度学习", "深度学习"),
    (r"嵌入式|单片机|" + _lat("MCU", "DSP", "FPGA"), "嵌入式"),
    (r"边缘计算", "边缘计算"),
    (r"物联网|" + _lat("IoT"), "物联网"),
    (r"数据挖掘|机器学习|算法", "机器学习"),      # 通用算法岗兜底归入机器学习簇
]
_RULES = [(re.compile(p, re.I), c) for p, c in TITLE_CLUSTER_RULES]

# 经验/学历原数据集无独立列，从 JD 正文按保守正则回填（仅照抄正文写明的内容，不做推测）
_RE_EXP = re.compile(r"(\d+)\s*年(?:以上|及以上)?(?:的)?(?:相关)?(?:工作|研发|开发|从业|项目)?经[验历]")
_RE_EDU = re.compile(r"(博士|硕士|研究生|本科|大专|专科)(?:及以上|以上)?学历")


def map_cluster(haystack: str) -> str | None:
    for pat, cluster in _RULES:
        if pat.search(haystack or ""):
            return cluster
    return None


def clean_text(t: str) -> str:
    """去 HTML 标签 / 实体 / 多余空白（该数据集约 18% 的正文带 <p> 标记）。"""
    t = str(t or "")
    t = re.sub(r"<(?:br|/?p|/?div|/?span|/?li|/?ul|/?strong)[^>]{0,60}>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]{1,60}>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("\xa0", " "))
    t = re.sub(r"[ \t　]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def derive_experience(text: str) -> str:
    m = _RE_EXP.search(text)
    return f"{m.group(1)}年以上经验" if m else ""


def derive_education(text: str) -> str:
    m = _RE_EDU.search(text)
    return m.group(1) if m else ""


def convert(batch: str, cap: int = 300, per_cluster_cap: int = 30) -> None:
    src = DATASETS_DIR / SRC_FILE
    if not src.exists():
        sys.exit(f"缺少原始数据集文件：{src}（先下载留存）")
    out_dir = BACKEND / "data" / "raw" / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    out_fp = out_dir / f"dataset_{DATASET_NAME}.jsonl"

    kept = seen = 0
    per_cluster: dict[str, int] = {}
    with open(src, encoding="utf-8", errors="replace", newline="") as f, \
            open(out_fp, "w", encoding="utf-8") as w:
        reader = csv.reader(f)
        header = next(reader)
        for i, row in enumerate(reader):
            if kept >= cap:
                break
            d = dict(zip(header, row))
            seen += 1
            title = clean_text(d.get("_c0", ""))
            # text 列格式： "<技能标签,逗号分隔>\t<JD 正文>"，标签可为空
            tags, _, body = str(d.get("text", "")).partition("\t")
            tags, body = clean_text(tags), clean_text(body)
            if len(body) < 100:
                continue
            cluster = map_cluster(f"{title} {tags} {body[:400]}")
            if not cluster:
                continue
            per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
            if per_cluster[cluster] > per_cluster_cap:   # 单簇封顶，保持多样性
                continue
            query_kw = QUERIES[cluster][0]   # import_raw 反查 cluster_hint 用
            rec = {
                "platform": f"dataset:{DATASET_NAME}",
                "company": "",              # 原数据集未提供，不臆造
                "job_title": title,
                "location": "",             # 原数据集未提供
                "salary_range": "",         # 原数据集未提供
                "experience_req": derive_experience(body),
                "education_req": derive_education(body),
                "publish_date": COLLECT_DATE,
                "url": f"{RAW_FILE_URL}#row={i}",
                "crawled_at": datetime.utcnow().strftime("%Y-%m-%d"),
                "raw_text": mask_pii(f"{title}\n{body}"),
                "extra": {
                    "query": query_kw,
                    "dataset": DATASET_NAME,
                    "skill_tags": tags,
                    "fields_derived": ["experience_req", "education_req"],
                    "note": "公开数据集历史切片（2024-07 HuggingFace 发布，AI/算法岗中文 JD）；"
                            "company/location/salary 原数据集缺失故留空；"
                            "经验/学历系从 JD 正文正则回填",
                },
            }
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kept += 1

    manifest = {
        "batch": batch,
        "tier": "dataset",
        "adapters": {f"dataset_{DATASET_NAME}": {
            "tier": "dataset", "authority": 0.7, "rate_limit_s": 0,
            "source_url": RAW_FILE_URL,
            "license": LICENSE,
            "collected": seen, "kept": kept,
            "finished_at": datetime.utcnow().strftime("%Y-%m-%d"),
        }},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"[dataset_import_aijob2024] 读取 {seen} 保留 {kept} → {out_fp}")
    print("[dataset_import_aijob2024] 各簇分布:", json.dumps(
        {k: min(v, per_cluster_cap) for k, v in sorted(per_cluster.items())}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="2024hist-r1")
    ap.add_argument("--cap", type=int, default=300)
    ap.add_argument("--per-cluster-cap", type=int, default=30)
    args = ap.parse_args()
    convert(args.batch, args.cap, args.per_cluster_cap)
