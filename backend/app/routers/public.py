"""公开门户用的只读聚合口径。

门户页 `/` 是未登录访客看到的第一屏，方案 A2 要求它展示**真实**的岗位数 / JD 数 /
雇主覆盖率，而不是写死的数字。但全站业务路由在 `main.py` 里统一挂了
`dependencies=[Depends(current_actor)]`，`/api/graph/stats` 对匿名访客返回 401
（线上实测），所以门户拿不到任何真数。

这里开一个**极小的公开面**来解决，而不是把 `/api/graph` 整个放开：

- 只暴露**已经公开发表**的汇总数字——这些数值同样印在作品介绍 PPT、README 和
  提交文档里，不构成新的信息披露。
- **逐字段白名单**，不是把 `stats_overview` 整个透出去。上游新增字段不会从这里
  漏出去，必须显式加进 `_PUBLIC_FIELDS` 才可见。
- 不含任何单岗位明细、用户数据、组织数据、证据 URL 或简历相关字段。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import graph_service

router = APIRouter(prefix="/api/public", tags=["public"])

# 白名单：只有这些键会出现在公开响应里。
_PUBLIC_FIELDS = (
    "total_jobs",
    "new_jobs",
    "total_skills",
    "total_jds",
    "avg_confidence",
    "identified_employer_coverage",
    "evidence_count",
)


@router.get("/stats")
def public_stats(db: Session = Depends(get_db)) -> dict:
    """门户首屏数据条。字段白名单见 `_PUBLIC_FIELDS`。"""
    overview = graph_service.stats_overview(db)
    return {key: overview.get(key) for key in _PUBLIC_FIELDS}
