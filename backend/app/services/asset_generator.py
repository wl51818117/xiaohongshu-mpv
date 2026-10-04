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

@dataclass
class DraftContext:
    """上游稿件上下文 —— 素材与稿件同源的依据。

    ★ 这是「素材工坊与稿件串联」的关键：没有它，
      build_cover_prompt 只能拿到 title，生成出来的图与正文毫无关系。
    """

    title: str
    keyword: str = ""
    body: str = ""
    tags: list[str] | None = None
    topic_title: str = ""

    @property
    def core_terms(self) -> list[str]:
        """从长尾词里取可埋入画面的核心词（与校验器同源）。"""
        import re as _re
        if not self.keyword:
            return []
        terms = [t for t in _re.split(r"[\s、,，/|+]+", self.keyword) if t]
        terms.sort(key=len, reverse=True)
        return terms[:2]

    @property
    def key_points(self) -> list[str]:
        """从正文里抽取画面要点。

        做法：按中文标点切句→ 取含数字或「是/有/用/选」等判断词的短句
        → 按字数排序取前几条。宁可粗糙也不要引入额外模型调用。
        """
        import re as _re
        if not self.body:
            return []
        sentences = _re.split(r"[。！？\n；;]+", self.body)
        scored: list[tuple[int, str]] = []
        for s in sentences:
            s = s.strip()
            if not (8 <= len(s) <= 30):
                continue
            score = 0
            if _re.search(r"\d", s):
                score += 2# 有具体数字更适合做画面
            if _re.search(r"(是|有|用|选|买|注意|避免|关键|重点|区别|方法)", s):
                score += 1
            if _re.search(r"(我|你|姐妹|宝|建议|实测)", s):
                score += 1
            if score:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:4]]


def build_cover_prompt(ctx: DraftContext, style: str = "realistic") -> str:
    """封面提示词 —— **吃稿件全上下文**。

    3:4 竖版是硬要求，直接写进提示词而不是生成后再裁，
    这样模型的构图会按竖版设计。
    """
    style_map = {
        "realistic": "写实摄影风格，自然光，浅景深，真实质感",
        "clean": "干净极简风，柔和光线，纯色背景，留白充足",
        "vivid": "色彩鲜明，视觉冲击强，高饱和",
    }
    desc = style_map.get(style, style_map["realistic"])

    # 关键词与画面要点：让图承载内容，而不是一张泛泛的漂亮图
    core = "、".join(ctx.core_terms)
    points = ctx.key_points
    parts = [
        f"小红书封面图，3:4 竖版构图（1080×1440）。",
        f"主题：{ctx.title}。",
        f"风格：{desc}。",
    ]
    if core:
        parts.append(f"画面需体现的核心概念：{core}。")
    if points:
        parts.append("画面要传达的信息点：" + "；".join(points[:3]) + "。")
    if ctx.tags:
        parts.append("相关品类参考：" + "、".join(ctx.tags[:4]) + "。")

    parts.append(
        "要求：主体突出，占画面 60% 以上；画面简洁；"
        "文字区域不超过画面 30%；右下角预留信息区。"
        "禁止出现：水印、二维码、联系方式、文字乱码。"
    )
    return "".join(parts)


def build_inner_prompt(ctx: DraftContext, index: int, total: int) -> str:
    """内页提示词，按顺序分配内容，且每张对应稿件的一个真实信息点。"""
    roles = [
        "产品全景展示",
        "材质/工艺细节特写",
        "使用场景实拍",
        "对比效果呈现",
        "核心卖点图解",
        "选购/使用指引",
    ]
    role = roles[(index - 1) % len(roles)]
    points = ctx.key_points
    parts = [
        f"小红书图文内页第 {index}/{total} 张，3:4 竖版（1080×1440）。",
        f"主题：{ctx.title}。画面内容：{role}。",
    ]
    # ★ 关键：每张内页承载正文里的一个真实信息点，
    #   而不是 6 张都用「产品全景」——那样图和文就各说各话。
    if points:
        idx = (index - 1) % len(points)
        parts.append(f"本张要表达的信息点：{points[idx]}。")
    if ctx.tags:
        parts.append(f"品类参考：{'、'.join(ctx.tags[:3])}。")
    parts.append(
        "要求：写实摄影风格，主体清晰，构图简洁，"
        "每张只讲一个点，不要堆砌信息。"
        "禁止出现：水印、二维码、联系方式。"
    )
    return "".join(parts)


def build_video_prompt(ctx: DraftContext | str, scene: str = "") -> str:
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
    title = ctx.title if isinstance(ctx, DraftContext) else str(ctx)
    return (
        f"基于首帧图生成短视频。主题：{title}。"
        f"运镜：{motion}。"
        f"要求：画面稳定不闪烁，人体/物体结构不变形，时长 3-5 秒。"
    )


# ── Provider 抽象 ─────────────────────────────────────────

# 生图配置（由 API 层写入 data/image_provider.json，密钥 Fernet 加密）
_PROVIDER_FILE = settings.data_dir / "image_provider.json"
PROVIDER_NAME = "local-placeholder"


def load_image_provider() -> dict:
    """读生图服务配置（**自动解密密钥**）。

    返回形如：
      {"enabled": bool, "base_url": str, "api_key": str, "model": str,
       "size": "1080x1440", "kind": "seedream|openai"}
    """
    if not _PROVIDER_FILE.exists():
        return {"enabled": False, "kind": "openai"}
    try:
        raw = json.loads(_PROVIDER_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"enabled": False, "kind": "openai"}

    raw = {**{"enabled": False, "kind": "openai"}, **raw}
    # 密钥是密文存的，用到时才解密
    cipher = str(raw.get("api_key") or "")
    if cipher:
        try:
            from app.api.settings import _dec

            raw["api_key"] = _dec(cipher)
        except Exception:  # noqa: BLE001
            # 解密失败通常是换机器了（密钥由机器信息派生）
            raw["api_key"] = ""
            raw["enabled"] = False
    return raw


def provider_status() -> dict:
    """生图配置状态（供前端提示，**不含密钥明文**）。"""
    cfg = load_image_provider()
    key = str(cfg.get("api_key") or "")
    return {
        "configured": bool(cfg.get("enabled") and cfg.get("base_url") and key),
        "enabled": bool(cfg.get("enabled")),
        "kind": cfg.get("kind", "openai"),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "size": cfg.get("size", f"{COVER_W}x{COVER_H}"),
        "key_masked": (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("已配置" if key else ""),
        "provider": PROVIDER_NAME,
        "hint": (
            "已接入真实生图服务"
            if (cfg.get("enabled") and cfg.get("base_url") and key)
            else "未配置生图 API，当前使用本地占位图（尺寸真实、画面为占位）。"
                 "请到「设置 → 生图 API」填写地址/密钥/模型。"
        ),
    }


def _call_provider(task: str, prompt: str, **kwargs) -> GenResult:
    """统一调用入口 —— 接真实生图只需在这里分发。

    分发逻辑：配置完整 → 走真实 API；否则 → 本地占位（保证管线可跑通）。
    上层（generate_cover/generate_inner）无需改动。
    """
    global PROVIDER_NAME
    _ensure_dirs()
    cfg = load_image_provider()
    if cfg.get("enabled") and cfg.get("base_url") and cfg.get("api_key"):
        result = _remote_image(task, prompt, cfg, **kwargs)
        # 真实调用失败时不静默回退——用户需要知道配置有问题
        if result.ok:
            PROVIDER_NAME = f"remote:{cfg.get('model', '?')}"
            return result
        return result
    PROVIDER_NAME = "local-placeholder"
    return _local_placeholder(task, prompt, **kwargs)


def _remote_image(task: str, prompt: str, cfg: dict, **kwargs) -> GenResult:
    """调真实生图 API。

    支持两类协议（覆盖国内主流服务）：
      - openai兼容：POST {base}/images/generations  {prompt, model, size, n}
        （OpenAI DALL·E / SiliconFlow / 多数聚合服务）
      - seedream：POST {base}/images/generations，额外带
        response_format / watermark 等参数，尺寸用 width/height
    """
    import httpx  # 局部导入：这个函数是可选路径

    base = str(cfg["base_url"]).rstrip("/")
    model = str(cfg.get("model") or "").strip()
    key = str(cfg["api_key"])
    kind = str(cfg.get("kind") or "openai")
    size = str(cfg.get("size") or f"{COVER_W}x{COVER_H}")
    n = int(kwargs.get("count", 1))
    if not model:
        return GenResult(ok=False, kind=task, files=[], prompt=prompt,
                         error="生图配置缺少模型名，请到设置里填写")

    payload: dict = {"model": model, "prompt": prompt, "n": n}
    if kind == "seedream":
        try:
            w, h = (int(x) for x in size.lower().split("x"))
        except Exception:  # noqa: BLE001
            w, h = COVER_W, COVER_H
        payload.update({"width": w, "height": h,
                        "response_format": "url", "watermark": False})
    else:
        payload["size"] = size

    url = f"{base}/images/generations"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {key}",
    }
    timeout = int(cfg.get("timeout") or 180)

    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return GenResult(ok=False, kind=task, files=[], prompt=prompt,
                         error=f"生图服务不可达（{base}）：{exc}")

    if r.status_code != 200:
        return GenResult(
            ok=False, kind=task, files=[], prompt=prompt,
            error=f"生图服务返回 HTTP {r.status_code}：{r.text[:200]}",
        )

    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return GenResult(ok=False, kind=task, files=[], prompt=prompt,
                         error="生图服务返回的不是合法 JSON")

    items = data.get("data") or []
    if not items:
        return GenResult(ok=False, kind=task, files=[], prompt=prompt,
                         error=f"生图服务未返回图片：{str(data)[:200]}")

    files: list[str] = []
    import base64 as _b64
    for idx, it in enumerate(items):
        b64 = it.get("b64_json")
        u = it.get("url")
        if b64:
            raw = _b64.b64decode(b64)
        elif u:
            try:
                raw = httpx.get(u, timeout=timeout).content
            except Exception:  # noqa: BLE001
                continue
        else:
            continue
        path = GEN_IMG_DIR / f"{task}_{uuid.uuid4().hex[:8]}_{idx}.png"
        try:
            path.write_bytes(raw)
        except OSError:
            continue
        # 统一规格：真实服务可能不按3:4 返回，落盘前纠正
        files.extend([_rel(_normalize_size(path))])

    if not files:
        return GenResult(ok=False, kind=task, files=[], prompt=prompt,
                         error="生图结果下载或保存失败")

    return GenResult(ok=True, kind=task, files=files, prompt=prompt,
                     provider=f"remote:{model}", meta={"count": len(files)})


def _normalize_size(path: Path) -> Path:
    """把图片统一成 3:4（1080×1440），不一致则加白边而非拉伸。

    拉伸会让商品变形；加白边更符合平台要求（信息流展示不变形）。
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            if (w, h) == (COVER_W, COVER_H):
                return path
            ratio = COVER_W / COVER_H
            if w / h > ratio:  # 太宽 → 裁两侧
                nw = int(h * ratio)
                left = (w - nw) // 2
                im2 = im.crop((left, 0, left + nw, h))
            else:  # 太高 → 裁上下
                nh = int(w / ratio)
                top = (h - nh) // 2
                im2 = im.crop((0, top, w, top + nh))
            im2 = im2.convert("RGB").resize((COVER_W, COVER_H), Image.LANCZOS)
            im2.save(path)
        return path
    except Exception:  # noqa: BLE001
        return path


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
        # 把提示词摘要画进图里 —— 这样即使不接真实API，
        # 也能一眼看出「这张图用的什么提示词」，验证稿件→提示词是否串联
        short = prompt[:60].replace("\n", " ")
        d.text((90, COVER_H - 100), short, fill=(110, 80, 50))
        if len(prompt) > 60:
            d.text((90, COVER_H - 76), prompt[60:120].replace("\n", " "), fill=(110, 80, 50))

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

def generate_cover(ctx: DraftContext, style: str = "realistic") -> GenResult:
    """生成封面（3 个候选，3:4）。"""
    prompt = build_cover_prompt(ctx, style)
    return _call_provider("cover", prompt, count=COVER_CANDIDATES)


def generate_inner(ctx: DraftContext, count: int = 6) -> GenResult:
    """生成内页（4-8 张，按顺序分配稿件信息点）。"""
    n = max(IMAGE_MIN, min(IMAGE_MAX, count))
    files: list[str] = []
    prompts: list[str] = []
    for i in range(1, n + 1):
        p = build_inner_prompt(ctx, i, n)
        prompts.append(p)
        r = _call_provider("inner", p, count=1)
        files.extend(r.files)
    return GenResult(
        ok=True, kind="inner", files=files,
        prompt=prompts[0] if prompts else "", provider=PROVIDER_NAME,
        meta={"count": n, "prompts": prompts},
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
