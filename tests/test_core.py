"""Unit tests for the GUI-independent core (run anywhere, stdlib only)."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "windows"))

from zadachi import autostart, protocol, telhandler  # noqa: E402
from zadachi.db import Database                      # noqa: E402
from zadachi.tray import TrayIcon                    # noqa: E402


class PhoneUtilsTest(unittest.TestCase):
    def test_normalize_by(self):
        self.assertEqual(protocol.normalize_phone("+375 (29) 123-45-67"), "+375291234567")
        self.assertEqual(protocol.normalize_phone("8 029 123-45-67"), "+375291234567")

    def test_normalize_ru(self):
        self.assertEqual(protocol.normalize_phone("8 916 123-45-67"), "+79161234567")

    def test_extract_from_text(self):
        phones = protocol.extract_phones("Позвонить +375 29 123-45-67 завтра, сумма 1500 руб")
        self.assertEqual(phones, ["+375291234567"])

    def test_not_phone(self):
        self.assertFalse(protocol.is_phone_like("12.05.2026"))
        self.assertEqual(protocol.extract_phones("завтра в 15:00"), [])


class TelLinkTest(unittest.TestCase):
    """Clicking a phone number on a web page arrives as a tel: URI."""

    def test_tel_uri(self):
        self.assertEqual(protocol.extract_number_from_uri("tel:+375 (29) 123-45-67"),
                         "+375291234567")
        self.assertEqual(protocol.extract_number_from_uri("callto:%2B375291234567"),
                         "+375291234567")
        self.assertEqual(protocol.extract_number_from_uri("tel:+375291234567;ext=12"),
                         "+375291234567")

    def test_parse_call_argument(self):
        self.assertEqual(protocol.parse_call_argument(["--call", "tel:80291234567"]),
                         "+375291234567")
        self.assertEqual(protocol.parse_call_argument(["tel:+375291234567"]),
                         "+375291234567")
        self.assertEqual(protocol.parse_call_argument(["--tray"]), "")
        self.assertEqual(protocol.parse_call_argument(["--call", "https://example.com"]), "")

    def test_handler_command_and_offwindows_noop(self):
        self.assertIn('--call "%1"', telhandler._handler_command())
        # Registry functions must be harmless on non-Windows (CI/dev).
        import sys
        if sys.platform != "win32":
            self.assertFalse(telhandler.is_tel_handler_registered())
            self.assertFalse(telhandler.set_tel_handler(True))


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_task_and_suggestions(self):
        self.db.add_task("Заявка на ремонт", phone="+375291112233", name="Иван")
        self.db.add_task("Заявка на замену", phone="+375291112233", name="Иван")
        self.db.add_call("+375291112233", name="Иван")
        self.assertIn("Заявка на ремонт", self.db.suggest_titles("Заявка"))
        self.assertIn("Иван", self.db.suggest_names("Ив"))
        self.assertIn("+375291112233", self.db.suggest_phones("375"))
        self.assertEqual(self.db.name_for_phone("+375291112233"), "Иван")

    def test_reminder_due_and_snooze(self):
        past = int(time.time()) - 10
        tid = self.db.add_task("Перезвонить", remind_at=past)
        self.assertEqual([t["id"] for t in self.db.due_tasks()], [tid])
        self.db.snooze_task(tid, 3600)
        self.assertEqual(self.db.due_tasks(), [])
        self.db.set_task_done(tid, True)
        self.assertEqual(self.db.list_tasks(), [])


class IconAssetsTest(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(__file__), "..")

    def _png_size(self, path):
        import struct
        with open(path, "rb") as f:
            head = f.read(24)
        self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n")
        return struct.unpack(">II", head[16:24])

    def test_windows_ico(self):
        path = os.path.join(self.ROOT, "windows", "icon.ico")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(4), b"\x00\x00\x01\x00")  # ICO magic

    def test_android_launcher_sizes(self):
        res = os.path.join(self.ROOT, "android", "app", "src", "main", "res")
        self.assertEqual(self._png_size(os.path.join(
            res, "mipmap-xxxhdpi", "ic_launcher.png")), (192, 192))
        self.assertEqual(self._png_size(os.path.join(
            res, "mipmap-xxxhdpi", "ic_launcher_foreground.png")), (432, 432))
        import xml.etree.ElementTree as ET
        ET.parse(os.path.join(res, "mipmap-anydpi-v26", "ic_launcher.xml"))

    def test_android_allows_lan_http(self):
        # Regression: Android 9+ blocks cleartext HTTP by default, which
        # broke the "test call" (Cleartext HTTP traffic ... not permitted).
        import xml.etree.ElementTree as ET
        app = os.path.join(self.ROOT, "android", "app", "src", "main")
        manifest = open(os.path.join(app, "AndroidManifest.xml"), encoding="utf-8").read()
        self.assertIn("@xml/network_security_config", manifest)
        cfg_path = os.path.join(app, "res", "xml", "network_security_config.xml")
        root = ET.parse(cfg_path).getroot()
        base = root.find("base-config")
        self.assertIsNotNone(base)
        self.assertEqual(base.get("cleartextTrafficPermitted"), "true")


class AndroidManifestTest(unittest.TestCase):
    def test_connected_device_fgs_has_companion_permission(self):
        # Regression: Android 14 crashes CallService with SecurityException
        # ("Starting FGS with type connectedDevice ... requires permissions")
        # unless a companion permission such as CHANGE_NETWORK_STATE is held.
        path = os.path.join(os.path.dirname(__file__), "..", "android",
                            "app", "src", "main", "AndroidManifest.xml")
        manifest = open(path, encoding="utf-8").read()
        self.assertIn("android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE", manifest)
        self.assertIn("android.permission.CHANGE_NETWORK_STATE", manifest)
        self.assertIn('android:foregroundServiceType="connectedDevice"', manifest)


class StartupTest(unittest.TestCase):
    def test_autostart_goes_to_tray_but_manual_launch_does_not(self):
        # Autostart entry must start hidden; the exe itself without
        # --tray always shows its window (regression: window used to
        # collapse immediately on open).
        self.assertIn("--tray", autostart._launch_command())

    def test_tray_is_noop_off_windows(self):
        tray = TrayIcon(object(), lambda: None)
        self.assertFalse(tray._added)
        tray.remove()  # must not raise


if __name__ == "__main__":
    unittest.main()
