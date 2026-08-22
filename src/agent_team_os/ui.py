"""提供生产控制台，并在前端产物缺失时显示中文指引页。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def install_preview_ui(app: FastAPI, console_dist: Path | None = None) -> None:
    if console_dist is not None and (console_dist / "index.html").is_file():
        assets = console_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="console-assets")

        @app.get("/", response_class=FileResponse, include_in_schema=False)
        def console_home() -> Path:
            return console_dist / "index.html"

        @app.get("/{frontend_path:path}", response_class=FileResponse, include_in_schema=False)
        def console_route(frontend_path: str) -> Path:
            if frontend_path == "v1" or frontend_path.startswith("v1/"):
                raise HTTPException(status_code=404, detail="API route not found")
            return console_dist / "index.html"

        return

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def console_missing() -> str:
        return _CHINESE_FALLBACK


_CHINESE_FALLBACK = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Agent-Team-OS · 控制平面</title>
  <style>
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:#07111b;color:#dcebf5;font-family:system-ui,sans-serif}
    main{max-width:680px;border:1px solid #29465a;padding:36px;background:#0c1a26}
    p{color:#91a9b8;line-height:1.8}code{color:#4ee4ce}a{color:#38a3ff}
  </style>
</head>
<body><main>
  <small>控制平面 · V0.3</small>
  <h1>前端控制台尚未构建</h1>
  <p>请在 <code>console</code> 目录安装锁定依赖并执行生产构建，然后重新启动系统。</p>
  <p><a href="/docs">打开接口文档</a></p>
</main></body></html>"""
