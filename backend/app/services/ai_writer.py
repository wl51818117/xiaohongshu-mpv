"""AI 创作服务：标题生成、多轮打磨、标签推荐、去AI 味。

统一走内核（Agent 编排），并解析 SSE 取文本。
输出统一经 `_clean` 清洗 —— 小模型会把思考过程一起吐出来。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


# ── 内核调用 ────────────────────────────────────────────

def _api_key() -> str:
    """内核鉴权凭据：环境变量优先（与内核自身优先级一致）。"""
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    try:
        f = Path(settings.data_dir) / "secrets.json"
        if f.exists():
            return str(json.loads(f.read_text(encoding="utf-8")).get("api_key") or "").strip()
    except Exception:
        pass
    return ""


async def ask(prompt: str, session: str | None = None, timeout: int = 180) -> str:
    """向内核提问，返回清洗后的纯文本。"""
    headers = {"content-type": "application/json"}
    key = _api_key()
    if key:
        headers["authorization"] = f"Bearer {key}"

    payload: dict[str, Any] = {"text": prompt}
    if session:
        payload["sessionId"] = session

    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                f"{settings.kernel_base_url}/v1/chat", json=payload, headers=headers
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"内核不可达（{settings.kernel_base_url}）：{exc}。"
            f"请确认已启动 提取harness/start-all.cmd"
        ) from exc

    if r.status_code != 200:
        raise RuntimeError(f"内核返回 HTTP {r.status_code}：{r.text[:160]}")

    parts: list[str] = []
    for line in r.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            o = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = o.get("text")
        if isinstance(t, str):
            parts.append(t)

    text = _clean("".join(parts))
    if not text:
        raise RuntimeError("内核未返回可用文本")
    return text


def _clean(text: str) -> str:
    """剥掉推理残留与 markdown，**优先保留 JSON 代码块**。

    ★踩过的坑：模型常先吐一段英文推理再给 ```json 块。
      早期按「行」剥英文前缀，把 ```json 那行一起带走了，
      导致后面 JSON 解析失败（表现为「内核未返回可用文本」）。
    """
    s = text.strip()
    if not s:
        return ""

    # ① 若存在 markdown 代码块，优先取第一个含 [ 或 { 的块
    for m in re.finditer(r"```(?:json)?\s*\n?([\s\S]*?)```", s):
        inner = m.group(1).strip()
        if inner.startswith(("[", "{")):
            return inner

    # ② 没有代码块但有裸 JSON，截取
    for opener, closer in (("[", "]"), ("{", "}")):
        i = s.find(opener)
        j = s.rfind(closer)
        if i != -1 and j > i:
            return s[i : j + 1].strip()

    # ③ 常规清洗（纯文本场景）
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(
        r"^(好的|当然|以下是|下面是|这是)[^\n]{0,40}?(标题|正文|文案|选项)?[:：]\s*", "", s
    )
    s = re.sub(r"```[a-z]*", "", s)
    s = re.sub(r"```", "", s)

    lines = s.split("\n")
    while lines and (
        not lines[0].strip()
        or re.match(r"^\s*[A-Za-z][A-Za-z\s,.:;'\"()\-]{20,}", lines[0])
        or re.search(r"\b(I need|Let me|Here's|First,|Need to)\b", lines[0], re.I)
    ):
        lines.pop(0)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ── 1. 批量爆款标题 ────────────────────────────────────

TITLE_PROMPT = """你���小红书爆款标题写手。为下面这个选题一次性生成 **{n} 个**标题候选。

【选题】{topic}
【目标人群】{persona}
【价值类型】{vtype}

标题硬性要求：
- 每个 **不超过 20 个字**
- **前 8-13 字**必须包含长尾关键词「{keyword}」
- 公式多样性：人群+痛点、数字+结果、反常识、提问代入、场景代入
- 不得出现极限词与导流话术
- 不得编造数据或虚假承诺

**只输出 JSON 数组**，每项是一个字符串，格式：
["标题1","标题2",...{n}项]

不要输出解释、不要 markdown 代码块。"""


async def gen_titles(
    topic: str,
    keyword: str,
    persona: str = "",
    vtype: str = "实用",
    n: int = 10,
    session: str | None = None,
) -> list[str]:
    """一次性生成 n 个爆款标题候选。"""
    prompt = TITLE_PROMPT.format(
        n=n, topic=topic, persona=persona or "不限", vtype=vtype, keyword=keyword
    )
    text = await ask(prompt, session=session)

    # 抽取 JSON 数组（模型可能带 markdown 或前后废话）
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            arr = json.loads(m.group(0))
            titles = [str(x).strip() for x in arr if str(x).strip()]
            if titles:
                return titles[:n]
        except json.JSONDecodeError:
            pass

    # 兜底：按行拆
    out: list[str] = []
    for line in text.splitlines():
        s = re.sub(r"^[\s\-\*\d\.、\)]+", "", line).strip().strip('「」"')
        s = re.sub(r"\d+\.\s*", "", s)
        if 4 <= len(s) <= 30:
            out.append(s)
    return out[:n] or [topic]


# ── 2. 多轮打磨 ────────────────────────────────────────

POLISH_ACTIONS: dict[str, dict[str, str]] = {
    "humanize": {
        "label": "去 AI 味",
        "prompt": """把下面正文改写成「不像 AI 写的」。

要求：
1. 多用短句、口语词（如「真的」「说白了」「姐妹们」）
2. 加入**具体的个人化细节**：踩过的坑、犹豫、真实场景
3. 删掉套话、书面语、排比句
4. 保留原意与信息量，不新增事实
5. 长度 {min_}-{max_} 字

【原文】
{body}""",
    },
    "polish": {
        "label": "润色",
        "prompt": """润色下面正文，保持原意，提升可读性。

要求：
1. 句子之间加过渡，逻辑更顺
2. 修辞用得克制，不要过度华丽
3. 段落分明，每段一个意思
4. 长尾词「{keyword}」自然出现 2-3 次（不要堆砌）
5. 长度 {min_}-{max_} 字

【原文】
{body}""",
    },
    "shorten": {
        "label": "精简",
        "prompt": """把下面正文压缩到 {min_}-{max_} 字。

要求：
1. 只删冗余与重复，不删关键信息
2. 保留长尾词「{keyword}」
3. 保持三段式结构

【原文】
{body}""",
    },
    "expand": {
        "label": "扩写",
        "prompt": """把下面正文扩写到 {min_}-{max_} 字。

要求：
1. 补充具体例子、数据、场景细节
2. 不要空话套话，新增内容要有信息量
3. 保持长尾词「{keyword}」

【原文】
{body}""",
    },
    "hook": {
        "label": "改开头",
        "prompt": """只重写正文的前 2-3 行（钩子），其余保持不变。

要求：
1. 第一句必须抓住注意力：痛点、反常识或数字冲击
2. 长尾词「{keyword}」要出现在**前 80 字内**
3. 不要「大家好」「今天分享」这类开场

【原文】
{body}""",
    },
}


async def polish(
    action: str,
    body: str,
    keyword: str = "",
    extra: str = "",
    session: str | None = None,
) -> str:
    """按动作打磨正文。

    extra 用于多轮对话：可传「再口语一点」「换个开头」等追加指令。
    """
    cfg = POLISH_ACTIONS.get(action)
    if not cfg:
        raise ValueError(f"不支持的打磨动作：{action}")

    prompt = cfg["prompt"].format(
        body=body, keyword=keyword or "（无需埋词）", min_=300, max_=800
    )
    if extra.strip():
        prompt += f"\n\n【额外要求】{extra.strip()}"
    return await ask(prompt, session=session)


async def chat(
    body: str,
    message: str,
    keyword: str = "",
    session: str | None = None,
) -> str:
    """多轮对话打磨：把当前正文带上，按用户指令改。"""
    prompt = f"""你在帮一篇小红书笔记打磨正文。当前正文：

【当前正文】
{body}

【用户要求】
{message}

要求：
1. 按用户要求改，只输出改后的正文
2. 保持长尾词「{keyword or '（无）'}」自然出现
3. 长度 300-800 字
4. 不要输出解释"""
    return await ask(prompt, session=session)


# ── 3. 智能标签推荐 ────────────────────────────────────

TAG_PROMPT = """根据下面的小红书笔记，推荐 **5-8 个**标签。

【标题】{title}
【正文摘要】{summary}
【人群】{persona}

要求：
1. 混合三类：**品类词**（买什么）、**场景词**（什么场合用）、**人群词**（谁用）
2. 不要「上热门」「热门推荐」这类大泛标签
3. 不要与内容无关的蹭流量标签
4. 每个标签不超过 8 个字

**只输出 JSON 数组**，格式：["标签1","标签2",...]
不要解释。"""


async def gen_tags(
    title: str,
    body: str,
    persona: str = "",
    session: str | None = None,
) -> list[str]:
    """推荐标签。"""
    prompt = TAG_PROMPT.format(
        title=title, summary=(body or "")[:300], persona=persona or "不限"
    )
    text = await ask(prompt, session=session)

    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            arr = json.loads(m.group(0))
            tags = [str(x).strip().lstrip("#") for x in arr if str(x).strip()]
            if tags:
                return tags[:8]
        except json.JSONDecodeError:
            pass

    out: list[str] = []
    for line in text.splitlines():
        s = re.sub(r"^[\s\-\*\d\.、\)]+", "", line).strip().strip('「」"#')
        s = re.split(r"[、,，\s]+", s)[0]
        if 2 <= len(s) <= 10:
            out.append(s)
    return out[:8]
