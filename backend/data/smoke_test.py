"""本地后端 API 冒烟测试（含大模型驱动端点）。"""
import sys, json, httpx
B = "http://127.0.0.1:8200"
c = httpx.Client(base_url=B, timeout=180)

def show(t, r):
    print(f"\n=== {t} === [{r.status_code}]")
    return r.json()

# 1) 岗位列表
jobs = show("jobs", c.get("/api/jobs", params={"size": 100}))
ml = next(j for j in jobs["items"] if j["name"] == "机器学习工程师")
print("机器学习工程师 id=", ml["id"], "required_count=", ml["required_count"], "conf=", ml["confidence"])

# 2) 人岗匹配 + 学习路径（不调用大模型建议）
r = show("match", c.post("/api/match/analyze", json={
    "job_id": ml["id"], "skills": ["Python", "机器学习", "SQL"], "generate_suggestions": False}))
print("score:", r["result"]["overall_score"], r["result"]["level"])
print("dims:", r["result"]["dimension_scores"])
print("missing:", [m["name"] for m in r["result"]["missing_required"]])
print("path:", [p["skill"] for p in r["learning_path"]])

# 3) 简历文本解析+匹配+建议（调用大模型）
if len(sys.argv) > 1 and sys.argv[1] == "full":
    resume = "求职意向：机器学习工程师\n技能：熟练Python、机器学习、深度学习、torch、推荐系统\n3年算法经验"
    r = show("resume+match", c.post("/api/match/analyze", json={
        "job_id": ml["id"], "resume_text": resume, "generate_suggestions": True}))
    print("score:", r["result"]["overall_score"])
    print("suggestion:", r["suggestions"].get("overall_advice", "")[:120])

    # 4) 新岗位发现（调用 Tavily+Serper+DeepSeek）
    r = show("discover", c.post("/api/discovery/discover",
                                json={"keyword": "AI智能体开发工程师", "save": False}))
    d = r["definition"]
    print("new job:", d["job_title"], "| 证据:", r["candidate"]["evidence_count"],
          "| 独立源:", r["candidate"]["independent_sources"])
    print("required:", [c2["name"] for c2 in d["capabilities"] if c2["importance"] == "required"][:8])
    print("scenarios:", d["typical_scenarios"][:3])

print("\nALL OK")
