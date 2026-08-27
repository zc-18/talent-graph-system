"""写操作闸门：演示站只读保护。

2026-07-28 与 07-30 两次事故的共同根因不是某个函数写错了，而是**线上演示站把改图谱
的按钮直接暴露给公网**：岗位详情页每条技能都渲染「编辑/删除」，另有「人工新增能力项」
「发现新岗位」「识别能力变化并演化」「加入成员简历」。任何访客（含爬虫）点一下就改生产
知识图谱，两次事故都是这么来的——第一次真删了 313 行，第二次刷了 40 条假的变更日志。

修单点逻辑只能挡住已知的那条路径，下一个写接口照样敞着。所以这里做一道总闸：
`READ_ONLY=1` 时，一切**改动图谱**的路由直接 403。

设计原则是「只读但不残废」——演示价值必须保住，否则答辩时没法演示：

* 纯展示型写接口（演化推演、新岗位发现）改走 **dry-run**：照常跑完 LLM/检索/聚合并把
  结果返回给前端，只是不落库。观众看到的过程与效果完全一致。
* 真正的持久化操作（人工增删改岗位/能力项、简历入库）没有只读等价物，一律 403。
* 简历解析、人岗匹配、问答助手不碰图谱，不受影响。
"""
from __future__ import annotations
from fastapi import HTTPException

from .config import settings
from .auth import Actor
from .permissions import require_hr
from fastapi import Depends

READ_ONLY_MESSAGE = (
    "演示站当前为只读模式：为保证评审期间图谱数据可复现，"
    "涉及修改知识图谱的操作已关闭。查询、简历解析、人岗匹配、演化推演不受影响。"
)


def is_read_only() -> bool:
    return bool(settings.read_only)


def require_write() -> None:
    """FastAPI 依赖：只读模式下拒绝写操作。

    用在没有只读等价物的路由上（人工增删改、简历入库）。
    """
    if is_read_only():
        raise HTTPException(status_code=403, detail=READ_ONLY_MESSAGE)


def require_org_append(actor: Actor = Depends(require_hr)) -> Actor:
    """Allow an authenticated HR to append organization-private data in READ_ONLY mode."""
    if actor.organization_id is None:
        raise HTTPException(status_code=403, detail="HR 未加入有效组织")
    return actor
