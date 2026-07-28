# -*- coding: utf-8 -*-
"""第二轮探测：验证「ATS 平台公开职位接口」是否可用（每个 tenant 只发 1 次请求）。

思路：大量 AI/科技公司的招聘官网托管在同一批 ATS（飞书招聘 *.jobs.feishu.cn / Moka /
北森）。若这些平台有统一的公开职位查询接口，写 1 个适配器即可覆盖几十家公司，
且全部属于「企业官方招聘官网主动公开」的最高权威度数据源。

用法：uv run python -X utf8 data/collect/probe_ats.py
"""
from __future__ import annotations
import json
import os
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 (academic-research-crawler)")

FEISHU_TENANTS = ["01ai", "horizon", "moonshotai", "minimax", "zhipu", "megvii",
                  "unitree", "modelbest", "wps", "sensetime", "cambricon"]

FEISHU_PATHS = [
    ("POST", "/api/v1/search/job/posts"),
    ("POST", "/api/v1/job/posts/search"),
    ("GET", "/api/v1/search/job/posts?limit=5&offset=0"),
]


def try_feishu(tenant: str, client: httpx.Client) -> dict:
    base = f"https://{tenant}.jobs.feishu.cn"
    out = {"tenant": tenant, "base": base, "hits": []}
    for method, path in FEISHU_PATHS:
        url = base + path
        body = {"job_category_id_list": [], "keyword": "", "limit": 5, "offset": 0,
                "location_code_list": [], "recruitment_id_list": [], "job_function_id_list": [],
                "subject_id_list": [], "portal_type": 2, "portal_entrance": 1}
        headers = {"website-path": "index", "portal-channel": "career",
                   "Referer": base + "/index", "Origin": base,
                   "Content-Type": "application/json"}
        try:
            if method == "POST":
                r = client.post(url, json=body, headers=headers)
            else:
                r = client.get(url, headers=headers)
            ct = r.headers.get("content-type", "")
            item = {"method": method, "path": path, "status": r.status_code, "ctype": ct[:30],
                    "len": len(r.text)}
            if "json" in ct:
                try:
                    j = r.json()
                    item["keys"] = list(j.keys())[:6]
                    data = j.get("data") or {}
                    if isinstance(data, dict):
                        item["data_keys"] = list(data.keys())[:8]
                        jl = data.get("job_post_list") or data.get("list") or []
                        item["n_jobs"] = len(jl) if isinstance(jl, list) else None
                        if isinstance(jl, list) and jl:
                            item["sample_title"] = jl[0].get("title")
                            item["sample_fields"] = list(jl[0].keys())[:14]
                    item["msg"] = str(j.get("message") or j.get("msg") or "")[:60]
                except Exception as e:
                    item["json_err"] = str(e)[:60]
            out["hits"].append(item)
            if item.get("n_jobs"):
                break
        except Exception as e:
            out["hits"].append({"method": method, "path": path, "err": f"{type(e).__name__}"})
        time.sleep(1.2)
    return out


OTHER_APIS = [
    # (名称, method, url, json_body)
    ("国聘网-职位搜索", "GET", "https://www.iguopin.com/api/job/search?page=1&limit=5", None),
    ("国聘网-api子域", "GET", "https://api.iguopin.com/api/job/list?page=1&limit=5", None),
    ("中国公共招聘网", "GET", "http://job.mohrss.gov.cn/", None),
    ("Moka-示例(用友)", "GET",
     "https://app.mokahr.com/api/outer/jobs?orgId=yonyou&page=1", None),
    ("大疆we.dji", "GET", "https://we.dji.com/api/v1/job/list?page=1&size=5", None),
    ("华为career", "GET",
     "https://career.huawei.com/reccampportal/services/portal/portalpub/getJobList", None),
]


def main() -> None:
    report = {"feishu": [], "others": []}
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True, verify=False) as c:
        for t in FEISHU_TENANTS:
            res = try_feishu(t, c)
            best = max((h.get("n_jobs") or 0) for h in res["hits"]) if res["hits"] else 0
            print(f"[feishu] {t:<12} best_n_jobs={best}  " +
                  "; ".join(f"{h.get('path','')}->{h.get('status', h.get('err'))}"
                            for h in res["hits"]))
            for h in res["hits"]:
                if h.get("sample_title"):
                    print(f"    ↳ 样例职位: {h['sample_title']}  字段={h.get('sample_fields')}")
            report["feishu"].append(res)
            time.sleep(1.5)

        for name, method, url, body in OTHER_APIS:
            try:
                r = c.request(method, url, json=body)
                ct = r.headers.get("content-type", "")
                item = {"name": name, "url": url, "status": r.status_code,
                        "ctype": ct[:30], "len": len(r.text), "preview": r.text[:200]}
            except Exception as e:
                item = {"name": name, "url": url, "err": f"{type(e).__name__}: {e}"[:100]}
            print(f"[other] {name:<18} {item.get('status', item.get('err'))} "
                  f"{item.get('ctype','')} len={item.get('len',0)}")
            report["others"].append(item)
            time.sleep(1.5)

    with open(os.path.join(HERE, "probe_ats_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n报告已写入 probe_ats_report.json")


if __name__ == "__main__":
    main()
