# -*- coding: utf-8 -*-
"""候选招聘数据源合规探测器（只发极少量请求，不采数据）。

对每个候选源：
  1) 取 robots.txt（原文留存 + 判断 User-agent:* 是否 Disallow 关键路径）
  2) 对候选入口发 1 次 GET，看 HTTP 状态 / 内容类型 / 是否重定向到登录页
输出：data/collect/probe_report.json  +  控制台表格

用法：
  uv run python -X utf8 data/collect/probe_sources.py
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

# name, 入口URL(仅探测), 类型说明
CANDIDATES = [
    # —— 企业官方招聘官网（tier=official, 权威度1.0）——
    ("阿里巴巴", "https://talent.alibaba.com/off-campus-position", "official"),
    ("百度", "https://talent.baidu.com/jobs/list", "official"),
    ("京东", "https://zhaopin.jd.com/web/job/job_info_list", "official"),
    ("小米", "https://hr.xiaomi.com/social", "official"),
    ("华为", "https://career.huawei.com/reccampportal/portal5/social-recruitment.html", "official"),
    ("科大讯飞", "https://campus.iflytek.com/official-pc/delivery", "official"),
    ("商汤科技", "https://www.sensetime.com/cn/join-us", "official"),
    ("海康威视", "https://hr.hikvision.com/social", "official"),
    ("大疆", "https://we.dji.com/zh-CN/position", "official"),
    ("联想", "https://talent.lenovo.com.cn/social", "official"),
    ("中兴通讯", "https://job.zte.com.cn/cms/society/", "official"),
    ("蔚来", "https://careers.nio.com/social", "official"),
    ("小鹏汽车", "https://www.xiaopeng.com/careers.html", "official"),
    ("理想汽车", "https://careers.lixiang.com/social", "official"),
    ("哔哩哔哩", "https://jobs.bilibili.com/social/positions", "official"),
    ("快手", "https://zhaopin.kuaishou.cn/recruit/e/#/social/", "official"),
    ("携程", "https://job.ctrip.com/social", "official"),
    ("用友", "https://app.mokahr.com/social-recruitment/yonyou/", "official"),
    ("金山办公", "https://wps.jobs.feishu.cn/index", "official"),
    ("智谱AI", "https://zhipu.jobs.feishu.cn/index", "official"),
    ("月之暗面", "https://moonshotai.jobs.feishu.cn/index", "official"),
    ("MiniMax", "https://minimax.jobs.feishu.cn/index", "official"),
    ("零一万物", "https://01ai.jobs.feishu.cn/index", "official"),
    ("面壁智能", "https://modelbest.jobs.feishu.cn/index", "official"),
    ("宇树科技", "https://unitree.jobs.feishu.cn/index", "official"),
    ("地平线", "https://horizon.jobs.feishu.cn/index", "official"),
    ("旷视科技", "https://megvii.jobs.feishu.cn/index", "official"),
    # —— 政府 / 公共就业平台（tier=gov, 权威度1.0）——
    ("中国公共招聘网", "https://job.mohrss.gov.cn/", "gov"),
    ("国聘网", "https://www.iguopin.com/", "gov"),
    ("国家大学生就业服务平台", "https://job.ncss.cn/student/jobs/index.html", "gov"),
    ("中国国家人才网", "https://www.newjobs.com.cn/", "gov"),
    ("上海公共招聘新平台", "https://www.12333sh.gov.cn/", "gov"),
    # —— 聚合/垂类平台（tier=aggregator，仅探测，是否启用另判）——
    ("实习僧", "https://www.shixiseng.com/interns", "aggregator"),
    ("牛客网", "https://www.nowcoder.com/jobs/recommend/campus", "aggregator"),
    ("拉勾", "https://www.lagou.com/wn/jobs", "aggregator"),
    ("猎聘", "https://www.liepin.com/zhaopin/", "aggregator"),
    ("智联招聘", "https://sou.zhaopin.com/", "aggregator"),
    ("BOSS直聘", "https://www.zhipin.com/web/geek/job", "aggregator"),
    ("看准网", "https://www.kanzhun.com/", "aggregator"),
]

LOGIN_HINTS = ["登录", "login", "signin", "sign-in", "passport", "captcha", "验证码", "人机验证"]


def parse_robots(text: str) -> dict:
    """粗解析：取 User-agent:* 分组下的 Disallow 规则。"""
    rules, cur, star = [], None, False
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        if ":" not in line:
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


def probe(name: str, url: str, tier: str, client: httpx.Client) -> dict:
    p = urllib.parse.urlsplit(url)
    origin = f"{p.scheme}://{p.netloc}"
    out = {"name": name, "url": url, "tier": tier, "host": p.netloc}

    # 1) robots.txt
    try:
        r = client.get(f"{origin}/robots.txt")
        out["robots_status"] = r.status_code
        if r.status_code == 200 and "text" in r.headers.get("content-type", ""):
            info = parse_robots(r.text)
            out["robots_blocks_all"] = info["blocks_all"]
            out["robots_disallow"] = info["star_disallow"][:12]
            out["robots_raw_len"] = len(r.text)
            path = p.path or "/"
            out["path_disallowed"] = any(
                d and d != "/" and path.startswith(d.rstrip("*")) for d in info["star_disallow"]
            ) or info["blocks_all"]
        else:
            out["robots_blocks_all"] = False
            out["robots_disallow"] = []
            out["path_disallowed"] = False
    except Exception as e:
        out["robots_status"] = f"ERR {type(e).__name__}"
        out["path_disallowed"] = None

    time.sleep(1.0)

    # 2) 入口页
    try:
        r = client.get(url)
        out["entry_status"] = r.status_code
        out["entry_final_url"] = str(r.url)
        out["entry_ctype"] = r.headers.get("content-type", "")[:40]
        body = r.text[:20000] if "text" in out["entry_ctype"] or "json" in out["entry_ctype"] else ""
        out["entry_len"] = len(r.text)
        low = body.lower()
        out["login_hint"] = [h for h in LOGIN_HINTS if h in low or h in body][:4]
        out["redirected"] = str(r.url).rstrip("/") != url.rstrip("/")
    except Exception as e:
        out["entry_status"] = f"ERR {type(e).__name__}"
        out["entry_final_url"] = ""
    return out


def main() -> None:
    results = []
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True, verify=False) as c:
        for name, url, tier in CANDIDATES:
            res = probe(name, url, tier, c)
            results.append(res)
            print(f"{name:<16} robots={res.get('robots_status')} blocksAll={res.get('robots_blocks_all')} "
                  f"pathDisallow={res.get('path_disallowed')} entry={res.get('entry_status')} "
                  f"len={res.get('entry_len', 0)} login={res.get('login_hint')}")
            time.sleep(1.5)

    path = os.path.join(HERE, "probe_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {path}")


if __name__ == "__main__":
    main()
