"""Tkinter GUI (stdlib only).

Features
--------
* Popup on every incoming call from the phone (same LAN) -> register the
  call, attach a task and a reminder in seconds.
* Tasks accumulate in SQLite; titles / names / phones are offered as
  autocomplete suggestions as soon as you start typing.
* Reminder timers: due tasks pop up (topmost) with sound, Done / +10 min.
* Click-to-call: if a phone number is copied anywhere on the PC (or pressed
  with Ctrl+Alt+D while in the clipboard) it is sent to the phone, which
  opens its default dialer. Also every number in the GUI has a Call button.
* Runs in background: close -> tray icon; autostart with Windows.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import autostart as autostart_mod
from . import protocol
from .config import Settings
from .db import Database
from .server import CallServer
from .tray import TrayIcon

REMIND_PRESETS = [
    ("Без напоминания", 0),
    ("Через 5 минут", 5 * 60),
    ("Через 15 минут", 15 * 60),
    ("Через 30 минут", 30 * 60),
    ("Через 1 час", 3600),
    ("Через 2 часа", 2 * 3600),
    ("Завтра в это время", 24 * 3600),
]


def fmt_ts(ts):
    if not ts:
        return "—"
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def beep(settings):
    if not settings.get("sound", True):
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        try:
            import tkinter as _tk
            _tk.Tk().bell()
        except Exception:
            pass


class AutocompleteEntry(ttk.Entry):
    """Entry with a suggestion listbox fed by `suggest_fn(prefix)`."""

    def __init__(self, master, suggest_fn, **kw):
        super().__init__(master, **kw)
        self._suggest_fn = suggest_fn
        self._listbox = None
        self.bind("<KeyRelease>", self._on_key)
        self.bind("<FocusOut>", lambda e: self.after(150, self._hide))
        self.bind("<Down>", self._focus_list)
        self.bind("<Escape>", lambda e: self._hide())

    def _hide(self):
        if self._listbox:
            self._listbox.destroy()
            self._listbox = None

    def _focus_list(self, _e=None):
        if self._listbox:
            self._listbox.focus_set()
            self._listbox.selection_set(0)

    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        prefix = self.get()
        items = []
        if prefix.strip():
            try:
                items = self._suggest_fn(prefix.strip()) or []
            except Exception:
                items = []
        self._hide()
        if not items:
            return
        lb = tk.Listbox(self.master, height=min(6, len(items)),
                        activestyle="dotbox")
        lb.place(x=self.winfo_x(), y=self.winfo_y() + self.winfo_height(),
                 width=max(self.winfo_width(), 240))
        for it in items:
            lb.insert("end", it)
        lb.bind("<<ListboxSelect>>", lambda e: self._pick(lb))
        lb.bind("<Return>", lambda e: self._pick(lb))
        lb.bind("<Escape>", lambda e: (self._hide(), self.focus_set()))
        self._listbox = lb

    def _pick(self, lb):
        sel = lb.curselection()
        if sel:
            self.delete(0, "end")
            self.insert(0, lb.get(sel[0]))
        self._hide()
        self.focus_set()
        self.icursor("end")


class ZadachiApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Zadachi — звонки и задачи")
        self.geometry("860x560")
        self.minsize(720, 460)

        self.settings = Settings()
        self.db = Database()
        self.events: "queue.Queue" = queue.Queue()
        self.server = CallServer(self.settings.get("token", ""), self.events,
                                 int(self.settings.get("pc_port", protocol.PC_PORT)))
        self._notified = set()          # task ids currently in a reminder popup
        self._clipboard_last = ""
        self._clipboard_phone = ""
        self._hotkey_down = False
        self._call_popup = None
        self.tray = None

        self._build_ui()
        self._apply_autostart()
        try:
            self.server.start()
            self._set_status(f"Сервер запущен, порт {self.server.pc_port}. "
                             f"Токен сопряжения: {self.settings.get('token')}")
        except OSError as exc:
            self._set_status(f"Не удалось запустить сервер: {exc}")

        if self.settings.get("minimize_to_tray", True):
            self.tray = TrayIcon(self, self._restore, self._tray_menu)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(300, self._poll_events)
        self.after(1500, self._check_reminders)
        self.after(900, self._watch_clipboard)
        self.after(150, self._watch_hotkey)
        self.refresh_tasks()
        self.refresh_calls()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Zadachi", font=("Segoe UI", 15, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="Запуск…")
        ttk.Label(top, textvariable=self.status_var).pack(side="left", padx=12)
        ttk.Button(top, text="➕ Новая задача", command=self._new_task_dialog).pack(side="right")
        ttk.Button(top, text="📞 Позвонить…", command=self._dial_dialog).pack(side="right", padx=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._build_tasks_tab(nb)
        self._build_calls_tab(nb)
        self._build_settings_tab(nb)

    def _build_tasks_tab(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Задачи и напоминания")
        cols = ("when", "title", "phone", "name")
        self.tasks_tree = ttk.Treeview(tab, columns=cols, show="headings", height=16)
        for col, title, w in (("when", "Напоминание", 130), ("title", "Задача", 330),
                              ("phone", "Телефон", 150), ("name", "Имя", 160)):
            self.tasks_tree.heading(col, text=title)
            self.tasks_tree.column(col, width=w)
        self.tasks_tree.pack(fill="both", expand=True)
        self.tasks_tree.bind("<Double-1>", lambda e: self._task_done())
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="✔ Выполнено", command=self._task_done).pack(side="left")
        ttk.Button(bar, text="⏰ +10 минут", command=lambda: self._task_snooze(600)).pack(side="left", padx=4)
        ttk.Button(bar, text="🗑 Удалить", command=self._task_delete).pack(side="left")
        ttk.Button(bar, text="📞 Позвонить", command=self._task_call).pack(side="left", padx=4)
        ttk.Button(bar, text="Обновить", command=self.refresh_tasks).pack(side="right")

    def _build_calls_tab(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Журнал звонков")
        cols = ("when", "phone", "name", "status", "note")
        self.calls_tree = ttk.Treeview(tab, columns=cols, show="headings", height=16)
        for col, title, w in (("when", "Время", 130), ("phone", "Телефон", 150),
                              ("name", "Имя", 150), ("status", "Статус", 110),
                              ("note", "Заметка", 260)):
            self.calls_tree.heading(col, text=title)
            self.calls_tree.column(col, width=w)
        self.calls_tree.pack(fill="both", expand=True)
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="📞 Перезвонить", command=self._call_selected_call).pack(side="left")
        ttk.Button(bar, text="➕ Задача по звонку", command=self._task_from_call).pack(side="left", padx=4)
        ttk.Button(bar, text="Обновить", command=self.refresh_calls).pack(side="right")

    def _build_settings_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Настройки")
        grid = ttk.Frame(tab)
        grid.pack(fill="x")

        ttk.Label(grid, text="Токен сопряжения (введите тот же в Android):").grid(row=0, column=0, sticky="w", pady=3)
        self.token_var = tk.StringVar(value=self.settings.get("token"))
        ttk.Entry(grid, textvariable=self.token_var, width=22, state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Button(grid, text="Новый токен", command=self._regen_token).grid(row=0, column=2, padx=4)

        ttk.Label(grid, text="IP телефона (пусто = авто при звонке):").grid(row=1, column=0, sticky="w", pady=3)
        self.phone_ip_var = tk.StringVar(value=self.settings.get("phone_ip", ""))
        ttk.Entry(grid, textvariable=self.phone_ip_var, width=22).grid(row=1, column=1, sticky="w")
        ttk.Button(grid, text="Сохранить и проверить", command=self._save_phone_ip).grid(row=1, column=2, padx=4)

        self.autostart_var = tk.BooleanVar(value=autostart_mod.is_autostart_enabled()
                                           or bool(self.settings.get("autostart", True)))
        self.tray_var = tk.BooleanVar(value=bool(self.settings.get("minimize_to_tray", True)))
        self.clip_var = tk.BooleanVar(value=bool(self.settings.get("clipboard_watch", True)))
        self.sound_var = tk.BooleanVar(value=bool(self.settings.get("sound", True)))
        for i, (var, text) in enumerate((
                (self.autostart_var, "Запускать вместе с Windows (фон)"),
                (self.tray_var, "Закрытие окна — сворачивать в трей"),
                (self.clip_var, "Следить за буфером обмена (номера для звонка)"),
                (self.sound_var, "Звук напоминаний"))):
            ttk.Checkbutton(grid, text=text, variable=var,
                            command=self._save_toggles).grid(row=2 + i, column=0, columnspan=2, sticky="w", pady=2)

        help_text = (
            "Как пользоваться:\n"
            "• Телефон и ПК должны быть в одной Wi-Fi сети (укажите её в Android-приложении).\n"
            "• При входящем звонке на телефоне здесь всплывёт окно — запишите заявку и таймер.\n"
            "• Скопируйте любой номер на ПК — появится кнопка звонка; либо нажмите Ctrl+Alt+D,\n"
            "  и номер из буфера уйдёт в звонилку телефона.\n"
            "• Разрешите в брандмауэре Windows порты TCP 39272 и UDP 39271 для локальной сети."
        )
        ttk.Label(tab, text=help_text, justify="left").pack(anchor="w", pady=10)

    # ------------------------------------------------------------- helpers
    def _set_status(self, text):
        self.status_var.set(text)

    def _apply_autostart(self):
        if self.settings.get("autostart", True):
            autostart_mod.set_autostart(True)

    def phone_ip(self):
        return (self.phone_ip_var.get().strip() if hasattr(self, "phone_ip_var")
                else self.settings.get("phone_ip", ""))

    def call_phone(self, number):
        number = protocol.normalize_phone(number)
        ip = self.phone_ip() or self.settings.get("phone_ip", "")
        if not protocol.is_phone_like(number):
            messagebox.showwarning("Zadachi", "Не похож на номер телефона.")
            return
        if not ip:
            messagebox.showwarning(
                "Zadachi",
                "IP телефона неизвестен. Совершите тестовый звонок с телефона "
                "или укажите IP вручную во вкладке «Настройки».")
            return

        def worker():
            try:
                protocol.send_dial(ip, number, self.settings.get("token", ""))
                self.after(0, lambda: self._set_status(f"Номер {number} отправлен на телефон"))
            except Exception as exc:  # network errors shown to user
                self.after(0, lambda: messagebox.showerror(
                    "Zadachi", f"Не удалось связаться с телефоном ({ip}):\n{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ refresh
    def refresh_tasks(self):
        if not hasattr(self, "tasks_tree"):
            return
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        for t in self.db.list_tasks(include_done=False):
            self.tasks_tree.insert("", "end", iid=str(t["id"]), values=(
                fmt_ts(t["remind_at"]) if t["remind_at"] else "без срока",
                t["title"], t["phone"], t["name"]))

    def refresh_calls(self):
        if not hasattr(self, "calls_tree"):
            return
        self.calls_tree.delete(*self.calls_tree.get_children())
        for c in self.db.list_calls(300):
            self.calls_tree.insert("", "end", iid=str(c["id"]), values=(
                fmt_ts(c["created_at"]), c["phone"], c["name"], c["status"], c["note"]))

    # ------------------------------------------------------ tasks actions
    def _selected(self, tree):
        sel = tree.selection()
        return int(sel[0]) if sel else None

    def _task_done(self):
        tid = self._selected(self.tasks_tree)
        if tid:
            self.db.set_task_done(tid, True)
            self._notified.discard(tid)
            self.refresh_tasks()

    def _task_snooze(self, seconds):
        tid = self._selected(self.tasks_tree)
        if tid:
            self.db.snooze_task(tid, seconds)
            self._notified.discard(tid)
            self.refresh_tasks()

    def _task_delete(self):
        tid = self._selected(self.tasks_tree)
        if tid and messagebox.askyesno("Zadachi", "Удалить задачу?"):
            self.db.delete_task(tid)
            self.refresh_tasks()

    def _task_call(self):
        tid = self._selected(self.tasks_tree)
        if tid:
            for t in self.db.list_tasks(include_done=True):
                if t["id"] == tid and t["phone"]:
                    self.call_phone(t["phone"])
                    return

    def _call_selected_call(self):
        cid = self._selected(self.calls_tree)
        if cid:
            for c in self.db.list_calls(500):
                if c["id"] == cid:
                    self.call_phone(c["phone"])
                    return

    def _task_from_call(self):
        cid = self._selected(self.calls_tree)
        if cid:
            for c in self.db.list_calls(500):
                if c["id"] == cid:
                    self._new_task_dialog(phone=c["phone"], name=c["name"], call_id=cid)
                    return

    # -------------------------------------------------------------- events
    def _poll_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self.after(300, self._poll_events)

    def _handle_event(self, ev):
        ip = ev.get("ip", "")
        if ip:
            self.settings.set("phone_ip", ip)
            if hasattr(self, "phone_ip_var"):
                self.phone_ip_var.set(ip)
        if ev["type"] == "call":
            p = ev["payload"]
            phone = protocol.normalize_phone(p.get("number") or p.get("phone") or "")
            if not phone:
                return
            status = str(p.get("event") or p.get("status") or "incoming")
            name = self.db.name_for_phone(phone)
            call_id = self.db.add_call(phone, status=status, name=name,
                                       device=str(p.get("device", "")))
            self.refresh_calls()
            if status in ("incoming", "ringing", "call"):
                self._show_call_popup(phone, call_id)
        elif ev["type"] == "ping":
            self._set_status(f"Телефон на связи: {ip}")

    # -------------------------------------------------------- call popup
    def _show_call_popup(self, phone, call_id):
        if self._call_popup is not None:
            try:
                self._call_popup.destroy()
            except tk.TclError:
                pass
        beep(self.settings)
        self.deiconify()
        win = tk.Toplevel(self)
        self._call_popup = win
        win.title("Входящий звонок")
        win.attributes("-topmost", True)
        win.geometry("420x300")
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), setattr(self, "_call_popup", None)))

        ttk.Label(win, text="📞 Входящий звонок", font=("Segoe UI", 13, "bold")).pack(pady=(10, 2))
        ttk.Label(win, text=phone, font=("Segoe UI", 16, "bold")).pack()
        known = self.db.name_for_phone(phone)
        if known:
            ttk.Label(win, text=known).pack()

        form = ttk.Frame(win, padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Имя:").grid(row=0, column=0, sticky="w", pady=2)
        name_e = AutocompleteEntry(form, self.db.suggest_names, width=32)
        name_e.grid(row=0, column=1, sticky="we", pady=2)
        if known:
            name_e.insert(0, known)
        ttk.Label(form, text="Заявка/задача:").grid(row=1, column=0, sticky="w", pady=2)
        title_e = AutocompleteEntry(form, self.db.suggest_titles, width=32)
        title_e.grid(row=1, column=1, sticky="we", pady=2)
        title_e.focus_set()

        ttk.Label(form, text="Напоминание:").grid(row=2, column=0, sticky="w", pady=2)
        remind_var = tk.StringVar(value=REMIND_PRESETS[2][0])
        ttk.Combobox(form, textvariable=remind_var, state="readonly", width=29,
                     values=[n for n, _ in REMIND_PRESETS]).grid(row=2, column=1, sticky="we", pady=2)

        def save():
            seconds = dict(REMIND_PRESETS).get(remind_var.get(), 0)
            remind_at = int(time.time()) + seconds if seconds else 0
            title = title_e.get().strip() or f"Перезвонить {phone}"
            self.db.add_task(title, phone=phone, name=name_e.get().strip(),
                             remind_at=remind_at, call_id=call_id)
            self.refresh_tasks()
            win.destroy()
            self._call_popup = None

        bar = ttk.Frame(win)
        bar.pack(pady=8)
        ttk.Button(bar, text="💾 Сохранить заявку", command=save).pack(side="left", padx=4)
        ttk.Button(bar, text="Пропустить", command=lambda: (win.destroy(), setattr(self, "_call_popup", None))).pack(side="left")
        win.bind("<Return>", lambda e: save())

    # --------------------------------------------------- new task / dial
    def _new_task_dialog(self, phone="", name="", call_id=0):
        win = tk.Toplevel(self)
        win.title("Новая задача")
        win.attributes("-topmost", True)
        win.geometry("430x250")
        form = ttk.Frame(win, padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Задача:").grid(row=0, column=0, sticky="w", pady=2)
        title_e = AutocompleteEntry(form, self.db.suggest_titles, width=34)
        title_e.grid(row=0, column=1, sticky="we", pady=2)
        title_e.focus_set()
        ttk.Label(form, text="Телефон:").grid(row=1, column=0, sticky="w", pady=2)
        phone_e = AutocompleteEntry(form, self.db.suggest_phones, width=34)
        phone_e.grid(row=1, column=1, sticky="we", pady=2)
        if phone:
            phone_e.insert(0, phone)
        ttk.Label(form, text="Имя:").grid(row=2, column=0, sticky="w", pady=2)
        name_e = AutocompleteEntry(form, self.db.suggest_names, width=34)
        name_e.grid(row=2, column=1, sticky="we", pady=2)
        if name:
            name_e.insert(0, name)
        ttk.Label(form, text="Напоминание:").grid(row=3, column=0, sticky="w", pady=2)
        remind_var = tk.StringVar(value=REMIND_PRESETS[0][0])
        ttk.Combobox(form, textvariable=remind_var, state="readonly", width=31,
                     values=[n for n, _ in REMIND_PRESETS]).grid(row=3, column=1, sticky="we", pady=2)

        def save():
            title = title_e.get().strip()
            if not title:
                messagebox.showwarning("Zadachi", "Введите текст задачи.", parent=win)
                return
            seconds = dict(REMIND_PRESETS).get(remind_var.get(), 0)
            remind_at = int(time.time()) + seconds if seconds else 0
            self.db.add_task(title, phone=protocol.normalize_phone(phone_e.get()),
                             name=name_e.get().strip(), remind_at=remind_at, call_id=call_id)
            self.refresh_tasks()
            self.refresh_calls()
            win.destroy()

        ttk.Button(win, text="💾 Сохранить", command=save).pack(pady=10)
        win.bind("<Return>", lambda e: save())

    def _dial_dialog(self, preset=""):
        win = tk.Toplevel(self)
        win.title("Позвонить с телефона")
        win.geometry("400x160")
        win.attributes("-topmost", True)
        ttk.Label(win, text="Номер (откроется в звонилке телефона):").pack(pady=(12, 4))
        phone_e = AutocompleteEntry(win, self.db.suggest_phones, width=30)
        phone_e.pack()
        if preset:
            phone_e.insert(0, preset)
        phone_e.focus_set()

        def go():
            self.call_phone(phone_e.get())
            win.destroy()

        ttk.Button(win, text="📞 Позвонить", command=go).pack(pady=10)
        win.bind("<Return>", lambda e: go())

    # ----------------------------------------------------------- reminders
    def _check_reminders(self):
        due = [t for t in self.db.due_tasks() if t["id"] not in self._notified]
        if due:
            self._show_reminder(due)
        self.after(15000, self._check_reminders)

    def _show_reminder(self, tasks):
        beep(self.settings)
        self.deiconify()
        win = tk.Toplevel(self)
        win.title("Напоминание")
        win.attributes("-topmost", True)
        win.geometry("440x280")
        ttk.Label(win, text="⏰ Напоминание", font=("Segoe UI", 13, "bold")).pack(pady=(10, 4))
        for t in tasks:
            self._notified.add(t["id"])
            row = ttk.Frame(win, padding=6)
            row.pack(fill="x")
            text = t["title"] + (f"\n{t['name']} {t['phone']}".strip() if (t["name"] or t["phone"]) else "")
            ttk.Label(row, text=text, wraplength=240).pack(side="left")

            def done(tid=t["id"], w=win):
                self.db.set_task_done(tid, True)
                self._notified.discard(tid)
                self.refresh_tasks()
                if not [x for x in tasks if x["id"] in self._notified]:
                    w.destroy()

            def snooze(tid=t["id"], w=win):
                self.db.snooze_task(tid, 600)
                self._notified.discard(tid)
                self.refresh_tasks()
                if not [x for x in tasks if x["id"] in self._notified]:
                    w.destroy()

            ttk.Button(row, text="✔ Готово", command=done).pack(side="right")
            ttk.Button(row, text="+10 мин", command=snooze).pack(side="right", padx=4)
            if t["phone"]:
                ttk.Button(row, text="📞", command=lambda p=t["phone"]: self.call_phone(p)).pack(side="right")

    # ------------------------------------------------- clipboard & hotkey
    def _watch_clipboard(self):
        if self.settings.get("clipboard_watch", True):
            try:
                text = self.clipboard_get()
            except tk.TclError:
                text = ""
            if text != self._clipboard_last:
                self._clipboard_last = text
                phones = protocol.extract_phones(text)
                if phones:
                    self._clipboard_phone = phones[0]
                    self._set_status(f"В буфере номер {phones[0]} — Ctrl+Alt+D или кнопка «Позвонить…»")
                    self._show_clipboard_toast(phones[0])
        self.after(900, self._watch_clipboard)

    def _show_clipboard_toast(self, phone):
        win = tk.Toplevel(self)
        win.title("Zadachi")
        win.attributes("-topmost", True)
        win.overrideredirect(True)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        win.geometry(f"320x86+{sw - 340}+{sh - 140}")
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Номер в буфере: {phone}").pack(anchor="w")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="📞 Позвонить", command=lambda: (self.call_phone(phone), win.destroy())).pack(side="left")
        ttk.Button(bar, text="Закрыть", command=win.destroy).pack(side="left", padx=6)
        win.after(15000, lambda: win.winfo_exists() and win.destroy())

    def _watch_hotkey(self):
        """Ctrl+Alt+D anywhere in Windows -> dial number from clipboard."""
        down = False
        try:
            import ctypes
            u = ctypes.windll.user32
            ctrl = u.GetAsyncKeyState(0x11) & 0x8000
            alt = u.GetAsyncKeyState(0x12) & 0x8000
            d = u.GetAsyncKeyState(0x44) & 0x8000
            down = bool(ctrl and alt and d)
        except Exception:
            down = False
        if down and not self._hotkey_down:
            number = self._clipboard_phone
            if not number:
                try:
                    phones = protocol.extract_phones(self.clipboard_get())
                    number = phones[0] if phones else ""
                except tk.TclError:
                    number = ""
            if number:
                self.call_phone(number)
        self._hotkey_down = down
        self.after(150, self._watch_hotkey)

    # ------------------------------------------------------------- settings
    def _regen_token(self):
        import secrets
        token = secrets.token_hex(4)
        self.settings.set("token", token)
        self.token_var.set(token)
        self.server.token = token
        messagebox.showinfo("Zadachi", "Токен обновлён на ПК. Введите новый токен в Android-приложении и перезапустите эту программу для надёжности.")

    def _save_phone_ip(self):
        ip = self.phone_ip_var.get().strip()
        self.settings.set("phone_ip", ip)
        if not ip:
            return

        def worker():
            try:
                protocol.ping_phone(ip, self.settings.get("token", ""))
                self.after(0, lambda: self._set_status(f"Телефон {ip} на связи ✔"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showwarning(
                    "Zadachi", f"Телефон {ip} не отвечает (та же Wi-Fi? токен?):\n{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _save_toggles(self):
        self.settings.set("autostart", bool(self.autostart_var.get()))
        self.settings.set("minimize_to_tray", bool(self.tray_var.get()))
        self.settings.set("clipboard_watch", bool(self.clip_var.get()))
        self.settings.set("sound", bool(self.sound_var.get()))
        autostart_mod.set_autostart(bool(self.autostart_var.get()))

    # ---------------------------------------------------------------- tray
    def _restore(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Открыть Zadachi", command=self._restore)
        menu.add_command(label="Новая задача", command=lambda: (self._restore(), self._new_task_dialog()))
        menu.add_separator()
        menu.add_command(label="Выход", command=self._quit)
        try:
            x, y = self.winfo_pointerxy()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _on_close(self):
        if self.settings.get("minimize_to_tray", True) and self.tray is not None:
            self.withdraw()
        else:
            self._quit()

    def _quit(self):
        try:
            if self.tray:
                self.tray.remove()
            self.server.stop()
            self.db.close()
        finally:
            self.destroy()


def main():
    app = ZadachiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
