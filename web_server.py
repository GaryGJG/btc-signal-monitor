"""Web 仪表盘：自动选取空闲端口，展示多空判定、指标面板与信号流。"""
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import settings as cfg

log = logging.getLogger("web")

_state_lock = threading.Lock()
_state = {"ready": False}

DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_bytes()


def update_state(new_state):
    global _state
    with _state_lock:
        _state = new_state


def find_free_port():
    """在配置区间内找空闲端口；bind 测试保证不抢占系统已占用端口。"""
    if cfg.WEB_PORT:
        candidates = [cfg.WEB_PORT]
    else:
        candidates = range(cfg.WEB_PORT_RANGE[0], cfg.WEB_PORT_RANGE[1])
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((cfg.WEB_BIND, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口区间 {cfg.WEB_PORT_RANGE} 内没有可用端口")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 用 endswith 判断，兼容反向代理未剥离的子路径前缀
        if self.path.split("?")[0].endswith("/api/status"):
            with _state_lock:
                body = json.dumps(_state, ensure_ascii=False).encode()
            self._respond(200, "application/json; charset=utf-8", body)
        elif self.path.endswith(".ico"):
            self._respond(404, "text/plain", b"not found")
        else:
            self._respond(200, "text/html; charset=utf-8", DASHBOARD_HTML)

    def _respond(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 交给主日志，避免刷屏


def start():
    port = find_free_port()
    server = ThreadingHTTPServer((cfg.WEB_BIND, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="web")
    thread.start()
    log.info("Web 仪表盘已启动: http://%s:%d/", cfg.WEB_BIND, port)
    return port
