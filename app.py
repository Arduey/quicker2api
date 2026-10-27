#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地调试入口。

宝塔部署请用 wsgi.py（应用名称填 `wsgi:app`）。

本地调试：
    python app.py        # 默认 0.0.0.0:8000
"""
from __future__ import annotations

import os

from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
