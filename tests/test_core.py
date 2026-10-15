"""Unit tests for the GUI-independent core (run anywhere, stdlib only)."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "windows"))

from zadachi import autostart, protocol, telhandler  # noqa: E402
from zadachi import db as db_module                  # noqa: E402
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

    def test_update_task(self):
        tid = self.db.add_task("Старая заявка", phone="+375291112233")
        self.db.update_task(tid, "Новая заявка", phone="+375294445566",
                            name="Пётр", remind_at=1234567890)
        task = self.db.get_task(tid)
        self.assertEqual(task["title"], "Новая заявка")
        self.assertEqual(task["phone"], "+375294445566")
        self.assertEqual(task["name"], "Пётр")
        self.assertEqual(task["remind_at"], 1234567890)
        self.assertEqual(self.db.name_for_phone("+375294445566"), "Пётр")


class TaskInteractionTest(unittest.TestCase):
    def test_double_click_edits_instead_of_completing(self):
        # Regression: double-click used to mark the task done, so it
        # silently vanished from the active task list.
        root = os.path.join(os.path.dirname(__file__), "..")
        app = open(os.path.join(root, "windows", "zadachi", "app.py"),
                   encoding="utf-8").read()
        self.assertIn('bind("<Double-1>", lambda e: self._task_edit_selected())', app)
        self.assertNotIn('bind("<Double-1>", lambda e: self._task_done())', app)
        self.assertIn("def _edit_task_dialog", app)
        # The task-list Call button must never fail silently when the
        # task has no phone number yet.
        self.assertIn("У этой задачи не указан телефон", app)
        self.assertIn("Позвонить по задаче", app)


class PortableDatabaseTest(unittest.TestCase):
    def test_legacy_db_is_copied_next_to_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy_dir = os.path.join(tmp, "AppData", "Zadachi")
            os.makedirs(legacy_dir)
            legacy = os.path.join(legacy_dir, "zadachi.db")
            old = Database(legacy)
            old.add_task("Не потерять эту заявку")
            old.close()

            portable = os.path.join(tmp, "Program", "zadachi.db")
            chosen = db_module.resolve_db_path(
                portable_path=portable,
                legacy_path=legacy)
            self.assertEqual(chosen, portable)
            self.assertTrue(os.path.isfile(portable))

            moved = Database(chosen)
            titles = [t["title"] for t in moved.list_tasks()]
            moved.close()
            self.assertIn("Не потерять эту заявку", titles)

    def test_existing_tasks_are_not_overwritten_by_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "legacy.db")
            old = Database(legacy)
            old.add_task("Старая")
            old.close()
            portable = os.path.join(tmp, "zadachi.db")
            current = Database(portable)
            current.add_task("Текущая")
            current.close()

            chosen = db_module.resolve_db_path(portable, legacy)
            self.assertEqual(chosen, portable)
            current = Database(chosen)
            titles = [t["title"] for t in current.list_tasks()]
            current.close()
            self.assertEqual(titles, ["Текущая"])


class CallerIdTest(unittest.TestCase):
    def test_phones_match_formats(self):
        self.assertTrue(protocol.phones_match("+375291234567", "80291234567"))
        self.assertTrue(protocol.phones_match("+375 (29) 123-45-67", "291234567"))
        self.assertFalse(protocol.phones_match("+375291234567", "+375297654321"))
        self.assertFalse(protocol.phones_match("", "+375291234567"))

    def test_name_lookup_tolerates_number_format(self):
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            db = Database(tmp.name)
            db.add_call("80291112233", name="Иван")
            self.assertEqual(db.name_for_phone("+375291112233"), "Иван")
            self.assertEqual(db.name_for_phone("291112233"), "Иван")
            db.update_call_phone_name(1, "+375299998877", "Пётр")
            self.assertEqual(db.name_for_phone("80299998877"), "Пётр")
            db.close()
        finally:
            os.unlink(tmp.name)


class ServerTest(unittest.TestCase):
    def test_health_and_call_identify_as_zadachi(self):
        import json
        import queue
        import urllib.error
        import urllib.request
        from zadachi.server import CallServer

        events = queue.Queue()
        server = CallServer("secret", events, pc_port=0).start()
        try:
            base = f"http://127.0.0.1:{server.pc_port}"
            with urllib.request.urlopen(base + "/api/health", timeout=3) as r:
                self.assertEqual(json.load(r)["app"], "Zadachi-PC")
            # Wrong token -> JSON 403 (never an HTML error page).
            req = urllib.request.Request(base + "/api/call", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(ctx.exception.code, 403)
            self.assertIn("Zadachi-PC", ctx.exception.read().decode())
            # Correct token -> event is queued for the GUI.
            req = urllib.request.Request(
                base + "/api/call", method="POST",
                data=json.dumps({"event": "incoming", "number": "+375291234567"}).encode(),
                headers={"X-Zadachi-Token": "secret", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                self.assertTrue(json.load(r)["ok"])
            self.assertEqual(events.get_nowait()["payload"]["number"], "+375291234567")
        finally:
            server.stop()


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


class AndroidPairingTest(unittest.TestCase):
    def test_phone_verifies_pc_identity_before_test_call(self):
        # Regression: a manually typed IP may lead to some other HTTP
        # server, which answered the test with an HTML "HTTP 501" page.
        # The phone must check /api/health says Zadachi-PC, fall back to
        # discovery, and use the values actually typed in the fields.
        base = os.path.join(os.path.dirname(__file__), "..", "android",
                            "app", "src", "main", "java", "by", "zadachi", "calls")
        net = open(os.path.join(base, "Net.java"), encoding="utf-8").read()
        main = open(os.path.join(base, "MainActivity.java"), encoding="utf-8").read()
        self.assertIn("isZadachiPc", net)
        self.assertIn("/api/health", net)
        self.assertIn("discoverPc", net)
        self.assertIn("syncPairingFromFields", main)
        self.assertIn("Net.isZadachiPc(ip, port)", main)


class IncomingCallFlowTest(unittest.TestCase):
    def test_real_incoming_call_can_open_task_note(self):
        # Regression: Android hid the caller number without READ_CALL_LOG,
        # so the phone sent an empty number and the PC silently dropped
        # the event instead of opening the new-task note.
        root = os.path.join(os.path.dirname(__file__), "..")
        manifest = open(os.path.join(
            root, "android", "app", "src", "main", "AndroidManifest.xml"),
            encoding="utf-8").read()
        calls = open(os.path.join(
            root, "android", "app", "src", "main", "java", "by", "zadachi",
            "calls", "CallService.java"), encoding="utf-8").read()
        main = open(os.path.join(
            root, "android", "app", "src", "main", "java", "by", "zadachi",
            "calls", "MainActivity.java"), encoding="utf-8").read()
        app = open(os.path.join(root, "windows", "zadachi", "app.py"),
                   encoding="utf-8").read()

        self.assertIn("android.permission.READ_CALL_LOG", manifest)
        self.assertIn("Manifest.permission.READ_CALL_LOG", main)
        self.assertIn("TelephonyCallback", calls)
        self.assertIn("handleCallState", calls)
        self.assertIn('phone or "unknown"', app)
        self.assertIn("Входящий звонок — новая задача", app)


class CallerIdAndroidTest(unittest.TestCase):
    def test_screening_service_and_contact_name(self):
        # Regression: broadcasts often deliver no caller number, so the
        # PC note opened with "number hidden" and no contact name.
        root = os.path.join(os.path.dirname(__file__), "..", "android",
                            "app", "src", "main")
        manifest = open(os.path.join(root, "AndroidManifest.xml"),
                        encoding="utf-8").read()
        screening = open(os.path.join(
            root, "java", "by", "zadachi", "calls",
            "ZadachiCallScreeningService.java"), encoding="utf-8").read()
        reporter = open(os.path.join(
            root, "java", "by", "zadachi", "calls", "CallReporter.java"),
            encoding="utf-8").read()
        main = open(os.path.join(
            root, "java", "by", "zadachi", "calls", "MainActivity.java"),
            encoding="utf-8").read()

        self.assertIn("android.permission.READ_CONTACTS", manifest)
        self.assertIn(".ZadachiCallScreeningService", manifest)
        self.assertIn("android.telecom.CallScreeningService", manifest)
        self.assertIn("setDisallowCall(false)", screening)
        self.assertIn("isActiveHere", screening)
        self.assertIn("PhoneLookup", reporter)
        self.assertIn('\\"name\\"', reporter)
        self.assertIn("ROLE_CALL_SCREENING", main)


class DialerLaunchTest(unittest.TestCase):
    def test_pc_call_reaches_dialer_despite_background_limits(self):
        # Regression: a direct startActivity() from the service was
        # silently blocked by Android, so the PC window closed and
        # nothing happened on the phone. There must also be a
        # full-screen/tappable notification path to the dialer.
        root = os.path.join(os.path.dirname(__file__), "..", "android",
                            "app", "src", "main")
        manifest = open(os.path.join(root, "AndroidManifest.xml"),
                        encoding="utf-8").read()
        calls = open(os.path.join(root, "java", "by", "zadachi", "calls",
                                  "CallService.java"), encoding="utf-8").read()
        dial = open(os.path.join(root, "java", "by", "zadachi", "calls",
                                 "DialActivity.java"), encoding="utf-8").read()
        self.assertIn(".DialActivity", manifest)
        self.assertIn("android.permission.USE_FULL_SCREEN_INTENT", manifest)
        self.assertIn("setFullScreenIntent", calls)
        self.assertIn("DialActivity.class", calls)
        self.assertIn("Intent.ACTION_DIAL", dial)


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
