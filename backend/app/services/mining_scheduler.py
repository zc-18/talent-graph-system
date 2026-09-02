"""每日动态挖掘作业的进程内调度器（00:00 Asia/Shanghai）。

形状照抄 services/confidence_batch.py::ConfidenceScheduler —— daemon 线程 +
Event.wait，不引入 APScheduler/Celery，与既有的 02:30 置信度批算保持一致。

两点需要写清楚：

1. **它不受 READ_ONLY 约束。** app/guards.py 是 HTTP 层的闸，挡的是访客点击触发的
   图谱写入；进程内的系统作业走服务层，和 02:30 置信度重算同理。约束由
   services/mining.py 自己的 INSERT-only 白名单负责（只碰 skill/job_skill/evidence，
   且 job_skill.status 恒为 candidate），而不是靠这里。
2. **默认关闭。** settings.mining_enabled 默认 False，本地跑数据脚本时不希望后台
   线程也在写库；只有服务器 .env 显式置 MINING_ENABLED=1 才启用。

触发时刻取 00:00，此时新的一天刚开始，该批次即以这一天为 run_date——这正是
「以当下为基准的下一天」的口径。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread

from ..config import settings
from ..db import SessionLocal

logger = logging.getLogger("talent-graph.mining")
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
UTC = timezone.utc


def next_scheduled_local(now: datetime | None = None) -> datetime:
    """下一次触发时刻（北京时区，带 tzinfo）。"""
    current = (now or datetime.now(UTC)).astimezone(BEIJING)
    target = current.replace(hour=settings.mining_scheduler_hour,
                             minute=settings.mining_scheduler_minute,
                             second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target


def next_scheduled_utc(now: datetime | None = None) -> datetime:
    """下一次触发时刻（naive UTC，供与 datetime.utcnow() 相减）。"""
    return next_scheduled_local(now).astimezone(UTC).replace(tzinfo=None)


class MiningScheduler:
    def __init__(self) -> None:
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def start(self) -> None:
        if not settings.mining_enabled:
            logger.info("daily mining scheduler disabled (MINING_ENABLED=0)")
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="mining-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            scheduled_local = next_scheduled_local()
            wait_seconds = max(0.0, (next_scheduled_utc() - datetime.utcnow()).total_seconds())
            logger.info("next daily mining run scheduled for %s", scheduled_local.isoformat())
            if self._stop.wait(wait_seconds):
                return
            # 延迟导入：调度器在应用启动时构造，而挖掘服务会拉起分片文件与 LLM 客户端，
            # 没必要为一个默认关闭的作业拖慢冷启动，也避免启动期的循环导入。
            try:
                from . import mining
            except Exception:
                logger.exception("mining service unavailable; skipping run")
                continue
            run_date = scheduled_local.strftime("%Y-%m-%d")
            db = SessionLocal()
            try:
                result = mining.run_daily_mining(db, run_date=run_date, dry_run=False)
                logger.info("daily mining run %s completed: %s", run_date, result)
            except Exception:
                logger.exception("scheduled daily mining run %s failed", run_date)
            finally:
                db.close()


scheduler = MiningScheduler()
