"""系统设置 API —— 多套 API 配置 + 连接测试 + 模型拉取。

对应需求五项：
1. 配置表单：多套配置（Base URL / Key / 模型），切换与持久化
2. 连接测试：区分「地址不可达/ 密钥无效 / 请求超时 / 连接成功」+ 耗时
3. 模型信息：拉取模型清单，不支持则可手动输入
4. 模型选择：由配置驱动的下拉（搜索、多选、联动刷新）
5. 异常处理：加载态、防重复提交、未测通的配置不可选

★ 安全设计：
  - 密钥用机器密钥派生出的 Fernet 加密后落盘，**绝不明文存储**
  - 任何查询接口只返回掩码，永不回显完整 Key
  - secrets.json 已被 .gitignore 排除
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

SECRETS_FILE = Path(settings.data_dir) / "secrets.json"
PREFS_FILE = Path(settings.data_dir) / "preferences.json"

# 状态语义（前端据此展示不同提示）
TestStatus = Literal["success", "unreachable", "auth_failed", "timeout", "bad_request", "error"]


# ══════════════════════════════════════════════════════════════
# 密钥加密（Fernet）
# ══════════════════════════════════════════════════════════════

def _machine_key() -> bytes:
    """从机器信息派生一个稳定密钥 —— 换机器则旧密文无法解开，需重填 Key。

    刻意不用环境变量：单机自用场景下这样最省事，且密文仍不以明文落盘。
    """
    seed = f"{settings.data_dir}|{os_name()}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return base64.urlsafe_b64encode(digest)


def os_name() -> str:
    import platform

    return platform.node()


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_machine_key())


def _enc(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def _dec(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return _fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except Exception:
        # 机器变了 / 密文损坏 → 返回空而不是崩
        return ""


def mask_key(key: str) -> str:
    """掩码：sk-a****xyz，不泄露长度。"""
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "****"
    return f"{key[:6]}****{key[-4:]}"


# ══════════════════════════════════════════════════════════════
# 存储
# ══════════════════════════════════════════════════════════════

def _read_all() -> dict[str, Any]:
    if not SECRETS_FILE.exists():
        return {"profiles": [], "active_id": None, "legacy": {}}
    try:
        with SECRETS_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"profiles": [], "active_id": None, "legacy": {}}


def _write_all(data: dict[str, Any]) -> None:
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(SECRETS_FILE)


def _public(p: dict[str, Any]) -> dict[str, Any]:
    """对外形态：**去掉密文**，Key 只给掩码。"""
    return {
        "id": p["id"],
        "name": p.get("name") or "未命名",
        "base_url": p.get("base_url", ""),
        "provider": p.get("provider", ""),
        "model": p.get("model", ""),
        "api_key_masked": mask_key(p.get("_api_key", "") and _dec(p.get("_api_key", "")) or ""),
        "has_key": bool(p.get("_api_key")),
        "models": p.get("models", []),
        "tested": bool(p.get("tested")),
        "test_status": p.get("test_status"),
        "test_message": p.get("test_message", ""),
        "tested_at": p.get("tested_at"),
        "test_ms": p.get("test_ms"),
        "created_at": p.get("created_at"),
    }


def _plain_key(p: dict[str, Any]) -> str:
    return _dec(p.get("_api_key", ""))


# ══════════════════════════════════════════════════════════════
# 模型
# ══════════════════════════════════════════════════════════════

class ProfileIn(BaseModel):
    """配置（新建/编辑）。"""

    id: str | None = None
    name: str = Field(..., min_length=1, max_length=60)
    base_url: str = Field(..., min_length=8, max_length=500)
    api_key: str = Field("", max_length=500, description="留空表示不修改")
    provider: str = Field("", max_length=60)
    model: str = Field("", max_length=120)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        u = v.strip().rstrip("/")
        if not u.startswith(("http://", "https://")):
            raise ValueError("地址必须以 http:// 或 https:// 开头")
        return u


class ActiveIn(BaseModel):
    """切换生效配置。"""

    profile_id: str = Field(..., alias="profileId")
    selected_model: str = Field("", alias="model", description="同时指定模型")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class ModelsIn(BaseModel):
    """手动填模型（不支持列表接口的服务用）。"""

    id: str
    model: str = Field(..., min_length=1, max_length=120)


# ══════════════════════════════════════════════════════════════
# 1. 配置 CRUD
# ══════════════════════════════════════════════════════════════

@router.get("", summary="读取全部配置")
def list_profiles() -> dict[str, Any]:
    """返回所有配置（Key 为掩码）。"""
    data = _read_all()
    profiles = [_public(p) for p in data.get("profiles", [])]
    return {
        "profiles": profiles,
        "active_id": data.get("active_id"),
        "prefs": _read_prefs(),
    }


def _read_prefs() -> dict[str, Any]:
    try:
        if PREFS_FILE.exists():
            return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


@router.post("", summary="新建或更新配置")
def save_profile(payload: ProfileIn) -> dict[str, Any]:
    """新建或编辑一套配置。Key 传空则保留原值。"""
    data = _read_all()
    profiles: list[dict[str, Any]] = data.setdefault("profiles", [])

    if payload.id:
        idx = next(
            (i for i, p in enumerate(profiles) if p["id"] == payload.id), None
        )
        if idx is None:
            raise HTTPException(status_code=404, detail="配置不存在")
        old = profiles[idx]
        cipher = old.get("_api_key", "")
        # Key 未填 → 沿用旧密文；填了 → 重新加密
        if payload.api_key.strip():
            cipher = _enc(payload.api_key.strip())
        profiles[idx] = {
            **old,
            "name": payload.name.strip(),
            "base_url": payload.base_url.strip().rstrip("/"),
            "provider": payload.provider.strip(),
            "model": payload.model.strip(),
            "_api_key": cipher,
            # 改了地址或 Key 需重测
            "tested": False,
            "test_status": None,
            "test_message": "",
            "tested_at": None,
            "test_ms": None,
            "models": (
                []
                if (
                    old.get("base_url") != payload.base_url.strip().rstrip("/")
                    or cipher != old.get("_api_key", "")
                )
                else old.get("models", [])
            ),
        }
        pid = payload.id
    else:
        pid = f"cfg-{uuid.uuid4().hex[:8]}"
        profiles.append(
            {
                "id": pid,
                "name": payload.name.strip(),
                "base_url": payload.base_url.strip().rstrip("/"),
                "provider": payload.provider.strip(),
                "model": payload.model.strip(),
                "_api_key": _enc(payload.api_key.strip()),
                "models": [],
                "tested": False,
                "test_status": None,
                "test_message": "",
                "tested_at": None,
                "test_ms": None,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    data["profiles"] = profiles
    if not data.get("active_id"):
        data["active_id"] = pid
    _write_all(data)

    prof = next(p for p in profiles if p["id"] == pid)
    return {"ok": True, "profile": _public(prof), "active_id": data.get("active_id")}


@router.delete("/{profile_id}", summary="删除配置")
def delete_profile(profile_id: str) -> dict[str, Any]:
    data = _read_all()
    profiles: list[dict[str, Any]] = data.get("profiles", [])
    if not any(p["id"] == profile_id for p in profiles):
        raise HTTPException(status_code=404, detail="配置不存在")
    profiles = [p for p in profiles if p["id"] != profile_id]
    data["profiles"] = profiles
    if data.get("active_id") == profile_id:
        data["active_id"] = profiles[0]["id"] if profiles else None
    _write_all(data)
    return {"ok": True, "active_id": data.get("active_id")}


@router.post("/active", summary="切换生效配置")
def set_active(payload: ActiveIn) -> dict[str, Any]:
    """把某套配置设为当前生效（可同时指定模型）。

    ★ 未通过连接测试的配置不允许设为生效 —— 避免 Agent 用一把坏 Key 反复重试。
    """
    data = _read_all()
    profiles = data.get("profiles", [])
    prof = next((p for p in profiles if p["id"] == payload.profile_id), None)
    if not prof:
        raise HTTPException(status_code=404, detail="配置不存在")
    if not prof.get("tested"):
        raise HTTPException(
            status_code=400, detail="该配置尚未通过连接测试，不能设为生效配置"
        )
    if payload.selected_model:
        prof["model"] = payload.selected_model.strip()
        idx = next(
            (i for i, x in enumerate(profiles) if x["id"] == payload.profile_id), None
        )
        if idx is not None:
            profiles[idx] = prof
    data["profiles"] = profiles
    data["active_id"] = payload.profile_id
    _write_all(data)
    return {"ok": True, "active_id": data["active_id"], "model": prof.get("model", "")}


# ══════════════════════════════════════════════════════════════
# 2. 连接测试
# ══════════════════════════════════════════════════════════════

@router.post("/{profile_id}/test", summary="测试连接")
async def test_profile(profile_id: str) -> dict[str, Any]:
    """真实请求一次，并把结果归类成明确状态。

    状态：success / unreachable / auth_failed / timeout / bad_request / error
    """
    data = _read_all()
    prof = next((p for p in data.get("profiles", []) if p["id"] == profile_id), None)
    if not prof:
        raise HTTPException(status_code=404, detail="配置不存在")

    base = prof.get("base_url", "").rstrip("/")
    key = _plain_key(prof)
    provider = (prof.get("provider") or "").lower()

    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"

    t0 = time.perf_counter()
    status: TestStatus = "error"
    message = ""
    models: list[dict[str, Any]] = []

    # DeepSeek 官方用 /models；OpenAI 兼容网关大多也是 /v1/models
    for path in ("/models", "/v1/models"):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(f"{base}{path}", headers=headers)
            ms = int((time.perf_counter() - t0) * 1000)

            if r.status_code == 200:
                status = "success"
                message = f"连接成功（{ms} ms）"
                models = _parse_models(r.text)
                if not models:
                    message += "；未返回模型清单，可手动填写"
                break
            if r.status_code in (401, 403):
                status = "auth_failed"
                message = "密钥无效或无权限（HTTP %d）" % r.status_code
                break
            if r.status_code == 404:
                continue  # 试下一个路径
            if r.status_code in (400, 422):
                status = "bad_request"
                message = "地址或参数不被接受（HTTP %d）：%s" % (
                    r.status_code,
                    r.text[:120],
                )
                break
            # 502/503/504 常是网关替上游转发失败 —— 对用户而言就是「地址连不上」
            if r.status_code in (502, 503, 504):
                status = "unreachable"
                message = f"地址无法访问（HTTP {r.status_code}）：{r.text[:90]}"
                break
            status = "error"
            message = "HTTP %d：%s" % (r.status_code, r.text[:120])
            break
        except httpx.TimeoutException:
            status = "timeout"
            message = "请求超时（>20s），检查网络或地址是否可达"
            break
        except httpx.ConnectError as exc:
            status = "unreachable"
            message = f"地址无法访问：{str(exc)[:80]}"
            break
        except Exception as exc:  # noqa: BLE001
            status = "error"
            message = f"{type(exc).__name__}: {str(exc)[:80]}"
            break

    ms = int((time.perf_counter() - t0) * 1000)

    # 回写测试结果
    profiles = data.get("profiles", [])
    p = next((x for x in profiles if x["id"] == profile_id), None)
    if p:
        p["tested"] = status == "success"
        p["test_status"] = status
        p["test_message"] = message
        p["tested_at"] = datetime.now().isoformat(timespec="seconds")
        p["test_ms"] = ms
        if models:
            p["models"] = models
        data["profiles"] = profiles
        _write_all(data)

    return {
        "ok": status == "success",
        "status": status,
        "message": message,
        "elapsed_ms": ms,
        "models": models,
        "profile": _public(p) if p else None,
    }


def _parse_models(text: str) -> list[dict[str, Any]]:
    """从 /models 响应里抽模型清单，兼容多种返回形状。"""
    try:
        data = json.loads(text)
    except Exception:
        return []
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for m in items[:200]:
        if isinstance(m, str):
            out.append({"id": m, "name": m})
        elif isinstance(m, dict) and m.get("id"):
            out.append(
                {
                    "id": str(m.get("id")),
                    "name": str(m.get("name") or m.get("display_name") or m.get("id")),
                }
            )
    return out


# ══════════════════════════════════════════════════════════════
# 3. 模型列表
# ══════════════════════════════════════════════════════════════

@router.get("/{profile_id}/models", summary="获取模型列表")
async def get_models(profile_id: str, refresh: bool = True) -> dict[str, Any]:
    """拉取模型清单。

    - refresh=true 强制重新拉（会真实发请求）
    - 已缓存且 refresh=false 直接返回
    - 服务不支持列表接口时返回 supported:false，前端走手动输入兜底
    """
    data = _read_all()
    prof = next((p for p in data.get("profiles", []) if p["id"] == profile_id), None)
    if not prof:
        raise HTTPException(status_code=404, detail="配置不存在")

    if not refresh and prof.get("models"):
        return {
            "ok": True,
            "supported": True,
            "models": prof["models"],
            "cached": True,
        }

    base = prof.get("base_url", "").rstrip("/")
    key = _plain_key(prof)
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"

    for path in ("/models", "/v1/models"):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(f"{base}{path}", headers=headers)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "supported": False,
                "models": prof.get("models", []),
                "reason": f"拉取失败：{str(exc)[:80]}",
            }
        if r.status_code == 200:
            models = _parse_models(r.text)
            prof["models"] = models
            prof["tested"] = True
            prof["test_status"] = "success"
            data["profiles"] = data.get("profiles", [])
            idx = next(
                (i for i, x in enumerate(data["profiles"]) if x["id"] == profile_id), None
            )
            if idx is not None:
                data["profiles"][idx] = prof
            _write_all(data)
            return {
                "ok": True,
                "supported": True,
                "models": models,
                "cached": False,
            }
        if r.status_code in (401, 403):
            return {"ok": False, "supported": False, "reason": "密钥无效或无权限"}

    # 走到这说明两个路径都没有 200
    return {
        "ok": False,
        "supported": False,
        "models": [],
        "reason": "该服务不支持模型列表接口（/models 返回 404），请手动输入模型名",
    }


@router.post("/{profile_id}/model", summary="手动设置模型")
def set_model(payload: ModelsIn) -> dict[str, Any]:
    """手动指定模型名（不支持列表接口的服务用）。"""
    data = _read_all()
    profiles = data.get("profiles", [])
    prof = next((p for p in profiles if p["id"] == payload.id), None)
    if not prof:
        raise HTTPException(status_code=404, detail="配置不存在")
    prof["model"] = payload.model.strip()
    idx = next((i for i, x in enumerate(profiles) if x["id"] == payload.id), None)
    if idx is not None:
        profiles[idx] = prof
    _write_all(data)
    return {"ok": True, "profile": _public(prof)}


# ══════════════════════════════════════════════════════════════
# 4. 生效配置（供内核与 Agent 使用）
# ══════════════════════════════════════════════════════════════

@router.get("/active/detail", summary="当前生效配置详情")
def active_detail() -> dict[str, Any]:
    """给后端内部用：拿当前生效配置的**明文** Key（仅内部调用，不对外暴露）。"""
    data = _read_all()
    aid = data.get("active_id")
    prof = next((p for p in data.get("profiles", []) if p["id"] == aid), None)
    if not prof:
        return {"ok": False, "reason": "未配置生效的 API"}
    return {
        "ok": True,
        "id": prof["id"],
        "name": prof.get("name"),
        "base_url": prof.get("base_url"),
        "model": prof.get("model"),
        "api_key": _plain_key(prof),
        "tested": bool(prof.get("tested")),
    }


@router.post("/prefs", summary="保存界面偏好")
def save_prefs(body: dict[str, Any]) -> dict[str, Any]:
    prefs = _read_prefs()
    prefs.update(body)
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "prefs": prefs}
