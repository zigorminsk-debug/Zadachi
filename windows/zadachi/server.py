"""PC-side LAN server: UDP discovery + HTTP API for call events (stdlib only).

Events are pushed into a queue; the Tk GUI polls the queue from its own
thread, so this module never touches Tk.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import protocol
from .protocol import UDP_PORT, PC_PORT


class CallServer:
    def __init__(self, token: str, events: "queue.Queue", pc_port: int = PC_PORT):
        self.token = token
        self.events = events
        self.pc_port = pc_port
        self._httpd = None
        self._udp = None
        self._threads = []

    # ---------- HTTP ----------
    def _make_handler(self):
        events, token = self.events, self.token

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "Zadachi-PC/1.1"
            sys_version = ""

            def log_message(self, *a):  # quiet
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Zadachi-App", "Zadachi-PC")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_error(self, code, message=None, explain=None):
                # Never answer with the stock HTML error page: the phone
                # uses the JSON marker to tell Zadachi apart from any
                # other HTTP server that may sit on the same IP/port.
                try:
                    self._reply(code, {"ok": False, "app": "Zadachi-PC",
                                       "error": message or "error"})
                except Exception:
                    pass

            def _authorized(self):
                return not token or self.headers.get(protocol.TOKEN_HEADER) == token

            def _clean_path(self):
                # Also accept absolute-form URLs some proxies/clients send.
                return urllib.parse.urlsplit(self.path).path

            def _dispatch(self, method):
                path = self._clean_path()
                if path == "/api/health":
                    if method == "GET":
                        self._reply(200, {"ok": True, "app": "Zadachi-PC"})
                    else:
                        self._reply(405, {"ok": False, "app": "Zadachi-PC",
                                          "error": "use GET"})
                    return
                if path not in ("/api/ping", "/api/call"):
                    self._reply(404, {"ok": False, "app": "Zadachi-PC"})
                    return
                if method not in ("POST", "PUT", "PATCH"):
                    self._reply(405, {"ok": False, "app": "Zadachi-PC",
                                      "error": "use POST"})
                    return
                if not self._authorized():
                    self._reply(403, {"ok": False, "app": "Zadachi-PC",
                                      "error": "bad token"})
                    return
                payload = protocol.parse_http_body(self)
                client_ip = self.client_address[0]
                if path == "/api/ping":
                    events.put({"type": "ping", "ip": client_ip, "payload": payload})
                    self._reply(200, {"ok": True, "app": "Zadachi-PC"})
                else:
                    events.put({"type": "call", "ip": client_ip, "payload": payload})
                    self._reply(200, {"ok": True, "app": "Zadachi-PC"})

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def do_PUT(self):
                self._dispatch("PUT")

            def do_PATCH(self):
                self._dispatch("PATCH")

            def do_DELETE(self):
                self._dispatch("DELETE")

        return Handler

    # ---------- UDP discovery ----------
    def _udp_loop(self):
        sock = self._udp
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except OSError:
                return
            if data.strip() == protocol.DISCOVERY_MESSAGE:
                reply = json.dumps({
                    "app": "Zadachi-PC", "port": self.pc_port,
                    "tokenRequired": bool(self.token),
                }).encode("utf-8")
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass
                self.events.put({"type": "discovery", "ip": addr[0], "payload": {}})

    def start(self):
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.pc_port), self._make_handler())
        self.pc_port = int(self._httpd.server_address[1])
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="zadachi-http")
        t.start()
        self._threads.append(t)
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._udp.bind(("0.0.0.0", UDP_PORT))
            t2 = threading.Thread(target=self._udp_loop, daemon=True, name="zadachi-udp")
            t2.start()
            self._threads.append(t2)
        except OSError:
            self._udp = None
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._udp:
            try:
                self._udp.close()
            except OSError:
                pass
            self._udp = None
