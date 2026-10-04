"""Agent 对话 API：SSE 流式转发 + 审批通道。

**绝不浏览器直连内核**（docs/01/08 铁律）——
所有内核交互都必须经这里，由后端转发并做鉴权/限流。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import kernel
from app.db.models import ToolCallLog
from app.db.session import get_db

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatRequest(BaseModel):
    """对话请求。"""

    text: str = Field(..., min_length=1, description="要跟内核说什么")
    # 会话约定：note-{topicId}，一个内容任务一个 session
    session_id: str | None = Field(None, description="可选，不传则内核新建")


class ApproveRequest(BaseModel):
    """审批决策。"""

    id: str
    allow: bool = True


@router.get("/kernel", summary="内核状态")
async def kernel_status() -> dict:
    """探活内核并返回可用工具清单。"""
    status = await kernel.check_kernel()
    status["tools"] = await kernel.list_kernel_tools() if status.get("ok") else []
    return status


@router.post("/chat", summary="对话（SSE 流式）")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """转发对话到内核，SSE 原样透传（不缓冲）。

    会话 ID 建议：note-{topicId}，可断线续传。
    """

    async def gen():
        text_buf: list[str] = []
        async for evt in kernel.stream_chat(req.text, req.session_id):
            yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"

            # 记录本轮工具调用（审计用）
            data = evt["data"]
            if isinstance(data, dict) and evt["event"] == "tool-call":
                db.add(
                    ToolCallLog(
                        tool_name=str(data.get("name", "")),
                        arguments=data.get("arguments", {}),
                        ok=1,
                    )
                )
                db.commit()

            if evt["event"] == "text-delta" and isinstance(data, dict):
                text_buf.append(str(data.get("text", "")))

        # 记录最终文本摘要
        if text_buf:
            full = "".join(text_buf)[:2000]
            db.add(ToolCallLog(tool_name="_assistant_text", result_summary=full, ok=1))
            db.commit()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache, no-transform",
            "x-accel-buffering": "no",  # 关键：禁代理缓冲
        },
    )


@router.post("/approve", summary="回审批决策")
async def approve(req: ApproveRequest) -> dict:
    """审批通道：内核在执行敏感工具前会 waterfall 出 approval-request。

    注意 fail-closed 语义：无客户端在线时不擅自放行，
    2 分钟无人应答按拒绝处理。
    """
    return await kernel.approve(req.id, req.allow)


@router.get("/tools", summary="内核工具清单")
async def tools() -> dict:
    """返回内核当前可调用的工具，供工作台前端展示。"""
    tool_list = await kernel.list_kernel_tools()
    return {"count": len(tool_list), "tools": tool_list}
