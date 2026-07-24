"""合法合规采集基座 —— 所有适配器共用的护栏（2026-07 整改，老师意见②）。

合规基线（写死，不可绕过）：
1. 只采**公开页面/公开接口**：不携带登录态、不绕过登录墙、不破解验证码。
2. 请求前检查并缓存 robots.txt；目标路径被 Disallow 则放弃该源并记录。
3. 单 host 请求间隔 ≥ RATE_LIMIT_S 秒 + 随机抖动；UA 注明高校竞赛研究用途。
4. 不采集任何个人信息：入库前对 JD 文本做手机号/邮箱/微信号掩码；
   若适配器字段命名出现 phone/mobile/wechat/email 等直接抛错（防手滑）。
5. 每批次落盘台账：{platform}.jsonl（数据）+ crawl_log.jsonl（每请求一行）
   + manifest.json（批次元数据）——原始留存即佐证。
"""
from __future__ import annotations
import json
import random
import re
import time
import urllib.robotparser
from datetime import datetime
from pathlib import Path

import httpx

RATE_LIMIT_S = 4.0          # 单 host 最小间隔（秒）
JITTER_S = 2.0              # 随机抖动上限
TIMEOUT_S = 20.0
UA = ("TalentGraph-Research/1.0 (university competition research; "
      "collects public job postings only; contact via project repo)")

# 禁止出现在字段名里的 PII 关键词（防止适配器误采联系方式）
_PII_FIELD_BAN = re.compile(r"phone|mobile|wechat|weixin|email|contact|联系人|手机", re.I)
# 文本中的 PII 兜底打码
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_WECHAT = re.compile(r"(微信|weixin|wechat)[:：\s]*[A-Za-z0-9_-]{5,20}", re.I)

RECORD_FIELDS = ["platform", "company", "job_title", "location", "salary_range",
                 "experience_req", "education_req", "publish_date", "url",
                 "crawled_at", "raw_text", "extra"]


def mask_pii(text: str) -> str:
    if not text:
        return text
    text = _RE_PHONE.sub("1**********", text)
    text = _RE_EMAIL.sub("***@***", text)
    text = _RE_WECHAT.sub(r"\1:***", text)
    return text


class RobotsGate:
    """robots.txt 检查（按 host 缓存）。取不到 robots.txt 时默认允许但记录。"""

    def __init__(self):
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        m = re.match(r"(https?://[^/]+)", url)
        if not m:
            return False
        host = m.group(1)
        if host not in self._cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = httpx.get(f"{host}/robots.txt", timeout=10,
                                 headers={"User-Agent": UA}, follow_redirects=True)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._cache[host] = rp
                else:
                    self._cache[host] = None  # 无 robots -> 默认允许
            except Exception:
                self._cache[host] = None
        rp = self._cache[host]
        return True if rp is None else rp.can_fetch(UA, url)


class BaseCollector:
    """适配器基类：子类实现 collect(query) -> list[dict]，其余护栏全在这里。"""

    platform: str = "base"
    tier: str = "official"          # official / gov / dataset / aggregator
    authority: float = 1.0          # 来源权威度（confidence 因子）

    def __init__(self, out_dir: Path, rate_limit_s: float = RATE_LIMIT_S):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limit_s = rate_limit_s
        self.robots = RobotsGate()
        self._last_req: dict[str, float] = {}
        self._log_path = self.out_dir / "crawl_log.jsonl"
        self._data_path = self.out_dir / f"{self.platform}.jsonl"
        self.stats = {"requests": 0, "collected": 0, "robots_blocked": 0, "errors": 0}
        self.client = httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": UA},
                                   follow_redirects=True)

    # ---------- 护栏化的请求 ----------
    def fetch(self, url: str, *, method: str = "GET", **kw) -> httpx.Response | None:
        if not self.robots.allowed(url):
            self._log(url, status="robots_disallow")
            self.stats["robots_blocked"] += 1
            return None
        host = re.match(r"https?://[^/]+", url).group(0)
        wait = self.rate_limit_s + random.uniform(0, JITTER_S) - (time.time() - self._last_req.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        t0 = time.time()
        try:
            resp = self.client.request(method, url, **kw)
            self._last_req[host] = time.time()
            self.stats["requests"] += 1
            self._log(url, status=resp.status_code, latency_ms=int((time.time() - t0) * 1000))
            return resp
        except Exception as e:
            self._last_req[host] = time.time()
            self.stats["errors"] += 1
            self._log(url, status=f"error:{type(e).__name__}")
            return None

    # ---------- 记录落盘 ----------
    def emit(self, record: dict) -> None:
        for k in record:
            if _PII_FIELD_BAN.search(k):
                raise ValueError(f"疑似个人信息字段被拦截: {k}（合规护栏）")
        rec = {k: record.get(k) for k in RECORD_FIELDS}
        rec["platform"] = rec.get("platform") or self.platform
        rec["crawled_at"] = rec.get("crawled_at") or datetime.now().isoformat(timespec="seconds")
        rec["raw_text"] = mask_pii(rec.get("raw_text") or "")
        with open(self._data_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.stats["collected"] += 1

    def _log(self, url: str, **kw) -> None:
        row = {"ts": datetime.now().isoformat(timespec="seconds"),
               "platform": self.platform, "url": url, **kw}
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---------- 批次收尾 ----------
    def write_manifest(self, notes: str = "") -> dict:
        manifest_path = self.out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {"adapters": {}}
        manifest["adapters"][self.platform] = {
            "tier": self.tier, "authority": self.authority,
            "rate_limit_s": self.rate_limit_s, "finished_at": datetime.now().isoformat(timespec="seconds"),
            "stats": self.stats, "notes": notes,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        return manifest

    def close(self):
        self.client.close()

    # 子类实现
    def collect(self, query: str, max_items: int = 30) -> int:
        raise NotImplementedError
