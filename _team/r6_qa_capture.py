# -*- coding: utf-8 -*-
"""Lane G r6 visual QA — 真实浏览器逐页验收。只发 GET（登录 + chat 除外，两者不写图谱）。"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get("QA_BASE", "http://localhost:5190")
OUT = r"C:\Users\32768\Desktop\project\挑战杯\_shots\out\r6"
os.makedirs(OUT, exist_ok=True)

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 375, "height": 812}

# 写操作黑名单：任何 non-GET 除了这几个显式放行的，一律 abort
ALLOW_POST = ("/api/auth/login", "/api/chat")

report = {"pages": {}, "notes": []}


def guard(route, request):
    if request.method != "GET":
        if any(p in request.url for p in ALLOW_POST):
            route.continue_()
            return
        print(f"  !! BLOCKED non-GET {request.method} {request.url}", flush=True)
        route.abort()
        return
    route.continue_()


def attach(page, bucket):
    page.on("console", lambda m: bucket["console"].append(
        {"type": m.type, "text": m.text[:400]}) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: bucket["console"].append({"type": "pageerror", "text": str(e)[:400]}))
    page.on("response", lambda r: bucket["bad"].append(
        {"status": r.status, "url": r.url}) if r.status >= 400 else None)
    page.on("requestfailed", lambda r: bucket["bad"].append(
        {"status": "FAILED", "url": r.url, "err": str(r.failure)}))


def shot(page, name, full=True):
    p = os.path.join(OUT, name + ".png")
    page.screenshot(path=p, full_page=full)
    return p


def visit(page, path, name, bucket_key, wait=2200, full=True):
    b = {"console": [], "bad": []}
    report["pages"].setdefault(bucket_key, b)
    b = report["pages"][bucket_key]
    handlers = {"console": [], "bad": []}
    # 用页面级 bucket 收集
    page._qa = b
    page.goto(BASE + path, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(wait)
    b["shot"] = shot(page, name, full=full)
    return b


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        results = {}

        # ---------- 桌面上下文 ----------
        ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=1,
                                  locale="zh-CN", ignore_https_errors=True)
        ctx.route("**/*", guard)
        page = ctx.new_page()
        buckets = {}

        def mk(key):
            b = {"console": [], "bad": []}
            buckets[key] = b
            return b

        cur = {"b": mk("_boot")}
        page.on("console", lambda m: cur["b"]["console"].append({"type": m.type, "text": m.text[:400]})
                if m.type == "error" else None)
        page.on("pageerror", lambda e: cur["b"]["console"].append({"type": "pageerror", "text": str(e)[:400]}))
        page.on("response", lambda r: cur["b"]["bad"].append({"status": r.status, "url": r.url})
                if r.status >= 400 else None)
        page.on("requestfailed", lambda r: cur["b"]["bad"].append(
            {"status": "FAILED", "url": r.url, "err": str(r.failure)}))

        def go(path, key, wait=2500, full=True):
            cur["b"] = mk(key)
            page.goto(BASE + path, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(wait)
            cur["b"]["shot"] = shot(page, key, full=full)
            return cur["b"]

        # === 1&2. 门户（匿名） ===
        b = go("/", "d-portal-anon")
        b["shot_vp"] = shot(page, "d-portal-anon-viewport", full=False)

        # === 3. 登录页 ===
        go("/login", "d-login")

        # 登录
        page.fill('input[autocomplete="username"]', "demo-admin")
        page.fill("input[type=password]", "DemoAdmin123!")
        page.click("button[type=submit]")
        page.wait_for_timeout(4000)
        results["after_login_url"] = page.url

        for path, key, wait in [
            ("/", "d-portal-auth", 3500),
            ("/dashboard", "d-dashboard", 4000),
            ("/talent", "d-talent", 4500),
            ("/jobs", "d-jobs", 4000),
            ("/admin", "d-admin", 4000),
            ("/me", "d-me", 3000),
            ("/panorama", "d-panorama", 6000),
            ("/match", "d-match", 3000),
            ("/history", "d-history", 3000),
            ("/feedback", "d-feedback", 3000),
            ("/hr", "d-hr", 3500),
            ("/evolution", "d-evolution", 3000),
            ("/discovery", "d-discovery", 3000),
        ]:
            go(path, key, wait)

        storage = ctx.storage_state()
        ctx.close()

        # ---------- 移动上下文 ----------
        ctx2 = browser.new_context(viewport=MOBILE, device_scale_factor=1, locale="zh-CN",
                                   is_mobile=True, has_touch=True, storage_state=storage)
        ctx2.route("**/*", guard)
        page2 = ctx2.new_page()
        cur2 = {"b": mk("_boot_m")}
        page2.on("console", lambda m: cur2["b"]["console"].append({"type": m.type, "text": m.text[:400]})
                 if m.type == "error" else None)
        page2.on("pageerror", lambda e: cur2["b"]["console"].append({"type": "pageerror", "text": str(e)[:400]}))
        page2.on("response", lambda r: cur2["b"]["bad"].append({"status": r.status, "url": r.url})
                 if r.status >= 400 else None)
        page2.on("requestfailed", lambda r: cur2["b"]["bad"].append(
            {"status": "FAILED", "url": r.url, "err": str(r.failure)}))

        def go2(path, key, wait=2500, full=True):
            cur2["b"] = mk(key)
            page2.goto(BASE + path, wait_until="domcontentloaded")
            try:
                page2.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page2.wait_for_timeout(wait)
            cur2["b"]["shot"] = shot(page2, key, full=full)
            return cur2["b"]

        for path, key, wait in [
            ("/", "m-portal", 3500),
            ("/login", "m-login", 2500),
            ("/dashboard", "m-dashboard", 4000),
            ("/talent", "m-talent", 4500),
            ("/jobs", "m-jobs", 4000),
            ("/admin", "m-admin", 4000),
            ("/me", "m-me", 3000),
            ("/panorama", "m-panorama", 6000),
            ("/match", "m-match", 3000),
            ("/hr", "m-hr", 3500),
        ]:
            go2(path, key, wait)

        ctx2.close()
        browser.close()

        results["buckets"] = buckets
        with open(os.path.join(OUT, "_qa_log.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)

        # 汇总打印
        print("=== after login url:", results["after_login_url"])
        for k, v in buckets.items():
            errs = v.get("console", [])
            bad = v.get("bad", [])
            if errs or bad:
                print(f"\n--- {k} ---")
                for e in errs[:8]:
                    print("  CONSOLE", e["type"], e["text"][:200])
                seen = set()
                for x in bad[:20]:
                    key = (x.get("status"), x.get("url"))
                    if key in seen:
                        continue
                    seen.add(key)
                    print("  NET", x.get("status"), x.get("url")[:160])
        print("\nOK. shots ->", OUT)


main()
