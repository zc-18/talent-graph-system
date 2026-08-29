import os
from playwright.sync_api import sync_playwright
BASE="http://localhost:5190"; OUT=r"C:\Users\32768\Desktop\project\挑战杯\_shots\out\r6"
ALLOW=("/api/auth/login","/api/chat")
def guard(route,req):
    if req.method!="GET" and not any(p in req.url for p in ALLOW):
        print("BLOCKED",req.method,req.url); route.abort(); return
    route.continue_()
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    for vp,tag in [({"width":1440,"height":900},"d"),({"width":375,"height":812},"m")]:
        ctx=b.new_context(viewport=vp,locale="zh-CN",device_scale_factor=1)
        ctx.route("**/*",guard); pg=ctx.new_page()
        bad=[]
        pg.on("response",lambda r: bad.append((r.status,r.url)) if r.status>=400 else None)
        pg.on("pageerror",lambda e: bad.append(("JS",str(e)[:180])))
        pg.goto(BASE+"/login",wait_until="domcontentloaded")
        pg.wait_for_selector('input[autocomplete="username"]',timeout=15000)
        pg.fill('input[autocomplete="username"]',"demo-admin"); pg.fill("input[type=password]","DemoAdmin123!")
        pg.click("button[type=submit]"); pg.wait_for_timeout(4500)
        pg.goto(BASE+"/jobs",wait_until="domcontentloaded")
        pg.wait_for_load_state("networkidle",timeout=20000); pg.wait_for_timeout(2500)
        pg.evaluate("""()=>{const d=[...document.querySelectorAll('div')].find(x=>x.getAttribute('class')===null&&x.onclick);}""")
        # 点第一张岗位卡的标题区（只读跳转）
        pg.click("text=岗位库管理", timeout=5000) if False else None
        cards = pg.locator("h3, .font-bold").first
        pg.evaluate("""()=>{ const el=[...document.querySelectorAll('div')].filter(d=>d.className==='' ); }""")
        # 直接用 react onClick：找卡片里的岗位名节点点一下
        try:
            pg.locator("div[class='']").first.click(timeout=3000)
        except Exception:
            pass
        if "/jobs/" not in pg.url:
            # 兜底：从 API 拿一个 id 直接 goto
            jid = pg.evaluate("""async()=>{const t=sessionStorage.getItem('talent_graph_session');
              const r=await fetch('/api/jobs?page=1&size=1',{headers:{Authorization:'Bearer '+t}});
              const d=await r.json(); return (d.items&&d.items[0])?d.items[0].id:null;}""")
            print(tag,"job id",jid)
            if jid: pg.goto(BASE+f"/jobs/{jid}",wait_until="domcontentloaded")
        try: pg.wait_for_load_state("networkidle",timeout=20000)
        except Exception: pass
        pg.wait_for_timeout(3500)
        print(tag,"url",pg.url)
        pg.screenshot(path=os.path.join(OUT,f"{tag}-jobdetail.png"),full_page=True)
        print(tag,"issues:",sorted(set(bad)) if bad else "无")
        ctx.close()
    b.close()
