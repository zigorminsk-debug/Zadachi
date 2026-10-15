"""User settings in %APPDATA%/Zadachi/settings.json (stdlib only)."""
from __future__ import annotations

import json
import os
import secrets

DEFAULTS = {
    "token": "",            # pairing token, generated on first run
    "phone_ip": "",         # last known phone IP (auto-updated on calls)
    "pc_port": 39272,
    "autostart": True,
    "minimize_to_tray": True,
    "clipboard_watch": True,
    "tel_handler": True,    # handle tel:/callto: links from browsers
    "sound": True,
    "reminder_sound_repeat": True,
}


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "Zadachi")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "settings.json")


class Settings:
    def __init__(self, path: str | None = None):
        self.path = path or settings_path()
        self.data = dict(DEFAULTS)
        self.load()
        if not self.data.get("token"):
            self.data["token"] = secrets.token_hex(4)  # 8 chars, easy to retype on phone
            self.save()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.data[key] = value
        self.save()
