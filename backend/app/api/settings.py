"""系统设置 API。

存三类配置：
  - 内核模型与 API Key（Key 只写入本地文件，**永不返回明文**）
  - 界面偏好（侧栏折叠等）
  - RSS 默认赛道

安全约束：
  - Key 落盘到 backend/data/secrets.json（已被 .gitignore 排除）
  - 查询接口一律返回掩码（如sk-a****xyz），不泄露完整 Key
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 密钥文件（不入库）
SECRETS_FILE = Path(settings.data_dir) / "secrets.json"
# 普通偏好（可入库，但默认也不入）
PREFS_FILE = Path(settings.data_dir) / "preferences.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return json.load(f)
    except Exception:  # noqa: BLE001
        pass
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mask_key(key: str) -> str:
    """把 API Key 掩码：sk-abc...xyz → sk-a****...xyz。

    只显示前后 4 位，中间固定长度星号，避免泄露长度信息。
    """
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "****"
    return f"{key[:6]}****{key[-4:]}"


class KernelConfig(BaseModel):
    """内核配置。"""

    base_url: str = Field("http://127.0.0.1:8787", description="内核地址")
    api_key: str = Field("", description="DeepSeek API Key（留空则不修改）")
    model: str = Field("", description="模型名，留空则用内核默认")
    provider: str = Field("deepseek-official", description="提供商")


class SettingsIn(BaseModel):
    kernel: KernelConfig | None = None
    prefs: dict[str, Any] | None = None


@router.get("", summary="读取设置")
def get_settings() -> dict[str, Any]:
    """返回当前设置。API Key 以掩码形式返回。"""
    sec = _read_json(SECRETS_FILE, {})
    prefs = _read_json(PREFS_FILE, {})

    key = sec.get("api_key", "")
    return {
        "kernel": {
            "base_url": sec.get("base_url", "http://127.0.0.1:8787"),
            "provider": sec.get("provider", "deepseek-official"),
            "model": sec.get("model", ""),
            # ★ 只回掩码，绝不回明文
            "api_key_masked": mask_key(key),
            "has_key": bool(key),
        },
        "prefs": prefs,
        "secrets_file_exists": SECRETS_FILE.exists(),
    }


@router.post("", summary="保存设置")
def save_settings(payload: SettingsIn) -> dict[str, Any]:
    """保存设置。API Key 留空表示保持原值不变。"""
    sec = _read_json(SECRETS_FILE, {})

    if payload.kernel is not None:
        k = payload.kernel
        if k.base_url:
            sec["base_url"] = k.base_url.rstrip("/")
        if k.provider:
            sec["provider"] = k.provider
        if k.model:
            sec["model"] = k.model
        # ★ 只有传了非空 Key 才覆盖，避免前端传空把已有 Key 清掉
        if k.api_key.strip():
            sec["api_key"] = k.api_key.strip()
        _write_json(SECRETS_FILE, sec)
        # 让本次运行的进程也能读到
        os.environ["DEEPSEEK_API_KEY"] = sec.get("api_key", "")
        os.environ["KERNEL_BASE_URL"] = sec.get("base_url", "")

    if payload.prefs is not None:
        prefs = _read_json(PREFS_FILE, {})
        prefs.update(payload.prefs)
        _write_json(PREFS_FILE, prefs)

    return {"ok": True, "message": "已保存（API Key 仅存本地）",
            "kernel": get_settings()["kernel"]}


@router.post("/kernel/test", summary="测试内核连接")
async def test_kernel() -> dict[str, Any]:
    """用当前配置测试内核连通性与鉴权状态。"""
    import httpx

    sec = _read_json(SECRETS_FILE, {})
    base = sec.get("base_url", "http://127.0.0.1:8787").rstrip("/")
    key = sec.get("api_key", "")

    headers = {}
    if key:
        headers["authorization"] = f"Bearer {key}"

    result: dict[str, Any] = {"reachable": False, "authenticated": False}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            # healthz 通常免鉴权，可用来判断是否可达
            r = await client.get(f"{base}/healthz", headers=headers)
            if r.status_code == 200:
                result["reachable"] = True
                data = r.json()
                result["model"] = data.get("model", {})
                result["apps"] = data.get("apps", {})

            # 带鉴权再探一次
            r2 = await client.get(f"{base}/v1/tools", headers=headers)
            result["authenticated"] = r2.status_code == 200
            result["tools_count"] = (
                r2.json().get("counts", {}).get("builtin", 0)
                if r2.status_code == 200
                else 0
            )
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:120]

    return result


@router.get("/models", summary="可用模型列表")
async def list_models() -> dict[str, Any]:
    """从内核读取当前生效的模型。"""
    import httpx

    sec = _read_json(SECRETS_FILE, {})
    base = sec.get("base_url", "http://127.0.0.1:8787").rstrip("/")
    key = sec.get("api_key", "")
    headers = {"authorization": f"Bearer {key}"} if key else {}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{base}/healthz", headers=headers)
            if r.status_code != 200:
                return {"ok": False, "models": []}
            model = r.json().get("model", {})
            return {
                "ok": True,
                "models": [
                    {
                        "id": model.get("model", "deepseek-flash"),
                        "provider": model.get("provider", "deepseek-official"),
                        "active": True,
                    }
                ],
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "models": [], "error": str(exc)[:100]}
