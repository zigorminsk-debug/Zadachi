"""LAN protocol between the Windows PC and the Android phone (stdlib only).

Ports
-----
UDP  39271  discovery (broadcast "ZADACHI_DISCOVER_PC" -> PC replies JSON)
TCP  39272  PC HTTP API:   POST /api/call , POST /api/ping , GET /api/health
TCP  39273  Phone HTTP API: POST /dial , POST /ping

Every HTTP request must carry header  X-Zadachi-Token: <pairing token>.
Phone numbers are normalised to digits with a leading '+' when possible.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.request
import urllib.error

UDP_PORT = 39271
PC_PORT = 39272
PHONE_PORT = 39273
DISCOVERY_MESSAGE = b"ZADACHI_DISCOVER_PC"
TOKEN_HEADER = "X-Zadachi-Token"

# Belarus / international friendly: +375 29 123-45-67, 8 029 1234567,
# +7 916 123-45-67, plain 10-15 digit runs, short service numbers (3-6 digits
# only when clearly separated). We require >=7 meaningful digits for clipboard
# detection to avoid matching dates / prices.
_PHONE_CANDIDATE = re.compile(r"\+?\d[\d\s\-()]{5,}\d")


def normalize_phone(raw: str) -> str:
    """'+375 (29) 123-45-67' -> '+375291234567'; '80291234567' -> '+375291234567'."""
    if not raw:
        return ""
    s = str(raw).strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if plus:
        return "+" + digits
    # BY: 80XXXXXXXXX (11 digits) -> +375XXXXXXXXX ; RU: 8XXXXXXXXXX -> +7XXXXXXXXXX
    if len(digits) == 11 and digits.startswith("80"):
        return "+375" + digits[2:]
    if len(digits) == 11 and digits.startswith("8"):
        return "+7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        return "+7" + digits
    return digits


def is_phone_like(text: str) -> bool:
    if not text:
        return False
    s = str(text).strip()
    if re.fullmatch(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", s):  # a date, not a phone
        return False
    digits = re.sub(r"\D", "", s)
    return 7 <= len(digits) <= 15


def extract_phones(text: str):
    """Find phone numbers inside arbitrary text (e.g. clipboard content)."""
    found, seen = [], set()
    for m in _PHONE_CANDIDATE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(0))
        if 7 <= len(digits) <= 15:
            n = normalize_phone(m.group(0))
            if n and n not in seen:
                seen.add(n)
                found.append(n)
    return found


def _post_json(url: str, payload: dict, token: str = "", timeout: float = 4.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header(TOKEN_HEADER, token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body else {}


def send_dial(phone_ip: str, number: str, token: str = "", timeout: float = 4.0):
    """Ask the phone to open its default dialer with `number`."""
    number = normalize_phone(number)
    return _post_json(f"http://{phone_ip}:{PHONE_PORT}/dial", {"number": number}, token, timeout)


def ping_phone(phone_ip: str, token: str = "", timeout: float = 3.0):
    return _post_json(f"http://{phone_ip}:{PHONE_PORT}/ping", {"from": "pc"}, token, timeout)


def parse_http_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
