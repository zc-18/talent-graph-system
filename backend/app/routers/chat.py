"""AI 助手对话路由（流式 SSE）。

「智岗小助手」：基于平台图谱数据，解答平台使用、岗位能力、职业规划等问题。
"""
from __future__ import annotations
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from .. import models, clients
from ..db import get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])

SYSTEM = """你是「智岗小助手」，智岗图谱(TalentGraph AI)平台的AI助手，平台主题是"多源异构数据驱动岗位和能力图谱构建与动态演化分析"。
平台四大功能：①新岗位发现与定义 ②既有岗位能力动态演化 ③新一代信息技术岗位全景能力图谱 ④人岗匹配诊断与差距分析。
你的职责：
1. 解答平台功能与使用方法；
2. 提供新一代信息技术（人工智能/大数据/智能系统/物联网）领域的岗位认知、技能要求与职业规划/学习路径建议；
3. 基于"已收录岗位"上下文回答岗位相关问题。
要求：用中文，简洁友好、条理清晰，适当用要点；不确定或平台没有的信息不要编造，可引导用户去对应功能页查看。回答控制在 250 字内。"""


def _context(db: Session) -> str:
    jobs = db.query(models.Job).all()
    by_cat: dict[str, list[str]] = {}
    for j in jobs:
        by_cat.setdefault(j.category or "其他", []).append(j.name)
    lines = [f"- {c}：{', '.join(sorted(set(names)))}" for c, names in by_cat.items()]
    return "【当前平台已收录岗位（按技术栈）】\n" + "\n".join(lines)


@router.post("")
def chat(payload: dict, db: Session = Depends(get_db)):
    user_msg = (payload.get("message") or "").strip()
    history = payload.get("history", []) or []
    messages = [{"role": "system", "content": SYSTEM + "\n\n" + _context(db)}]
    for h in history[-8:]:
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            messages.append({"role": role, "content": h["content"][:1500]})
    messages.append({"role": "user", "content": user_msg or "你好"})

    def gen():
        try:
            for delta in clients.chat_stream(messages):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'delta': '（助手暂时不可用：' + str(e)[:60] + '）'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/suggestions")
def suggestions():
    return {"items": ["平台有哪些核心功能？", "如何提升人岗匹配度？",
                      "提示词工程师需要哪些技能？", "新岗位是怎么发现的？"]}
