"""飞书招聘 SaaS 招聘官网适配器（*.jobs.feishu.cn，企业官方招聘官网）。

合规要点：
- 走的是企业招聘官网**公开职位查询接口**，不登录、不带任何登录态；
  csrf token 由 /api/v1/csrf/token 面向所有访客公开发放（会话卫生，非访问控制），
  浏览器附带的 `_signature` 反爬签名**未复现也未绕过**——去掉后接口照常返回，
  说明该接口本身对公众开放。
- 租户清单来自 `probe_feishu_tenants.py` 的探测结果 `feishu_tenants.json`，
  只保留公开职位数 > 0 的租户；每租户独立 host，由 base.fetch 各自控频。
- 只取岗位商业信息（标题/职责/要求/城市/发布时间），不碰任何候选人或 HR 信息。

tier=official，权威度 1.0。
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

from ..base import BaseCollector

TENANTS_FILE = Path(__file__).resolve().parents[1] / "feishu_tenants.json"

# 探测不可用时的兜底清单（slug -> 展示名）
FALLBACK_TENANTS = [
    {"base": "https://nio.jobs.feishu.cn", "slug": "nio", "company": "蔚来"},
    {"base": "https://xiaopeng.jobs.feishu.cn", "slug": "xiaopeng", "company": "小鹏汽车"},
    {"base": "https://poizon.jobs.feishu.cn", "slug": "poizon", "company": "得物"},
    {"base": "https://01ai.jobs.feishu.cn", "slug": "01ai", "company": "零一万物"},
]

SEARCH_PATH = "/api/v1/search/job/posts"
CSRF_PATH = "/api/v1/csrf/token"
PAGE_SIZE = 10          # 服务端硬上限：单次最多返回 10 条
MAX_PAGES = 60


def _clean_company(title: str, slug: str) -> str:
    """从招聘官网 <title> 里取公司名（'加入零一万物AGI' -> '零一万物AGI'）。"""
    t = (title or "").strip()
    for prefix in ("加入", "欢迎加入", "Join "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    for sep in ("-", "|", "｜", "!", "！"):
        if sep in t:
            t = t.split(sep)[0]
    t = t.replace("社会招聘", "").replace("社招", "").strip()
    return t or slug


class FeishuATSCollector(BaseCollector):
    """一个适配器覆盖所有使用飞书招聘 SaaS 官网的企业。

    注意：`max_items` 在本适配器里是**单租户上限**（因为一次检索要横跨多家公司），
    run_collect 传入 --per-query 时按「每公司最多 N 条」理解。
    """

    platform = "feishu_ats"
    tier = "official"
    authority = 1.0

    def __init__(self, out_dir, rate_limit_s: float = 4.0):
        super().__init__(out_dir, rate_limit_s)
        self.tenants = self._load_tenants()
        self._tokens: dict[str, str] = {}
        self._seen_ids: set[str] = set()

    # ---------- 租户清单 ----------
    def _load_tenants(self) -> list[dict]:
        try:
            data = json.loads(TENANTS_FILE.read_text("utf-8"))
            valid = data.get("valid") or []
            out = []
            for t in valid:
                if not t.get("base") or not t.get("count"):
                    continue
                out.append({"base": t["base"], "slug": t["slug"],
                            "company": _clean_company(t.get("title", ""), t["slug"]),
                            "portal_type": t.get("portal_type", 6)})
            if out:
                return out
        except Exception:
            pass
        return [dict(t, portal_type=6) for t in FALLBACK_TENANTS]

    # ---------- csrf ----------
    def _headers(self, base: str) -> dict:
        h = {"website-path": "index", "portal-channel": "saas-career",
             "portal-platform": "pc", "referer": f"{base}/index/position/list",
             "content-type": "application/json",
             "accept": "application/json, text/plain, */*", "accept-language": "zh-CN"}
        tok = self._tokens.get(base)
        if tok:
            h["x-csrf-token"] = tok
        return h

    def _ensure_token(self, base: str) -> None:
        if base in self._tokens:
            return
        resp = self.fetch(base + CSRF_PATH, method="POST", headers=self._headers(base))
        if resp is not None and resp.status_code == 200:
            try:
                tok = (resp.json().get("data") or {}).get("token")
                if tok:
                    self._tokens[base] = tok
            except Exception:
                pass

    # ---------- 采集 ----------
    def collect(self, query: str, max_items: int = 12) -> int:
        got = 0
        for t in self.tenants:
            got += self._collect_tenant(t, query, max_items)
        return got

    def collect_catalog(self, max_per_tenant: int = 400) -> int:
        """全量模式：不带检索词，直接翻完每家企业的公开职位目录。

        飞书 ATS 的关键词检索只匹配标题，按检索词采会漏掉大量相关岗位；
        全量拉取后由入库/管线侧按标题与正文做领域过滤，召回率高得多。

        采用**多租户轮转**：每轮给每个租户翻一页。各租户是不同 host，
        base.fetch 的单 host ≥4s 间隔照常生效，但不同 host 的等待互相重叠，
        在不降低任何礼貌性的前提下把墙钟时间压到约 1/N。
        """
        state = {t["slug"]: {"tenant": t, "offset": 0, "got": 0, "dry": 0, "done": False}
                 for t in self.tenants}
        for t in self.tenants:
            self._ensure_token(t["base"])
        rounds = 0
        while rounds < MAX_PAGES and not all(s["done"] for s in state.values()):
            for s in state.values():
                if s["done"]:
                    continue
                if s["got"] >= max_per_tenant:
                    s["done"] = True
                    continue
                n, page_len = self._fetch_page(s["tenant"], "", s["offset"],
                                               max_per_tenant - s["got"])
                s["got"] += n
                s["offset"] += max(page_len, 0)
                s["dry"] = s["dry"] + 1 if n == 0 else 0
                if page_len < PAGE_SIZE or s["dry"] >= 3:
                    s["done"] = True
            rounds += 1
        total = 0
        for s in state.values():
            t = s["tenant"]
            print(f"    [feishu_ats] {t['company']}({t['slug']}) -> {s['got']} 条")
            total += s["got"]
        return total

    def _collect_tenant(self, tenant: dict, query: str, max_items: int) -> int:
        """按检索词采单个租户（run_collect 默认路径）。"""
        self._ensure_token(tenant["base"])
        got, offset, page = 0, 0, 0
        while got < max_items and page < MAX_PAGES:
            n, page_len = self._fetch_page(tenant, query, offset, max_items - got)
            got += n
            offset += max(page_len, 0)
            page += 1
            if page_len < PAGE_SIZE:
                break
        return got

    def _fetch_page(self, tenant: dict, query: str, offset: int, remaining: int) -> tuple[int, int]:
        """取一页；返回 (新增条数, 该页返回条数)。page_len<PAGE_SIZE 表示已到末页。"""
        base, company = tenant["base"], tenant["company"]
        # 注意：翻页参数必须放在 **JSON body** 里，查询串里的 offset 会被服务端忽略
        body = {
            "keyword": query, "limit": PAGE_SIZE, "offset": offset,
            "portal_type": tenant.get("portal_type", 6), "portal_entrance": 1,
        }
        params = {
            "keyword": query, "limit": PAGE_SIZE, "offset": offset,
            "job_category_id_list": "", "tag_id_list": "", "location_code_list": "",
            "subject_id_list": "", "recruitment_id_list": "",
            "portal_type": tenant.get("portal_type", 6),
            "job_function_id_list": "", "storefront_id_list": "", "portal_entrance": 1,
        }
        resp = self.fetch(base + SEARCH_PATH, method="POST", params=params,
                          json=body, headers=self._headers(base))
        if resp is None or resp.status_code != 200:
            return 0, 0
        try:
            data = resp.json().get("data") or {}
        except Exception:
            return 0, 0
        posts = data.get("job_post_list") or []
        got = 0
        for p in posts:
            if got >= remaining:
                break
            pid = str(p.get("id") or "")
            if not pid or pid in self._seen_ids:
                continue
            desc = (p.get("description") or "").strip()
            req = (p.get("requirement") or "").strip()
            if len(desc) + len(req) < 50:
                continue
            cities = [c.get("name") for c in (p.get("city_list") or []) if c.get("name")]
            ts = p.get("publish_time")
            pub = ""
            if ts:
                try:
                    pub = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
                except Exception:
                    pub = ""
            self._seen_ids.add(pid)
            self.emit({
                "company": company,
                "job_title": p.get("title") or "",
                "location": "、".join(cities[:3]),
                "publish_date": pub,
                "url": f"{base}/index/position/{pid}/detail",
                "raw_text": f"岗位职责：\n{desc}\n\n任职要求：\n{req}",
                "extra": {"tenant": tenant["slug"], "query": query,
                          "category": (p.get("job_category") or {}).get("name"),
                          "recruit_type": (p.get("recruit_type") or {}).get("name")},
            })
            got += 1
        return got, len(posts)
