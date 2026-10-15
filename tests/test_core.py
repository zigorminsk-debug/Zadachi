"""Unit tests for the GUI-independent core (run anywhere, stdlib only)."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "windows"))

from zadachi import protocol          # noqa: E402
from zadachi.db import Database       # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
