"""素材工坊 API（M4）。

生成封面/内页、图生视频、ffmpeg 合成、素材校验。
生成结果写回 drafts 的 cover_url / images 字段，前端即时可见。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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
def generate(req: GenerateRequest, db: Session = Depends(get_db)) -> dict:
    """生成图片素材并写回稿件。

    ★ 当前是本地占位实现（尺寸真实、内容待接 AI API），
      接真实生图只需替换 asset_generator._call_provider。
    """
    draft = db.query(Draft).get(req.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="稿件不存在")

    title = draft.title or (draft.topic.title if draft.topic else "未命名")

    if req.kind == "cover":
        r = ag.generate_cover(req.draft_id, title, req.style)
        if r.ok and r.files:
            # 首个作为当前封面，其余留作备选（存images 前位）
            draft.cover_url = r.files[0]
            draft.images = r.files
            db.commit()
        return {
            "ok": r.ok, "kind": "cover", "files": r.files,
            "prompt": r.prompt, "provider": r.provider, "error": r.error,
            "validation": ag.validate_asset(r.files[0]) if r.files else None,
        }

    r = ag.generate_inner(req.draft_id, title, req.count)
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
    }


@router.post("/video", summary="图生视频")
def gen_video(req: VideoRequest, db: Session = Depends(get_db)) -> dict:
    """基于首帧图生成视频片段。

    ★ 必须图生视频：文生视频画面不可控，保证不了与商品一致，电商不可用。
    当前返回运镜提示词 + 待接入说明（真实生视频需接 Seedance 等 API）。
    """
    draft = db.query(Draft).get(req.draft_id)
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
    draft = db.query(Draft).get(req.draft_id)
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
