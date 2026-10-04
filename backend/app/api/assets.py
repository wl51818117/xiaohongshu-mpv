"""素材工坊 API（M4）。

生成封面/内页、图生视频、ffmpeg 合成、素材校验。
生成结果写回 drafts 的 cover_url / images 字段，前端即时可见。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Draft
from app.db.session import get_db
from app.services import asset_generator as ag

router = APIRouter(prefix="/api/assets", tags=["assets"])


class GenerateRequest(BaseModel):
    """生成素材请求。"""

    draft_id: int
    kind: str = Field("cover", description="cover 封面 / inner 内页")
    style: str = Field("realistic", description="realistic / clean / vivid")
    count: int = Field(6, ge=1, le=8, description="内页数量")


class VideoRequest(BaseModel):
    """图生视频请求。"""

    draft_id: int
    first_frame: str = Field(..., description="首帧图路径（做图生视频的输入）")
    scene: str = Field("static", description="rotate / push / static / detail")


class ComposeRequest(BaseModel):
    """视频合成请求。"""

    draft_id: int
    clips: list[str] = Field(..., min_length=1)


class ValidateRequest(BaseModel):
    path: str
    kind: str = Field("image", description="image / video")


@router.post("/generate", summary="生成封面或内页")
async def generate(
    request: Request,
    draft_id: int | None = None,
    kind: str = "cover",
    style: str = "realistic",
    count: int = 6,
    db: Session = Depends(get_db),
) -> dict:
    """生成图片素材并写回稿件。

    参数来源：优先 JSON body，其次 query —— 两者都支持，
    方便从地址栏或 curl 快速触发。

    ★ body 用 Request 自己读，不声明成 Pydantic 模型：
      声明 `GenerateRequest | None` 时，body 传 `{}` 会被 FastAPI
      拿去实例化该模型并因缺必填字段直接 422，
      根本走不到「query 兜底」的合并逻辑（踩过）。
    """
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}

    if body.get("draft_id"):
        draft_id = int(body["draft_id"])
    if body.get("kind"):
        kind = str(body["kind"])
    if body.get("style"):
        style = str(body["style"])
    if body.get("count"):
        count = int(body["count"])

    if not draft_id:
        raise HTTPException(status_code=400, detail="缺少 draft_id")

    draft = db.get(Draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="稿件不存在")

    title = draft.title or (draft.topic.title if draft.topic else "未命名")

    # ★ 构造稿件上下文 —— 素材与稿件同源的关键。
    #   原来只传 title，导致生成出来的图与正文内容毫无关系。
    #   keyword 存在 Topic 上（Draft 本身没有这个字段），
    #   且 Draft 可能没有关联 Topic，所以逐级兜底。
    keyword = draft.topic.keyword_target if draft.topic else ""
    ctx = ag.DraftContext(
        title=title,
        keyword=keyword or "",
        body=draft.body or "",
        tags=list(draft.tags or []),
        topic_title=(draft.topic.title if draft.topic else ""),
    )

    if kind == "cover":
        r = ag.generate_cover(ctx, style)
        if r.ok and r.files:
            # 首个作为当前封面，其余留作备选（存images 前位）
            draft.cover_url = r.files[0]
            draft.images = r.files
            db.commit()
        return {
            "ok": r.ok, "kind": "cover", "files": r.files,
            "prompt": r.prompt, "provider": r.provider, "error": r.error,
            "validation": ag.validate_asset(r.files[0]) if r.files else None,
            "context_used": {
                "keyword": ctx.keyword,
                "key_points": ctx.key_points,
                "tags": ctx.tags,
                "body_len": len(ctx.body),
            },
        }

    r = ag.generate_inner(ctx, count)
    if r.ok and r.files:
        # 内页追加（封面保持在首位）
        existing = list(draft.images or [])
        if draft.cover_url and not existing:
            existing = [draft.cover_url]
        draft.images = existing + r.files
        db.commit()
    return {
        "ok": r.ok, "kind": "inner", "files": r.files,
        "prompt": r.prompt, "provider": r.provider, "error": r.error,
        "validation": None,
        "context_used": {
            "keyword": ctx.keyword,
            "key_points": ctx.key_points,
            "tags": ctx.tags,
            "body_len": len(ctx.body),
        },
    }


@router.get("/provider", summary="生图服务配置状态")
def get_provider() -> dict:
    """返回生图配置状态（**不含密钥明文**）。"""
    return ag.provider_status()


class ProviderIn(BaseModel):
    """生图服务配置。"""

    base_url: str = Field("", description="服务地址，如 https://api.siliconflow.cn/v1")
    api_key: str = Field("", description="API 密钥（加密存储）")
    model: str = Field("", description="模型名，如 black-forest-labs/FLUX.1-schnell")
    kind: str = Field("openai", description="openai / seedream")
    size: str = Field("1080x1440", description="出图尺寸，硬要求 3:4")
    enabled: bool = Field(False, description="是否启用真实生图")
    timeout: int = Field(180, ge=10, le=600)


@router.put("/provider", summary="保存生图服务配置")
def save_provider(cfg: ProviderIn) -> dict:
    """保存生图配置。密钥 Fernet 加密落盘，查询只返掩码。"""
    from app.api.settings import _enc  # 复用同一套加密

    base = cfg.base_url.strip().rstrip("/")
    if cfg.enabled:
        missing = [
            name
            for name, val in (("地址", base), ("密钥", cfg.api_key.strip()),
                               ("模型", cfg.model.strip()))
            if not val
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"启用生图前必须填写：{'、'.join(missing)}",
            )
        if not base.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="地址必须以 http:// 或 https:// 开头")

    data = {
        "base_url": base,
        "api_key": _enc(cfg.api_key.strip()) if cfg.api_key.strip() else "",
        "model": cfg.model.strip(),
        "kind": cfg.kind if cfg.kind in ("openai", "seedream") else "openai",
        "size": cfg.size or "1080x1440",
        "enabled": cfg.enabled,
        "timeout": cfg.timeout,
    }
    ag._PROVIDER_FILE.parent.mkdir(parents=True, exist_ok=True)
    ag._PROVIDER_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "status": ag.provider_status()}


class ProviderTestIn(BaseModel):
    base_url: str
    api_key: str
    model: str = ""
    kind: str = "openai"
    size: str = "1080x1440"


@router.post("/provider/test", summary="测试生图服务连通性")
def test_provider(cfg: ProviderTestIn) -> dict:
    """真实发一次最小生图请求，验证地址/密钥/模型是否可用。"""
    import time

    from app.api.settings import _dec

    key = cfg.api_key.strip()
    # 允许用已保存的掩码回测
    if not key or "..." in key:
        saved = ag.load_image_provider()
        key = _dec(saved.get("api_key") or "") if saved.get("api_key") else ""

    if not cfg.base_url.strip() or not key:
        return {"ok": False, "status": "incomplete",
                "message": "地址与密钥都不能为空"}

    base = cfg.base_url.strip().rstrip("/")
    model = (cfg.model or "").strip() or "black-forest-labs/FLUX.1-schnell"
    size = cfg.size or "1080x1440"
    payload: dict = {"model": model, "prompt": "一朵云", "n": 1}
    if cfg.kind == "seedream":
        try:
            w, h = (int(x) for x in size.lower().split("x"))
        except Exception:  # noqa: BLE001
            w, h = 1080, 1440
        payload.update({"width": w, "height": h, "response_format": "url",
                        "watermark": False})
    else:
        payload["size"] = size

    t0 = time.time()
    try:
        import httpx

        r = httpx.post(
            f"{base}/images/generations",
            json=payload,
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {key}"},
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "unreachable", "ms": int((time.time() - t0) * 1000),
                "message": f"地址无法访问：{exc}"}
    ms = int((time.time() - t0) * 1000)

    if r.status_code == 200:
        try:
            has = bool((r.json().get("data") or []))
        except Exception:  # noqa: BLE001
            has = False
        return {"ok": True, "status": "success" if has else "bad_request", "ms": ms,
                "message": "连接成功，已返回图片" if has else "连接成功但未返回图片，检查模型名"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": "auth_failed", "ms": ms,
                "message": "密钥无效或无权限"}
    if r.status_code == 404:
        return {"ok": False, "status": "bad_request", "ms": ms,
                "message": "接口不存在，检查地址是否含/v1 等路径后缀"}
    if r.status_code in (502, 503, 504):
        return {"ok": False, "status": "unreachable", "ms": ms,
                "message": "服务暂时不可用（网关错误），稍后重试"}
    return {"ok": False, "status": "error", "ms": ms,
            "message": f"HTTP {r.status_code}：{r.text[:150]}"}


@router.post("/video", summary="图生视频")
def gen_video(req: VideoRequest, db: Session = Depends(get_db)) -> dict:
    """基于首帧图生成视频片段。

    ★ 必须图生视频：文生视频画面不可控，保证不了与商品一致，电商不可用。
    当前返回运镜提示词 + 待接入说明（真实生视频需接 Seedance 等 API）。
    """
    draft = db.get(Draft, req.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="稿件不存在")

    title = draft.title or (draft.topic.title if draft.topic else "未命名")
    prompt = ag.build_video_prompt(title, req.scene)

    src = req.first_frame
    real = ag.ASSET_ROOT.parent / src
    if not real.exists():
        raise HTTPException(status_code=400, detail=f"首帧图不存在：{src}")

    return {
        "ok": True,
        "kind": "video",
        "prompt": prompt,
        "first_frame": src,
        "provider": "pending-api",
        "note": (
            "运镜提示词已生成。真实视频需接 Seedance/Vidu 等图生视频 API，"
            "接入点：asset_generator._call_provider"
        ),
        "hint": "AI 单镜头最佳 3-5 秒，画面才稳定不崩坏",
    }


@router.post("/compose", summary="ffmpeg 合成视频")
def compose(req: ComposeRequest, db: Session = Depends(get_db)) -> dict:
    """把多个片段拼成一条完整视频（统一 3:4 / 30fps）。"""
    draft = db.get(Draft, req.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="稿件不存在")

    r = ag.compose_video(req.clips)
    result = {
        "ok": r.ok, "files": r.files, "error": r.error,
        "meta": r.meta, "validation": None, "probe": None,
    }
    if r.ok and r.files:
        result["validation"] = ag.validate_asset(r.files[0], "video")
        result["probe"] = ag.probe_media(r.files[0])
        # 合成产物写回稿件（放 cover 位之外的 videos 字段用 images 承载不便，
        # 这里存在素材列表首项）
        draft.images = list(draft.images or []) + r.files
        db.commit()
    return result


@router.post("/validate", summary="素材规格校验")
def validate(req: ValidateRequest) -> dict:
    """按 docs/02 的硬规格校验素材（图/视频）。"""
    return ag.validate_asset(req.path, req.kind)


@router.get("/probe", summary="探测媒体规格")
def probe(path: str) -> dict:
    """ffprobe 探测，返回分辨率/帧率/时长。"""
    return ag.probe_media(path)
