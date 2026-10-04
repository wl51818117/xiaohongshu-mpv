"""应用配置：集中管理，避免散落各处的硬编码。

技术栈依据 docs/08-终极选型方案v4单账号版.md。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/ 是项目根
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """从环境变量或 .env 读取配置。"""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 服务 ──
    app_name: str = "电商工作台"
    app_version: str = "0.1.0"
    debug: bool = True

    # ── 数据库 ──
    # MVP 用 SQLite 零依赖；生产改 PostgreSQL 只需换这一行
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'data' / 'workbench.db'}"

    # ── dsh 内核 ──
    kernel_base_url: str = "http://127.0.0.1:8787"
    kernel_timeout_seconds: int = 120

    # ── 内容规格（docs/02-内容规格与合规基线.md）──
    xhs_title_max_chars: int = 20
    xhs_body_min_chars: int = 300
    xhs_body_max_chars: int = 800
    xhs_tags_min: int = 3
    xhs_tags_max: int = 5
    xhs_img_min: int = 4
    xhs_img_max: int = 8

    # ── 选题转换与二次创作（docs/07）──
    # 至少满足的差异化维度数：换人群/换场景/换角度/补增量
    xhs_differentiation_min: int = 3
    # 同质化相似度阈值，超过判低质（平台抄袭判定线是0.60，留 0.10 余量）
    xhs_similarity_limit: float = 0.70

    @property
    def data_dir(self) -> Path:
        """确保数据目录存在。"""
        path = BACKEND_ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """单例配置。生产环境可改用 redis/memcached 缓存。"""
    return Settings()


settings = get_settings()
