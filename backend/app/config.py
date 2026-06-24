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
    cors_origins: str = "*"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
