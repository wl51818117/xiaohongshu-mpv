"""选题状态同步 —— 全系统唯一入口。

★ 为什么要收敛成单一函数（2026-10 修复）：
  之前有 **6 条路径**能改`draft.body` 或 `topic.status`，
  其中只有 3 条遵守「校验通过⇄ 状态 done」的不变量：

    | 路径 | 原来是否同步状态 |
    |---|---|
    | POST /api/drafts (create) | ✅ |
    | PATCH /api/drafts (update) | ✅ |
    | POST /api/drafts/generate-copy | ✅ |
    | POST /api/ai/polish | ❌ 只改 body 不管状态 |
    | POST /api/ai/chat | ❌ 同上 |
    | PATCH /api/agent/data/topics | ❌ 任意枚举值直通 |

  结果产生了实测脏数据：topic 13状态 `done`，
  但其 draft 4 `passed=False`。成因是 polish 把已通过的正文改劣化了，
  而状态不回退。流程图、看板、发布队列全依赖这个状态，
  一旦失真这三个视图就全是错的。

现在：所有写 `draft.body` / `draft.validation` 的路径
都必须调用 `sync_topic_status(db, draft)`，不变量只有这一处实现。
"""

from __future__ import annotations

from app.db.models import Draft, TopicStatus


def sync_topic_status(db, draft: Draft | None) -> str:
    """按稿件当前校验结果同步选题状态。返回变更后的状态。

    规则（唯一实现，不在别处重复）：
      校验通过 → done
      校验不通过 → 退回 claimed（不是 pooled，因为它已经被动过）
      无关联选题 → 不动
    """
    if draft is None or draft.topic_id is None:
        return "no-topic"

    topic = getattr(draft, "topic", None)
    if topic is None:
        # 关联的选题已被删除，不做处理
        return "no-topic"

    passed = bool((draft.validation or {}).get("passed"))
    before = topic.status

    if passed:
        topic.status = TopicStatus.DONE
    else:
        # 只有「本来是 done」才需要退回，避免把还没动过的 pooled 误改
        if topic.status == TopicStatus.DONE:
            topic.status = TopicStatus.CLAIMED

    return str(topic.status or before or "")
