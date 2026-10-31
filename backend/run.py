# -*- coding: utf-8 -*-
"""run.py — uvicorn 启动入口（端口 8000）"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
