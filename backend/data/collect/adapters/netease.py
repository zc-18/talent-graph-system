"""网易招聘官网适配器（hr.163.com 公开社招接口，列表即含完整 JD，无需登录）。"""
from __future__ import annotations
from ..base import BaseCollector

LIST_URL = "https://hr.163.com/api/hr163/position/queryPage"


class NeteaseCollector(BaseCollector):
    platform = "netease"
    tier = "official"
    authority = 1.0

    def collect(self, query: str, max_items: int = 30) -> int:
        got = 0
        page = 1
        while got < max_items and page <= 5:
            resp = self.fetch(LIST_URL, method="POST", json={
                "currentPage": page, "pageSize": 10, "keyword": query})
            if resp is None or resp.status_code != 200:
                break
            data = (resp.json().get("data") or {})
            items = data.get("list") or []
            if not items:
                break
            for it in items:
                if got >= max_items:
                    break
                desc = it.get("description") or ""
                req = it.get("requirement") or ""
                if not (desc or req):
                    continue
                places = it.get("workPlaceNameList") or []
                self.emit({
                    "company": "网易" + (f"·{it['productName']}" if it.get("productName") else ""),
                    "job_title": it.get("name", ""),
                    "location": "/".join(places[:3]),
                    "experience_req": it.get("reqWorkYearsName") or "",
                    "education_req": it.get("reqEducationName") or "",
                    "publish_date": str(it.get("updateTime") or ""),
                    "url": f"https://hr.163.com/job-detail.html?id={it.get('id')}",
                    "raw_text": f"岗位职责：\n{desc}\n\n任职要求：\n{req}",
                    "extra": {"post_type": it.get("firstPostTypeName"),
                              "dept": it.get("firstDepName"), "query": query},
                })
                got += 1
            page += 1
        return got
