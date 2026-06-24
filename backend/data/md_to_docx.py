"""将提交用 Markdown 文档转换为 Word(.docx)。

支持：# / ## / ### 标题、表格、- / · 列表、**加粗**、代码块、普通段落、引用。
"""
from __future__ import annotations
import os
import re
import sys
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

DOCS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))
# Markdown 源文件统一放在 docs/_source/，交付目录 docs/ 下只保留生成的 .docx
SRC = os.path.join(DOCS, "_source")
FILES = ["作品设计与实现方案.md", "测试方案与报告.md", "部署说明.md", "演示视频脚本.md", "技术答辩文档.md"]

ACCENT = RGBColor(0x36, 0x52, 0xD9)
INK = RGBColor(0x1E, 0x29, 0x3B)
MUTED = RGBColor(0x55, 0x65, 0x73)


def add_runs(paragraph, text):
    """处理 **加粗** 与 `代码`。"""
    parts = re.split(r"(\*\*.+?\*\*|`.+?`)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = paragraph.add_run(part[2:-2]); r.bold = True
        elif part.startswith("`") and part.endswith("`"):
            r = paragraph.add_run(part[1:-1]); r.font.name = "Consolas"
        else:
            paragraph.add_run(part)


def md_table(doc, rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows if "---" not in r]
    if not cells:
        return
    ncol = len(cells[0])
    t = doc.add_table(rows=len(cells), cols=ncol)
    t.style = "Light Grid Accent 1"
    for i, row in enumerate(cells):
        for j in range(ncol):
            cell = t.rows[i].cells[j]
            cell.text = ""
            para = cell.paragraphs[0]
            add_runs(para, row[j] if j < len(row) else "")
            for run in para.runs:
                run.font.size = Pt(9.5)
                if i == 0:
                    run.bold = True


def convert(path):
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia', "Microsoft YaHei")

    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    i = 0
    in_code = False
    code_buf = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                pc = doc.add_paragraph()
                r = pc.add_run("\n".join(code_buf))
                r.font.name = "Consolas"; r.font.size = Pt(9)
                pc.paragraph_format.left_indent = Inches(0.2)
                code_buf = []; in_code = False
            else:
                in_code = True
            i += 1; continue
        if in_code:
            code_buf.append(line); i += 1; continue

        if line.startswith("# "):
            h = doc.add_heading(line[2:].strip(), level=0)
            for r in h.runs:
                r.font.color.rgb = ACCENT
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=1)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=2)
        elif line.startswith("> "):
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Inches(0.2)
            add_runs(p, line[2:].strip())
            for r in p.runs:
                r.italic = True; r.font.color.rgb = MUTED
        elif line.strip().startswith("|") and "|" in line:
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i]); i += 1
            md_table(doc, tbl)
            continue
        elif re.match(r"^[-*·]\s+", line.strip()) or re.match(r"^\d+\.\s+", line.strip()):
            txt = re.sub(r"^[-*·]\s+", "", line.strip())
            txt = re.sub(r"^\d+\.\s+", "", txt)
            p = doc.add_paragraph(style="List Bullet")
            add_runs(p, txt)
        elif line.strip() == "---":
            pass
        elif line.strip() == "":
            pass
        else:
            p = doc.add_paragraph()
            add_runs(p, line)
        i += 1

    out = os.path.join(DOCS, os.path.splitext(os.path.basename(path))[0] + ".docx")
    doc.save(out)
    print("生成:", os.path.basename(out))


if __name__ == "__main__":
    for fn in FILES:
        convert(os.path.join(SRC, fn))
