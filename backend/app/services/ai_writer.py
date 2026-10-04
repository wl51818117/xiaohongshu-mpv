"""AI 创作服务：标题生成、多轮打磨、标签推荐、去AI 味。

统一走内核（Agent 编排），并解析 SSE 取文本。
输出统一经 `_clean` 清洗 —— 小模型会把思考过程一起吐出来。

★★ 凭据有两种，别混（这个坑踩过一次，表现为「AI 写作全线 401」）：
  1. **内核凭据**（hbridge 静态 token）—— 我们连内核时用，
     来自 `settings.kernel_auth_headers`。
  2. **模型密钥**（DEEPSEEK_API_KEY）—— 是给*内核*去调模型的，
     拿它当内核凭据会被 401。
  之前误用了第 2 种，导致标题/打磨/标签三个功能全挂。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


# ── 历史经验注入（持续增强的接线点）───────────────────────

def _inject_experience(prompt: str, limit: int = 3) -> str:
    """把知识库里检索到的历史经验拼到提示词前面。

    ★ 为什么放在这里：
      ask() 是所有 LLM 调用的唯一出口。经验不是给人看的黑盒，
      而是**真的要喂给模型**——否则知识库只是个手动查询的摆设。

    用 prompt 前 500 字做检索输入：BM25 输入太长无益，也避免提示词膨胀。
    检索失败不阻断主流程（知识库挂了不该导致生成失败）。
    """
    try:
        from app.db.session import SessionLocal
        from app.services import knowledge as kb

        db = SessionLocal()
        try:
            block = kb.inject_experience(db, prompt[:500], limit=limit)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return prompt

    if not block:
        return prompt
    return block + prompt


# ── 内核调用 ────────────────────────────────────────────

def _model_key() -> str:
    """模型密钥（给内核调模型用），**不是**内核的接入凭据。"""
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
    """向内核提问，返回清洗后的纯文本。

    ★ 这里是**所有** LLM 调用的唯一出口（gen_titles / polish / chat /
      gen_tags 都走它），所以「注入历史经验」只需改这一处，全链路生效。
      —— 这就是持续增强的接线点（借鉴 Reflexion：反思写进记忆，
      下次任务带着它）。
    """
    # ── 注入历史经验（先查库，再拼进提示词）──
    prompt = _inject_experience(prompt)

    # hbridge v2.1 起 /v1/* 全部要鉴权，漏头就是 401
    headers = {"content-type": "application/json", **settings.kernel_auth_headers}

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
        raise RuntimeError(
            f"内核返回 HTTP {r.status_code}：{r.text[:160]}"
            + (
                "\n提示：内核鉴权失败。检查 settings.kernel_token，"
                "或 kernel_patch_file 是否指向内核 profile 的 cordis.patch.yml。"
                if r.status_code == 401
                else ""
            )
        )

    # ★ 按 event 行区分 text-delta 与 reasoning-delta。
    #   原来只要 data 里有 text 就收，结果把模型的思考过程当正文返回了。
    parts: list[str] = []
    pending_event: str | None = None
    for line in r.text.splitlines():
        line = line.strip()
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
            o = json.loads(raw)
        except json.JSONDecodeError:
            continue

        kind = pending_event
        if kind is None:
            # 兜底：没 event 行时看载荷里的 kind/type
            kind = str(o.get("type") or "")
        if kind and kind != "text-delta":
            # 明确不是正文（reasoning-delta / tool-call / turn-start 等）→ 跳过
            pending_event = None
            continue
        pending_event = None

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

# ★ 重构思路（原来只给一句"写10个爆款标题"，模型自由发挥，实测不可用）：
#   1) 给**公式库**而不是形容词—— 明确 6 种爆款公式，各自的结构模板
#   2) 给**反例**—— 列出实测不合格的写法，让模型避开
#   3) **确定性校验+ 自动重写**—— 拿到结果先本地校验，不合格的不放行
#   4) 埋词要求从"前8-13字含整个长尾词"改成"含核心词"——
#      多词长尾词（如「充电桩 排队」）整串塞进标题必超20 字且读不通

TITLE_FORMULAS = """1. **人群+痛点**：「打工人{核心词}踩的坑」—— 直接点名人群
2. **数字+结果**：「{核心词}的 3 个关键判断」—— 有具体数字更可信
3. **反常识**：「{核心词}其实是智商税」—— 打破惯性认知
4. **提问代入**：「{核心词}到底该怎么选？」—— 疑问句点击率高
5. **场景代入**：「国庆去-service {核心词}」—— 锁定具体场景
6. **避坑清单**：「{核心词}千万别踩这 3 个坑」—— 损失厌恶"""

TITLE_PROMPT = """你是小红书爆款标题写手。为下面这个选题一次性生成 **{n} 个**标题候选。

【选题】{topic}
【目标人群】{persona}
【价值类型】{vtype}
【必须包含的核心词】{core}

标题硬性要求（**每一条都必须满足**）：
1. 每个标题 **不超过 20 个字**（含标点）
2. 核心词「{core}」必须出现在标题的**前 10 个字内**（搜索权重第一来源）
3. 必须使用下面 6 种爆款公式中的至少 4 种，**不要重复同一种公式**：
{formulas}
4. 禁止极限词（最/第一/绝对/全网最好）、禁止导流话术、禁止编造数据
5. 像人写的，不要书面语，不要「探讨」「解析」这类空词

【反面示例（不要这样写）】
- 「{topic}的全面分析与详细介绍」—— 书面语、无钩子、无公式
- 「如何{core}？一篇讲清」—— 废话开头，没有具体信息
- 「{core}技巧大全」—— 太泛，缺少人群或场景锚点

**只输出 JSON 数组**，每项是一个字符串，格式：
["标题1","标题2",...{n}项]

不要输出解释、不要 markdown 代码块。"""


def _core_terms(keyword: str) -> list[str]:
    """把长尾词拆成可埋入标题的核心词。

    与 draft_validator._keyword_terms 同源 —— 校验器和生成器必须用同一套
    拆词规则，否则「生成出来的标题」过不了「自己的校验器」。
    """
    if not keyword:
        return []
    terms = [t for t in re.split(r"[\s、,，/|+]+", keyword) if t]
    if not terms:
        return [keyword]
    terms.sort(key=len, reverse=True)
    return terms[:2]


def score_title(title: str, core_terms: list[str]) -> tuple[bool, list[str]]:
    """标题确定性校验：返回 (是否合格, 问题列表)。

    ★ 与 prompt 里的要求一一对应 —— prompt 说了什么，这里就查什么。
      只靠模型自觉是不够的，实测它会超字数、漏埋词。
    """
    issues: list[str] = []
    t = (title or "").strip().strip('「」"')
    if not t:
        return False, ["空标题"]
    if len(t) > 20:
        issues.append(f"{len(t)}字超上限")
    if core_terms:
        pos = min((t.find(c) for c in core_terms if c in t), default=-1)
        if pos == -1:
            issues.append("未含核心词")
        elif pos > 10:
            issues.append(f"核心词在第{pos + 1}字，应在前10字内")
    # 空话开头（模型爱写）
    for bad in ("如何", "浅谈", "探讨", "解析", "全面分析", "详细介绍", "一篇讲清"):
        if t.startswith(bad):
            issues.append(f"以空词「{bad}」开头")
            break
    return (not issues), issues


def _strip_title(t: str) -> str:
    """去掉模型常见的包裹符号与编号前缀。"""
    s = (t or "").strip()
    s = re.sub(r"^\s*\d+\s*[.、)）]\s*", "", s)
    s = s.strip().strip("「」\"'“”‘’ ")
    return s


def _parse_title_list(text: str) -> list[str]:
    """从模型输出里抽出标题列表（JSON 优先，退回按行）。"""
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            arr = json.loads(m.group(0))
            out = [_strip_title(str(x)) for x in arr]
            out = [x for x in out if x]
            if out:
                return out
        except json.JSONDecodeError:
            pass

    out = []
    for line in text.splitlines():
        s = _strip_title(line)
        if not s:
            continue
        # 单行可能含多个引号分隔的标题
        parts = re.findall(r'[""「」]([^""「」]{4,30})[""「」]', line)
        if parts:
            out.extend(_strip_title(p) for p in parts)
        elif 4 <= len(s) <= 30:
            out.append(s)
    return out


async def gen_titles(
    topic: str,
    keyword: str,
    persona: str = "",
    vtype: str = "实用",
    n: int = 10,
    session: str | None = None,
) -> list[str]:
    """一次性生成 n 个爆款标题候选（**带确定性校验**）。

    流程：生成 → 本地校验 → 不合格的丢弃并标记 → 不足则补生成一轮。
    这样返回给用户的**至少都是能过校验器**的标题。
    """
    core = _core_terms(keyword)
    core_str = "、".join(core) if core else keyword

    prompt = TITLE_PROMPT.format(
        n=n, topic=topic, persona=persona or "不限", vtype=vtype,
        core=core_str, formulas=TITLE_FORMULAS,
    )
    text = await ask(prompt, session=session)
    raw = _parse_title_list(text)

    # ── 确定性校验 + 去重 ──
    good: list[str] = []
    seen: set[str] = set()
    rejected: list[dict] = []
    for t in raw:
        ok, issues = score_title(t, core)
        key = re.sub(r"[^\w一-鿿]", "", t)
        if not ok:
            rejected.append({"title": t, "issues": issues})
            continue
        if key in seen:
            continue
        seen.add(key)
        good.append(t)

    # ── 不足则补一轮（把不合格的作为反例喂回去）──
    if len(good) < n:
        retry = prompt
        if rejected:
            retry += (
                "\n\n【上一轮不合格示例（不要重复这些问题）】\n"
                + "\n".join(f"- {r['title']}：{'、'.join(r['issues'])}" for r in rejected[:5])
            )
        try:
            text2 = await ask(retry, session=session)
            for t in _parse_title_list(text2):
                ok, _ = score_title(t, core)
                key = re.sub(r"[^\w一-鿿]", "", t)
                if ok and key not in seen:
                    seen.add(key)
                    good.append(t)
                    if len(good) >= n:
                        break
        except Exception:  # noqa: BLE001
            pass  # 补生成失败不影响已拿到的结果

    result = good[:n]

    # ── 沉淀：把成功/失败样本写回知识库（持续增强的闭环）──
    # 借鉴 ExpeL：拿「一条成功 + 一条失败」对照提炼规则。
    # 这里的「提炼」由确定性校验器完成（比 LLM 抽取可靠）。
    try:
        from app.services import experience as exp

        exp.record_title_outcome(
            topic=topic, keyword=keyword, persona=persona,
            accepted=result, rejected=rejected,
        )
    except Exception:  # noqa: BLE001
        pass  # 沉淀失败不影响生成结果

    return result


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
