# -*- coding: utf-8 -*-
"""Lane G r6 — 第三轮回归扫描：图标可见性 / 对比度 / 元素重叠 / 残留旧色。"""
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


# 图标块：有背景（渐变或实色）却没有可见 svg 子元素 → 就是「纯渐变方块无字形」
ICON_JS = r"""() => {
  const out = [];
  for (const el of document.querySelectorAll('span,div,button,a')) {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 20 || r.width > 90 || r.height > 90) continue;
    if (Math.abs(r.width - r.height) > 6) continue;         // 近正方形
    const cs = getComputedStyle(el);
    const hasBg = cs.backgroundImage !== 'none' ||
                  (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent');
    if (!hasBg) continue;
    if (!/rounded|rings|grid|place-items/.test(el.className.toString())) continue;
    const svg = el.querySelector('svg');
    const img = el.querySelector('img');
    const txt = (el.textContent || '').trim();
    if (svg) {
      const sr = svg.getBoundingClientRect();
      if (sr.width < 6 || sr.height < 6) {
        out.push({why: 'svg-zero-size', cls: el.className.toString().slice(0, 80),
                  rect: [Math.round(r.width), Math.round(r.height)]});
        continue;
      }
      // svg 描边/填充与容器背景同色 → 看不见
      const scs = getComputedStyle(svg);
      out.push({ok: true, stroke: scs.stroke, fill: scs.fill, color: scs.color,
                bg: cs.backgroundColor, bgImg: cs.backgroundImage.slice(0, 40),
                cls: el.className.toString().slice(0, 60)});
      continue;
    }
    if (img || txt) continue;
    out.push({why: 'EMPTY-BLOCK', cls: el.className.toString().slice(0, 90),
              bg: cs.backgroundColor, bgImg: cs.backgroundImage.slice(0, 80),
              rect: [Math.round(r.width), Math.round(r.height)],
              at: [Math.round(r.x), Math.round(r.y)]});
  }
  return out;
}"""

# 文字对比度：只看真正的文本叶子节点
CONTRAST_JS = r"""() => {
  const lum = (c) => {
    const m = c.match(/\d+(\.\d+)?/g); if (!m) return null;
    const [r,g,b] = m.slice(0,3).map(Number);
    const f = v => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055,2.4); };
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b);
  };
  const bgOf = (el) => {
    let n = el;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      const bg = cs.backgroundColor;
      const m = bg.match(/rgba?\(([^)]+)\)/);
      if (m) { const p = m[1].split(',').map(s=>parseFloat(s));
        if (p.length < 4 || p[3] > 0.85) return bg; }
      if (cs.backgroundImage !== 'none') return null;   // 渐变/图片，跳过不判
      n = n.parentElement;
    }
    return 'rgb(242,245,251)';
  };
  const bad = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const t = (el.textContent||'').trim(); if (t.length < 2) continue;
    const r = el.getBoundingClientRect(); if (r.width<4||r.height<4) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility==='hidden'||cs.opacity==='0') continue;
    const bg = bgOf(el); if (!bg) continue;
    const l1 = lum(cs.color), l2 = lum(bg); if (l1==null||l2==null) continue;
    const ratio = (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight)||400;
    const large = size>=24 || (size>=18.66 && weight>=700);
    const need = large ? 3.0 : 4.5;
    if (ratio < need - 0.15) {
      bad.push({txt: t.slice(0,26), ratio: +ratio.toFixed(2), need, size,
                fg: cs.color, bg, cls: el.className.toString().slice(0,60)});
    }
  }
  return bad;
}"""

ROUTES = ["/", "/dashboard", "/panorama", "/jobs", "/talent", "/admin", "/me",
          "/match", "/history", "/feedback", "/evolution", "/discovery", "/login"]


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        ctx.route("**/*", guard)
        pg = ctx.new_page()
        pg.goto(BASE + "/login", wait_until="domcontentloaded")
        pg.wait_for_selector('input[autocomplete="username"]', timeout=15000)
        pg.fill('input[autocomplete="username"]', "demo-admin")
        pg.fill("input[type=password]", "DemoAdmin123!")
        pg.click("button[type=submit]")
        pg.wait_for_timeout(4500)

        print("========== 空图标块 / svg 零尺寸 ==========")
        for r in ROUTES:
            pg.goto(BASE + r, wait_until="domcontentloaded")
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            pg.wait_for_timeout(2000)
            res = pg.evaluate(ICON_JS)
            probs = [x for x in res if not x.get("ok")]
            if probs:
                print(f"-- {r}: {len(probs)} 处")
                for x in probs[:8]:
                    print("   ", json.dumps(x, ensure_ascii=False))
            # svg 与容器同色检测
            same = [x for x in res if x.get("ok") and x.get("bg") and x.get("color")
                    and x["bgImg"] == "none" and x["color"] == x["bg"]]
            for x in same[:5]:
                print(f"-- {r} 同色图标:", json.dumps(x, ensure_ascii=False))

        print("\n========== 文本对比度不足 ==========")
        for r in ROUTES:
            pg.goto(BASE + r, wait_until="domcontentloaded")
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            pg.wait_for_timeout(2000)
            bad = pg.evaluate(CONTRAST_JS)
            # 去重
            uniq, seen = [], set()
            for x in bad:
                k = (x["fg"], x["bg"], round(x["size"]))
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(x)
            if uniq:
                print(f"-- {r}: {len(bad)} 个节点 / {len(uniq)} 种组合")
                for x in uniq[:8]:
                    print("   ", json.dumps(x, ensure_ascii=False))

        ctx.close()
        b.close()


main()
