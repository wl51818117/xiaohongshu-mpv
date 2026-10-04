"""Bridge 桥接层：把浏览器端能力接到内核。

对应 hbridge v2.1 协议（docs/接口协议.md）。

为什么必须有这一层：
  **内核带着 pwsh / read / write 工具，绝不能暴露给浏览器。**
  所以前端 → 我们后端 → 内核，前端永远拿不到内核的直接访问权。

职责：
  1. POST /v1/ticket    —— 用静态凭据换短时票据（票据不能自续，故须服务端持密钥）
  2. /v1/*             —— 转发浏览器请求到内核（SSE 保持不缓冲）
  3. appTokens/appId白名单 —— 在这里收口，避免前端伪造身份
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.core.config import settings

router = APIRouter(prefix="/api/bridge", tags=["bridge"])

# 内核地址（复用全局配置）
KERNEL = settings.kernel_base_url

# 本工作台在内核里的应用身份
APP_ID = os.getenv("HARNESS_APP_ID", "workbench")

# 与内核 host 配置里的 ticketSecret 保持一致。
# 内核侧默认不配 ticketSecret 时，票据通道仍可用（静态凭据兜底）。
_TICKET_SECRET = os.getenv("HARNESS_TICKET_SECRET", "")

# 内核接受的静态凭据（appTokens 里给本工作台配的 token）
# 取值优先级：环境变量 HARNESS_APP_TOKEN -> settings.kernel_auth_headers（自动读内核 patch）
# ★ 必须与 app.agent.kernel 用同一份凭据来源。之前这里只认环境变量、
#   agent/kernel.py 又完全不带头，导致「bridge 显示可用、Agent 却 401」的分裂现象。
_STATIC_TOKEN = os.getenv("HARNESS_APP_TOKEN", "") or settings.kernel_auth_headers.get(
    "authorization", ""
)[len("Bearer "):]


def _auth_headers(ticket: str | None, client_id: str | None) -> dict[str, str]:
    """组装内核侧鉴权头。票据优先，回落静态 token。

    ★ 两个必须注意的点（都在 guard.js 里踩过）：
     1. 头名是 `x-harness-app`，不是 `x-app-id`（见 appIdOfRequest）。
     2. 票据必须绑 appId，且请求声明的 appId 要与票据一致 ——
        带 appId 的票据只能用于同一个 appId 的请求，跨用直接 401。
    """
    headers = {"x-harness-app": APP_ID}
    if ticket:
        headers["authorization"] = f"Bearer {ticket}"
    elif _STATIC_TOKEN:
        headers["authorization"] = f"Bearer {_STATIC_TOKEN}"
    if client_id:
        headers["x-harness-client"] = client_id
    return headers


@router.post("/ticket", summary="签发短时票据")
async def issue_ticket(request: Request) -> dict[str, Any]:
    """用服务端持有的静态凭据换一张短时票据给浏览器。

    浏览器拿票据后调/v1/*，有效期默认 10 分钟，过期再换即可。
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    payload = {
        "appId": body.get("appId") or APP_ID,
        "clientId": body.get("clientId"),
        "ttlMs": body.get("ttlMs"),
    }

    # 优先用配置的 ticketSecret 在本地签（避免多一跳，且不依赖内核是否开启票据端点）
    if _TICKET_SECRET:
        payload.pop("ttlMs", None)
        payload["ticketSecret"] = _TICKET_SECRET

    headers = {"content-type": "application/json"}
    if _STATIC_TOKEN:
        headers["authorization"] = f"Bearer {_STATIC_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{KERNEL}/v1/ticket", json=payload, headers=headers
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"无法连接内核（{KERNEL}）：{exc}"
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"内核签发票据失败：{resp.text[:200]}",
        )
    return resp.json()


@router.get("/status", summary="bridge 通道状态")
async def bridge_status() -> dict[str, Any]:
    """报告桥接是否可用，便于前端判断要不要启用。

    注意：/v1/capabilities 也受鉴权保护，所以这里要带上票据；
    拿不到就退回报「不可用」而不是抛错——前端据此降级即可。
    """
    probe = {"available": False, "app_id": APP_ID}

    # 先换票
    ticket: str | None = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{KERNEL}/v1/ticket",
                json={"appId": APP_ID},
                headers=_auth_headers(None, None),
            )
            if resp.status_code == 200:
                ticket = resp.json().get("ticket")
    except Exception as exc:  # noqa: BLE001
        probe["reason"] = f"票据签发失败：{exc}"
        return probe

    if not ticket:
        probe["reason"] = "内核未签发票据（检查 host 的 token / ticketSecret 配置）"
        return probe

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{KERNEL}/v1/capabilities",
                headers=_auth_headers(ticket, None),
            )
            if resp.status_code != 200:
                probe["reason"] = f"内核返回 HTTP {resp.status_code}"
                return probe
            caps = resp.json()
    except Exception as exc:  # noqa: BLE001
        probe["reason"] = str(exc)
        return probe

    return {
        "available": True,
        "app_id": APP_ID,
        "protocol": caps.get("protocol"),
        "version": caps.get("version"),
        "features": caps.get("features", {}),
    }


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PATCH", "DELETE", "PUT"],
    summary="转发到内核（bridge 通道）",
)
async def proxy(path: str, request: Request) -> Response:
    """把浏览器请求原样转发到内核。

    SSE（/v1/chat、/v1/tool-result 等）走流式转发，**禁止缓冲**，
    否则前端收不到增量事件。
    """
    # SDK 走标准 authorization 头；也兼容 x-harness-ticket 手动传。
    ticket = request.headers.get("x-harness-ticket")
    if not ticket:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            ticket = auth[7:].strip()

    # 静态凭据回落（仅后端持有）
    if not ticket:
        ticket = _STATIC_TOKEN or None

    client_id = request.headers.get("x-harness-client") or request.headers.get(
        "x-harness-client-id"
    )
    incoming_auth = request.headers.get("authorization")

    headers = _auth_headers(ticket, client_id)
    # 浏览器若已带静态 token（如调试），透传之
    if not headers.get("authorization") and incoming_auth:
        headers["authorization"] = incoming_auth
    headers["content-type"] = "application/json"

    # 路径拼接：SDK 的 baseUrl 是 /api/bridge，它自己会拼 /v1/xxx，
    # 所以这里收到的 path 已经是 "v1/capabilities" 这类形态 —— 直接续到内核根。
    clean = path.lstrip("/")
    url = f"{KERNEL}/{clean}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    body = None
    if request.method in {"POST", "PATCH", "PUT"}:
        raw = await request.body()
        if raw:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="请求体不是合法JSON")

    client = httpx.AsyncClient(timeout=settings.kernel_timeout_seconds)

    # SSE 端点：/chat 走流式转发（禁缓冲），否则前端收不到增量事件
    if path.endswith("chat") and request.method == "POST":
        # SSE：流式转发，禁缓冲
        async def gen():
            try:
                async with client.stream(
                    "POST", url, json=body, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "ignore")[:300]
                        yield f"event: error\ndata: {json.dumps({'message': detail}, ensure_ascii=False)}\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            except Exception as exc:  # noqa: BLE001
                yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache, no-transform",
                "x-accel-buffering": "no",
            },
        )

    # 其余端点普通转发
    try:
        resp = await client.request(request.method, url, json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"内核通信失败：{exc}") from exc
    finally:
        await client.aclose()

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )