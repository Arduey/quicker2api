#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WSGI 入口 —— 宝塔「Python 项目」用 gunicorn/uwsgi 启动时指向这个文件。

为什么不用 app.py：
    项目里同时存在 app.py（入口）和 app/（包），而 Python 导入 "app" 时
    会优先解析成同名包，于是 gunicorn 的 `app:app` 拿到的是包对象，
    报 AttributeError: module 'app' has no attribute 'app'。
    改成 `wsgi:app` 就不会有歧义。

宝塔里对应填写：
    启动方式：gunicorn
    应用名称：wsgi:app
    通讯协议：wsgi
"""
from __future__ import annotations

from app import create_app

app = create_app()

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
