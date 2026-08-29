# -*- coding: utf-8 -*-
"""Build authority_sources_r6.json with VERBATIM excerpts pulled from the archived PDFs.

Every excerpt below is sliced out of the real PDF text (data/authority/*.pdf),
never hand-written, so the registry cannot drift from the archived file.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _pdf_text() -> dict[str, str]:
    """直接从归档 PDF 抽正文；有缓存就用缓存（缓存可随时删）。"""
    cache = HERE / "_pdftext.json"
    if cache.exists():
        return json.load(open(cache, encoding="utf-8"))
    import fitz  # PyMuPDF
    out = {}
    for f in sorted(HERE.glob("mohrss_*.pdf")):
        doc = fitz.open(f)
        out[f.name] = re.sub(r"[ 	]+", " ", "".join(p.get_text() for p in doc))
        doc.close()
    return out


TEXT = _pdf_text()
# 去掉全部空白后再匹配：PDF 抽取的换行/空格位置不稳定，但字序稳定。
BY = {k.split("_")[2]: (k, re.sub(r"\s+", "", v)) for k, v in TEXT.items()}

BASE = "https://zsgx.mohrss.gov.cn/uploads/2024-10-24/"
NOTICE = ("http://www.mohrss.gov.cn/xxgk2020/fdzdgknr/qt/gztz/202110/"
          "t20211008_424969.html")


def grab(std: str, start: str, end: str | None = None, maxlen: int = 420) -> str:
    """Verbatim slice from the archived PDF text; whitespace normalized only."""
    _, txt = BY[std]
    start, end = re.sub(r"\s+", "", start), re.sub(r"\s+", "", end or "")
    i = txt.find(start)
    if i < 0:
        raise SystemExit(f"引文未在 PDF 中找到，拒绝生成：{std} / {start!r}")
    j = txt.find(end, i + len(start)) if end else -1
    seg = txt[i:j] if j > 0 else txt[i:i + maxlen]
    if not seg.strip():
        raise SystemExit(f"引文切片为空，拒绝生成：{std} / {start!r}")
    return seg.strip()


AI = "人工智能工程技术人员国家职业标准"
BD = "大数据工程技术人员国家职业标准"
CC = "云计算工程技术人员国家职业标准"
IOT = "物联网工程技术人员国家职业标准"
IIOT = "工业互联网工程技术人员国家职业标准"

STD_META = {
    AI: dict(occ="人工智能工程技术人员", code="2-02-10-09",
             file="mohrss_2021-09_人工智能工程技术人员国家职业标准_人社部.pdf",
             date="2021-09-29", doc="人社厅发〔2021〕70号"),
    IOT: dict(occ="物联网工程技术人员", code="2-02-10-10",
              file="mohrss_2021-09_物联网工程技术人员国家职业标准_人社部.pdf",
              date="2021-09-29", doc="人社厅发〔2021〕70号"),
    CC: dict(occ="云计算工程技术人员", code="2-02-10-12",
             file="mohrss_2021-09_云计算工程技术人员国家职业标准_人社部.pdf",
             date="2021-09-29", doc="人社厅发〔2021〕70号"),
    IIOT: dict(occ="工业互联网工程技术人员", code="2-02-10-13",
               file="mohrss_2021-09_工业互联网工程技术人员国家职业标准_人社部.pdf",
               date="2021-09-29", doc="人社厅发〔2021〕70号"),
    # 大数据标准属更早一批（智能制造/大数据/区块链），确切颁布日期未取到 -> 留空。
    # confidence_batch.py:134-137 对 publish_date IS NULL 同样判定有效。
    BD: dict(occ="大数据工程技术人员", code="2-02-10-11",
             file="mohrss_2021-03_大数据工程技术人员国家职业标准_人社部.pdf",
             date=None, doc="（2021 年版，颁布文号未取到）"),
}

# job_name -> (standard, excerpt spec, 对应关系说明)
MAP = [
    ("机器学习工程师", AI, ("1.3 职业定义", "1.4 专业技术等级"),
     "标准职业定义直接点名「相关算法、深度学习等多种技术的分析、研究、开发」；全文「机器学习」出现 47 次"),
    ("深度学习工程师", AI, ("1.3 职业定义", "1.4 专业技术等级"),
     "标准职业定义正文出现「深度学习」；全文「深度学习」出现 103 次"),
    ("自然语言处理工程师", AI, ("1.4 专业技术等级", "1.5 职业环境条件"),
     "标准五个职业方向之一即「自然语言及语音处理产品实现」"),
    ("计算机视觉工程师", AI, ("1.4 专业技术等级", "1.5 职业环境条件"),
     "标准五个职业方向之一即「计算机视觉产品实现」；全文「计算机视觉」出现 120 次"),
    ("大数据开发工程师", BD, ("1.3 职业定义", "1.4 专业技术等级"),
     "标准职业定义即大数据采集/清洗/分析/治理/挖掘的工程技术人员，与岗位职责同义"),
    ("数据分析师", BD, ("1.4 专业技术等级", "1.5 职业环境条件"),
     "标准三个职业方向之一即「大数据分析」，职业功能含「大数据分析与挖掘」"),
    ("大数据平台工程师", BD, ("3.1 初级", "职业功能 工作内容"),
     "标准职业功能明列「大数据平台管理与运维」，与岗位名一一对应"),
    ("数据仓库工程师", BD, ("5.2 数据建 模", "5.3 数据预"),
     "标准「数据建模」工作内容的相关知识要求即数据仓库知识/层次建模/维度建模，"
     "是数据仓库工程师的核心能力；但标准未单列「数据仓库」职业方向，属上位职业覆盖"),
    ("云计算工程师", CC, ("1.3 职业定义", "1.4 专业技术等级"),
     "标准职业名与岗位名直接对应"),
    ("运维开发工程师(SRE)", CC, ("1.4 专业技术等级", "1.5 职业环境条件"),
     "标准两个职业方向之一即「云计算运维」，职业功能含「云计算平台运维」；全文「运维」出现 27 次"),
    ("物联网开发工程师", IOT, ("1.3 职业定义", "1.4 专业技术等级"),
     "标准职业名与岗位名直接对应"),
    ("边缘计算工程师", IOT, ("3.1 初级", "3.1.1 物联网嵌入式开发方向"),
     "标准职业功能明列「物联网边缘计算系统应用开发」，并在权重表中单列占比 30%"),
    ("工业互联网工程师", IIOT, ("1.3 职业定义", "1.4 专业技术等级"),
     "标准职业名与岗位名直接对应"),
]

out = {
    "comment": "R6 权威佐证登记表（严格考证版）。每条均为人社部官方域名 zsgx.mohrss.gov.cn "
               "可下载的国家职业技术技能标准 PDF，已本地归档；excerpt 由 build_authority_r6.py "
               "从归档 PDF 正文逐字切片生成，非人工撰写。找不到一手出处的岗位一律不收录。",
    "generated_by": "data/authority/build_authority_r6.py",
    "promulgation_notice": NOTICE,
    "promulgation_notice_local": "data/authority/mohrss_2021-09_颁布7个国家职业标准通知_人社部.html",
    "sources": {},
}
for job, std, (s, e), why in MAP:
    m = STD_META[std]
    out["sources"][job] = {
        "kind": "policy",
        "title": f"《{m['occ']}国家职业技术技能标准》（职业编码 {m['code']}）",
        "issuer": "人力资源社会保障部、工业和信息化部",
        "publish_date": m["date"],
        "url": BASE + f"{m['occ']}国家职业技术技能标准.pdf",
        "excerpt": grab(std, s, e),
        "local_file": f"data/authority/{m['file']}",
        "occupation_code": m["code"],
        "doc_number": m["doc"],
        "mapping_rationale": why,
    }

path = HERE / "authority_sources_r6.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {path.name}: {len(out['sources'])} 岗位")
for k, v in out["sources"].items():
    print(f"  {k:<22} {v['occupation_code']}  excerpt {len(v['excerpt'])} 字")
