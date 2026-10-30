# -*- coding: utf-8 -*-
"""公开主页：不登录也能浏览接口列表。

和用户中心 /u/apis 的区别：
  - 不需要登录，任何访客都能看
  - 卡片上的「调用」入口指向详情页，未登录点进去会引导登录
  - 收藏需要登录，未登录点收藏会跳登录页

搜索与分类筛选和 /u/apis 共用同一套逻辑（portal._visible_apis）。
"""
from __future__ import annotations

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)

from .. import config, quicker, security
from ..db import get_db
from ..util import api_categories, csrf_valid, json_err, json_ok, now_ts, text

bp = Blueprint("home", __name__)


def _public_apis(db, keyword: str = "", category: str = ""):
    """主页用的接口列表。

    直接复用 portal 的那份查询，保证两个页面的数据口径一致
    （维护中的可见、下线的隐藏、平均耗时只算成功调用）。
    """
    from .portal import _favorite_ids, _visible_apis

    viewer = security.current_viewer()
    rows = _visible_apis(db, keyword=keyword, category=category)
    return rows, _favorite_ids(db, viewer), viewer


@bp.get("/")
def index():
    """站点根路径 = 公开主页。

    已登录用户也能看，侧栏会正常显示用户中心入口；
    未登录则显示「登录 / 注册」按钮。
    """
    if not config.is_installed():
        return redirect("/install")

    db = get_db()
    keyword = text(request.args.get("q"), 64)
    category = text(request.args.get("category"), 32)
    apis, favorites, viewer = _public_apis(db, keyword=keyword, category=category)

    # 分类下拉框的两类来源合并：
    #   1. 平台设置里预设的分类（管理员维护的完整清单）
    #   2. 接口上实际用到、但预设里没有的分类（避免老数据在下拉框里消失）
    # 注意必须基于「全量接口」收集，不能用筛选后的 apis，
    # 否则一旦按分类筛选过，下拉框就只剩当前这一个选项。
    from .portal import _visible_apis

    all_rows = _visible_apis(db)
    used: list[str] = []
    for preset in api_categories():
        if preset not in used:
            used.append(preset)
    for row in all_rows:
        name = (row.get("category") or "").strip()
        if name and name not in used:
            used.append(name)

    return render_template(
        "home.html",
        apis=apis,
        viewer=viewer,
        user=viewer,
        is_admin=security.viewer_is_admin(viewer),
        favorites=favorites,
        keyword=keyword,
        category=category,
        categories=used,
        stats={
            "apis": len(apis),
            "calls": sum(int(a.get("call_count") or 0) for a in apis),
        },
    )


@bp.post("/favorites/<int:api_id>")
def toggle_favorite(api_id: int):
    """收藏 / 取消收藏。必须登录。"""
    viewer = security.current_viewer()
    if viewer is None:
        if request.headers.get("X-Requested-With") == "fetch":
            return json_err("请先登录后再收藏", "unauthorized", 401)
        return redirect(url_for("auth.login", next=request.full_path))

    if not csrf_valid():
        if request.headers.get("X-Requested-With") == "fetch":
            return json_err("页面已过期，请刷新后重试", "csrf", 403)
        flash("页面已过期，请刷新后重试", "error")
        return redirect(url_for("home.index"))

    db = get_db()
    row = db.query_one(
        "SELECT id FROM apis WHERE id = %s AND status IN (%s, %s)",
        (api_id, quicker.STATUS_ONLINE, quicker.STATUS_MAINTENANCE))
    if row is None:
        if request.headers.get("X-Requested-With") == "fetch":
            return json_err("接口不存在或已下线", "not_found", 404)
        flash("接口不存在或已下线", "error")
        return redirect(url_for("home.index"))

    existing = db.query_one(
        "SELECT id FROM api_favorites WHERE user_id = %s AND api_id = %s",
        (viewer["id"], api_id))
    if existing:
        db.execute("DELETE FROM api_favorites WHERE id = %s", (existing["id"],))
        favorited = False
    else:
        db.insert(
            "INSERT INTO api_favorites (user_id, api_id, created_at) VALUES (%s, %s, %s)",
            (viewer["id"], api_id, now_ts()))
        favorited = True

    if request.headers.get("X-Requested-With") == "fetch":
        return json_ok(favorited=favorited)
    # 普通表单提交：回到来源页，保留筛选条件
    back = request.form.get("back") or url_for("home.index")
    return redirect(back)
