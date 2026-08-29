# -*- coding: utf-8 -*-
"""只读：按「标题必须自带岗位领域词」的口径统计各批次可用条数与可用新雇主。
输出的正则就是建议在 import 前加的标题白名单。"""
from __future__ import annotations
import json, os, re, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from app.db import SessionLocal
from sqlalchemy import text
from app.services.employer_resolution import normalize_employer_name
from data.import_raw import _query_cluster_map, is_it_domain

# 簇 -> 标题白名单（标题自身必须体现岗位领域，正文命中不算）
TITLE_OK = {
 "自动驾驶": r"自动驾驶|智驾|智能驾驶|辅助驾驶|无人驾驶|无人车|感知算法|点云|激光雷达|BEV|"
             r"决策规划|规控|域控|SLAM|定位与建图|端到端算法",
 "机器人算法": r"机器人|机械臂|运动控制|运控|导航算法|SLAM|具身|AGV|AMR|无人系统|多足|人形",
 "多模态": r"多模态|视觉语言|VLM|VLA|跨模态|图文|文生图|文生视频|视频生成|图像生成",
 "智能硬件": r"硬件|嵌入式|固件|电路|驱动开发|智能终端|可穿戴|整机|结构设计|BSP|单片机|MCU|FPGA",
 "车联网": r"车联网|智能网联|座舱|车载|T-?Box|AUTOSAR|域控|整车|车规|汽车电子|TSP|车云|OTA",
 "嵌入式": r"嵌入式|固件|BSP|驱动|RTOS|单片机|MCU|Linux内核",
 "AIGC": r"AIGC|生成式|文生|图像生成|视频生成|扩散|Diffusion|数字内容",
 "数据分析": r"数据分析|商业分析|BI|数据洞察|经营分析",
 "大数据平台": r"大数据|数据平台|实时计算|流计算|数据中台|Flink|Spark",
 "物联网": r"物联网|IoT|MQTT|设备接入|边缘网关|传感|终端接入",
 "计算机视觉": r"计算机视觉|机器视觉|视觉算法|视觉感知|图像算法|图像处理|CV算法|OCR|目标检测|图像分割|3D重建|点云|视频算法",
 "推荐算法": r"推荐|搜索|排序|召回|特征工程|广告算法",
 "数据仓库": r"数仓|数据仓库|数据治理|数据质量|ETL|数据建模",
 "AI产品": r"产品经理|产品总监|产品负责人|产品策划",
 "运维开发": r"运维|SRE|可观测|监控|稳定性|DevOps|发布|值班",
 "自然语言处理": r"自然语言|NLP|对话|语义|文本|大语言模型|LLM",
 "深度学习": r"深度学习|神经网络|模型训练|端侧推理|模型压缩|算子|推理引擎",
 "数据开发": r"数据开发|数据研发|数据管道|数据集成|ETL|数仓开发",
 "云计算": r"云计算|云原生|云平台|Kubernetes|K8s|虚拟化|云网络|网络架构|IaaS|PaaS",
 "工业互联网": r"工业互联网|工业物联网|工业软件|MES|SCADA|PLC|OPC|工业控制|数采|产线",
 "提示词工程": r"提示词|Prompt|大模型应用|AI应用",
 "边缘计算": r"边缘计算|边缘|端侧|端边|网关",
}
NON_ENG = re.compile(r"视觉设计|视觉传达|平面设计|美育|活动设计|"
                     r"产品经理|设计师|采购|销售|运营|人力|财务|法务|市场部|品牌|行政|客服|"
                     r"培训|讲师|BD岗|商务|投资|战略|公关|供应链|翻译|文案|编辑|主播|摄影|"
                     r"会计|出纳|审计|护士|医师|教师|司机|保安|厨师|前台|秘书|操作工|"
                     r"装配|叉车|钳工|焊工|电工|质检员|库管|仓管|专员|主管|经理岗")
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

def main():
    qmap = _query_cluster_map()
    tmap = json.loads((Path(__file__).resolve().parents[1] / "data" / "collect" /
                       "title_map.json").read_text("utf-8"))["cluster_job_name"]
    db = SessionLocal()
    known = {n for (n,) in db.execute(text("SELECT normalized_name FROM employer")).fetchall() if n}
    known |= {normalize_employer_name(c) for (c,) in
              db.execute(text("SELECT DISTINCT company FROM raw_jd")).fetchall() if c}
    known.discard("")
    urls = {u for (u,) in db.execute(text("SELECT source_url FROM raw_jd")).fetchall() if u}
    db.rollback(); db.close()

    for b in (sys.argv[1:] or ["2026R6T2"]):
        d = RAW / b
        if not d.exists(): print(f"!! {b} 不存在"); continue
        st = defaultdict(lambda: {"n":0,"keep":0,"emp":set(),"kemp":set(),"knew":set(),
                                  "per_emp":Counter()})
        for fp in sorted(d.glob("*.jsonl")):
            if fp.name == "crawl_log.jsonl": continue
            for line in fp.open(encoding="utf-8"):
                if not line.strip(): continue
                r = json.loads(line)
                q = ((r.get("extra") or {}).get("query")) or ""
                cl = qmap.get(q)
                if not cl: continue
                s = st[cl]; s["n"] += 1
                nm = normalize_employer_name(r.get("company") or "")
                if nm: s["emp"].add(nm)
                t = r.get("job_title") or ""
                if (r.get("url") or "") in urls: continue
                if not re.search(TITLE_OK[cl], t, re.I): continue
                if cl != "AI产品" and NON_ENG.search(t): continue
                if not is_it_domain(t, r.get("raw_text") or ""): continue
                s["keep"] += 1
                if nm:
                    s["kemp"].add(nm); s["per_emp"][nm] += 1
                    if nm not in known: s["knew"].add(nm)
        print(f"\n=========== 批次 {b}：标题白名单过滤后 ===========")
        print(f"{'簇 -> 岗位':<34}{'采到':>6}{'可用':>6}{'可用率':>7}{'雇主':>6}{'可用雇主':>9}{'其中新雇主':>10}{'超5条的雇主':>11}")
        tk=tn=0
        for cl, s in sorted(st.items(), key=lambda x: -x[1]["n"]):
            over = [f"{k}({v})" for k, v in s["per_emp"].items() if v > 5]
            print(f"{cl+' -> '+tmap.get(cl,'?'):<34}{s['n']:>6}{s['keep']:>6}"
                  f"{100.0*s['keep']/max(1,s['n']):>6.1f}%{len(s['emp']):>6}{len(s['kemp']):>9}"
                  f"{len(s['knew']):>10}{len(over):>11}")
            tk += s["keep"]; tn += s["n"]
        allnew = set().union(*[s["knew"] for s in st.values()]) if st else set()
        print(f"\n合计：采到 {tn} 条 → 标题白名单后可用 {tk} 条 ({100.0*tk/max(1,tn):.1f}%)")
        print(f"可用条目里此前未出现过的新雇主：{len(allnew)} 家")
        print("\n--- 可用新雇主清单 ---")
        for cl, s in sorted(st.items()):
            if s["knew"]:
                print(f"  [{tmap.get(cl,cl)}] {len(s['knew'])} 家: " + "、".join(sorted(s["knew"])[:40]))
main()
