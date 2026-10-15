"""PC-side LAN server: UDP discovery + HTTP API for call events (stdlib only).

Events are pushed into a queue; the Tk GUI polls the queue from its own
thread, so this module never touches Tk.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
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

            def log_message(self, *a):  # quiet
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                return not token or self.headers.get(protocol.TOKEN_HEADER) == token

            def do_GET(self):
                if self.path == "/api/health":
                    self._reply(200, {"ok": True, "app": "Zadachi-PC"})
                else:
                    self._reply(404, {"ok": False})

            def do_POST(self):
                if not self._authorized():
                    self._reply(403, {"ok": False, "error": "bad token"})
                    return
                payload = protocol.parse_http_body(self)
                client_ip = self.client_address[0]
                if self.path == "/api/ping":
                    events.put({"type": "ping", "ip": client_ip, "payload": payload})
                    self._reply(200, {"ok": True})
                elif self.path == "/api/call":
                    events.put({"type": "call", "ip": client_ip, "payload": payload})
                    self._reply(200, {"ok": True})
                else:
                    self._reply(404, {"ok": False})

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
