"""Railway HTTP：健康检查 + 微信账号绑定页 /bind。"""

from __future__ import annotations

import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable


class BindStatus:
    def __init__(self):
        self.state = "idle"  # idle | waiting | done | error
        self.qr_url = ""
        self.message = ""
        self.lock = threading.Lock()


bind_status = BindStatus()
_on_bind: Callable[[], None] | None = None


def start(port: int | None = None, on_bind: Callable[[], None] | None = None) -> HTTPServer:
    global _on_bind
    _on_bind = on_bind
    port = port or int(os.environ.get("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/health"):
                self._text(200, "ok")
            elif path == "/bind/status":
                with bind_status.lock:
                    payload = {
                        "state": bind_status.state,
                        "qr_url": bind_status.qr_url,
                        "message": bind_status.message,
                    }
                self._json(200, payload)
            elif path == "/bind":
                if _on_bind and bind_status.state in ("idle", "done", "error"):
                    with bind_status.lock:
                        bind_status.state = "waiting"
                        bind_status.qr_url = ""
                        bind_status.message = "正在生成二维码…"
                    _on_bind()
                self._bind_page()
            else:
                self.send_response(404)
                self.end_headers()

        def _text(self, code: int, body: str):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _bind_page(self):
            with bind_status.lock:
                state = bind_status.state
                qr_url = bind_status.qr_url
                message = bind_status.message
            link = (
                f'<p><a href="{html.escape(qr_url)}" target="_blank" rel="noopener">'
                f"点此用微信打开扫码链接</a></p>"
                if qr_url
                else "<p>二维码生成中，请刷新本页或查看 Deploy Logs…</p>"
            )
            body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>绑定微信 EVA</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem">
<h1>绑定你的微信</h1>
<p>微信 iLink Bot 无法分享名片，<strong>每位使用者需用自己的微信扫码绑定一次</strong>。</p>
<ol>
<li>在 iOS 微信 8.0.70+ 启用 ClawBot 插件（设置 → 插件）</li>
<li>点击下方链接，在手机微信中确认登录</li>
<li>绑定成功后，在该微信里给 ClawBot 发消息即可使用 EVA</li>
</ol>
<p>状态：<strong>{html.escape(state)}</strong> {html.escape(message)}</p>
{link}
<p style="color:#666;font-size:0.9rem">本页每 5 秒自动刷新。也可在 Railway Logs 中查看二维码。</p>
</body></html>"""
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, _format, *_args):
            pass

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"> HTTP: http://0.0.0.0:{port}/health  |  绑定: /bind", flush=True)
    return server


def set_bind_qr(url: str) -> None:
    with bind_status.lock:
        bind_status.qr_url = url
        bind_status.message = "请用微信扫码并在手机上确认"


def set_bind_done(message: str) -> None:
    with bind_status.lock:
        bind_status.state = "done"
        bind_status.message = message


def set_bind_error(message: str) -> None:
    with bind_status.lock:
        bind_status.state = "error"
        bind_status.message = message
