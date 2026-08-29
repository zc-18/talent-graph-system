# -*- coding: utf-8 -*-
"""Lane G r6 — 第二轮：移动端（带登录）+ ChatBot markdown + 逐项 DOM/像素断言。"""
import json, os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("QA_BASE", "http://localhost:5190")
OUT = r"C:\Users\32768\Desktop\project\挑战杯\_shots\out\r6"
os.makedirs(OUT, exist_ok=True)
ALLOW_POST = ("/api/auth/login", "/api/chat")
findings = []
netlog = {}


def guard(route, request):
    if request.method != "GET" and not any(p in request.url for p in ALLOW_POST):
        print(f"  !! BLOCKED {request.method} {request.url}", flush=True)
        route.abort()
        return
    route.continue_()


def login(page):
    page.goto(BASE + "/login", wait_until="domcontentloaded")
    page.wait_for_selector('input[autocomplete="username"]', timeout=15000)
    page.fill('input[autocomplete="username"]', "demo-admin")
    page.fill("input[type=password]", "DemoAdmin123!")
    page.click("button[type=submit]")
    page.wait_for_timeout(4500)
    return page.url


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ============ 桌面：DOM 断言 + ChatBot ============
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        ctx.route("**/*", guard)
        pg = ctx.new_page()
        cur = {"k": "boot"}
        netlog.setdefault("boot", [])
        pg.on("response", lambda r: netlog.setdefault(cur["k"], []).append((r.status, r.url))
              if r.status >= 400 else None)
        pg.on("requestfailed", lambda r: netlog.setdefault(cur["k"], []).append(("FAIL", r.url)))
        pg.on("pageerror", lambda e: findings.append(("pageerror", cur["k"], str(e)[:200])))

        # ---- 检查 1：门户 KPI 条不被裁切 ----
        cur["k"] = "portal"
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_load_state("networkidle", timeout=20000)
        pg.wait_for_timeout(2500)
        kpi = pg.evaluate("""() => {
          const sec = document.querySelector('section[aria-label="平台实时数据"]');
          if (!sec) return {found:false};
          const bar = sec.firstElementChild;
          const cells = [...bar.children].map(c => {
            const label = c.querySelector('div');
            const num = c.querySelector('span.tabular-nums');
            const lr = label.getBoundingClientRect();
            const br = bar.getBoundingClientRect();
            return {
              label: label.textContent.trim(),
              labelTop: Math.round(lr.top), labelBottom: Math.round(lr.bottom),
              barTop: Math.round(br.top),
              clippedTop: lr.top < br.top - 0.5,
              num: num ? num.textContent.trim() : null,
              numTop: num ? Math.round(num.getBoundingClientRect().top) : null,
              visible: lr.height > 0 && lr.width > 0,
            };
          });
          const br = bar.getBoundingClientRect();
          // 元素在 KPI 条上方中心点被谁接住 —— 若不是条内元素说明被遮罩盖住
          const probe = document.elementFromPoint(br.left + 40, br.top + 18);
          return {found:true, cells, barTop: Math.round(br.top), barH: Math.round(br.height),
                  topmostAtLabel: probe ? probe.className.toString().slice(0,90) : null,
                  overflow: getComputedStyle(bar).overflow};
        }""")
        print("### KPI:", json.dumps(kpi, ensure_ascii=False, indent=1))

        # hero 图是否真的可见（采样遮罩之上的实际渲染色差）
        hero = pg.evaluate("""() => {
          const img = document.querySelector('img[src="/portal-hero.webp"]');
          if (!img) return {found:false};
          const r = img.getBoundingClientRect();
          const cs = getComputedStyle(img);
          const mask = img.nextElementSibling;
          return {found:true, hidden: img.hidden, natural:[img.naturalWidth,img.naturalHeight],
                  rect:[Math.round(r.width),Math.round(r.height)], opacity: cs.opacity,
                  maskBg: mask ? getComputedStyle(mask).backgroundImage.slice(0,220) : null};
        }""")
        print("### HERO:", json.dumps(hero, ensure_ascii=False))

        # ---- 检查 8：ChatBot ----
        cur["k"] = "chat"
        pg.click('button[aria-label="打开 AI 助手"]')
        pg.wait_for_timeout(1200)
        pg.screenshot(path=os.path.join(OUT, "d-chat-open.png"))
        panel = pg.evaluate("""() => {
          const d = document.querySelector('div[role="dialog"][aria-label="智岗小助手对话"]');
          if (!d) return {found:false};
          const hd = d.firstElementChild;
          const img = d.querySelector('img[src="/assistant.webp"]');
          return {found:true, headerBg: getComputedStyle(hd).backgroundImage.slice(0,240),
                  avatarOk: !!img && img.naturalWidth>0};
        }""")
        print("### CHAT PANEL:", json.dumps(panel, ensure_ascii=False))

        pg.fill('div[role="dialog"] input, div[role="dialog"] textarea', "平台有哪些核心功能？")
        pg.click('button[aria-label="发送"]')
        # 等流式结束
        for _ in range(60):
            pg.wait_for_timeout(1000)
            done = pg.evaluate("""() => {
              const b = document.querySelector('button[aria-label="发送"]');
              return b && !b.disabled;
            }""")
            txt = pg.evaluate("""() => {
              const d = document.querySelector('div[role="dialog"]');
              return d ? d.innerText.length : 0;
            }""")
            if done and txt > 300:
                break
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT, "d-chat-answer.png"))
        md = pg.evaluate("""() => {
          const d = document.querySelector('div[role="dialog"]');
          const bubbles = [...d.querySelectorAll('ul, ol, strong, code, p')];
          const body = d.innerText;
          return {
            ul: d.querySelectorAll('ul li').length,
            ol: d.querySelectorAll('ol li').length,
            strong: d.querySelectorAll('strong').length,
            rawDash: /(^|\\n)\\s*[-*]\\s+\\S/.test(body),
            rawStar: body.includes('**'),
            rawHash: /(^|\\n)#{1,4}\\s/.test(body),
            text: body.slice(-900),
          };
        }""")
        print("### CHAT MD:", json.dumps(md, ensure_ascii=False, indent=1))

        # ---- 检查 10：全站残留深墨蓝 / 旧 slate 灰 ----
        cur["k"] = "audit"
        AUDIT_JS = """() => {
          const bad = {ink:[], slate:[]};
          const INK = new Set(['rgb(15, 42, 74)','rgb(15,42,74)']);
          const SLATE = new Set(['rgb(100, 116, 139)','rgb(71, 85, 105)','rgb(51, 65, 85)',
                                 'rgb(148, 163, 184)','rgb(30, 41, 59)','rgb(15, 23, 42)']);
          for (const el of document.querySelectorAll('*')) {
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            const bg = cs.backgroundColor, fg = cs.color, bi = cs.backgroundImage;
            if (INK.has(bg) || (bi && bi.includes('15, 42, 74'))) {
              bad.ink.push({tag: el.tagName, cls: el.className.toString().slice(0,70),
                            bg, area: Math.round(r.width*r.height)});
            }
            if (SLATE.has(fg) && (el.textContent||'').trim().length > 1 &&
                el.children.length === 0) {
              bad.slate.push({tag: el.tagName, cls: el.className.toString().slice(0,70),
                              fg, txt: (el.textContent||'').trim().slice(0,30)});
            }
          }
          bad.ink.sort((a,b)=>b.area-a.area);
          return {ink: bad.ink.slice(0,12), inkN: bad.ink.length,
                  slate: bad.slate.slice(0,12), slateN: bad.slate.length};
        }"""
        routes = ["/", "/dashboard", "/panorama", "/jobs", "/talent", "/admin", "/me",
                  "/match", "/history", "/feedback", "/hr", "/evolution", "/discovery", "/login"]
        audit = {}
        for r in routes:
            cur["k"] = "audit" + r
            pg.goto(BASE + r, wait_until="domcontentloaded")
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            pg.wait_for_timeout(1800)
            audit[r] = pg.evaluate(AUDIT_JS)
        print("### AUDIT:")
        for r, v in audit.items():
            if v["inkN"] or v["slateN"]:
                print(f"  {r}: ink={v['inkN']} slate={v['slateN']}")
                for x in v["ink"][:4]:
                    print("     INK ", x)
                for x in v["slate"][:4]:
                    print("     SLATE", x)
        ctx.close()

        # ============ 移动 375x812（重新登录） ============
        ctx2 = browser.new_context(viewport={"width": 375, "height": 812}, locale="zh-CN",
                                   is_mobile=True, has_touch=True, device_scale_factor=2)
        ctx2.route("**/*", guard)
        pg2 = ctx2.new_page()
        cur2 = {"k": "m-boot"}
        pg2.on("response", lambda r: netlog.setdefault("M" + cur2["k"], []).append((r.status, r.url))
               if r.status >= 400 else None)
        pg2.on("requestfailed", lambda r: netlog.setdefault("M" + cur2["k"], []).append(("FAIL", r.url)))
        pg2.on("pageerror", lambda e: findings.append(("pageerror", "M" + cur2["k"], str(e)[:200])))

        cur2["k"] = "m-login"
        pg2.goto(BASE + "/login", wait_until="domcontentloaded")
        pg2.wait_for_load_state("networkidle", timeout=20000)
        pg2.wait_for_timeout(2000)
        pg2.screenshot(path=os.path.join(OUT, "m-login.png"), full_page=True)
        print("### mobile login url:", login(pg2))

        for path, key, wait in [("/", "m-portal", 3500), ("/dashboard", "m-dashboard", 4000),
                                ("/talent", "m-talent", 4500), ("/jobs", "m-jobs", 4000),
                                ("/admin", "m-admin", 4000), ("/me", "m-me", 3000),
                                ("/panorama", "m-panorama", 6000), ("/match", "m-match", 3000),
                                ("/history", "m-history", 3000), ("/hr", "m-hr", 3500)]:
            cur2["k"] = key
            pg2.goto(BASE + path, wait_until="domcontentloaded")
            try:
                pg2.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            pg2.wait_for_timeout(wait)
            pg2.screenshot(path=os.path.join(OUT, key + ".png"), full_page=True)
            # 横向溢出检测
            ov = pg2.evaluate("""() => {
              const de = document.documentElement;
              const over = [];
              for (const el of document.querySelectorAll('body *')) {
                const r = el.getBoundingClientRect();
                if (r.width > 1 && (r.right > de.clientWidth + 2 || r.left < -2)) {
                  const p = el.parentElement;
                  if (p && p.getBoundingClientRect().right > de.clientWidth + 2) continue;
                  over.push({tag: el.tagName, cls: el.className.toString().slice(0,60),
                             right: Math.round(r.right), left: Math.round(r.left)});
                }
              }
              return {scrollW: de.scrollWidth, clientW: de.clientWidth, over: over.slice(0,6)};
            }""")
            if ov["scrollW"] > ov["clientW"] + 1:
                print(f"  !! {key} 横向溢出 scrollW={ov['scrollW']} clientW={ov['clientW']}")
                for x in ov["over"]:
                    print("     ", x)

        # 1024px 断点上下各测一次
        for w in (1023, 1025):
            pg2.set_viewport_size({"width": w, "height": 900})
            for path, key in [("/dashboard", f"bp{w}-dashboard"), ("/talent", f"bp{w}-talent"),
                              ("/", f"bp{w}-portal")]:
                pg2.goto(BASE + path, wait_until="domcontentloaded")
                try:
                    pg2.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                pg2.wait_for_timeout(2500)
                pg2.screenshot(path=os.path.join(OUT, key + ".png"), full_page=False)

        ctx2.close()
        browser.close()

    print("\n### NET >=400 / FAILED:")
    seen = set()
    for k, v in netlog.items():
        for st, u in v:
            if (st, u) in seen:
                continue
            seen.add((st, u))
            print(f"  [{k}] {st} {u[:150]}")
    print("\n### PAGE ERRORS:", findings)


main()
