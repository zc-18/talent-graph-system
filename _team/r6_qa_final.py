# -*- coding: utf-8 -*-
"""Lane G r6 — 第四轮：修复复验 + 未覆盖页面 + FAB 遮挡检测。"""
import json, os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("QA_BASE", "http://localhost:5190")
OUT = r"C:\Users\32768\Desktop\project\挑战杯\_shots\out\r6"
ALLOW_POST = ("/api/auth/login", "/api/chat")


def guard(route, request):
    if request.method != "GET" and not any(p in request.url for p in ALLOW_POST):
        print(f"  !! BLOCKED {request.method} {request.url}", flush=True)
        route.abort()
        return
    route.continue_()


CONTRAST_ONE = r"""(sel) => {
  const lum = (c) => {
    const m = c.match(/\d+(\.\d+)?/g); const [r,g,b] = m.slice(0,3).map(Number);
    const f = v => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055,2.4); };
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b);
  };
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const cs = getComputedStyle(el);
    let n = el.parentElement, bg = 'rgb(255,255,255)';
    while (n) { const b = getComputedStyle(n).backgroundColor;
      const p = (b.match(/[\d.]+/g)||[]).map(Number);
      if (p.length < 4 || p[3] > 0.85) { bg = b; break; } n = n.parentElement; }
    const l1 = lum(cs.color), l2 = lum(bg);
    out.push({txt: el.textContent.trim().slice(0,14), fg: cs.color, bg,
              ratio: +(((Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05))).toFixed(2)});
  }
  return out;
}"""

# FAB 是否压住了可点击元素
FAB_JS = r"""() => {
  const fab = document.querySelector('button[aria-label="打开 AI 助手"], button[aria-label="关闭 AI 助手"]');
  if (!fab) return {found:false};
  const r = fab.getBoundingClientRect();
  const hits = [];
  for (const el of document.querySelectorAll('button,a,input,[role="button"],[tabindex]')) {
    if (el === fab || fab.contains(el)) continue;
    const q = el.getBoundingClientRect();
    if (q.width<4||q.height<4) continue;
    const ox = Math.min(r.right,q.right)-Math.max(r.left,q.left);
    const oy = Math.min(r.bottom,q.bottom)-Math.max(r.top,q.top);
    if (ox>4 && oy>4) hits.push({tag: el.tagName, label: (el.getAttribute('aria-label')||el.textContent||'').trim().slice(0,28),
                                 cls: el.className.toString().slice(0,60),
                                 overlapPct: Math.round(100*ox*oy/(q.width*q.height))});
  }
  return {found:true, rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width)], hits};
}"""


def login(pg):
    pg.goto(BASE + "/login", wait_until="domcontentloaded")
    pg.wait_for_selector('input[autocomplete="username"]', timeout=15000)
    pg.fill('input[autocomplete="username"]', "demo-admin")
    pg.fill("input[type=password]", "DemoAdmin123!")
    pg.click("button[type=submit]")
    pg.wait_for_timeout(4500)


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)

        # ---- 桌面 ----
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        ctx.route("**/*", guard)
        pg = ctx.new_page()
        bad = []
        pg.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
        pg.on("requestfailed", lambda r: bad.append(("FAIL", r.url)))
        pg.on("pageerror", lambda e: bad.append(("JS", str(e)[:200])))

        # 修复复验：门户 KPI 标签对比度
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_load_state("networkidle", timeout=20000)
        pg.wait_for_timeout(2500)
        r = pg.evaluate(CONTRAST_ONE, 'section[aria-label="平台实时数据"] .text-xs')
        print("### 门户 KPI 标签对比度（修复后）:")
        for x in r:
            print("   ", json.dumps(x, ensure_ascii=False))
        pg.screenshot(path=os.path.join(OUT, "fix-portal-kpi.png"), full_page=False)

        login(pg)

        # 岗位详情（此前未覆盖）
        pg.goto(BASE + "/jobs", wait_until="domcontentloaded")
        pg.wait_for_load_state("networkidle", timeout=20000)
        pg.wait_for_timeout(2500)
        href = pg.evaluate("""() => { const a=document.querySelector('a[href^="/jobs/"]'); return a?a.getAttribute('href'):null; }""")
        print("### job detail href:", href)
        if href:
            pg.goto(BASE + href, wait_until="domcontentloaded")
            try:
                pg.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            pg.wait_for_timeout(3500)
            pg.screenshot(path=os.path.join(OUT, "d-jobdetail.png"), full_page=True)

        for path, key in [("/discovery", "d-discovery2"), ("/evolution", "d-evolution2"),
                          ("/history", "d-history2"), ("/admin", "d-admin2"), ("/me", "d-me2"),
                          ("/dashboard", "d-dashboard2"), ("/talent", "d-talent2")]:
            pg.goto(BASE + path, wait_until="domcontentloaded")
            try:
                pg.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            pg.wait_for_timeout(3000)
            pg.screenshot(path=os.path.join(OUT, key + ".png"), full_page=True)
            fab = pg.evaluate(FAB_JS)
            if fab.get("hits"):
                print(f"### FAB 遮挡 [{path}] @1440:", json.dumps(fab["hits"][:5], ensure_ascii=False))
        ctx.close()

        # ---- 移动 FAB 遮挡 ----
        ctx2 = b.new_context(viewport={"width": 375, "height": 812}, locale="zh-CN",
                             is_mobile=True, has_touch=True, device_scale_factor=2)
        ctx2.route("**/*", guard)
        pg2 = ctx2.new_page()
        pg2.on("response", lambda r: bad.append(("M", r.status, r.url)) if r.status >= 400 else None)
        pg2.on("requestfailed", lambda r: bad.append(("M-FAIL", r.url)))
        login(pg2)
        for path in ["/me", "/dashboard", "/talent", "/jobs", "/match"]:
            pg2.goto(BASE + path, wait_until="domcontentloaded")
            try:
                pg2.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            pg2.wait_for_timeout(3000)
            # 滚到底再测，FAB 是 fixed
            pg2.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            pg2.wait_for_timeout(900)
            fab = pg2.evaluate(FAB_JS)
            if fab.get("hits"):
                print(f"### FAB 遮挡 [{path}] @375:", json.dumps(fab["hits"][:5], ensure_ascii=False))
            pg2.screenshot(path=os.path.join(OUT, "mfab" + path.replace("/", "-") + ".png"), full_page=False)
        ctx2.close()
        b.close()

    seen, uniq = set(), []
    for x in bad:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    print("\n### NET/JS 问题:", uniq if uniq else "无")


main()
