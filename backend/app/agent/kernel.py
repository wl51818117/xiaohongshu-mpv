"""dsh 内核集成（Agent 编排层）。

安全铁律（docs/01/08）：
  **绝不浏览器直连内核** —— 内核带 pwsh/read/write 工具，
  直连等于把 shell 暴露给前端。所有请求必须经我们后端转发。

内核机制（已实测 docs/01）：
  - POST /v1/chat 返回 SSE 流
  - 一个 sessionId = 一个常驻 Agent
  - 会话约定：note-{topicId}，可断线续传
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx

from app.core.config import settings

# 内核 SSE 事件类型（docs/01 README 第四章）
SSE_EVENTS = [
    "session", "text-delta", "reasoning-delta",
    "tool-call", "tool-result",
    "approval-request", "done", "error",
]


async def check_kernel() -> dict:
    """探活内核。未启动时返回 ok=False 而非抛异常。

    用 /v1/sessions 而非 /healthz：后者不存在（404），
    带鉴权打真实的业务接口，顺带验证凭据是否有效——
    否则会出现「状态显示在线，一发消息就 401」的假象（这个坑踩过）。
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.kernel_base_url}/v1/sessions",
                headers=settings.kernel_auth_headers,
            )
            if resp.status_code == 200:
                return {"ok": True, **(resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {})}
            if resp.status_code == 401:
                return {"ok": False, "error": "内核鉴权失败（401）", "hint": "检查 kernel_token / kernel_patch_file"}
            if resp.status_code == 404:
                # 老版本没有 /v1/sessions，退回探活
                probe = await client.get(f"{settings.kernel_base_url}/healthz")
                if probe.status_code == 200:
                    return {"ok": True}
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def stream_chat(text: str, session_id: str | None = None) -> AsyncGenerator[dict, None]:
    """向内核发起对话，流式接收 SSE 事件。

    产出统一格式：{"event": <类型>, "data": <载荷>}
    """
    payload: dict = {"text": text}
    if session_id:
        payload["sessionId"] = session_id

    # hbridge v2.1 起 /v1/* 全部要鉴权，漏头就是 401
    headers = {"content-type": "application/json", **settings.kernel_auth_headers}

    try:
        async with httpx.AsyncClient(
            timeout=settings.kernel_timeout_seconds
        ) as client:
            async with client.stream(
                "POST",
                f"{settings.kernel_base_url}/v1/chat",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    detail = ""
                    try:
                        detail = resp.read().decode("utf-8", "replace")[:200]
                    except Exception:  # noqa: BLE001
                        pass
                    yield {
                        "event": "error",
                        "data": {
                            "message": f"内核返回 HTTP {resp.status_code}",
                            "detail": detail,
                            "hint": _auth_hint(resp.status_code),
                        },
                    }
                    return

                # 内核 SSE 帧形如：
                #   event: text-delta
                #   data: {"text":"..."}
                # event 行是权威的事件类型，必须读；读不到才靠载荷推断。
                pending_event: str | None = None
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("event:"):
                        pending_event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    yield {
                        "event": pending_event or _infer_event(data),
                        "data": data,
                    }
                    pending_event = None

    except httpx.TimeoutException:
        yield {"event": "error", "data": {"message": "内核响应超时"}}
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": {"message": str(exc)}}


def _auth_hint(status: int) -> str:
    """401/403 给出可执行的排查指引，别只丢一个数字。"""
    if status == 401:
        return (
            "内核鉴权失败。后端未带对 authorization 头——检查 settings.kernel_token，"
            "或 kernel_patch_file 是否指向内核 profile 的 cordis.patch.yml。"
        )
    if status == 403:
        return "内核拒绝来源。检查 allowedOrigins 是否包含前端 origin。"
    return ""


def _infer_event(data: object) -> str:
    """读不到 event 行时的兜底推断。

    注意：内核实发 `reasoning-delta`，这里只在没有 event 行时才猜，
    且**不再把 reasoning 当 text**（旧版会把模型思考过程当正文显示）。
    """
    if data is None:
        return "done"
    if isinstance(data, dict):
        if "toolName" in data and "id" in data:
            return "approval-request"
        kind = data.get("kind")
        if kind == "session-event":
            return str(data.get("type") or "message").split("/")[-1].replace("start", "start")
        if "message" in data:
            return "error"
        if "text" in data:
            return "text-delta"
    return "message"


async def approve(approval_id: str, allow: bool = True) -> dict:
    """回审批决策（内核 fail-closed：无人应答 2 分钟按拒绝）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.kernel_base_url}/v1/approve",
                json={"id": approval_id, "decision": "allow" if allow else "deny"},
                headers=settings.kernel_auth_headers,
            )
            detail: object
            try:
                detail = resp.json()
            except Exception:  # noqa: BLE001
                # 401/403 返回的是 JSON，但别假设一定是
                detail = resp.text[:200]
            return {"ok": resp.status_code == 200, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def list_kernel_tools() -> list[dict]:
    """取内核工具清单（内置 + 自定义 + 客户端上报的动态能力）。

    必须带鉴权头：hbridge v2.1 起 /v1/* 全要 Bearer，
    否则拿到 401 -> 这里返回 [] -> 界面显示「工具 0 个」，看不出真实原因。
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.kernel_base_url}/v1/tools",
                headers=settings.kernel_auth_headers,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            out: list[dict] = []
            for group in ("builtin", "custom", "client"):
                out += [t for t in data.get(group, []) if t.get("registered")]
            return out
    except Exception:  # noqa: BLE001
        return []
