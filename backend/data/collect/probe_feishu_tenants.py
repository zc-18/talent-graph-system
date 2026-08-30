# -*- coding: utf-8 -*-
"""飞书招聘 SaaS 招聘官网（*.jobs.feishu.cn）租户探测 + 公开职位量统计。

背景：大量 AI / 机器人 / 芯片 / 智能汽车公司把「加入我们」托管在飞书招聘 SaaS 官网，
接口统一且**公开**（无需登录、无需签名）：
    POST {base}/api/v1/csrf/token                        -> 公开发放 csrf token
    POST {base}/api/v1/search/job/posts?...portal_type=6 -> 公开职位列表（含 JD 全文）
属于企业官方主动公开的招聘信息，tier=official、权威度 1.0。

本脚本只判定「租户是否存在 + 公开职位总数 + 公司名」，每租户 ≤3 次请求。
部分租户会 302 到自有域名（如 sensetime → hr.sensetime.com），以最终域名为准。

用法：uv run python -X utf8 data/collect/probe_feishu_tenants.py
"""
from __future__ import annotations
import json
import os
import re
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("TalentGraph-Research/1.0 (university competition research; "
      "collects public job postings only)")

# 候选租户 slug（新一代信息技术领域为主；已由搜索/实测确认的排在前面）
SLUGS = [
    # 已确认存在
    "01ai", "nio", "shengshu", "mobvoi", "sensetime", "xiaopeng", "poizon", "iq",
    "arashivision", "bambulab", "dedao", "ecoflow",
    # 大模型 / AIGC
    "moonshot", "kimi", "minimaxi", "zhipuai", "baichuan", "stepfun", "modelbest",
    "infinigence", "aminer", "lingyiwanwu", "sensecore", "jieyue", "wisemodel",
    # 视觉 / 语音 / 传统 AI
    "megvii", "yitu", "cloudwalk", "4paradigm", "unisound", "aispeech", "deepglint",
    "intellifusion", "malong", "abcpen",
    # 机器人 / 具身智能
    "unitree", "agibot", "galbot", "ubtech", "flexiv", "dreame", "roborock", "narwal",
    "estun", "leju", "orionstar", "keenon", "pudurobotics",
    # 芯片 / 算力 / 云
    "horizon", "cambricon", "mthreads", "birentech", "enflame", "metax", "blacksesame",
    "iluvatar", "eswin", "vastaitech", "moorethreads", "sophgo", "axera",
    # 智能汽车 / 自动驾驶
    "lixiang", "liauto", "pony", "ponyai", "weride", "momenta", "haomo", "zhito",
    "hozonauto", "avatr", "voyah", "im-motors", "leapmotor", "seres",
    # 互联网 / 数据 / 物联网
    "bilibili", "zhihu", "xiaohongshu", "mihoyo", "dewu", "tuya", "quectel", "envision",
    "datacanvas", "kyligence", "pingcap", "zilliz", "oceanbase", "juicefs", "streamnative",
    "insta360", "dji", "hesaitech", "robosense", "innovusion",
    # ---- R6 扩池（2026-08-29，Lane F）：为 T2「补新雇主」补候选租户 ----
    # 智能汽车 / 车联网 / 域控 Tier-1
    "banma", "ecarx", "thundersoft", "desaysv", "hirain", "jingwei", "zeekr",
    "lynkco", "jidu", "aiways", "nezha", "hycan", "arcfox", "deeproute",
    "qcraft", "holomatic", "zongmutech", "minieye", "nullmax", "tusimple",
    "inceptio", "uisee", "neolix", "westwell", "trunk", "freetech", "iflytekauto",
    # 机器人 / 具身智能（新一批）
    "fourier", "robotera", "astribot", "limxdynamics", "engineai", "booster",
    "dobot", "jaka", "geekplus", "syrius", "hairobotics", "deeprobotics",
    "elephantrobotics", "youibot", "siasun", "cloudminds",
    # 智能硬件 / 消费电子 / 可穿戴
    "anker", "ninebot", "xreal", "rokid", "zepp", "amazfit", "goertek",
    "transsion", "yealink", "unisoc", "ingenic", "espressif", "quectelwireless",
    # 多模态 / 大模型（新一批）
    "minimax", "deepseek", "zhipu", "baai", "openbmb", "shengshuai", "vivo",
    "kunlun", "seedream",
    # ---- R7 扩池（2026-08-30，Lane D）：为「补 2024/2025 存量 JD」扩大飞书租户池 ----
    # 大模型 / AIGC / AI 应用（新一批）
    "deeplang", "langboat", "rcrai", "emotibot", "zhuiyi", "xverse", "siliconflow", "hpcaitech",
    "luchentech", "shlab", "pjlab", "bigai", "pixverse", "hidreamai", "lingxin", "westlakemind",
    "xianyuan", "highflyer", "antgroup", "volcengine", "tiamat", "laiye", "datagrand", "aishu",
    # 具身智能 / 机器人（新一批）
    "noetix", "ai2robotics", "tarsai", "xsquarerobot", "galaxea", "spiritai", "quicktron",
    "forwardx", "standardrobots", "seer", "seergroup", "gausium", "ecovacs", "rokae", "aubo",
    "agilerobots", "mechmind", "aqrose", "xyzrobotics", "mogoauto", "hanrobot", "visionnav",
    "yunji", "tinavi",
    # 自动驾驶 / 智能汽车（新一批）
    "idriverplus", "whiterhino", "zelostech", "zvision", "leishen", "semidrive", "siengine",
    "houmo", "motovis", "zhituauto", "joyson", "neusoftreach", "pateo", "archermind", "kotei",
    "deepal", "aion", "gacaion", "xiaomi", "xiaomiev", "catarc", "rising", "lotustech",
    # 芯片 / 半导体 / 算力（新一批）
    "verisilicon", "gigadevice", "montage", "willsemi", "smartsens", "galaxycore", "bestechnic",
    "bluetrum", "allwinner", "rockchip", "amlogic", "asrmicro", "nucleisys", "thead", "yusur",
    "dapustor", "memblaze", "ymtc", "empyrean", "xepic", "primarius", "uniic", "cxmt", "hygon",
    "loongson", "phytium", "zhaoxin", "starfive", "canaan",
    # 云原生 / 数据库 / 大数据 / 开发者工具
    "taosdata", "oushu", "sequoiadb", "transwarp", "dameng", "kingbase", "whaleops", "databend",
    "matrixorigin", "guance", "jihu", "koderover", "alauda", "qingcloud", "daocloud", "tenxcloud",
    "caicloud", "ucloud", "ksyun", "qiniu", "upyun", "agora", "rongcloud", "easemob", "jiguang",
    "sensorsdata", "growingio", "ishumei", "fanruan", "yonghong", "guandata", "hengshi", "dtstack",
    "deepexi", "hashdata",
    # 互联网 / 游戏 / 在线教育 / 内容平台
    "bytedance", "kuaishou", "meituan", "pdd", "jd", "didiglobal", "ke", "ctrip", "lilithgames",
    "papergames", "hypergryph", "kurogames", "xdinc", "pwrd", "youzu", "moonton", "yuanfudao",
    "zuoyebang", "tal", "gaotu", "ximalaya", "shein", "manbang", "huolala",
    # 金融科技 / 量化
    "duxiaoman", "webank", "mybank", "lufax", "hithink", "hundsun", "eastmoney", "futu",
    "tigerbrokers", "mobvista", "ubiquant", "lingjun", "minghong", "yanfu", "wenbo", "tydw",
    "shouqianba", "lianlian",
    # 智能硬件 / 物联网 / 消费电子（新一批）
    "fibocom", "meigsmart", "ezviz", "hikvision", "dahuatech", "uniview", "honor", "oppo",
    "huaqin", "longcheer", "wingtech", "luxshare", "aactechnologies", "cvte", "iflytek", "soundai",
    "inmo", "rayneo", "llvision", "keep", "viomi", "tineco", "xgimi", "jmgo", "baseus", "ugreen",
    "creality", "anycubic", "autelrobotics", "xag",
    # 医疗 AI / 生物计算
    "uii", "uih", "deepwise", "infervision", "shukun", "airdoc", "xtalpi", "insilico", "biomap",
    "mgitech",
    # 新能源 / 工业互联网 / 工业软件
    "catl", "eve", "svolt", "calb", "longi", "sungrow", "aesc", "zwsoft", "caxa", "blacklake",
    "rootcloud", "supos", "lenovo", "envisiondigital", "xcmg", "cosmoplat", "inspur",
]

Q = ("?keyword=&limit=1&offset=0&job_category_id_list=&tag_id_list=&location_code_list="
     "&subject_id_list=&recruitment_id_list=&portal_type={pt}&job_function_id_list="
     "&storefront_id_list=&portal_entrance=1")

_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def check(slug: str, c: httpx.Client) -> dict:
    out = {"slug": slug}
    try:
        r = c.get(f"https://{slug}.jobs.feishu.cn/")
        out["home"] = r.status_code
        if r.status_code != 200:
            return out
        base = f"{r.url.scheme}://{r.url.host}"
        out["base"] = base
        m = _TITLE.search(r.text)
        out["title"] = (m.group(1).strip()[:40] if m else "")
    except Exception as e:
        out["home"] = f"ERR {type(e).__name__}"
        return out

    h = {"website-path": "index", "portal-channel": "saas-career", "portal-platform": "pc",
         "referer": base + "/index/position/list", "content-type": "application/json",
         "accept": "application/json, text/plain, */*", "accept-language": "zh-CN"}
    time.sleep(0.8)
    try:
        t = c.post(base + "/api/v1/csrf/token", headers=h)
        tok = (t.json().get("data") or {}).get("token")
        if tok:
            h["x-csrf-token"] = tok
    except Exception as e:
        out["csrf"] = f"ERR {type(e).__name__}"

    for pt in (6, 2):
        time.sleep(0.8)
        try:
            r2 = c.post(base + "/api/v1/search/job/posts" + Q.format(pt=pt), json={}, headers=h)
            d = (r2.json().get("data") or {}) if "json" in r2.headers.get("content-type", "") else {}
            cnt = d.get("count")
            if cnt:
                out.update({"portal_type": pt, "count": cnt})
                jl = d.get("job_post_list") or []
                if jl:
                    out["sample"] = jl[0].get("title")
                    out["has_desc"] = bool(jl[0].get("description"))
                break
        except Exception as e:
            out["api"] = f"ERR {type(e).__name__}"
    return out


def main() -> None:
    rows = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=20.0,
                      follow_redirects=True, verify=False) as c:
        for s in SLUGS:
            r = check(s, c)
            rows.append(r)
            if r.get("count"):
                print(f"  OK  {s:<16} n={r['count']:>5}  pt={r['portal_type']}  "
                      f"{r.get('title','')[:24]}  样例: {r.get('sample')}")
            time.sleep(0.6)

    ok = sorted([r for r in rows if r.get("count")], key=lambda x: -x["count"])
    total = sum(r["count"] for r in ok)
    print(f"\n有效租户 {len(ok)}/{len(SLUGS)}，公开职位合计 {total} 条")
    with open(os.path.join(HERE, "feishu_tenants.json"), "w", encoding="utf-8") as f:
        json.dump({"checked": len(rows), "valid": ok, "all": rows}, f,
                  ensure_ascii=False, indent=2)
    print("→ feishu_tenants.json")


if __name__ == "__main__":
    main()
