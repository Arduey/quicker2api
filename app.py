#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API 分发平台 —— 入口文件。

宝塔「Python 项目」部署：
    项目启动文件 / WSGI 文件：app.py
    启动方式：gunicorn（命令由宝塔自动生成，指向 app:app）
    端口：自己填一个，例如 8000

本地调试：
    python app.py        # 默认 0.0.0.0:8000
"""
from __future__ import annotations

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
