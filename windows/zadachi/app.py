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

import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

from . import autostart as autostart_mod
from . import protocol
from . import telhandler
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


def log_error(context: str):
    """Append the current exception to %APPDATA%/Zadachi/error.log."""
    try:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "Zadachi")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {context} ---\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


def find_icon_path():
    """icon.ico: PyInstaller bundle (_MEIPASS) / beside the exe / repo."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", ""), "icon.ico"),
        os.path.join(os.path.dirname(sys.executable), "icon.ico"),
        os.path.join(os.path.dirname(here), "icon.ico"),          # windows/icon.ico (dev)
        os.path.join(os.getcwd(), "icon.ico"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""


class ZadachiApp(tk.Tk):
    def __init__(self, start_in_tray: bool = False):
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
        self._popup_phone_entry = None
        self._popup_name_entry = None
        self._popup_phone_var = None
        self._popup_name_var = None
        # De-duplication of the same call reported by several Android
        # sources (broadcast, telephony callback, call screening).
        self._last_incoming_call_id = 0
        self._last_incoming_phone = ""
        self._last_incoming_at = 0.0
        self.tray = None

        self._build_ui()
        self._apply_autostart()
        if self.settings.get("tel_handler", True):
            telhandler.set_tel_handler(True)
        try:
            self.server.start()
            self._set_status(f"Сервер запущен, порт {self.server.pc_port}. "
                             f"Токен сопряжения: {self.settings.get('token')}")
        except OSError as exc:
            self._set_status(f"Не удалось запустить сервер: {exc}")

        # Tray is optional decoration: it must NEVER kill the app window.
        if self.settings.get("minimize_to_tray", True):
            try:
                self.tray = TrayIcon(self, self._restore, self._tray_menu,
                                     icon_path=self._icon_path)
            except Exception:
                self.tray = None
                log_error("tray icon init failed (continuing without tray)")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(300, self._poll_events)
        self.after(1500, self._check_reminders)
        self.after(900, self._watch_clipboard)
        self.after(150, self._watch_hotkey)
        self.refresh_tasks()
        self.refresh_calls()

        # A manual launch always shows the window; only the Windows
        # autostart entry passes --tray and starts hidden in background.
        if start_in_tray and self.tray is not None:
            self.withdraw()
        else:
            self.state("normal")
            self.deiconify()
            self.lift()

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
        # Double-click edits the task. (It used to mark it done, so the
        # task simply vanished from the active list — very surprising.)
        self.tasks_tree.bind("<Double-1>", lambda e: self._task_edit_selected())
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="✏ Изменить", command=self._task_edit_selected).pack(side="left")
        ttk.Button(bar, text="✔ Выполнено", command=self._task_done).pack(side="left", padx=4)
        ttk.Button(bar, text="⏰ +10 минут", command=lambda: self._task_snooze(600)).pack(side="left", padx=4)
        ttk.Button(bar, text="🗑 Удалить", command=self._task_delete).pack(side="left")
        ttk.Button(bar, text="📞 Позвонить по задаче", command=self._task_call).pack(side="left", padx=4)
        ttk.Button(bar, text="Обновить", command=self.refresh_tasks).pack(side="right")
        self.show_done_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Показать выполненные", variable=self.show_done_var,
                        command=self.refresh_tasks).pack(side="right", padx=6)

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
        self.tel_var = tk.BooleanVar(value=bool(self.settings.get("tel_handler", True)))
        self.sound_var = tk.BooleanVar(value=bool(self.settings.get("sound", True)))
        for i, (var, text) in enumerate((
                (self.autostart_var, "Запускать вместе с Windows (фон)"),
                (self.tray_var, "Закрытие окна — сворачивать в трей"),
                (self.clip_var, "Следить за буфером обмена (номера для звонка)"),
                (self.tel_var, "Номера-ссылки tel: на сайтах — звонить через телефон"),
                (self.sound_var, "Звук напоминаний"))):
            ttk.Checkbutton(grid, text=text, variable=var,
                            command=self._save_toggles).grid(row=2 + i, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Label(tab, text=f"База данных: {self.db.path}\n"
                            "Лежит рядом с Zadachi.exe — при обновлении заменяйте "
                            "только exe, файл базы не удаляйте, он подхватится сам.",
                  justify="left", wraplength=760).pack(anchor="w", pady=(10, 0))

        help_text = (
            "Как пользоваться:\n"
            "• Телефон и ПК должны быть в одной Wi-Fi сети (укажите её в Android-приложении).\n"
            "• При входящем звонке на телефоне здесь всплывёт окно — запишите заявку и таймер.\n"
            "• Клик по номеру-ссылке (tel:) на любом сайте — звонок уходит на телефон\n"
            "  (при первом клике браузер может попросить выбрать Zadachi).\n"
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
        include_done = bool(getattr(self, "show_done_var", None) and
                            self.show_done_var.get())
        for t in self.db.list_tasks(include_done=include_done):
            self.tasks_tree.insert("", "end", iid=str(t["id"]), values=(
                fmt_ts(t["remind_at"]) if t["remind_at"] else "без срока",
                ("✔ " if t["done"] else "") + t["title"], t["phone"], t["name"]))

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

    def _task_edit_selected(self):
        tid = self._selected(self.tasks_tree)
        if tid:
            task = self.db.get_task(tid)
            if task:
                self._edit_task_dialog(task)

    def _edit_task_dialog(self, task):
        win = tk.Toplevel(self)
        win.title("Изменить задачу")
        win.geometry("430x310")
        win.attributes("-topmost", True)
        form = ttk.Frame(win, padding=10)
        form.pack(fill="x")

        ttk.Label(form, text="Задача:").grid(row=0, column=0, sticky="w", pady=2)
        title_e = AutocompleteEntry(form, self.db.suggest_titles, width=34)
        title_e.grid(row=0, column=1, sticky="we", pady=2)
        title_e.insert(0, task["title"])
        title_e.focus_set()

        ttk.Label(form, text="Телефон:").grid(row=1, column=0, sticky="w", pady=2)
        phone_e = AutocompleteEntry(form, self.db.suggest_phones, width=34)
        phone_e.grid(row=1, column=1, sticky="we", pady=2)
        if task["phone"]:
            phone_e.insert(0, task["phone"])

        ttk.Label(form, text="Имя:").grid(row=2, column=0, sticky="w", pady=2)
        name_e = AutocompleteEntry(form, self.db.suggest_names, width=34)
        name_e.grid(row=2, column=1, sticky="we", pady=2)
        if task["name"]:
            name_e.insert(0, task["name"])

        ttk.Label(form, text="Напоминание:").grid(row=3, column=0, sticky="w", pady=2)
        keep_label = ("Оставить: " + fmt_ts(task["remind_at"])) if task["remind_at"] \
            else "Оставить без напоминания"
        remind_var = tk.StringVar(value=keep_label)
        values = [keep_label] + [name for name, _ in REMIND_PRESETS]
        ttk.Combobox(form, textvariable=remind_var, state="readonly", width=31,
                     values=values).grid(row=3, column=1, sticky="we", pady=2)

        done_var = tk.BooleanVar(value=bool(task["done"]))
        ttk.Checkbutton(form, text="Выполнено", variable=done_var).grid(
            row=4, column=1, sticky="w", pady=4)

        def save():
            title = title_e.get().strip()
            if not title:
                messagebox.showwarning("Zadachi", "Введите текст задачи.", parent=win)
                return
            choice = remind_var.get()
            if choice == keep_label:
                remind_at = int(task["remind_at"] or 0)
            else:
                seconds = dict(REMIND_PRESETS).get(choice, 0)
                remind_at = int(time.time()) + seconds if seconds else 0
            self.db.update_task(
                task["id"], title,
                phone=protocol.normalize_phone(phone_e.get()),
                name=name_e.get().strip(),
                remind_at=remind_at, done=bool(done_var.get()))
            self._notified.discard(task["id"])
            self.refresh_tasks()
            win.destroy()

        ttk.Button(win, text="💾 Сохранить", command=save).pack(pady=10)
        win.bind("<Return>", lambda e: save())

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
        if not tid:
            messagebox.showwarning("Zadachi", "Сначала выберите задачу в списке.")
            return
        task = self.db.get_task(tid)
        if not task:
            return
        phone = protocol.normalize_phone(task["phone"] or "")
        if not protocol.is_phone_like(phone):
            # Never fail silently: let the user fill the number in.
            messagebox.showwarning(
                "Zadachi",
                "У этой задачи не указан телефон. Откроется окно "
                "редактирования — впишите номер и сохраните, затем "
                "нажмите «Позвонить по задаче» ещё раз.")
            self._edit_task_dialog(task)
            return
        self._set_status(f"Отправляю {phone} на телефон…")
        self.call_phone(phone)

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
            status = str(p.get("event") or p.get("status") or "incoming")
            supplied_name = str(p.get("name") or p.get("contactName") or "").strip()
            # Name priority: phone's own address book (sent by Android),
            # then our accumulated contacts/history (format-tolerant).
            name = supplied_name or (self.db.name_for_phone(phone) if phone else "")
            is_incoming = status in ("incoming", "ringing", "call")
            now = time.time()
            same_call = (
                is_incoming and self._last_incoming_call_id
                and now - self._last_incoming_at < 30
                and (not phone or not self._last_incoming_phone
                     or protocol.phones_match(phone, self._last_incoming_phone)))
            if same_call:
                # The same call reported again, now possibly identified:
                # update the journal row and the already-open task note
                # instead of creating a duplicate call.
                call_id = self._last_incoming_call_id
                if phone:
                    self.db.update_call_phone_name(call_id, phone, name)
                    self._last_incoming_phone = phone
                self._last_incoming_at = now
                self._update_call_popup(phone, name)
                self.refresh_calls()
                return
            # Even a hidden/empty number must immediately open the new-task
            # note: the user can type the number and task while it rings.
            call_id = self.db.add_call(phone or "unknown", status=status, name=name,
                                       device=str(p.get("device", "")))
            if is_incoming:
                self._last_incoming_call_id = call_id
                self._last_incoming_phone = phone
                self._last_incoming_at = now
            elif status in ("missed", "ended"):
                self._last_incoming_at = 0.0
                self._last_incoming_phone = ""
            self.refresh_calls()
            if is_incoming:
                self._show_call_popup(phone, call_id, name=name)
        elif ev["type"] == "ping":
            self._set_status(f"Телефон на связи: {ip}")

    def _update_call_popup(self, phone, name):
        """Fill number/name into the already-open task note when a later
        event identifies the caller (without touching typed text)."""
        if self._call_popup is None:
            return
        try:
            if not self._call_popup.winfo_exists():
                return
        except tk.TclError:
            self._call_popup = None
            return
        if phone:
            if self._popup_phone_var is not None:
                self._popup_phone_var.set(phone)
            entry = self._popup_phone_entry
            if entry is not None and not entry.get().strip():
                entry.insert(0, phone)
        if name:
            if self._popup_name_var is not None and not self._popup_name_var.get():
                self._popup_name_var.set(name)
            entry = self._popup_name_entry
            if entry is not None and not entry.get().strip():
                entry.insert(0, name)
        self._call_popup.lift()

    # -------------------------------------------------------- call popup
    def _show_call_popup(self, phone, call_id, name=""):
        if self._call_popup is not None:
            try:
                if self._call_popup.winfo_exists():
                    # A task note is already open (possibly for this same
                    # call): raise it instead of destroying typed text.
                    self._call_popup.lift()
                    self._call_popup.focus_force()
                    return
            except tk.TclError:
                self._call_popup = None
        beep(self.settings)
        self.state("normal")
        self.deiconify()
        self.lift()
        win = tk.Toplevel(self)
        self._call_popup = win
        win.title("Входящий звонок — новая задача")
        win.attributes("-topmost", True)
        win.geometry("430x330")

        def close_popup():
            self._call_popup = None
            self._popup_phone_entry = None
            self._popup_name_entry = None
            self._popup_phone_var = None
            self._popup_name_var = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_popup)

        ttk.Label(win, text="📞 Входящий звонок — новая задача",
                  font=("Segoe UI", 13, "bold")).pack(pady=(10, 2))
        known = name or (self.db.name_for_phone(phone) if phone else "")
        self._popup_phone_var = tk.StringVar(value=phone or "Номер скрыт")
        self._popup_name_var = tk.StringVar(value=known or "")
        ttk.Label(win, textvariable=self._popup_phone_var,
                  font=("Segoe UI", 16, "bold")).pack()
        ttk.Label(win, textvariable=self._popup_name_var).pack()

        form = ttk.Frame(win, padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Телефон:").grid(row=0, column=0, sticky="w", pady=2)
        phone_e = AutocompleteEntry(form, self.db.suggest_phones, width=32)
        phone_e.grid(row=0, column=1, sticky="we", pady=2)
        if phone:
            phone_e.insert(0, phone)
        self._popup_phone_entry = phone_e

        ttk.Label(form, text="Имя:").grid(row=1, column=0, sticky="w", pady=2)
        name_e = AutocompleteEntry(form, self.db.suggest_names, width=32)
        name_e.grid(row=1, column=1, sticky="we", pady=2)
        if known:
            name_e.insert(0, known)
        self._popup_name_entry = name_e
        ttk.Label(form, text="Заявка/задача:").grid(row=2, column=0, sticky="w", pady=2)
        title_e = AutocompleteEntry(form, self.db.suggest_titles, width=32)
        title_e.grid(row=2, column=1, sticky="we", pady=2)
        title_e.focus_set()

        ttk.Label(form, text="Напоминание:").grid(row=3, column=0, sticky="w", pady=2)
        remind_var = tk.StringVar(value=REMIND_PRESETS[2][0])
        ttk.Combobox(form, textvariable=remind_var, state="readonly", width=29,
                     values=[n for n, _ in REMIND_PRESETS]).grid(row=3, column=1, sticky="we", pady=2)

        def save():
            seconds = dict(REMIND_PRESETS).get(remind_var.get(), 0)
            remind_at = int(time.time()) + seconds if seconds else 0
            entered_phone = protocol.normalize_phone(phone_e.get())
            title = title_e.get().strip()
            if not title:
                title = (f"Перезвонить {entered_phone}" if entered_phone
                         else "Задача по входящему звонку")
            self.db.add_task(title, phone=entered_phone, name=name_e.get().strip(),
                             remind_at=remind_at, call_id=call_id)
            self.refresh_tasks()
            close_popup()

        bar = ttk.Frame(win)
        bar.pack(pady=8)
        ttk.Button(bar, text="💾 Сохранить заявку", command=save).pack(side="left", padx=4)
        ttk.Button(bar, text="Пропустить", command=close_popup).pack(side="left")
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
        self.settings.set("tel_handler", bool(self.tel_var.get()))
        self.settings.set("sound", bool(self.sound_var.get()))
        autostart_mod.set_autostart(bool(self.autostart_var.get()))
        telhandler.set_tel_handler(bool(self.tel_var.get()))

    # ---------------------------------------------------------------- tray
    def _restore(self):
        self.state("normal")
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


def run_call_request(number: str) -> bool:
    """Handle `Zadachi.exe --call tel:+375...` (browser click on a number).

    Sends the number straight to the phone and shows a small result
    window. Returns False only when the phone is not paired yet — the
    caller then opens the full app so the user can finish setup.
    Does not start the HTTP server or a second full instance.
    """
    settings = Settings()
    ip = settings.get("phone_ip", "")
    if not ip:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "Zadachi",
            f"Номер {number} получен, но телефон ещё не сопряжён.\n"
            "Откроется Zadachi: совершите тестовый звонок с телефона "
            "или укажите IP телефона в «Настройках».", parent=root)
        root.destroy()
        return False

    db = Database()
    root = tk.Tk()
    root.title("Zadachi — звонок")
    root.geometry("380x140")
    root.attributes("-topmost", True)
    if sys.platform == "win32":
        icon = find_icon_path()
        if icon:
            try:
                root.iconbitmap(default=icon)
            except tk.TclError:
                pass
    status = tk.StringVar(value=f"Отправляю {number} на телефон…")
    ttk.Label(root, textvariable=status, wraplength=340,
              font=("Segoe UI", 11)).pack(padx=14, pady=(16, 8))
    ttk.Button(root, text="Закрыть", command=root.destroy).pack(pady=4)

    def worker():
        try:
            protocol.send_dial(ip, number, settings.get("token", ""))
            db.add_call(number, status="outgoing", note="Клик по номеру (tel:)")
            db.close()

            def done():
                status.set(f"✔ Номер {number} передан на телефон: откроется звонилка "
                           "или уведомление «Набрать номер»")
                root.after(2500, root.destroy)
            root.after(0, done)
        except Exception as exc:
            db.close()

            def fail():
                status.set(
                    f"✘ Телефон {ip} не ответил.\n"
                    "Проверьте: телефон в разрешённой Wi-Fi сети, Zadachi на нём "
                    f"активна, токены совпадают.\n({exc})")
            root.after(0, fail)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    number = protocol.parse_call_argument(argv)
    if number and run_call_request(number):
        return
    app = ZadachiApp(start_in_tray="--tray" in argv)
    app.mainloop()


if __name__ == "__main__":
    main()
