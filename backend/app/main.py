"""FastAPI 应用入口。"""
from __future__ import annotations
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .config import settings
from .db import init_db
from .routers import jobs, graph, discovery, evolution, match, chat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("talent-graph")

app = FastAPI(
    title="多源异构数据驱动岗位和能力图谱系统",
    description="新岗位发现·既有岗位能力演化·全景图谱·人岗匹配诊断（含反幻觉交叉验证）",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_origins == "*" else settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(graph.router)
app.include_router(discovery.router)
app.include_router(evolution.router)
app.include_router(match.router)
app.include_router(chat.router)


@app.on_event("startup")
def _startup():
    try:
        init_db()
        logger.info("数据库初始化完成")
    except Exception as e:  # noqa: BLE001
        logger.error("数据库初始化失败: %s", e)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "talent-graph", "version": "1.0.0"}


# ---------------- 静态前端（SPA）----------------
# 若存在打包后的前端 dist，则由后端同端口托管，无需额外 nginx。
_STATIC_DIR = os.environ.get("STATIC_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

if os.path.isdir(_STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api"):
            return {"detail": "Not Found"}
        candidate = os.path.join(_STATIC_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
else:
    @app.get("/")
    def root():
        return {"name": "多源异构数据驱动岗位和能力图谱系统 API", "docs": "/docs",
                "health": "/api/health"}

