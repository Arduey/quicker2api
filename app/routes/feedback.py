# -*- coding: utf-8 -*-
"""「我有话说」：需求与问题反馈。

用户可以开话题，其他人在下面像评论区一样回复，任何人都能看、
能评论别人开的话题。

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
from ..util import now_ts, text
from .portal import guard

bp = Blueprint("feedback", __name__)

TITLE_MAX = 120
BODY_MAX = 5000


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

    rows = db.query(
        "SELECT t.id, t.user_id, t.user_nickname, t.title, t.body, t.reply_count, "
        "t.last_reply_at, t.created_at, t.updated_at, "
        "COALESCE(r.read_at, 0) AS read_at, "
        "COALESCE(r.read_reply_count, 0) AS read_reply_count "
        "FROM topics t "
        "LEFT JOIN topic_reads r ON r.topic_id = t.id AND r.user_id = %s "
        "ORDER BY t.updated_at DESC, t.id DESC",
        (uid,),
    )

    cared = _cared_topic_ids(db, viewer)
    is_admin = bool(viewer.get("is_admin"))
    unread = 0
    for row in rows:
        tid = int(row["id"])
        # 只有「关心的话题」才谈得上未读，否则全是别人的话题会一片红点
        row["cared"] = is_admin or tid in cared
        # 与 unread_count 同一口径：更新时间或回复条数任一超前即为未读，
        # 单看时间戳会漏掉「同一秒内发生的回复」。
        row["unread"] = bool(row["cared"]) and (
            int(row["updated_at"] or 0) > int(row["read_at"] or 0)
            or int(row["reply_count"] or 0) > int(row["read_reply_count"] or 0))
        row["is_mine"] = int(row["user_id"]) == uid
        if row["unread"]:
            unread += 1

    return render_template("portal/feedback.html", viewer=viewer, user=viewer,
                           is_admin=is_admin, topics=rows, unread=unread)


# ───────────────────────── 发新话题 ─────────────────────────
@bp.route("/u/feedback/new", methods=["GET", "POST"])
@guard
def topic_new():
    viewer = security.current_viewer()
    error = None
    title = ""
    body = ""

    if request.method == "POST":
        title = text(request.form.get("title"), TITLE_MAX)
        body = text(request.form.get("body"), BODY_MAX)
        if len(title) < 2:
            error = "标题至少 2 个字"
        elif not body:
            error = "说说你的想法吧，正文不能为空"
        else:
            db = get_db()
            uid = int(viewer["id"])
            nick = viewer.get("nickname") or viewer.get("email") or ""
            ts = now_ts()
            topic_id = db.insert(
                "INSERT INTO topics (user_id, user_nickname, title, body, reply_count, "
                "last_reply_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, 0, 0, %s, %s)",
                (uid, nick, title, body, ts, ts))
            # 自己的话题先标成已读，否则刚发完就提示「有未读」
            _mark_read(db, uid, int(topic_id))
            flash("已发布，感谢你的反馈", "success")
            return redirect(url_for("feedback.topic_detail", topic_id=topic_id))

    return render_template("portal/feedback_new.html", viewer=viewer, user=viewer,
                           is_admin=security.viewer_is_admin(viewer),
                           error=error, title=title, body=body,
                           title_max=TITLE_MAX, body_max=BODY_MAX)


# ───────────────────────── 详情（评论区） ─────────────────────────
@bp.get("/u/feedback/<int:topic_id>")
@guard
def topic_detail(topic_id: int):
    viewer = security.current_viewer()
    db = get_db()
    uid = int(viewer["id"])

    topic = db.query_one(
        "SELECT t.*, COALESCE(r.read_at, 0) AS read_at FROM topics t "
        "LEFT JOIN topic_reads r ON r.topic_id = t.id AND r.user_id = %s "
        "WHERE t.id = %s", (uid, topic_id))
    if topic is None:
        flash("话题不存在或已被删除", "error")
        return redirect(url_for("feedback.topic_list"))

    replies = db.query(
        "SELECT id, user_id, user_nickname, body, created_at FROM topic_replies "
        "WHERE topic_id = %s ORDER BY id ASC", (topic_id,))
    for reply in replies:
        reply["is_mine"] = int(reply["user_id"]) == uid

    # 打开即已读
    _mark_read(db, uid, topic_id)

    return render_template("portal/feedback_detail.html", viewer=viewer, user=viewer,
                           is_admin=security.viewer_is_admin(viewer),
                           topic=topic, replies=replies,
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
