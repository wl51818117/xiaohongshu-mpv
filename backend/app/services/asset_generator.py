"""素材生成服务（M4 素材工坊）。

两条产线：
  图文——AI 生图（封面 3 候选 + 内页 4-8 张）
  视频——首帧图 → 图生视频 → ffmpeg 拼接合成

★ 关键约束（来自 docs/02 与实测评测）：
  - 封面必须 3:4（1080×1440），点击率<2.5% 难晋级流量池 → 出 3 候选让人挑
  - 视频**必须用图生视频**：文生视频画面不可控，无法保证与商品一致，电商不可用
  - AI 素材生命周期仅约 15 天，需打 freshness 标签
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.config import settings

# ── 规格常量（docs/02-内容规格与合规基线）──
COVER_W, COVER_H = 1080, 1440          # 3:4 竖版
IMAGE_MIN, IMAGE_MAX = 4, 8# 内页数量
COVER_CANDIDATES = 3                    # 封面出 3 个候选
VIDEO_MAX_SEC = 60                      # 视频时长上限（秒）
AI_ASSET_TTL_DAYS = 15                  # AI 素材生命周期

# 素材根目录（不入库）
ASSET_ROOT = settings.data_dir.parent / "assets"
GEN_IMG_DIR = ASSET_ROOT / "images"
GEN_VIDEO_DIR = ASSET_ROOT / "videos"


def _ensure_dirs() -> None:
    for d in (ASSET_ROOT, GEN_IMG_DIR, GEN_VIDEO_DIR):
        d.mkdir(parents=True, exist_ok=True)


@dataclass
class GenResult:
    """一次生成的产物清单。"""

    ok: bool
    kind: str# image / video / compose
    files: list[str]
    prompt: str = ""
    provider: str = ""
    error: str = ""
    meta: dict | None = None


def _rel(path: Path) -> str:
    """转成相对后端目录的路径，便于前端引用。"""
    try:
        return str(path.relative_to(settings.data_dir.parent)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


# ── 提示词构造 ────────────────────────────────────────────

def build_cover_prompt(title: str, style: str = "realistic") -> str:
    """封面提示词。

    3:4 竖版是硬要求，直接写进提示词而不是生成后再裁——
    这样模型的构图会按竖版设计。
    """
    style_map = {
        "realistic": "写实摄影风格，自然光，浅景深，真实质感",
        "clean": "干净极简风，柔和光线，纯色背景，留白充足",
        "vivid": "色彩鲜明，视觉冲击强，高饱和",
    }
    desc = style_map.get(style, style_map["realistic"])
    return (
        f"小红书封面图，3:4 竖版构图（1080×1440）。"
        f"主题：{title}。"
        f"风格：{desc}。"
        f"要求：主体突出，占画面 60% 以上；画面简洁；"
        f"文字区域不超过画面 30%；右下角预留信息区。"
        f"禁止出现：水印、二维码、联系方式、文字乱码。"
    )


def build_inner_prompt(title: str, index: int, total: int) -> str:
    """内页提示词，按九宫格顺序分配内容。"""
    roles = [
        "产品全景展示",
        "材质/工艺细节特写",
        "使用场景实拍",
        "对比效果呈现",
        "核心卖点图解",
        "选购/使用指引",
    ]
    role = roles[(index - 1) % len(roles)]
    return (
        f"小红书图文内页第 {index}/{total} 张，3:4 竖版（1080×1440）。"
        f"主题：{title}。画面内容：{role}。"
        f"要求：写实摄影风格，主体清晰，构图简洁，"
        f"每张只讲一个点，不要堆砌信息。"
        f"禁止出现：水印、二维码、联系方式。"
    )


def build_video_prompt(title: str, scene: str = "") -> str:
    """图生视频的运镜提示词。

    ★ 注意：实际调用时以图片为主，提示词只描述运镜与动态，
    AI 单镜头最佳 3-5 秒，画面才稳定不崩坏。
    """
    scene_map = {
        "rotate": "镜头缓慢环绕主体，运动幅度小",
        "push": "镜头缓慢推近主体",
        "static": "镜头基本静止，只有主体轻微动作",
        "detail": "镜头聚焦细节做微距移动",
    }
    motion = scene_map.get(scene, scene_map["static"])
    return (
        f"基于首帧图生成短视频。主题：{title}。"
        f"运镜：{motion}。"
        f"要求：画面稳定不闪烁，人体/物体结构不变形，时长 3-5 秒。"
    )


# ── Provider 抽象（对应 TODO P2 的 AI Provider 层）─────────

def _call_provider(task: str, prompt: str, **kwargs) -> GenResult:
    """统一调用入口。

    ★ 当前实现：本地「无外部依赖」的可运行方案。
      真实 AI 生图需接Seedream / 本机 ImageGen，此处留好接口形状——
      只要换掉这个函数，上层不用动。

    为什么先做占位实现：
      MVP 阶段最该验证的是「素材管线能不能跑通」（下载、尺寸校验、
      ffmpeg 合成、规格拦截），而不是画面美不美。接API 只是换一行。
    """
    _ensure_dirs()
    return _local_placeholder(task, prompt, **kwargs)


def _local_placeholder(task: str, prompt: str, **kwargs) -> GenResult:
    """本地占位实现：产出符合规格的占位文件。

    用 PIL 画一张带文字的规格占位图，保证：
      - 尺寸真实符合 3:4
      - 流程能跑通（生成→落盘→入库→前端展示）
      - 后续接真实 API 时上层零改动
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return GenResult(
            ok=False, kind=task, files=[], prompt=prompt,
            error="未安装 Pillow，无法生成占位图。pip install pillow",
        )

    _ensure_dirs()
    files: list[str] = []
    seed = int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16)
    hue = seed % 360

    for i in range(int(kwargs.get("count", 1))):
        # HLS→RGB 在 colorsys，不在 PIL.ImageColor（踩过）
        r, g, b = colorsys.hls_to_rgb(hue / 360.0, 0.78, 0.92)
        img = Image.new("RGB", (COVER_W, COVER_H), (int(r * 255), int(g * 255), int(b * 255)))
        d = ImageDraw.Draw(img)

        # 标注：这是占位图，避免误当真实素材
        name = f"{task}_{uuid.uuid4().hex[:8]}.png"
        path = GEN_IMG_DIR / name

        d.rectangle([60, 60, COVER_W - 60, 200], fill=(255, 255, 255))
        d.text((90, 100), "占位图 · 待接AI 生图", fill=(120, 80, 20))

        # 底部标注尺寸
        d.text((90, COVER_H - 140), f"{COVER_W}x{COVER_H} (3:4)", fill=(90, 60, 30))
        short = prompt[:40].replace("\n", " ")
        d.text((90, COVER_H - 100), short, fill=(110, 80, 50))

        img.save(path)
        files.append(_rel(path))

    return GenResult(
        ok=True,
        kind=task,
        files=files,
        prompt=prompt,
        provider="local-placeholder",
        meta={"note": "占位实现，接真实生图 API 只需替换 _call_provider"},
    )


# ── 对外接口 ──────────────────────────────────────────────

def generate_cover(draft_id: int, title: str, style: str = "realistic") -> GenResult:
    """生成封面（3 个候选，3:4）。"""
    return _call_provider(
        "cover", build_cover_prompt(title, style), count=COVER_CANDIDATES
    )


def generate_inner(draft_id: int, title: str, count: int = 6) -> GenResult:
    """生成内页（4-8 张，按九宫格顺序）。"""
    n = max(IMAGE_MIN, min(IMAGE_MAX, count))
    files: list[str] = []
    prompts: list[str] = []
    for i in range(1, n + 1):
        p = build_inner_prompt(title, i, n)
        prompts.append(p)
        r = _call_provider("inner", p, count=1)
        files.extend(r.files)
    return GenResult(
        ok=True, kind="inner", files=files,
        prompt=prompts[0] if prompts else "", provider="local-placeholder",
    )


def compose_video(clips: list[str], out_name: str | None = None) -> GenResult:
    """ffmpeg 拼接视频 + 烧录字幕位预留。

    ★ 只做拼接与规格统一，不做智能剪辑 —— 素材本身由图生视频产出。
    """
    _ensure_dirs()
    if not clips:
        return GenResult(ok=False, kind="compose", files=[], error="没有待合成的片段")

    existing = [c for c in clips if (ASSET_ROOT.parent / c).exists() or Path(c).exists()]
    if not existing:
        return GenResult(
            ok=False, kind="compose", files=[],
            error=f"片段文件不存在（收到 {len(clips)} 个）",
        )

    name = out_name or f"compose_{uuid.uuid4().hex[:8]}.mp4"
    out = GEN_VIDEO_DIR / name

    # 统一为 1080×1440(3:4) 竖版、30fps
    cmd = [
        "ffmpeg", "-y",
        *sum([["-i", str(Path(c).resolve())] for c in existing], []),
        "-filter_complex",
        "[0:v]scale=1080:1440:force_original_aspect_ratio=decrease,"
        "pad=1080:1440:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v]",
        "-map", "[v]",
    ]
    # 统一音频：无音频则补静音
    has_audio = True
    for c in existing:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=codec_type", "-of", "csv=p=0", str(Path(c).resolve())],
                capture_output=True, text=True, timeout=15,
            )
            if "audio" not in probe.stdout:
                has_audio = False
                break
        except Exception:
            has_audio = False
            break

    if has_audio:
        cmd += ["-map", "0:a?"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    cmd += ["-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += [str(out)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return GenResult(
            ok=False, kind="compose", files=[],
            error="找不到 ffmpeg，请确认已安装并加入 PATH",
        )
    except subprocess.TimeoutExpired:
        return GenResult(ok=False, kind="compose", files=[], error="ffmpeg 合成超时")

    if r.returncode != 0 or not out.exists():
        tail = (r.stderr or "")[-300:]
        return GenResult(ok=False, kind="compose", files=[], error=f"ffmpeg 失败：{tail}")

    return GenResult(
        ok=True, kind="compose", files=[_rel(out)], provider="ffmpeg",
        meta={"clips": len(existing), "duration_note": f"上限 {VIDEO_MAX_SEC}s"},
    )


def probe_media(path: str) -> dict:
    """探测素材规格（ffprobe），供校验器与前端展示用。"""
    p = Path(path)
    real = p if p.exists() else (ASSET_ROOT.parent / path)
    if not real.exists():
        return {"ok": False, "error": "文件不存在"}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(real.resolve())],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-200:]}
        data = json.loads(r.stdout)
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if not v:
            return {"ok": False, "error": "无视频流"}
        fps_s = v.get("avg_frame_rate", "0/1")
        try:
            num, den = fps_s.split("/")
            fps = round(int(num) / int(den), 2) if int(den) else 0
        except Exception:
            fps = 0
        return {
            "ok": True,
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": fps,
            "duration": round(float(data.get("format", {}).get("duration", 0)), 2),
            "codec": v.get("codec_name"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def validate_asset(path: str, kind: str = "image") -> dict:
    """素材规格校验（对照 docs/02）。"""
    p = Path(path)
    real = p if p.exists() else (ASSET_ROOT.parent / path)
    issues: list[str] = []

    if not real.exists():
        return {"passed": False, "issues": ["文件不存在"]}

    try:
        from PIL import Image
    except ImportError:
        return {"passed": True, "issues": [], "note": "未装 Pillow，跳过校验"}

    if kind == "image":
        with Image.open(real) as im:
            w, h = im.size
        ratio = w / h if h else 0
        target = COVER_W / COVER_H
        if abs(ratio - target) > 0.02:
            issues.append(
                f"比例 {w}x{h}（{ratio:.3f}）非 3:4（{target:.3f}），影响信息流展示"
            )
        size_kb = real.stat().st_size / 1024
        if size_kb > 10 * 1024:
            issues.append(f"体积 {size_kb/1024:.1f}MB 超过 10MB，建议压缩")
    else:
        info = probe_media(str(real))
        if not info.get("ok"):
            issues.append(info.get("error", "探测失败"))
        else:
            if (info["width"], info["height"]) != (COVER_W, COVER_H):
                issues.append(
                    f"分辨率 {info['width']}x{info['height']}，建议 {COVER_W}x{COVER_H}"
                )
            if info.get("fps", 0) < 25:
                issues.append(f"帧率 {info.get('fps')} 低于 25fps，画面易卡")
            if info.get("duration", 0) > VIDEO_MAX_SEC:
                issues.append(
                    f"时长 {info.get('duration')}s 超过 {VIDEO_MAX_SEC}s，完播率会掉"
                )

    return {"passed": not issues, "issues": issues}
