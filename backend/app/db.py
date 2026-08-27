"""数据库引擎与会话。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from .config import settings

# 连接池保持很小，避免占用云服务器内存。本地浏览器/E2E 可通过
# DATABASE_URL_OVERRIDE 使用隔离的 SQLite 文件，绝不需要连接云 MySQL。
_engine_options = {"pool_pre_ping": True, "echo": False}
if settings.database_url.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}
else:
    _engine_options.update(pool_size=5, max_overflow=2, pool_recycle=1800)
engine = create_engine(settings.database_url, **_engine_options)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表。"""
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
