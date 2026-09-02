"""把竞赛 Excel（35MB xlsx / sharedStrings 解压后 75MB）离线切成小分片 jsonl。

为什么要这个脚本：
    线上服务器只剩 ~786MB 空闲内存、systemd 又把 MemoryMax 卡在 420M，
    在服务器上解析这个 xlsx 必然 OOM（光 sharedStrings.xml 解压就 75MB，
    再加 XML 树和 Python str 对象轻松破 500MB）。
    所以**只在本地跑一次**，把它转成每 1000 行一个的 jsonl 分片，
    服务端逐片流式读取即可，峰值内存和单片大小同量级。

设计约束：
    - 不新增任何依赖（不用 openpyxl / pandas），只用标准库 zipfile + ElementTree.iterparse。
      本脚本永远只在本地跑，因此允许把 sharedStrings 全量读进内存。
    - 单元格必须按 r 属性里的**列字母**定位（"C12" -> "C"）。xlsx 会把空单元格
      整个 <c> 省掉（实测第 3 行就没有 E/F），按下标取值必然错位。
    - 输出字段严格对齐 data/collect/base.py::RECORD_FIELDS，与既有采集台账同格式。
    - company 恒为 null：这份语料本来就没有雇主列，下游雇主交叉验证闸门依赖
      "没有就是没有"这个诚实事实，绝不能凭空编一个。
    - 忠实转换，不做清洗：空文本行也照样保留，清洗是下游的事。

用法（backend/ 下）：
    uv run python -X utf8 data/build_aggregate_shards.py                # 生成分片
    uv run python -X utf8 data/build_aggregate_shards.py --stats --top 300   # 只看词频，不落盘
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.collect.base import RECORD_FIELDS, mask_pii  # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

DEFAULT_SOURCE = Path(__file__).resolve().parents[3] / "竞赛数据-仅限比赛使用.xlsx"
DEFAULT_OUT = Path(__file__).resolve().parent / "aggregate_source"

SHEET_XML = "xl/worksheets/sheet1.xml"
SHARED_XML = "xl/sharedStrings.xml"

# A-F 六列的表头（用于校验，防止换了文件还照旧解析）
EXPECTED_HEADER = ["岗位分类", "岗位名称", "公司领域", "技能需求", "职位描述", "职位要求"]
COL_LETTERS = ["A", "B", "C", "D", "E", "F"]

PLATFORM = "boss_sim"
SOURCE_LABEL = "BOSS直聘"
TIER = "simulated"
AUTHORITY = 0.6
ROWS_PER_SHARD = 1000

# 技能需求列的分隔符与需要剥掉的括号残渣（原始数据里有 "(Hadoop、Hive)" 这种，
# 朴素切分会得到 "Hive)"）
_SPLIT_RE = re.compile(r"[、，,/;；]")
_STRIP_CHARS = "（）()【】[]〔〕「」<>《》 \t\r\n　"

_COL_RE = re.compile(r"^([A-Z]+)")


# ---------------- xlsx 读取 ----------------

def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """整表读入共享字符串（本地专用；<si> 可能由多个富文本 <r><t> 拼成）。"""
    if SHARED_XML not in zf.namelist():
        return []
    out: list[str] = []
    with zf.open(SHARED_XML) as fh:
        for event, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag != f"{NS}si":
                continue
            out.append("".join(t.text or "" for t in elem.iter(f"{NS}t")))
            elem.clear()
    return out


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    """一个 <c> 的文本值。t="s" 走共享串表；inlineStr 走 <is><t>；其余取 <v> 原文。"""
    ctype = cell.get("t")
    if ctype == "s":
        v = cell.find(f"{NS}v")
        if v is None or v.text is None:
            return ""
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ""
    if ctype == "inlineStr":
        is_el = cell.find(f"{NS}is")
        if is_el is None:
            return ""
        return "".join(t.text or "" for t in is_el.iter(f"{NS}t"))
    v = cell.find(f"{NS}v")
    return v.text or "" if v is not None else ""


def iter_rows(zf: zipfile.ZipFile, shared: list[str]):
    """按文档顺序产出 (sheet_row_number, {列字母: 文本})。空单元格/整列缺失都不会错位。"""
    with zf.open(SHEET_XML) as fh:
        for event, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag != f"{NS}row":
                continue
            r_attr = elem.get("r")
            row_no = int(r_attr) if r_attr and r_attr.isdigit() else None
            cells: dict[str, str] = {}
            for c in elem.findall(f"{NS}c"):
                ref = c.get("r") or ""
                m = _COL_RE.match(ref)
                if not m:
                    continue
                cells[m.group(1)] = _cell_text(c, shared)
            yield row_no, cells
            elem.clear()


def open_source(source: Path) -> tuple[zipfile.ZipFile, list[str]]:
    if not source.exists():
        raise SystemExit(f"找不到源文件：{source}")
    zf = zipfile.ZipFile(source)
    names = zf.namelist()
    if SHEET_XML not in names:
        raise SystemExit(f"{source.name} 里没有 {SHEET_XML}，不是预期的单表结构")
    shared = load_shared_strings(zf)
    if not shared:
        raise SystemExit("共享字符串表为空，无法解析（预期该文件所有单元格都是 t=\"s\"）")
    return zf, shared


def check_header(cells: dict[str, str]) -> None:
    got = [(cells.get(letter) or "").strip() for letter in COL_LETTERS]
    if got != EXPECTED_HEADER:
        raise SystemExit(f"表头不符合预期：\n  期望 {EXPECTED_HEADER}\n  实得 {got}")


# ---------------- 模式一：切片落盘 ----------------

def build_shards(source: Path, out_dir: Path, rows_per_shard: int) -> dict:
    zf, shared = open_source(source)
    print(f"共享字符串 {len(shared)} 条；开始解析 {SHEET_XML} ...")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{PLATFORM}_*.jsonl"):
        old.unlink()

    built_at = datetime.now().isoformat(timespec="seconds")
    shards: list[dict] = []
    data_row = 0
    header_seen = False
    drift_warned = False
    fh = None
    shard_rows = 0
    shard_index = -1
    shard_first = 0

    def close_shard():
        nonlocal fh
        if fh is not None:
            fh.close()
            shards.append({"index": shard_index,
                           "file": f"{PLATFORM}_{shard_index:03d}.jsonl",
                           "rows": shard_rows,
                           "first_row_no": shard_first,
                           "last_row_no": shard_first + shard_rows - 1})
            fh = None

    for sheet_row, cells in iter_rows(zf, shared):
        if not header_seen:
            check_header(cells)
            header_seen = True
            continue
        data_row += 1
        if sheet_row is not None and sheet_row - 1 != data_row and not drift_warned:
            print(f"  ! 提示：第 {data_row} 条数据的 sheet 行号是 {sheet_row}（xlsx 省略了空行），"
                  f"row_no 按顺序编号，不跟随 sheet 行号")
            drift_warned = True

        if fh is None or shard_rows >= rows_per_shard:
            close_shard()
            shard_index += 1
            shard_rows = 0
            shard_first = data_row
            fh = open(out_dir / f"{PLATFORM}_{shard_index:03d}.jsonl", "w",
                      encoding="utf-8", newline="\n")

        desc = cells.get("E") or ""
        req = cells.get("F") or ""
        rec = {
            "platform": PLATFORM,
            "company": None,          # 该语料无雇主列——保持诚实的 null，下游闸门依赖这一点
            "job_title": cells.get("B") or "",
            "location": None,
            "salary_range": None,
            "experience_req": None,
            "education_req": None,
            "publish_date": None,
            "url": None,
            "crawled_at": built_at,
            "raw_text": mask_pii(desc + "\n" + req),
            "extra": {"row_no": data_row,
                      "job_category": cells.get("A") or "",
                      "company_domain": cells.get("C") or "",
                      "skill_tags": cells.get("D") or ""},
        }
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        shard_rows += 1
        if data_row % 10000 == 0:
            print(f"  ... {data_row} 行")

    close_shard()
    zf.close()

    manifest = {
        "source_label": SOURCE_LABEL,
        "platform": PLATFORM,
        "tier": TIER,
        "authority": AUTHORITY,
        "source_file": source.name,
        "built_at": built_at,
        "total_rows": data_row,
        "rows_per_shard": rows_per_shard,
        "shards": shards,
        "columns": list(EXPECTED_HEADER),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"完成：{len(shards)} 个分片 / {data_row} 条 -> {out_dir}")
    return manifest


# ---------------- 模式二：统计词频 ----------------

def split_skill_cell(text: str) -> list[str]:
    """技能需求单元格 -> token 列表。切完必须剥掉首尾括号，否则 '(Hadoop、Hive)' 会漏出 'Hive)'。"""
    out = []
    for piece in _SPLIT_RE.split(text or ""):
        tok = piece.strip().strip(_STRIP_CHARS).strip()
        if tok:
            out.append(tok)
    return out


def run_stats(source: Path, top: int) -> None:
    zf, shared = open_source(source)
    counts = Counter()
    nonempty = Counter()
    total = 0
    header_seen = False
    bracket_leak = 0

    for sheet_row, cells in iter_rows(zf, shared):
        if not header_seen:
            check_header(cells)
            header_seen = True
            continue
        total += 1
        for letter, name in zip(COL_LETTERS, EXPECTED_HEADER):
            if (cells.get(letter) or "").strip():
                nonempty[name] += 1
        raw = cells.get("D") or ""
        for piece in _SPLIT_RE.split(raw):
            if piece.strip() != piece.strip().strip(_STRIP_CHARS).strip():
                bracket_leak += 1
        for tok in split_skill_cell(raw):
            counts[tok] += 1
    zf.close()

    print(f"数据行总数: {total}")
    print("各列非空计数:")
    for name in EXPECTED_HEADER:
        print(f"  {name:<8} {nonempty[name]:>7}  ({nonempty[name] / total:.1%})")
    print(f"\n技能需求 token 去重数: {len(counts)}  总出现次数: {sum(counts.values())}")
    print(f"括号残渣被修正的 token 次数: {bracket_leak}（若为 0 说明数据里没有这个坑）")
    print(f"\nTop {top} 技能 token:")
    print(f"{'#':>4}  {'count':>7}  token")
    for i, (tok, n) in enumerate(counts.most_common(top), 1):
        print(f"{i:>4}  {n:>7}  {tok}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="竞赛 Excel -> 按天分片的 jsonl（本地一次性脚本）")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="源 xlsx 路径")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--rows-per-shard", type=int, default=ROWS_PER_SHARD)
    ap.add_argument("--stats", action="store_true", help="只统计词频，不写分片")
    ap.add_argument("--top", type=int, default=300, help="--stats 打印的 token 数")
    args = ap.parse_args()

    if args.stats:
        run_stats(args.source, args.top)
    else:
        build_shards(args.source, args.out, args.rows_per_shard)


if __name__ == "__main__":
    main()
