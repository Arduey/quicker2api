#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地调试入口。

宝塔部署请用 wsgi.py（gunicorn 填 `wsgi:app`）：
    项目里同时有 app.py 和 app/ 包，`app:app` 会被解析成包对象而报错。

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
