#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WSGI 入口 —— 宝塔「Python 项目」用 gunicorn/uwsgi 启动时指向这个文件。

宝塔里对应填写：
    启动方式：gunicorn
    应用名称：wsgi:app
    通讯协议：wsgi

说明：项目里同时存在 app.py 和 app/ 包，直接写 `app:app` 时 Python 会优先
把 `app` 解析成同名包。现在 app/__init__.py 末尾也挂了 `app` 实例，
所以 `app:app` 和 `wsgi:app` 两种写法都能启动，这里只是更明确的那一种。
"""
from __future__ import annotations

import os

from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
