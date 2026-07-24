"""腾讯招聘官网适配器（careers.tencent.com 公开查询接口，无需登录）。"""
from __future__ import annotations
from ..base import BaseCollector

LIST_URL = "https://careers.tencent.com/tencentcareer/api/post/Query"
DETAIL_URL = "https://careers.tencent.com/tencentcareer/api/post/ByPostId"


class TencentCollector(BaseCollector):
    platform = "tencent"
    tier = "official"
    authority = 1.0

    def collect(self, query: str, max_items: int = 30) -> int:
        got = 0
        page = 1
        while got < max_items and page <= 5:
            resp = self.fetch(LIST_URL, params={
                "keyword": query, "pageIndex": page, "pageSize": 10, "language": "zh-cn"})
            if resp is None or resp.status_code != 200:
                break
            posts = (resp.json().get("Data") or {}).get("Posts") or []
            if not posts:
                break
            for p in posts:
                if got >= max_items:
                    break
                detail = {}
                d = self.fetch(DETAIL_URL, params={"postId": p["PostId"], "language": "zh-cn"})
                if d is not None and d.status_code == 200:
                    detail = d.json().get("Data") or {}
                resp_text = detail.get("Responsibility") or p.get("Responsibility") or ""
                req_text = detail.get("Requirement") or ""
                if not (resp_text or req_text):
                    continue
                self.emit({
                    "company": "腾讯",
                    "job_title": p.get("RecruitPostName", ""),
                    "location": p.get("LocationName", ""),
                    "experience_req": p.get("RequireWorkYearsName") or detail.get("RequireWorkYearsName") or "",
                    "publish_date": p.get("LastUpdateTime", ""),
                    "url": p.get("PostURL", ""),
                    "raw_text": f"岗位职责：\n{resp_text}\n\n任职要求：\n{req_text}",
                    "extra": {"category": p.get("CategoryName"), "bg": p.get("BGName"),
                              "product": p.get("ProductName"), "query": query},
                })
                got += 1
            page += 1
        return got
