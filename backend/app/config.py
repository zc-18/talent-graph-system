"""全局配置：从环境变量 / .env 读取。"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 数据库
    db_host: str = "101.200.184.201"
    db_port: int = 13306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "talent_graph"
    database_url_override: str | None = None

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Tavily
    tavily_api_key: str = ""

    # Serper.dev (Google 检索，第二独立来源)
    serper_api_key: str = ""

    # 向量嵌入
    embed_api_key: str = ""
    embed_base_url: str = "http://101.200.184.201:7001/v1"
    embed_model: str = "bge-small-zh-v1.5"
    embed_dim: int = 512

    app_port: int = 8200
    app_env: str = "development"
    cors_origins: str = "*"

    # 演示站只读开关（见 app/guards.py）。默认 False 以免本地/离线跑数据脚本被误挡；
    # 公网部署的 .env 必须显式 READ_ONLY=1。
    read_only: bool = False

    # System-owned confidence refresh. Public READ_ONLY only blocks user graph writes;
    # this evidence replay remains enabled and runs at 02:30 Asia/Shanghai.
    confidence_scheduler_enabled: bool = True
    confidence_scheduler_hour: int = 2
    confidence_scheduler_minute: int = 30

    # 每日动态数据挖掘（模拟聚合源 BOSS直聘，语料由赛事方提供）。
    # 默认 False：本地跑数据脚本时不希望后台线程也在写库；只有服务器 .env 置
    # MINING_ENABLED=1 才开启夜间作业。注意 guards.py 是 HTTP 层的闸，
    # 进程内调度器不受 READ_ONLY 约束（与 02:30 置信度批算同理），
    # 写入范围由 services/mining.py 的 INSERT-only 白名单自行约束。
    mining_enabled: bool = False
    mining_scheduler_hour: int = 0
    mining_scheduler_minute: int = 0
    mining_rows_per_day: int = 1000
    # 硬性日预算（人民币元）。累计超过即停止调用 LLM，剩余行降级为纯规则抽取。
    mining_daily_budget_cny: float = 0.30
    mining_llm_batch_size: int = 10
    # 挖掘专用 key：把这份预算和 chat/discovery 的用量隔开；留空则回落到 deepseek_api_key
    mining_llm_api_key: str = ""
    mining_shard_dir: str = ""          # 留空 = backend/data/aggregate_source
    mining_source_label: str = "BOSS直聘"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
