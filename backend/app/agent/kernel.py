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
    """探活内核。未启动时返回 ok=False 而非抛异常。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.kernel_base_url}/healthz")
            if resp.status_code == 200:
                return {"ok": True, **resp.json()}
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

    try:
        async with httpx.AsyncClient(
            timeout=settings.kernel_timeout_seconds
        ) as client:
            async with client.stream(
                "POST",
                f"{settings.kernel_base_url}/v1/chat",
                json=payload,
                headers={"content-type": "application/json"},
            ) as resp:
                if resp.status_code != 200:
                    yield {
                        "event": "error",
                        "data": {"message": f"内核返回 HTTP {resp.status_code}"},
                    }
                    return

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    # 内核 SSE 帧形如 "event: text-delta\ndata: {...}"
                    yield {"event": _infer_event(raw, data), "data": data}

    except httpx.TimeoutException:
        yield {"event": "error", "data": {"message": "内核响应超时"}}
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": {"message": str(exc)}}


def _infer_event(raw: str, data: dict) -> str:
    """从载荷推断事件类型。

    内核不同版本帧格式略有差异，这里做兼容推断：
    有 text 且是 str-> text-delta；有 toolName -> approval-request 等。
    """
    if isinstance(data, dict):
        if "toolName" in data and "id" in data:
            return "approval-request"
        if "text" in data:
            # 无法区分 text/reasoning，默认 text
            return "text-delta"
        if "message" in data:
            return "error"
    if data is None:
        return "done"
    return "message"


async def approve(approval_id: str, allow: bool = True) -> dict:
    """回审批决策（内核 fail-closed：无人应答 2 分钟按拒绝）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.kernel_base_url}/v1/approve",
                json={"id": approval_id, "decision": "allow" if allow else "deny"},
            )
            return {"ok": resp.status_code == 200, "detail": resp.json()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def list_kernel_tools() -> list[dict]:
    """取内核工具清单（含内置 26 个 + 自定义）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.kernel_base_url}/v1/tools")
            if resp.status_code != 200:
                return []
            data = resp.json()
            registered = [t for t in data.get("builtin", []) if t.get("registered")]
            registered += [t for t in data.get("custom", []) if t.get("registered")]
            return registered
    except Exception:  # noqa: BLE001
        return []
