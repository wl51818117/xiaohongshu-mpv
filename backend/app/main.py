"""应用入口。

启动：cd backend && uvicorn app.main:app --reload
文档： http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (agent, agent_data, ai, analytics, assets, bridge, commerce,
                     drafts, feeds, knowledge, pipeline, publish)
from app.api import settings as settings_api
from app.core.config import settings
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表，关闭时无需额外清理。"""
    init_db()
    print(f"[startup] {settings.app_name} v{settings.app_version}")
    print(f"[startup] 数据库: {settings.database_url}")
    print(f"[startup] 内核地址: {settings.kernel_base_url}")
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "小红书电商内容工作台· MVP\n\n"
        "本阶段实现一条最小可运行主流程：**RSS 采集 → 选题转换 → 选题库**\n\n"
        "设计依据 docs/08-终极选型方案v4单账号版.md"
    ),
    lifespan=lifespan,
)

# CORS：显式白名单，**不用 * + allow_credentials 的危险组合**
# ★ 安全修复（2026-10）：
#   原来是 `allow_origins=["*"] if debug else []` 配`allow_credentials=True`。
#   这两个一起用等于「任意网站的 JS 都能带凭据访问本机 API」——
#   而 /api/bridge/* 能把请求转发到带 pwsh/read/write 工具的 Agent 内核，
#   等于把 shell 暴露给了整个互联网。debug 只应放宽日志，不应放宽来源。
_ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",  # vite preview
    "http://localhost:4173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pipeline.router)
app.include_router(agent.router)
# Agent 写入接口：内核工具通过它把产出落库
app.include_router(agent_data.router)
# 稿件接口：文案编辑与规格校验
app.include_router(drafts.router)
# bridge 桥接：让浏览器端能力接到内核（hbridge v2.1）
app.include_router(bridge.router)
# 素材工坊：AI 生图 / 图生视频 / ffmpeg 合成
app.include_router(assets.router)
# 数据看板：快照 / 爆文提炼 / 回流选题
app.include_router(analytics.router)
# 发布队列：组装发布包 + 发布前自检（不代提交）
app.include_router(publish.router)
# AI 创作：标题批量生成 / 多轮打磨 / 标签推荐
app.include_router(ai.router)
# RSS 源管理：可视化增删改，避免手改 json
app.include_router(feeds.router)
# 系统设置：内核 API Key / 模型 / 界面偏好
app.include_router(settings_api.router)

app.include_router(knowledge.router)

# 商品/人群/咨询（2026-10 获客漏斗）
app.include_router(commerce.router)


# ── 生成的素材静态服务（封面/内页/视频）────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent
# ★ 安全修复（2026-10）：
#   原来挂的是 _BACKEND_DIR（整个后端目录），于是
#   `/files/data/secrets.json`（API 密钥密文）与
#   `/files/data/workbench.db`（整个数据库）都能直接 HTTP 下载。
#   叠加 Fernet 密钥由「数据目录路径 + 机器名」派生（都是公开信息），
#   等于任何人都能自行解密密钥。
#   现在只挂 assets/ —— 只有生成的素材需要被前端访问。
_ASSETS_DIR = _BACKEND_DIR / "assets"
if _ASSETS_DIR.exists():
    app.mount("/files", StaticFiles(directory=str(_ASSETS_DIR)), name="files")
    # 前端仍按 /files/assets/... 引用，故此处补一个 assets 前缀映射
    app.mount(
        "/files/assets",
        StaticFiles(directory=str(_ASSETS_DIR)),
        name="files-assets",
    )


@app.get("/", summary="服务信息")
def root() -> dict:
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "pipeline": [
            "GET  /api/pipeline/status   流水线状态",
            "POST /api/pipeline/collect  RSS 采集入库",
            "POST /api/pipeline/convert  素材转选题",
            "GET  /api/topics            选题库",
            "GET  /api/materials         素材库",
        ],
        "agent": [
            "GET  /api/agent/kernel      内核状态",
            "POST /api/agent/chat        对话（SSE）",
            "POST /api/agent/approve     审批决策",
        ],
    }
