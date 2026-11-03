# -*- coding: utf-8 -*-
"""「我有话说」：需求与问题反馈。

用户可以开话题，其他人在下面像评论区一样回复，任何人都能看、
能评论别人开的话题。

分类、筛选、搜索、点赞、管理员操作：
  - 分类在创建时必选，列表上按分类筛选
  - 筛选维度：全部 / 我发布的 / 我参与的（我开的话题 + 我回复过的）
  - 关键词匹配标题与正文（精准子串，与接口列表同一套逻辑）
  - 点赞一人一次，可取消；计数冗余在 topics.like_count
  - 管理员可置顶 / 标记已完成 / 删除
  - 管理员发的帖子和回复都带「官方」标

未读提醒的口径（三处保持一致，改的时候要一起改）：
  - 「关心的话题」= 自己开的 / 自己回复过的；管理员关心全部
  - 「未读」= 话题的 updated_at 比我的 read_at 新
  - 自己发帖或回帖时顺手刷新自己的 read_at，所以自己的操作
    不会给自己产生未读
  - 打开话题详情页即标记已读
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import security
from ..db import get_db
from ..util import highlight, now_ts, text
from .portal import guard

bp = Blueprint("feedback", __name__)

TITLE_MAX = 120
BODY_MAX = 5000

# 分类：创建时必选。顺序即列表上筛选按钮的顺序，
# 从「最需要被处理」排到「最随意」。
TOPIC_CATEGORIES = [
    ("requirement", "需求收集"),
    ("bug", "问题反馈"),
    ("improve", "优化建议"),
    ("usage", "使用问题"),
    ("chat", "随便聊聊"),
]
CATEGORY_LABELS = dict(TOPIC_CATEGORIES)
CATEGORY_KEYS = [k for k, _ in TOPIC_CATEGORIES]

# 列表筛选维度
SCOPES = [
    ("all", "全部"),
    ("mine", "我发布的"),
    ("joined", "我参与的"),
]


def _normalize_category(value) -> str:
    """空值或非法值一律当成空串，由调用方决定是否报错。"""
    key = (value or "").strip()
    return key if key in CATEGORY_LABELS else ""


def _category_label(key) -> str:
    return CATEGORY_LABELS.get(key or "", "未分类")


# ─────────────────── 未读计算（列表页与侧栏共用） ───────────────────
def _cared_topic_ids(db, viewer) -> set[int]:
    """我「关心」的话题 id：我开的 + 我回复过的。

    管理员不需要这个集合——他关心全部，判断时直接放行。
    """
    uid = int(viewer["id"])
    mine = db.query("SELECT id FROM topics WHERE user_id = %s", (uid,))
    replied = db.query("SELECT DISTINCT topic_id FROM topic_replies WHERE user_id = %s",
                       (uid,))
    ids = {int(r["id"]) for r in mine}
    ids |= {int(r["topic_id"]) for r in replied}
    return ids


def unread_count(db, viewer) -> int:
    """侧边栏徽章用：有多少个话题有未读。

    管理员看全部话题，普通用户只看自己关心的那些。

    判定同时看两个信号，任一满足即算未读：
      - topics.updated_at > read_at      有更新的动作
      - topics.reply_count > read_reply_count   有没读过的回复
    第二个是必要的：时间戳只到秒，若回复与阅读落在同一秒，
    单看时间戳会漏掉这次提醒。
    """
    if not viewer:
        return 0
    uid = int(viewer["id"])
    is_admin = bool(viewer.get("is_admin"))
    sql = (
        "SELECT COUNT(*) AS c FROM topics t "
        "LEFT JOIN topic_reads r ON r.topic_id = t.id AND r.user_id = %s "
        "WHERE (t.updated_at > COALESCE(r.read_at, 0) "
        "       OR t.reply_count > COALESCE(r.read_reply_count, 0)) "
    )
    args: list = [uid]
    if not is_admin:
        sql += ("AND (t.user_id = %s OR EXISTS ("
                "SELECT 1 FROM topic_replies tr "
                "WHERE tr.topic_id = t.id AND tr.user_id = %s)) ")
        args.extend([uid, uid])
    return int(db.scalar(sql, tuple(args)) or 0)


def _mark_read(db, user_id: int, topic_id: int, reply_count: int | None = None) -> None:
    """把话题标记为已读到此刻。

    同时记下「已读到第几条回复」。read_at 用 GREATEST 保证不会往回拨
    （两次请求乱序到达时也不会退化成未读）；reply_count 直接取当前值，
    因为它只会单调增加。

    reply_count 为 None 时现查一次，避免调用方都要自己数。
    """
    if reply_count is None:
        reply_count = int(db.scalar(
            "SELECT reply_count FROM topics WHERE id = %s", (topic_id,)) or 0)
    db.execute(
        "INSERT INTO topic_reads (user_id, topic_id, read_at, read_reply_count) "
        "VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE read_at = GREATEST(read_at, VALUES(read_at)), "
        "read_reply_count = GREATEST(read_reply_count, VALUES(read_reply_count))",
        (user_id, topic_id, now_ts(), int(reply_count)))


# ───────────────────────── 列表 ─────────────────────────
@bp.get("/u/feedback")
@guard
def topic_list():
    viewer = security.current_viewer()
    db = get_db()
    uid = int(viewer["id"])
    is_admin = bool(viewer.get("is_admin"))

    keyword = text(request.args.get("q"), 60)
    category = _normalize_category(request.args.get("category"))
    scope = request.args.get("scope") or "all"
    if scope not in dict(SCOPES):
        scope = "all"

    where, args = [], []
    if category:
        where.append("t.category = %s")
        args.append(category)
    if keyword:
        # % 和 _ 在 LIKE 里是通配符，搜这两个字面字符时要转义
        like = "%" + keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(t.title LIKE %s OR t.body LIKE %s)")
        args.extend([like, like])
    if scope == "mine":
        where.append("t.user_id = %s")
        args.append(uid)
    elif scope == "joined":
        where.append("(t.user_id = %s OR EXISTS ("
                     "SELECT 1 FROM topic_replies tr2 "
                     "WHERE tr2.topic_id = t.id AND tr2.user_id = %s))")
        args.extend([uid, uid])

    sql = (
        "SELECT t.id, t.user_id, t.user_nickname, t.title, t.body, t.category, "
        "t.is_pinned, t.state, t.view_count, t.like_count, "
        "t.reply_count, t.last_reply_at, t.created_at, t.updated_at, "
        "COALESCE(r.read_at, 0) AS read_at, "
        "COALESCE(r.read_reply_count, 0) AS read_reply_count, "
        "COALESCE(u.is_admin, 0) AS author_is_admin "
        "FROM topics t "
        "LEFT JOIN topic_reads r ON r.topic_id = t.id AND r.user_id = %s "
        "LEFT JOIN users u ON u.id = t.user_id "
    )
    full_args = [uid] + args
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    # 置顶永远在最前，其余按最后活动时间倒序
    sql += "ORDER BY t.is_pinned DESC, t.updated_at DESC, t.id DESC"

    rows = db.query(sql, tuple(full_args))

    cared = _cared_topic_ids(db, viewer)
    unread = 0
    for row in rows:
        tid = int(row["id"])
        row["category_label"] = _category_label(row.get("category"))
        row["is_official"] = bool(int(row.get("author_is_admin") or 0))
        # 只有「关心的话题」才谈得上未读，否则全是别人的话题会一片红点
        row["cared"] = is_admin or tid in cared
        # 与 unread_count 同一口径：更新时间或回复条数任一超前即为未读，
        # 单看时间戳会漏掉「同一秒内发生的回复」。
        row["unread"] = bool(row["cared"]) and (
            int(row["updated_at"] or 0) > int(row["read_at"] or 0)
            or int(row["reply_count"] or 0) > int(row["read_reply_count"] or 0))
        row["is_mine"] = int(row["user_id"]) == uid
        # 列表上标题与正文都要能标红命中内容
        row["title_html"] = highlight(row["title"], keyword, max_len=200)
        row["body_html"] = highlight(row["body"], keyword, max_len=120)
        # 最近活动：有人回复就看回复时间，否则看发帖时间
        row["last_active"] = int(row["last_reply_at"] or 0) or int(row["updated_at"] or 0)
        if row["unread"]:
            unread += 1

    # 分类计数：只统计当前筛选（关键词 + 维度）下的可见话题，
    # 这样「点分类后数字跟着变」符合直觉。置顶不影响计数。
    counts = {key: 0 for key in CATEGORY_KEYS}
    counts[""] = 0
    for row in rows:
        key = row.get("category") or ""
        counts[key] = counts.get(key, 0) + 1

    return render_template(
        "portal/feedback.html", viewer=viewer, user=viewer, is_admin=is_admin,
        topics=rows, unread=unread, categories=TOPIC_CATEGORIES,
        counts=counts, keyword=keyword, active_category=category,
        scope=scope, scopes=SCOPES, total=len(rows))


# ───────────────────────── 发新话题 ─────────────────────────
@bp.route("/u/feedback/new", methods=["GET", "POST"])
@guard
def topic_new():
    viewer = security.current_viewer()
    error = None
    title = ""
    body = ""
    category = ""

    if request.method == "POST":
        title = text(request.form.get("title"), TITLE_MAX)
        body = text(request.form.get("body"), BODY_MAX)
        category = _normalize_category(request.form.get("category"))
        if not category:
            error = "请选择一个分类"
        elif len(title) < 2:
            error = "标题至少 2 个字"
        elif not body:
            error = "说说你的想法吧，正文不能为空"
        else:
            db = get_db()
            uid = int(viewer["id"])
            nick = viewer.get("nickname") or viewer.get("email") or ""
            ts = now_ts()
            topic_id = db.insert(
                "INSERT INTO topics (user_id, user_nickname, title, body, category, "
                "reply_count, last_reply_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s)",
                (uid, nick, title, body, category, ts, ts))
            # 自己的话题先标成已读，否则刚发完就提示「有未读」
            _mark_read(db, uid, int(topic_id))
            flash("已发布，感谢你的反馈", "success")
            return redirect(url_for("feedback.topic_detail", topic_id=topic_id))

    return render_template("portal/feedback_new.html", viewer=viewer, user=viewer,
                           is_admin=security.viewer_is_admin(viewer),
                           error=error, title=title, body=body, category=category,
                           categories=TOPIC_CATEGORIES,
                           title_max=TITLE_MAX, body_max=BODY_MAX)


# ───────────────────────── 详情（评论区） ─────────────────────────
@bp.get("/u/feedback/<int:topic_id>")
@guard
def topic_detail(topic_id: int):
    viewer = security.current_viewer()
    db = get_db()
    uid = int(viewer["id"])

    topic = db.query_one(
        "SELECT t.*, COALESCE(r.read_at, 0) AS read_at, "
        "COALESCE(u.is_admin, 0) AS author_is_admin "
        "FROM topics t "
        "LEFT JOIN topic_reads r ON r.topic_id = t.id AND r.user_id = %s "
        "LEFT JOIN users u ON u.id = t.user_id "
        "WHERE t.id = %s", (uid, topic_id))
    if topic is None:
        flash("话题不存在或已被删除", "error")
        return redirect(url_for("feedback.topic_list"))

    topic["category_label"] = _category_label(topic.get("category"))
    topic["is_official"] = bool(int(topic.get("author_is_admin") or 0))

    replies = db.query(
        "SELECT tr.id, tr.user_id, tr.user_nickname, tr.body, tr.created_at, "
        "COALESCE(u.is_admin, 0) AS author_is_admin "
        "FROM topic_replies tr "
        "LEFT JOIN users u ON u.id = tr.user_id "
        "WHERE tr.topic_id = %s ORDER BY tr.id ASC", (topic_id,))
    for reply in replies:
        reply["is_mine"] = int(reply["user_id"]) == uid
        reply["is_official"] = bool(int(reply.get("author_is_admin") or 0))

    # 打开即已读，并记一次查看
    _mark_read(db, uid, topic_id)
    db.execute("UPDATE topics SET view_count = view_count + 1 WHERE id = %s", (topic_id,))
    topic["view_count"] = int(topic.get("view_count") or 0) + 1

    liked = bool(db.scalar(
        "SELECT 1 FROM topic_likes WHERE topic_id = %s AND user_id = %s",
        (topic_id, uid)))

    return render_template("portal/feedback_detail.html", viewer=viewer, user=viewer,
                           is_admin=security.viewer_is_admin(viewer),
                           topic=topic, replies=replies, liked=liked,
                           is_mine=int(topic["user_id"]) == uid,
                           body_max=BODY_MAX)


@bp.post("/u/feedback/<int:topic_id>/reply")
@guard
def topic_reply(topic_id: int):
    viewer = security.current_viewer()
    db = get_db()
    uid = int(viewer["id"])

    topic = db.query_one("SELECT id FROM topics WHERE id = %s", (topic_id,))
    if topic is None:
        flash("话题不存在或已被删除", "error")
        return redirect(url_for("feedback.topic_list"))

    body = text(request.form.get("body"), BODY_MAX)
    if not body:
        flash("回复内容不能为空", "error")
        return redirect(url_for("feedback.topic_detail", topic_id=topic_id))

    nick = viewer.get("nickname") or viewer.get("email") or ""
    ts = now_ts()
    db.insert(
        "INSERT INTO topic_replies (topic_id, user_id, user_nickname, body, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (topic_id, uid, nick, body, ts))
    db.execute(
        "UPDATE topics SET reply_count = reply_count + 1, last_reply_at = %s, "
        "updated_at = %s WHERE id = %s", (ts, ts, topic_id))
    # 自己刚回的，对自己而言已经读过了
    _mark_read(db, uid, topic_id)

    return redirect(url_for("feedback.topic_detail", topic_id=topic_id) + "#r-bottom")


# ───────────────────────── 点赞 ─────────────────────────
@bp.post("/u/feedback/<int:topic_id>/like")
@guard
def topic_like(topic_id: int):
    """切换点赞。已赞则取消，未赞则点上。

    计数用 like_count 冗余存，且用 GREATEST 兜底不低于 0，
    避免并发下明细与计数出现负数。
    """
    viewer = security.current_viewer()
    db = get_db()
    uid = int(viewer["id"])

    row = db.query_one("SELECT id FROM topics WHERE id = %s", (topic_id,))
    if row is None:
        flash("话题不存在或已被删除", "error")
        return redirect(url_for("feedback.topic_list"))

    existing = db.scalar(
        "SELECT 1 FROM topic_likes WHERE topic_id = %s AND user_id = %s",
        (topic_id, uid))
    if existing:
        db.execute("DELETE FROM topic_likes WHERE topic_id = %s AND user_id = %s",
                   (topic_id, uid))
        db.execute("UPDATE topics SET like_count = GREATEST(like_count - 1, 0) "
                   "WHERE id = %s", (topic_id,))
    else:
        db.insert("INSERT INTO topic_likes (user_id, topic_id, created_at) "
                  "VALUES (%s, %s, %s)", (uid, topic_id, now_ts()))
        db.execute("UPDATE topics SET like_count = like_count + 1 WHERE id = %s",
                   (topic_id,))

    return redirect(url_for("feedback.topic_detail", topic_id=topic_id) + "#topic-body")


# ───────────────────────── 管理员操作 ─────────────────────────
def _require_admin_action(topic_id: int):
    """管理员操作的公共前置：校验身份、确认话题存在。

    返回 (topic, None) 或 (None, 重定向响应)。
    """
    viewer = security.current_viewer()
    if not security.viewer_is_admin(viewer):
        flash("只有管理员可以执行这个操作", "error")
        return None, redirect(url_for("feedback.topic_list"))
    db = get_db()
    topic = db.query_one("SELECT id, is_pinned, state FROM topics WHERE id = %s",
                         (topic_id,))
    if topic is None:
        flash("话题不存在或已被删除", "error")
        return None, redirect(url_for("feedback.topic_list"))
    return topic, None


@bp.post("/u/feedback/<int:topic_id>/pin")
@guard
def topic_pin(topic_id: int):
    """置顶 / 取消置顶。"""
    topic, resp = _require_admin_action(topic_id)
    if resp is not None:
        return resp
    db = get_db()
    target = 0 if int(topic.get("is_pinned") or 0) else 1
    db.execute("UPDATE topics SET is_pinned = %s WHERE id = %s", (target, topic_id))
    flash("已置顶" if target else "已取消置顶", "success")
    return redirect(url_for("feedback.topic_detail", topic_id=topic_id))


@bp.post("/u/feedback/<int:topic_id>/state")
@guard
def topic_state(topic_id: int):
    """标记已完成 / 改回进行中。"""
    topic, resp = _require_admin_action(topic_id)
    if resp is not None:
        return resp
    db = get_db()
    target = 0 if int(topic.get("state") or 0) else 1
    db.execute("UPDATE topics SET state = %s WHERE id = %s", (target, topic_id))
    flash("已标记为完成" if target else "已改回进行中", "success")
    return redirect(url_for("feedback.topic_detail", topic_id=topic_id))


@bp.post("/u/feedback/<int:topic_id>/delete")
@guard
def topic_delete(topic_id: int):
    """删除话题及其回复、点赞、已读记录。

    只有管理员能删。作者本人想删可以找管理员，
    避免话题被删后其他人的回复跟着消失、讨论失去上下文。
    """
    topic, resp = _require_admin_action(topic_id)
    if resp is not None:
        return resp
    db = get_db()
    db.execute("DELETE FROM topic_replies WHERE topic_id = %s", (topic_id,))
    db.execute("DELETE FROM topic_likes WHERE topic_id = %s", (topic_id,))
    db.execute("DELETE FROM topic_reads WHERE topic_id = %s", (topic_id,))
    db.execute("DELETE FROM topics WHERE id = %s", (topic_id,))
    flash("话题已删除", "success")
    return redirect(url_for("feedback.topic_list"))
