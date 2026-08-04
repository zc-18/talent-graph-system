# -*- coding: utf-8 -*-
"""候选**简历语料**源合规探测器（只发极少量请求，不采数据）—— 老师意见⑧。

与 `probe_sources.py`（招聘 JD 源）同思路，但探测对象是简历语料：
  A) 公开数据集（HuggingFace）：查数据集元信息与许可证，确认可直链、非 gated
  B) 简历范文/模板站：取 robots.txt + 对若干候选路径各发 1 次 GET，做路径发现
  C) 检索与代码托管入口：确认 GitHub / HF 直链可达（真实公开中文简历靠检索发现）

输出：data/collect/probe_resume_report.json + 控制台表格

**本脚本一条简历都不采**，只判断"能不能采、该不该采"。

用法（backend/ 下）：
    uv run python -X utf8 data/collect/probe_resume_sources.py
"""
from __future__ import annotations
import json
import os
import time
import urllib.parse

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 (academic-research-crawler; contact: competition-project)")

# —— A) 公开数据集候选（HuggingFace）——
HF_DATASETS = [
    "brackozi/Resume",                      # 首选：MIT，962 行，Category+Resume 全文
    "ahmedheakl/resume-atlas",              # 备选：体量大，正文已小写去标点
    "InferencePrince555/Resume-Dataset",    # 备选：Apache-2.0，指令式
    "Sachinkelenjaguri/Resume_dataset",
    "ganchengguang/resume_seven_class",     # Apache-2.0
]

# —— B) 简历范文/模板站候选（同一 host 给多个候选路径做路径发现）——
SAMPLE_SITES = [
    ("应届毕业生网", ["https://www.yjbys.com/jianli/",
                 "https://www.yjbys.com/jianli/fanwen/",
                 "https://www.yjbys.com/jianli/geren/"]),
    ("第一范文网", ["https://www.diyifanwen.com/jianli/",
                "https://www.diyifanwen.com/qiuzhi/gerenjianli/"]),
    ("瑞文网", ["https://www.ruiwen.com/jianli/",
             "https://www.ruiwen.com/gerenjianli/"]),
    ("学习啦", ["https://www.xuexila.com/qiuzhi/jianli/",
             "https://www.xuexila.com/jianli/"]),
    ("五百丁", ["https://www.500d.me/",
             "https://www.500d.me/moban/",
             "https://www.500d.me/jianli/",
             "https://www.500d.me/article/list/"]),
    ("乔布简历", ["https://cv.qiaobutang.com/",
              "https://cv.qiaobutang.com/sample/"]),
    ("简历本", ["https://www.jianliben.com/",
             "https://www.jianliben.com/fanwen/"]),      # robots 已知 Disallow /resume/
    ("猎聘简历模板", ["https://lpt.liepin.com/"]),
    ("智联简历", ["https://jianli.zhaopin.com/"]),
    ("51job简历", ["https://jianli.51job.com/"]),
]

# —— C) 检索 / 代码托管入口（真实公开中文简历的发现渠道）——
DISCOVERY = [
    ("HuggingFace", "https://huggingface.co/api/datasets?search=resume&limit=1"),
    ("HF datasets-server", "https://datasets-server.huggingface.co/valid"),
    ("GitHub API", "https://api.github.com/rate_limit"),
    ("GitHub raw", "https://raw.githubusercontent.com/geekcompany/ResumeSample/master/README.md"),
    ("GitHub Pages 示例", "https://resume.github.io/"),
]

LOGIN_HINTS = ["登录", "login", "signin", "sign-in", "passport", "captcha", "验证码", "人机验证"]
# 判断一个 200 页面是不是"简历范文/模板索引页"
CONTENT_HINTS = ["简历范文", "简历模板", "个人简历", "求职简历", "工程师简历", "简历下载"]


def decode_body(r: httpx.Response) -> str:
    """按 utf-8 → gb18030 → latin-1 依次解码。

    国内老牌范文站（应届毕业生网/瑞文网等）响应头只写 `text/html` 不带 charset，
    httpx 会默认按 utf-8 解，中文全部解码失败 → 关键词一个都命中不到，
    会把"能用的源"误判成"没内容"。这里按字节自行判别。
    """
    for enc in ("utf-8", "gb18030"):
        try:
            return r.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1", errors="replace")


def parse_robots(text: str) -> dict:
    """粗解析：取 User-agent:* 分组下的 Disallow 规则。"""
    rules, cur, star = [], None, False
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if cur != "ua":
                star = False
            cur = "ua"
            if v == "*":
                star = True
        else:
            cur = "rule"
            if star and k == "disallow":
                rules.append(v)
    return {"star_disallow": rules, "blocks_all": "/" in rules}


def path_blocked(path: str, rules: list[str], blocks_all: bool) -> bool:
    if blocks_all:
        return True
    p = path or "/"
    return any(d and d != "/" and p.startswith(d.rstrip("*")) for d in rules)


def probe_dataset(ds_id: str, c: httpx.Client) -> dict:
    """HF 数据集：元信息（许可证/是否 gated/下载量）+ 首行可读性。"""
    out = {"group": "dataset", "name": ds_id, "url": f"https://huggingface.co/datasets/{ds_id}"}
    try:
        # 注意：数据集 id 里的 "/" 不能 percent-encode，HF 接受的是原样路径
        r = c.get(f"https://huggingface.co/api/datasets/{ds_id}")
        out["meta_status"] = r.status_code
        if r.status_code == 200:
            d = r.json()
            tags = d.get("tags", []) or []
            out["license"] = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), None)
            out["gated"] = d.get("gated", False)
            out["private"] = d.get("private", False)
            out["downloads"] = d.get("downloads", 0)
    except Exception as e:
        out["meta_status"] = f"ERR {type(e).__name__}"

    time.sleep(0.8)
    try:
        r = c.get("https://datasets-server.huggingface.co/first-rows",
                  params={"dataset": ds_id, "config": "default", "split": "train"})
        out["rows_status"] = r.status_code
        if r.status_code == 200:
            d = r.json()
            out["columns"] = [f["name"] for f in d.get("features", [])]
            rows = d.get("rows", [])
            out["sample_len"] = max((len(str(v)) for r0 in rows[:3] for v in r0["row"].values()), default=0)
        else:
            out["rows_error"] = str(r.json().get("error", ""))[:120]
    except Exception as e:
        out["rows_status"] = f"ERR {type(e).__name__}"
    return out


def probe_site(name: str, urls: list[str], c: httpx.Client) -> dict:
    """范文站：host 级 robots + 每个候选路径 1 次 GET（路径发现）。"""
    p0 = urllib.parse.urlsplit(urls[0])
    origin = f"{p0.scheme}://{p0.netloc}"
    out = {"group": "sample", "name": name, "host": p0.netloc, "paths": []}

    rules, blocks_all = [], False
    try:
        r = c.get(f"{origin}/robots.txt")
        out["robots_status"] = r.status_code
        if r.status_code == 200 and "text" in r.headers.get("content-type", ""):
            info = parse_robots(decode_body(r))
            rules, blocks_all = info["star_disallow"], info["blocks_all"]
            out["robots_disallow"] = rules[:14]
            out["robots_blocks_all"] = blocks_all
            out["robots_raw_len"] = len(r.text)
    except Exception as e:
        out["robots_status"] = f"ERR {type(e).__name__}"

    for u in urls:
        time.sleep(1.2)
        path = urllib.parse.urlsplit(u).path or "/"
        rec = {"url": u, "path_disallowed": path_blocked(path, rules, blocks_all)}
        if rec["path_disallowed"]:
            rec["skipped"] = "robots 禁止，未请求"
            out["paths"].append(rec)
            continue
        try:
            r = c.get(u)
            body_full = decode_body(r)
            rec["status"] = r.status_code
            rec["final_url"] = str(r.url)
            rec["len"] = len(body_full)
            rec["charset"] = "utf-8" if body_full == r.content.decode("utf-8", "replace") else "gb18030"
            body = body_full[:60000]
            low = body.lower()
            rec["login_hint"] = [h for h in LOGIN_HINTS if h in low or h in body][:3]
            rec["content_hits"] = {h: body.count(h) for h in CONTENT_HINTS if h in body}
            rec["looks_like_index"] = sum(rec["content_hits"].values()) >= 3
        except Exception as e:
            rec["status"] = f"ERR {type(e).__name__}"
        out["paths"].append(rec)
    return out


def probe_discovery(name: str, url: str, c: httpx.Client) -> dict:
    out = {"group": "discovery", "name": name, "url": url}
    try:
        r = c.get(url)
        out["status"] = r.status_code
        out["len"] = len(r.text)
        out["final_url"] = str(r.url)
    except Exception as e:
        out["status"] = f"ERR {type(e).__name__}"
    return out


def main() -> None:
    results = []
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    with httpx.Client(headers=headers, timeout=25.0, follow_redirects=True, verify=False) as c:
        print("=== A) 公开数据集 ===")
        for ds in HF_DATASETS:
            r = probe_dataset(ds, c)
            results.append(r)
            print(f"{ds:<40} meta={r.get('meta_status')} lic={r.get('license')} "
                  f"gated={r.get('gated')} rows={r.get('rows_status')} cols={r.get('columns')}")
            time.sleep(1.0)

        print("\n=== B) 简历范文/模板站 ===")
        for name, urls in SAMPLE_SITES:
            r = probe_site(name, urls, c)
            results.append(r)
            ok = [p for p in r["paths"] if p.get("looks_like_index")]
            print(f"{name:<14} robots={r.get('robots_status')} 可用索引路径={len(ok)}/{len(r['paths'])}")
            for p in r["paths"]:
                print(f"    {p['url']:<48} {p.get('skipped') or p.get('status')} "
                      f"len={p.get('len', 0)} index={p.get('looks_like_index')} "
                      f"hits={p.get('content_hits')}")
            time.sleep(1.5)

        print("\n=== C) 发现渠道 ===")
        for name, url in DISCOVERY:
            r = probe_discovery(name, url, c)
            results.append(r)
            print(f"{name:<22} {r.get('status')} len={r.get('len', 0)}")
            time.sleep(1.0)

    path = os.path.join(HERE, "probe_resume_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {path}")


if __name__ == "__main__":
    main()
