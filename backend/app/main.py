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

from app.api import agent, agent_data, analytics, assets, bridge, drafts, feeds, pipeline, publish
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

# CORS：仅开发期放开，生产需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
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
# RSS 源管理：可视化增删改，避免手改 json
app.include_router(feeds.router)
# 系统设置：内核 API Key / 模型 / 界面偏好
app.include_router(settings_api.router)


# ── 生成的素材静态服务（封面/内页/视频）────────────────────
# 素材在 backend/assets/ 下，_rel() 返回相对 backend 的路径（如
# assets/images/cover_xxx.png），所以直接挂 backend 根目录，
# 前端用 /files/assets/images/cover_xxx.png 引用。
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ASSETS_DIR = _BACKEND_DIR / "assets"
if _ASSETS_DIR.exists():
    app.mount("/files", StaticFiles(directory=str(_BACKEND_DIR)), name="files")


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
