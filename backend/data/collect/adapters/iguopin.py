"""国聘网适配器（www.iguopin.com，中央广播电视总台主办的国家级公共招聘平台）。

合规要点：
- 公开职位检索接口，无需登录、无需 token；只取岗位商业信息。
- 列表接口本身即返回 JD 全文（contents 字段），无需再打详情接口，请求量最小化。
- 返回体里含 `contact_user` 等字段，本适配器**不读取、不落盘**（base.emit 的
  PII 字段护栏也会二次拦截）。

tier=gov，权威度 1.0（政府/国家级公共就业服务平台）。
"""
from __future__ import annotations
import re

from ..base import BaseCollector

API = "https://gp-api.iguopin.com/api/jobs/v1/recom-job"
DETAIL_WEB = "https://www.iguopin.com/job/detail?id={jid}"
PAGE_SIZE = 20
MAX_PAGES = 5

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    s = _TAG.sub("\n", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


class IguopinCollector(BaseCollector):
    platform = "iguopin"
    tier = "gov"
    authority = 1.0

    def __init__(self, out_dir, rate_limit_s: float = 4.0):
        super().__init__(out_dir, rate_limit_s)
        self._seen: set[str] = set()

    def collect(self, query: str, max_items: int = 20) -> int:
        got, page = 0, 1
        headers = {"content-type": "application/json",
                   "origin": "https://www.iguopin.com",
                   "referer": "https://www.iguopin.com/"}
        while got < max_items and page <= MAX_PAGES:
            body = {"search": {"page": page, "page_size": PAGE_SIZE, "keyword": query},
                    "recom": {"update_time": True, "company_nature": True, "hot_job": True}}
            resp = self.fetch(API, method="POST", json=body, headers=headers)
            if resp is None or resp.status_code != 200:
                break
            try:
                data = resp.json().get("data") or {}
            except Exception:
                break
            rows = data.get("list") or []
            if not rows:
                break
            for r in rows:
                if got >= max_items:
                    break
                jid = str(r.get("job_id") or "")
                if not jid or jid in self._seen:
                    continue
                text = _strip_html(r.get("contents") or "")
                if len(text) < 50:
                    continue
                districts = [d.get("area_cn") or "" for d in (r.get("district_list") or [])]
                lo, hi = r.get("min_wage") or 0, r.get("max_wage") or 0
                salary = (f"{lo}-{hi}{r.get('wage_unit_cn') or ''}"
                          if (lo or hi) else ("面议" if r.get("is_negotiable") else ""))
                self._seen.add(jid)
                self.emit({
                    "company": r.get("company_name") or "",
                    "job_title": r.get("job_name") or "",
                    "location": "、".join([d for d in districts if d][:3]),
                    "salary_range": salary,
                    "experience_req": r.get("experience_cn") or "",
                    "education_req": r.get("education_cn") or "",
                    "publish_date": (r.get("start_time") or r.get("create_time") or "")[:10],
                    "url": DETAIL_WEB.format(jid=jid),
                    "raw_text": text,
                    "extra": {"query": query, "category": r.get("category_cn"),
                              "recruitment_type": r.get("recruitment_type_cn"),
                              "is_graduates": r.get("is_graduates")},
                })
                got += 1
            if len(rows) < PAGE_SIZE:
                break
            page += 1
        return got
