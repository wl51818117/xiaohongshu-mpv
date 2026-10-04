"""应用配置：集中管理，避免散落各处的硬编码。

技术栈依据 docs/08-终极选型方案v4单账号版.md。
"""

from __future__ import annotations

import re
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
    # 内核静态凭据。留空则自动从内核 profile 的 cordis.patch.yml 读
    # （那是唯一真源，避免两处各存一份 token 后悄悄不一致）。
    kernel_token: str = ""
    kernel_app_id: str = "workbench"
    # 内核 profile 的 patch 文件路径，用于自动取 token。
    kernel_patch_file: str = ""

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

    @property
    def kernel_auth_headers(self) -> dict[str, str]:
        """内核请求所需的头。

        hbridge v2.1 起 **所有** /v1/* 都要 `authorization: Bearer <token>`，
        漏掉会得到 401（表现为「Agent 在线但一句话都不回」）。
        token 优先取环境变量，取不到就从内核 profile 的 patch 文件里读——
        那是唯一真源，避免两处各存一份后悄悄不一致。
        """
        headers: dict[str, str] = {}
        token = self.kernel_token.strip() or _token_from_kernel_patch()
        if token:
            headers["authorization"] = f"Bearer {token}"
        if self.kernel_app_id:
            headers["x-harness-app"] = self.kernel_app_id
        return headers


def _token_from_kernel_patch() -> str:
    """从内核 profile 的 cordis.patch.yml 取全局静态 token。

    只认独立成行的 `token:` —— `appTokens:` 里也含 "token" 字样，
    用宽松正则会先匹配到它，导致 401（这个坑踩过）。
    """
    raw = settings.kernel_patch_file.strip()
    if not raw:
        # 默认按 start-all.js 的约定路径推断
        guess = Path(r"E:/Ai-workbuddy/提取harness/.dsh/profiles/kernel/cordis.patch.yml")
        raw = str(guess) if guess.exists() else ""
    if not raw or not Path(raw).exists():
        return ""
    try:
        text = Path(raw).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^[ \t]+token:[ \t]*['\"]([^'\"]+)['\"]", text, re.M)
    return m.group(1) if m else ""


@lru_cache
def get_settings() -> Settings:
    """单例配置。生产环境可改用 redis/memcached 缓存。"""
    return Settings()


settings = get_settings()
