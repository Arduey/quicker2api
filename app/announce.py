# -*- coding: utf-8 -*-
"""公告：管理员发布，全体登录用户可见。

设计取舍：
  - 一次只提示一条。横幅只显示最新那条未读公告，关闭后下一条才顶上来。
    多条未读一起铺开会把页面顶部占满，用户反而全部忽略。
  - 「已读」是每人一条记录，同一公告对同一人只记一次，之后不再打扰。
  - 下线（is_active = 0）立刻生效：用户可以看不见，但记录保留可再上线。
  - 只有两档重要程度：普通 / 重要。黑白体系里颜色扛不住语义，
    重要公告靠更重的左边线和加粗标题区分。
"""
from __future__ import annotations

from .util import now_ts

# 重要程度：0 普通，1 重要
ANNOUNCEMENT_LEVELS = [
    (0, "普通"),
    (1, "重要"),
]
LEVEL_LABELS = dict(ANNOUNCEMENT_LEVELS)


def normalize_level(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number in LEVEL_LABELS else 0


def level_label(value) -> str:
    return LEVEL_LABELS.get(normalize_level(value), "普通")


def unread_for(db, user_id: int):
    """取当前用户最该看的那条公告（最新一条未读）。

    没有未读时返回 None。管理员也走同一条规则——
    管理员发的公告对自己同样算未读，这样能立刻看到发布效果。
    """
    if not user_id:
        return None
    row = db.query_one(
        "SELECT a.id, a.title, a.body, a.level, a.created_at "
        "FROM announcements a "
        "WHERE a.is_active = 1 AND NOT EXISTS ("
        "  SELECT 1 FROM announcement_reads r "
        "  WHERE r.announcement_id = a.id AND r.user_id = %s) "
        "ORDER BY a.level DESC, a.created_at DESC, a.id DESC "
        "LIMIT 1",
        (int(user_id),))
    if row is None:
        return None
    row["level_label"] = level_label(row.get("level"))
    row["is_important"] = normalize_level(row.get("level")) == 1
    return row


def unread_total(db, user_id: int) -> int:
    """未读公告总条数，用于横幅上的「还有 N 条」。"""
    if not user_id:
        return 0
    return int(db.scalar(
        "SELECT COUNT(*) AS c FROM announcements a "
        "WHERE a.is_active = 1 AND NOT EXISTS ("
        "  SELECT 1 FROM announcement_reads r "
        "  WHERE r.announcement_id = a.id AND r.user_id = %s)",
        (int(user_id),)) or 0)


def mark_read(db, user_id: int, announcement_id: int) -> None:
    """标记已读。重复关闭同一条不会报错，也不会改时间。"""
    db.execute(
        "INSERT INTO announcement_reads (user_id, announcement_id, read_at) "
        "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE read_at = read_at",
        (int(user_id), int(announcement_id), now_ts()))


def read_count(db, announcement_id: int) -> int:
    """已读人数，后台列表上显示，方便判断公告有没有被看到。"""
    return int(db.scalar(
        "SELECT COUNT(*) AS c FROM announcement_reads WHERE announcement_id = %s",
        (int(announcement_id),)) or 0)
