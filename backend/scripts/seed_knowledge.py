"""把平台规格与项目踩坑种进知识库。

★ 为什么要「种」而不是只靠 Obsidian 导入：
  实测检索「标题埋词位置」时，召回的全是 Android 状态栏那类工程经验
  ——因为 OB 库里根本没有小红书平台的内容规格。
  知识库缺了本领域最该查的东西，RAG 就形同虚设。

幂等：同标题 upsert，重复执行不会产生重复条目。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.services import knowledge as kb  # noqa: E402

# 平台规格（来源 docs/02-内容规格与合规基线.md、docs/00-平台流程调研结论.md）
SPEC_ITEMS = [
    {
        "title": "标题必须前10字埋入核心长尾词",
        "kind": "spec",
        "why": "小红书搜索权重的第一来源是标题前段。只在正文里出现等于没做搜索布局，"
               "用户搜「内衣 面料」时你的笔记可能根本进不了结果页。",
        "how": "把长尾词拆成核心词（取最长的 2 个词，整串塞进标题会超字数且读不通），"
               "让核心词出现在标题第 1-10 字。标题总长≤20 字。"
               "校验器 draft_validator._keyword_terms 与标题生成器 ai_writer._core_terms "
               "必须用同一套拆词规则，否则「生成出来的标题」过不了「自己的校验器」。",
        "pitfall": "多词长尾词（如「手机银行 功能 职场人 办事效率」）整串埋进标题必然超20 字。",
        "tags": "xhs,标题,埋词,搜索权重",
    },
    {
        "title": "正文 300-800 字且前 80 字必须埋词",
        "kind": "spec",
        "why": "小红书正文不足 300 字会被折叠降权；核心词出现在 80 字之后，"
               "系统判定为「非主题内容」同样降权。",
        "how": "全文 300-800 字（实测 380-650 字最优），核心词在前 80 字内出现，"
               "全文自然出现 2-3 次。超过 3 次属堆砌，可能触发限流。",
        "pitfall": "为凑字数加空话套话会同时踩「低质」和「AI 味」两个坑。",
        "tags": "xhs,正文,字数,埋词",
    },
    {
        "title": "标签要混合品类词场景词人群词共 3-5 个",
        "kind": "spec",
        "why": "单一大泛标签（如「上热门」）竞争激烈且与内容无关，容易被判蹭流量。"
               "混合三类标签才能覆盖不同的搜索入口。",
        "how": "3-5 个，必须同时包含：品类词（买什么）、场景词（什么场合用）、人群词（谁用）。"
               "每个标签不超过 8 字。",
        "pitfall": "「上热门」「热门推荐」这类大泛标签没有精准流量，还可能被判无效。",
        "tags": "xhs,标签,搜索",
    },
    {
        "title": "封面必须 3:4 竖版 1080x1440 且出3 个候选",
        "kind": "spec",
        "why": "信息流展示位是 3:4，非 3:4 会被裁切或留白。点击率低于 2.5% 难晋级流量池，"
               "所以封面是最需要 A/B 的环节。",
        "how": "尺寸 1080×1440 写进提示词（让模型按竖版构图），而不是生成后再裁。"
               "一次出 3 个候选人工挑。真实服务返回非 3:4 时加白边纠正，"
               "不要拉伸——拉伸会让商品变形。",
        "pitfall": "视频也必须 1080×1440/ 30fps，文生视频画面不可控，电商不可用，必须图生视频。",
        "tags": "xhs,封面,规格,3:4",
    },
    {
        "title": "AI 生成内容必须主动标识",
        "kind": "spec",
        "why": "平台明确要求 AI 生成/合成内容必须标识，不标识即违规。"
               "但平台同时明确「主动标注不影响流量」。",
        "how": "文案用「本内容含 AI 辅助生成部分」，视频用「本内容含 AI 生成的画面与配音」。"
               "不必具体说明哪部分用了 AI，过犹不及反而像在掩饰。",
        "pitfall": "不要用变体规避检测，平台能识别语义相似度。",
        "tags": "xhs,合规,AI声明",
    },
    {
        "title": "站外导流是平台处罚最重的一类违规",
        "kind": "spec",
        "why": "导流（微信/电话/二维码）判例：曾有爬取平台数据被判有期徒刑三年、判赔 110 万的案例。"
               "导流的处罚同样严厉。",
        "how": "正文与标签一律不出现：加微信/vx/扣1/私信获取/扫码/二维码/淘宝口令/看主页。"
               "结尾用「把这三个判断记住」这类自然行动建议，不要「关注我」「评论区扣1」。",
        "pitfall": "「点赞领」「关注领」「私信我」属诱导互动，同样违规。",
        "tags": "xhs,合规,导流,红线",
    },
    {
        "title": "不爬取小红书数据不做 AI 全自动发布",
        "kind": "spec",
        "why": "爬取平台数据有判刑判赔判例；平台 2026 明确封禁 AI 托管账号"
               "（已处置 80 万+），风控含鼠标行为指纹。",
        "how": "采集只走公开 RSS 源。发布链路做「半自动」——程序组装好发布包并做12 项自检，"
               "人工点确认。",
        "pitfall": "半自动不是偷懒，是合规前提。",
        "tags": "xhs,合规,红线,采集",
    },
    {
        "title": "选题关键词质量过滤：整串长尾词会产生垃圾词",
        "kind": "pitfall",
        "why": "RSS 标题里截取的关键词可能是「天高速公路新」这种被截断的碎片，"
               "整串埋进标题既超字数又读不通，还会污染下游全部环节。",
        "how": "关键词先分词再取最长的 2 个词作核心词；"
               "对明显不完整的词（长度<3 或含单字连接）做过滤，"
               "或回退到素材标题的前 N 个字。",
        "pitfall": "实测出过「天高速公路新」「月广州海关监」这类垃圾词，"
                   "写进标题后校验必然失败且读不通。",
        "tags": "xhs,选题,关键词,数据质量",
    },
    {
        "title": "素材生成必须吃稿件全上下文而非只吃标题",
        "kind": "pitfall",
        "why": "只用 title 构造提示词，生成出来的图与正文内容毫无关系——"
               "图和文各说各话，图文不符会被判低质。",
        "how": "提示词要注入：标题 + 核心关键词 + 从正文抽取的 3-4 个信息点"
               "（按中文标点切句，取含数字或判断词的短句）+ 标签。"
               "内页每张对应正文的一个信息点，不要 6 张都用「产品全景」。",
        "pitfall": "占位图时代就把提示词摘要画进图里，"
                   "这样不接真实 API 也能一眼看出串联是否生效。",
        "tags": "素材,提示词,图文一致",
    },
    {
        "title": "确定性校验器用规则引擎而非 LLM",
        "kind": "insight",
        "why": "平台红线（字数、埋词位置、标签数量、合规词）是硬指标，"
               "让 LLM 判断会漏且不可复现。",
        "how": "用 draft_validator.validate_draft 做规则校验，"
               "prompt 里说了什么要求，校验器就查什么——两处必须对齐。"
               "生成器产出后立即本地校验，不合格的重生成，不放行。",
        "pitfall": "只在 prompt 里写要求而不做本地校验，等于把质量交给模型的自觉。",
        "tags": "校验,架构,质量",
    },
    {
        "title": "AI 写作调用内核要区分两种凭据",
        "kind": "pitfall",
        "why": "hbridge v2.1 起所有 /v1/* 都要 Bearer 鉴权头。"
               "曾把 DEEPSEEK_API_KEY（模型密钥，给内核调模型用）"
               "误当成内核接入凭据，导致标题/打磨/标签三个功能全部 401。",
        "how": "内核接入凭据统一从 settings.kernel_auth_headers 取，"
               "自动从内核 profile 的 cordis.patch.yml 读（唯一真源）。"
               "模型密钥是另一回事，只在内核内部使用。",
        "pitfall": "取 token 的正则必须只匹配独立成行的 `token:`，"
                   "否则会先匹配到 appTokens 里的内容导致 401。"
                   "另外 /healthz 不鉴权且实际 404，用它探活会掩盖故障。",
        "tags": "hbridge,鉴权,踩坑,AI写作",
    },
    {
        "title": "SSE 解析必须读 event 行不要靠载荷猜",
        "kind": "pitfall",
        "why": "内核实发标准双行帧（event: text-delta / data: {...}）。"
               "原来只要 data 里有 text 就收，结果把 reasoning-delta"
               "（模型思考过程）当正文返回给用户。",
        "how": "读 event 行作为权威事件类型，读不到才用载荷推断。"
               "这和「401 被显示成『内核未返回文本』」是同一类错误——"
               "猜出来的信息会误导排查方向。",
        "pitfall": "任何「兼容不同版本」的猜字段逻辑，都会在某个版本静默出错。",
        "tags": "SSE,hbridge,踩坑",
    },
    {
        "title": "Pydantic 可选 body 参数会让 query 兜底失效",
        "kind": "pitfall",
        "why": "接口声明 `req: GenerateRequest | None = None` 时，"
               "前端同时传 query 和空 body `{}`，FastAPI 会先尝试实例化该模型，"
               "因缺必填字段直接 422，根本走不到 query 兜底的合并逻辑。",
        "how": "同时支持 body 和 query 时，用 `request: Request` 自己读 body，"
               "解析失败就当空 dict 处理，再与 query 合并。"
               "query 参数用 Query() 而非 Field() 赋默认值。",
        "pitfall": "FastAPI 里`limit: int = Field(50)` 会报"
                   "「non-body parameters must be in path, query, header or cookie」。",
        "tags": "FastAPI,踩坑,后端",
    },
    {
        "title": "UI 按钮要封装组件不要用 !important 硬凑尺寸",
        "kind": "insight",
        "why": "全项目曾有 18 种按钮写法，靠 `!min-h-[26px] !px-2 !text-[11px]` 凑尺寸。"
               "同样的语义按钮在不同页面高度不一致，改主题时也改不动。"
               "还出现过 CSS 里从没定义过的 btn-accent——按钮渲染成无主色的裸样式。",
        "how": "封装 <Button> 组件（5 变体 × 4 尺寸 + loading 态），"
               "全部改为组件调用。设计令牌里没定义的类要么补定义要么换掉。",
        "pitfall": "Tailwind 4 的 @apply 不能引用自定义组件类，"
                   "utility 要逐条展开。",
        "tags": "UI,前端,一致性,踩坑",
    },
    {
        "title": "Python 脚本写文件会把 LF 变成 CRLF",
        "kind": "pitfall",
        "why": "在 Windows 上用 `pathlib.write_text()` 写文件，"
               "会把原本的 LF 换行全部改成 CRLF，导致 git diff 显示整个文件被重写"
               "（实测一个文件显示 994 行变更，实际只改了 40 行）。",
        "how": "写完检查 `path.read_bytes().count(13)`，非 0 就replace 成 LF。"
               "或直接用 Edit 工具改文件。仓库加 .gitattributes 统一 eol=lf。",
        "pitfall": "批量改文件后用 `git diff -w --numstat` 对比，"
                   "能立刻发现真实改动量与显示不符。",
        "tags": "git,Python,踩坑,协作",
    },
]

# 项目踩坑（来自本项目实测，归入错误本）
MISTAKES = [
    {
        "scene": "AI 写作",
        "symptom": "标题生成、去AI 味、智能标签三个功能全部 401，界面无任何提示",
        "cause": "ai_writer._api_key() 拿 DEEPSEEK_API_KEY（模型密钥）当内核接入凭据，"
                 "而 hbridge v2.1 要求的是另一个静态 token。两者都不是一回事。",
        "fix": "改用 settings.kernel_auth_headers，自动从内核 cordis.patch.yml 读；"
               "401 时返回可执行的排查提示而不是裸状态码。",
    },
    {
        "scene": "素材工坊",
        "symptom": "生成的图与稿件内容毫无关系，图文不符",
        "cause": "build_cover_prompt 只接受 title 参数，正文/关键词/标签全丢。",
        "fix": "引入 DraftContext 传全上下文，提示词注入核心词与正文抽取的信息点。",
    },
    {
        "scene": "接口调试",
        "symptom": "POST /api/assets/generate 返回 422，query 参数明明传了",
        "cause": "req: GenerateRequest | None 声明导致空 body 被拿去实例化模型并因缺字段失败，"
                 "走不到 query 兜底。",
        "fix": "改用 Request 自己读 body 再与 query 合并。",
    },
    {
        "scene": "错误排查",
        "symptom": "界面显示「内核未返回文本」，看不出真实原因",
        "cause": "401 被包在 SSE 流里，前端只弹了 toast，对话框里留一句无信息量占位文案。",
        "fix": "后端返回 hint 字段；前端在气泡内直接展示 message/detail/hint。",
    },
    {
        "scene": "知识库",
        "symptom": "检索「标题埋词位置」召回的全是 Android 状态栏那类工程经验",
        "cause": "Obsidian 库里没有小红书平台的内容规格，"
             "本领域最该查的东西缺失，RAG 形同虚设。",
        "fix": "把平台规格与项目踩坑主动 seed 进知识库（seed_knowledge.py），"
               "不能只靠 OB 导入。",
    },
]


def main() -> int:
    db = SessionLocal()
    try:
        created = updated = 0
        for item in SPEC_ITEMS:
            r = kb.add_knowledge(db=db, **item)
            if r.get("action") == "created":
                created += 1
            else:
                updated += 1
        print(f"知识条目：新增 {created}，更新 {updated}")

        m = 0
        for item in MISTAKES:
            # 错误本允许重复（是事件），但同一症状不重复记
            r = kb.add_mistake(db=db, **item)
            if r.get("ok"):
                m += 1
        print(f"错误本：新增 {m} 条")

        stats = kb.knowledge_stats(db)
        print(f"知识库现有 {stats['total']} 条，错误本 {stats['mistakes']} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
