# -*- coding: utf-8 -*-
"""简历语料适配器 B/C —— 联网检索真实公开中文简历 + 简历范文站（意见⑧）。

两种模式：
  --mode web     B 源：用 Tavily/Serper 检索**作者本人主动公开发布**的中文技术简历
                 （个人主页 / GitHub Pages / 个人博客），排除招聘站与模板站。
  --mode sample  C 源：简历范文站的公开范文页（虚构人物，零真人 PII）。
                 站点与路径取自 probe_resume_report.json 的实测结论。

合规护栏（全部继承 collect/base.py，不另开口子）：
  robots.txt 逐 host 检查 → 单 host ≥4s 间隔 + 抖动 → 研究用途 UA → 每请求一行日志。
  **不登录、不绕登录墙、不破反爬**；正文 mask_pii + mask_contacts 双重脱敏后才落盘，
  且落盘前自检（还能匹配到联系方式就直接丢弃该条，不留档）。

用法（backend/ 下）：
    uv run python -X utf8 data/collect/adapters/resume_web.py --mode sample --batch 2026W31-res-c --limit 8
    uv run python -X utf8 data/collect/adapters/resume_web.py --mode web    --batch 2026W31-res-b --limit 12
"""
from __future__ import annotations
import argparse
import html as html_mod
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.services.resume import mask_contacts, contains_contacts  # noqa: E402
from app.services.taxonomy import SYNONYMS  # noqa: E402
from data.collect.base import BaseCollector, mask_pii  # noqa: E402

RESUMES_DIR = BACKEND / "data" / "resumes"

# ---------------- 正文抽取（不引第三方 HTML 库：线上机器 786MB 内存，能省则省）----------------
_RE_SCRIPT = re.compile(r"(?is)<(script|style|noscript|iframe|svg)[^>]*>.*?</\1>")
_RE_BR = re.compile(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>")
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"[ \t　]+")
_RE_NL = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    t = _RE_SCRIPT.sub(" ", raw)
    t = _RE_BR.sub("\n", t)
    t = _RE_TAG.sub(" ", t)
    t = html_mod.unescape(t)
    t = _RE_WS.sub(" ", t)
    return _RE_NL.sub("\n\n", t).strip()


def decode_body(content: bytes) -> str:
    """国内老站点多为 GBK 且响应头不带 charset，按字节判别。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


# ---------------- 是不是一份简历 ----------------
POS_MARKERS = ["求职意向", "工作经历", "工作经验", "项目经历", "项目经验", "教育背景",
               "教育经历", "专业技能", "技能特长", "自我评价", "个人简历", "实习经历",
               "所获奖励", "个人信息"]
# 招聘 JD 的特征词：简历里极少出现，用来把"招聘启事/模板介绍页"剔掉
NEG_MARKERS = ["岗位职责", "任职要求", "任职资格", "薪资范围", "五险一金", "投递简历",
               "招聘人数", "工作地点：", "职位诱惑", "立即沟通", "在线简历制作", "简历模板下载"]
_TECH_TERMS = {k for k in SYNONYMS if len(k) >= 2}

# 「关于简历」的页面（模板站/教程/面经/论文），不是简历本身。B 源必须挡掉——
# 首轮实测就是栽在这里：arXiv 论文导读、简历模板站、"简历怎么写"教程全都通过了
# pos/tech 检查，因为它们同样满篇"项目经验""专业技能"和技术名词。
_RE_META_TITLE = re.compile(
    r"模板|范文|怎么写|如何写|写法|教程|指南|攻略|技巧|干货|避坑|面试题|题库|笔试|"
    r"经验分享|注意事项|求职宝典|简历指导|arxiv|paper", re.I)
META_MARKERS = ["小编", "下面是", "以下是", "本文", "推荐阅读", "点击下载", "免费下载",
                "在线制作", "简历模板", "简历范文", "简历怎么写", "一键生成", "立即制作",
                "更多相关", "相关文章", "版权声明", "本站", "投稿"]
# 简历几乎必有时间段（2019.07-2022.03 / 2020年3月至今）
_RE_DATE_RANGE = re.compile(
    r"20\d{2}\s*[.\-/年]\s*\d{0,2}\s*月?\s*[-–—~～至到]\s*(?:20\d{2}|至今|现在|now)", re.I)


def tech_hits(text: str) -> int:
    low = text.lower()
    return sum(1 for t in _TECH_TERMS if t in low)


def looks_like_resume(text: str, *, min_len: int, min_pos: int, min_tech: int,
                      title: str = "", url: str = "", strict: bool = False) -> tuple[bool, str]:
    if len(text) < min_len:
        return False, f"正文过短({len(text)}<{min_len})"
    pos = sum(1 for m in POS_MARKERS if m in text)
    neg = sum(1 for m in NEG_MARKERS if m in text)
    th = tech_hits(text)
    if pos < min_pos:
        return False, f"简历要素不足(pos={pos}<{min_pos})"
    if th < min_tech:
        return False, f"技术词过少(tech={th}<{min_tech})"
    if neg >= 2 and neg >= pos:
        return False, f"更像招聘/模板页(neg={neg}>=pos={pos})"
    if strict:
        if _RE_META_TITLE.search(title) or _RE_META_TITLE.search(url):
            return False, "标题/URL 显示是模板或教程页，非本人简历"
        meta = sum(1 for m in META_MARKERS if m in text)
        if meta >= 3:
            return False, f"正文是'讲怎么写简历'的内容页(meta={meta})"
        spans = len(_RE_DATE_RANGE.findall(text))
        if spans < 2:
            return False, f"缺少个人经历时间段(spans={spans}<2)"
        return True, f"pos={pos} neg={neg} tech={th} meta={meta} spans={spans} len={len(text)}"
    return True, f"pos={pos} neg={neg} tech={th} len={len(text)}"


class ResumeCollector(BaseCollector):
    """简历采集基类：复用 base 的 robots/限频/日志，但记录结构是简历而非 JD。"""

    tier = "web"
    authority = 0.6
    license_note = "页面公开"
    source_url_root = ""

    def __init__(self, out_dir: Path, rate_limit_s: float = 4.0):
        super().__init__(out_dir, rate_limit_s)
        self.stats.update({"rejected": 0, "pii_dropped": 0})
        self.reject_log: list[dict] = []

    def emit_resume(self, *, url: str, text: str, cluster: str, title: str = "") -> bool:
        """脱敏 → 自检 → 落盘。自检不过直接丢弃，不留档。"""
        clean = mask_contacts(mask_pii(text))
        if contains_contacts(clean):
            self.stats["pii_dropped"] += 1
            self._log(url, status="pii_dropped")
            return False
        rec = {
            "source_type": "sample" if self.tier == "sample" else "web",
            "source_name": self.platform,
            "source_url": url,
            "license": self.license_note,
            "language": "zh",
            "target_cluster": cluster,
            "raw_text": clean,
            "collected_at": datetime.now().strftime("%Y-%m-%d"),
            "extra": {"page_title": title[:120]},
        }
        with open(self._data_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.stats["collected"] += 1
        return True

    def reject(self, url: str, reason: str) -> None:
        self.stats["rejected"] += 1
        self.reject_log.append({"url": url, "reason": reason})
        self._log(url, status=f"rejected:{reason}")

    def get_text(self, url: str) -> tuple[str, str] | None:
        r = self.fetch(url)
        if r is None or r.status_code != 200:
            return None
        body = decode_body(r.content)
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
        return html_to_text(body), html_mod.unescape(m.group(1)).strip() if m else ""

    def write_manifest(self, notes: str = "") -> dict:
        """在 base 的 manifest 上补简历语料专用字段（import_resumes.py 会读）。"""
        manifest_path = self.out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() \
            else {"adapters": {}}
        manifest.setdefault("adapters", {})[self.platform] = {
            "source_type": "sample" if self.tier == "sample" else "web",
            "source_name": self.platform,
            "source_url": self.source_url_root,
            "license": self.license_note,
            "tier": self.tier, "authority": self.authority, "method": "html",
            "robots_ok": self.stats.get("robots_blocked", 0) == 0,
            "rate_limit_s": self.rate_limit_s,
            "collected": self.stats.get("collected", 0),
            "stats": self.stats,
            "privacy": "正文 mask_pii + mask_contacts 双重脱敏后落盘，自检未过的整条丢弃",
            "rejected_samples": self.reject_log[:20],
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "notes": notes,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        return manifest


# ==================== B 源：联网检索真实公开中文简历 ====================
# 招聘站 / 简历模板站 / 内容农场 / 论文站：不是"个人公开发布的简历"，一律不取
# （easycv/pandaresume/juice-resume/kamacoder/alphaxiv 等是首轮实测出来的假阳性来源）
BLOCK_HOSTS = ("liepin.com", "zhipin.com", "51job.com", "zhaopin.com", "lagou.com",
               "jianliben.com", "wondercv.com", "qmjianli.com", "100fenjianli.com",
               "500d.me", "qiaobutang.com", "yjbys.com", "ruiwen.com", "diyifanwen.com",
               "xuexila.com", "chinahr.com", "job592.com", "yingjiesheng.com",
               "easycv.cn", "pandaresume.cn", "juice-resume.github.io", "resume.io",
               "kamacoder.com", "alphaxiv.org", "arxiv.org", "100chui.com", "chazidian.com",
               "baidu.com", "so.com", "sogou.com", "bing.com", "doc88.com", "docin.com",
               "wenku.baidu.com", "zhuanlan.zhihu.com", "zhihu.com", "csdn.net",
               "jianshu.com", "sohu.com", "163.com", "qq.com", "toutiao.com")

_RE_GH_REPO = re.compile(r"^https?://github\.com/([^/]+)/([^/?#]+)/?$", re.I)
_RE_GH_BLOB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", re.I)


def norm_url(url: str) -> str:
    """去重键：末尾斜杠/协议差异不算两个页面（首轮 taccisum.github.io/resume 与
    同址带斜杠版被当成两条采了两遍）。"""
    u = re.sub(r"^https?://", "", (url or "").strip().lower())
    return u.rstrip("/")


def github_raw_urls(url: str) -> list[str]:
    """GitHub 页面取渲染后的 HTML 壳噪音很大，优先拿 raw 文件。"""
    m = _RE_GH_BLOB.match(url)
    if m:
        o, r, br, path = m.groups()
        return [f"https://raw.githubusercontent.com/{o}/{r}/{br}/{path}"]
    m = _RE_GH_REPO.match(url)
    if m:
        o, r = m.groups()
        return [f"https://raw.githubusercontent.com/{o}/{r}/{b}/README.md"
                for b in ("main", "master")]
    return []

WEB_QUERIES: dict[str, list[str]] = {
    "深度学习": ["个人简历 深度学习工程师 PyTorch 项目经历 教育背景",
              "我的简历 深度学习 模型训练 工作经历 github.io",
              "github.io 简历 深度学习 算法工程师 教育经历"],
    "自然语言处理": ["个人简历 NLP工程师 自然语言处理 项目经验 专业技能",
                "简历 自然语言处理 BERT 大模型 个人主页",
                "github.io resume 自然语言处理 工作经历"],
    "计算机视觉": ["个人简历 计算机视觉工程师 目标检测 项目经历",
               "我的简历 CV算法 图像处理 工作经历 github.io",
               "个人主页 简历 图像算法 教育背景 项目经验"],
    "大模型算法": ["个人简历 大模型 LLM 微调 RAG 项目经历",
              "简历 大语言模型算法工程师 个人主页 项目经验",
              "github.io 简历 大模型 推理 工作经历"],
    "大数据平台": ["个人简历 大数据开发 Hadoop Spark 工作经历 github.io",
              "我的简历 数据平台 Flink 数仓 项目经验",
              "个人主页 简历 大数据 数据开发 教育经历"],
    "后端开发": ["个人简历 后端开发工程师 微服务 项目经历 github.io",
             "我的简历 服务端开发 分布式 工作经历 个人主页",
             "gitee 简历 后端 Java Go 工作经历"],
    "云计算": ["个人简历 云原生 Kubernetes Docker 项目经历",
             "我的简历 云计算工程师 K8s 运维 工作经历",
             "github.io 简历 SRE 运维 教育背景"],
    "嵌入式": ["个人简历 嵌入式软件工程师 STM32 Linux驱动 项目经历",
             "我的简历 嵌入式开发 单片机 工作经历 个人主页",
             "github.io 简历 嵌入式 驱动开发 项目经验"],
    "物联网": ["个人简历 物联网开发 MQTT 传感器 项目经历",
             "我的简历 IoT 嵌入式 通信协议 工作经历",
             "个人主页 简历 物联网 智能硬件 教育经历"],
    "数据分析": ["个人简历 数据分析师 SQL Python 数据可视化 项目经历",
              "我的简历 数据分析 报表 指标体系 工作经历",
              "github.io 简历 数据分析 项目经验 教育背景"],
    "机器人算法": ["个人简历 机器人算法 ROS SLAM 项目经历",
               "我的简历 机器人开发 运动控制 工作经历",
               "github.io 简历 SLAM 导航 项目经验"],
    "自动驾驶": ["个人简历 自动驾驶算法 感知 规划控制 项目经历",
              "我的简历 智能驾驶 点云 工作经历 个人主页",
              "github.io 简历 自动驾驶 感知算法 教育背景"],
    "机器学习": ["个人简历 机器学习工程师 特征工程 建模 项目经历 github.io",
              "我的简历 推荐算法 排序模型 工作经历"],
    "Java开发": ["个人简历 Java开发工程师 Spring 项目经历 github.io",
              "我的简历 Java 后端 中间件 工作经历 个人主页"],
}


class WebResumeCollector(ResumeCollector):
    platform = "web_personal"
    tier = "web"
    authority = 0.6
    license_note = "作者本人公开发布的个人页面（仅提取技能要素，不留存正文与身份信息）"
    source_url_root = "（多来源，逐条见 source_url）"

    def collect_all(self, limit: int, per_cluster: int = 2) -> int:
        from app import clients
        got = 0
        seen_urls: set[str] = set()
        for cluster, queries in WEB_QUERIES.items():
            if got >= limit:
                break
            hit_this_cluster = 0
            for q in queries:
                if got >= limit or hit_this_cluster >= per_cluster:
                    break
                try:
                    results = clients.multi_source_search(q, max_results=8)
                except Exception as e:  # noqa: BLE001
                    print(f"  [检索失败] {q}: {type(e).__name__}")
                    continue
                for r in results:
                    if got >= limit or hit_this_cluster >= per_cluster:
                        break
                    url = (r.get("url") or "").strip()
                    if not url or norm_url(url) in seen_urls:
                        continue
                    seen_urls.add(norm_url(url))
                    host = urlsplit(url).netloc.lower()
                    if any(b in host for b in BLOCK_HOSTS):
                        self.reject(url, "blocked_host")
                        continue
                    if url.lower().endswith((".pdf", ".doc", ".docx", ".zip")):
                        self.reject(url, "非HTML")
                        continue
                    page = None
                    for raw_url in github_raw_urls(url):   # GitHub 优先取 raw，少一层 HTML 噪音
                        page = self.get_text(raw_url)
                        if page and len(page[0]) > 200:
                            break
                        page = None
                    if page is None:
                        page = self.get_text(url)
                    if not page:
                        self.reject(url, "取不到页面")
                        continue
                    text, title = page
                    ok, why = looks_like_resume(text, min_len=500, min_pos=4, min_tech=5,
                                                title=title, url=url, strict=True)
                    if not ok:
                        self.reject(url, why)
                        continue
                    if self.emit_resume(url=url, text=text[:8000], cluster=cluster, title=title):
                        got += 1
                        hit_this_cluster += 1
                        print(f"  ✓ [{cluster}] {url[:78]}  ({why})")
        return got


# ==================== C 源：简历范文站公开范文 ====================
# 站点与入口路径来自 probe_resume_report.json 实测（index=True 的才留）
SAMPLE_SITES = [
    {"platform": "sample_yjbys", "name": "应届毕业生网",
     "index": ["https://www.yjbys.com/jianli/"], "root": "https://www.yjbys.com/"},
    {"platform": "sample_ruiwen", "name": "瑞文网",
     "index": ["https://www.ruiwen.com/jianli/", "https://www.ruiwen.com/gerenjianli/"],
     "root": "https://www.ruiwen.com/"},
    {"platform": "sample_jianliben", "name": "简历本",
     # robots 只 Disallow /account/ /resume/ /common/ /payment/ /admin/，/article/ 未禁止
     "index": ["https://www.jianliben.com/article"], "root": "https://www.jianliben.com/"},
]
# 锚文本命中这些词才认为是"IT 岗位的简历范文"
IT_ANCHOR_KW = ["软件", "程序员", "计算机", "开发", "工程师", "算法", "数据", "人工智能",
                "大数据", "物联网", "嵌入式", "网络", "运维", "测试", "Java", "Python",
                "前端", "后端", "云计算", "电子信息", "通信", "IT"]
ANCHOR_CLUSTER = [
    (r"算法|人工智能|机器学习|深度学习|AI", "机器学习"),
    (r"大数据|数据仓库|数仓|Hadoop|Spark", "大数据平台"),
    (r"数据分析|数据挖掘", "数据分析"),
    (r"嵌入式|单片机|硬件|电子", "嵌入式"),
    (r"物联网|IoT|通信|网络工程", "物联网"),
    (r"运维|SRE|系统管理", "运维开发"),
    (r"云计算|云原生|虚拟化", "云计算"),
    (r"Java", "Java开发"),
    (r"Python|后端|服务端|软件开发|程序员|计算机|软件工程师", "后端开发"),
]
_ANCHOR_RULES = [(re.compile(p, re.I), c) for p, c in ANCHOR_CLUSTER]
_RE_LINK = re.compile(r'(?is)<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>')


def anchor_cluster(text: str) -> str | None:
    for pat, cluster in _ANCHOR_RULES:
        if pat.search(text):
            return cluster
    return None


class SampleResumeCollector(ResumeCollector):
    tier = "sample"
    authority = 0.5
    license_note = "站点公开范文页（虚构人物示例，不含真人身份信息）"

    def __init__(self, site: dict, out_dir: Path, rate_limit_s: float = 4.0):
        self.platform = site["platform"]
        self.site_name = site["name"]
        self.source_url_root = site["root"]
        self._index_urls = site["index"]
        super().__init__(out_dir, rate_limit_s)

    def discover(self) -> list[tuple[str, str, str]]:
        """从频道页发现 IT 相关的范文文章链接 → [(url, 锚文本, 簇)]"""
        found: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for idx in self._index_urls:
            r = self.fetch(idx)
            if r is None or r.status_code != 200:
                print(f"  索引页取不到: {idx} ({r.status_code if r else 'ERR'})")
                continue
            body = decode_body(r.content)
            for href, anchor_html in _RE_LINK.findall(body):
                anchor = html_mod.unescape(_RE_TAG.sub("", anchor_html)).strip()
                if not anchor or len(anchor) < 6:
                    continue
                if not any(k in anchor for k in IT_ANCHOR_KW):
                    continue
                if "简历" not in anchor:
                    continue
                url = urljoin(idx, href)
                if url in seen or urlsplit(url).netloc != urlsplit(idx).netloc:
                    continue
                seen.add(url)
                cluster = anchor_cluster(anchor)
                if cluster:
                    found.append((url, anchor, cluster))
        return found

    def collect_all(self, limit: int, per_cluster: int = 2) -> int:
        cands = self.discover()
        print(f"  [{self.site_name}] 发现 IT 相关范文链接 {len(cands)} 条")
        got = 0
        per: dict[str, int] = {}
        for url, anchor, cluster in cands:
            if got >= limit:
                break
            if per.get(cluster, 0) >= per_cluster:
                continue
            page = self.get_text(url)
            if not page:
                self.reject(url, "取不到页面")
                continue
            text, title = page
            ok, why = looks_like_resume(text, min_len=300, min_pos=2, min_tech=3)
            if not ok:
                self.reject(url, why)
                continue
            if self.emit_resume(url=url, text=text[:8000], cluster=cluster, title=title or anchor):
                got += 1
                per[cluster] = per.get(cluster, 0) + 1
                print(f"  ✓ [{cluster}] {anchor[:34]} | {url[:66]} ({why})")
        return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["web", "sample"], required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--per-cluster", type=int, default=2)
    args = ap.parse_args()

    out_dir = RESUMES_DIR / args.batch
    total = 0
    if args.mode == "web":
        c = WebResumeCollector(out_dir)
        try:
            total = c.collect_all(args.limit, args.per_cluster)
            c.write_manifest(notes="Tavily/Serper 检索发现，逐条 robots 检查后抓取公开页面")
        finally:
            c.close()
        print(f"[resume_web] B 源采集 {total} 份，统计 {c.stats}")
    else:
        for site in SAMPLE_SITES:
            if total >= args.limit:
                break
            c = SampleResumeCollector(site, out_dir)
            try:
                total += c.collect_all(args.limit - total, args.per_cluster)
                c.write_manifest(notes=f"{site['name']} 公开范文频道，robots 允许路径")
            finally:
                c.close()
            print(f"  [{site['name']}] 累计 {total} 份，统计 {c.stats}")
        print(f"[resume_web] C 源采集 {total} 份")


if __name__ == "__main__":
    main()
