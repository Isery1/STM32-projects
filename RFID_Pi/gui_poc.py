"""
RFID kiosk terminal: Tkinter UI + MFRC522 (or mock) + server upload via AuthenticatedSession.
RFID polling runs on a background thread; HTTP runs in a worker thread; UI updates on the main thread.
"""
import sys
import os
import time
import datetime
import logging
import threading
import tkinter as tk
from tkinter import ttk

import config
from auth import AuthenticatedSession

FONT_FAMILY = "Helvetica"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("app_debug.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _create_reader():
    """Return (reader, is_mock, gpio_module_or_none). Matches headless main.py behavior."""
    if os.getenv("USE_MOCK_RFID", "False").lower() in ("true", "1", "yes"):
        from mock_rfid import MockSimpleMFRC522 as _Reader

        logger.warning("USE_MOCK_RFID: using mock reader.")
        return _Reader(), True, None
    try:
        import RPi.GPIO as GPIO
        from mfrc522 import SimpleMFRC522

        logger.info("Loaded RPi.GPIO and SimpleMFRC522.")
        return SimpleMFRC522(), False, GPIO
    except ImportError as e:
        from mock_rfid import MockSimpleMFRC522 as _Reader

        logger.warning("Hardware libraries unavailable; using mock (%s).", e)
        return _Reader(), True, None


class KioskApp(tk.Tk):
    def __init__(self, defer_boot_sequence: bool = False):
        super().__init__()

        self.title("RFID Time Terminal Kiosk")
        self.geometry("800x480")
        self.resizable(False, False)
        self.configure(bg="#121826")
        self._closing = False
        self.defer_boot_sequence = defer_boot_sequence

        self.COLORS = {
            "bg": "#121826",
            "card": "#1f2937",
            "text": "#f3f4f6",
            "accent": "#3b82f6",
            "success": "#10b981",
            "warning": "#f59e0b",
            "subtext": "#9ca3af",
            "error": "#ef4444",
        }

        self.create_styles()

        if defer_boot_sequence:
            self._boot_detail_labels = {}
            self._boot_wrap = None
            self._boot_subtitle = None
            self._build_boot_screen()
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(200, self._kickoff_boot_sequence)
            return

        self._complete_initialization_after_boot()

    def _build_boot_screen(self):
        wrap = tk.Frame(self, bg=self.COLORS["bg"])
        wrap.pack(expand=True, fill="both", padx=40, pady=36)
        self._boot_wrap = wrap

        tk.Label(
            wrap,
            text="Starting system",
            font=(FONT_FAMILY, 26, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        ).pack(pady=(0, 8))

        self._boot_subtitle = tk.Label(
            wrap,
            text="Running startup checks…",
            font=(FONT_FAMILY, 14),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self._boot_subtitle.pack(pady=(0, 20))

        rows = [
            ("hardware", "1. RFID reader & GPIO"),
            ("network", "2. Network (LAN & Wi‑Fi)"),
            ("server", "3. Time server (auth endpoint)"),
        ]
        for key, title in rows:
            row = tk.Frame(wrap, bg=self.COLORS["bg"])
            row.pack(fill="x", pady=10)
            tk.Label(
                row,
                text=title,
                font=(FONT_FAMILY, 15, "bold"),
                anchor="w",
                bg=self.COLORS["bg"],
                fg=self.COLORS["text"],
            ).pack(fill="x")
            lbl = tk.Label(
                row,
                text="…",
                font=(FONT_FAMILY, 13),
                anchor="w",
                bg=self.COLORS["bg"],
                fg=self.COLORS["subtext"],
            )
            lbl.pack(fill="x", padx=(18, 0))
            self._boot_detail_labels[key] = lbl

    def _kickoff_boot_sequence(self):
        if self._closing:
            return

        def worker():
            import boot_sequence  # Local import: optional heavy deps

            def on_step(phase: str, state: str, detail: str = ""):
                self.after(0, lambda p=phase, s=state, d=detail: self._apply_boot_step(p, s, d))

            try:
                boot_sequence.run_boot_checks(on_step=on_step, cli_exit=False)
            except boot_sequence.BootCheckFailed as e:
                self.after(0, lambda err=e: self._boot_sequence_failed(err))
            except Exception as e:
                logger.exception("Boot sequence error")
                self.after(0, lambda err=e: self._boot_sequence_failed(err))
            else:
                self.after(0, self._boot_sequence_ok)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_boot_step(self, phase: str, state: str, detail: str = ""):
        if self._closing or not self.winfo_exists():
            return
        lbl = self._boot_detail_labels.get(phase)
        if lbl:
            if state == "running":
                lbl.config(text="In progress…", fg=self.COLORS["warning"])
            elif state == "skip":
                lbl.config(text=detail or "Skipped.", fg=self.COLORS["subtext"])
            elif state == "ok":
                lbl.config(text=detail or "OK", fg=self.COLORS["success"])
            elif state == "error":
                lbl.config(text=detail or "Failed", fg=self.COLORS["error"])
            else:
                lbl.config(text=detail or state, fg=self.COLORS["subtext"])
        if self._boot_subtitle:
            subtitle = {
                "hardware": "Checking RFID hardware…",
                "network": "Checking LAN / Wi‑Fi and internet…",
                "server": "Checking time server…",
            }.get(phase, "Startup…")
            self._boot_subtitle.config(text=subtitle, fg=self.COLORS["subtext"])

    def _boot_sequence_ok(self):
        if self._closing or not self.winfo_exists():
            return
        if self._boot_subtitle:
            self._boot_subtitle.config(
                text="All checks passed — loading terminal…",
                fg=self.COLORS["success"],
            )
        self.after(350, self._finalize_boot_transition)

    def _finalize_boot_transition(self):
        if self._closing or not self.winfo_exists():
            return
        if self._boot_wrap is not None:
            self._boot_wrap.destroy()
            self._boot_wrap = None
        self._complete_initialization_after_boot()

    def _boot_sequence_failed(self, err: BaseException):
        if self._closing or not self.winfo_exists():
            return
        msg = str(err)
        code = getattr(err, "exit_code", None)
        if code is not None:
            msg = f"[{code}] {msg}"
        if self._boot_subtitle:
            self._boot_subtitle.config(
                text="Startup failed — fix the issue and restart",
                fg=self.COLORS["error"],
            )
        tk.Label(
            self._boot_wrap,
            text=msg,
            font=(FONT_FAMILY, 12),
            wraplength=680,
            justify="left",
            bg=self.COLORS["bg"],
            fg=self.COLORS["error"],
        ).pack(pady=(24, 0))
        tk.Label(
            self._boot_wrap,
            text="Close this window, fix the problem, then start the app again.",
            font=(FONT_FAMILY, 11),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        ).pack(pady=(8, 0))

    def _complete_initialization_after_boot(self):
        self.api_session = AuthenticatedSession()
        self.reader, self._is_mock_hw, self._gpio = _create_reader()

        self.current_state = "REST"
        self.active_action = None
        self.timeout_timer = None
        self.rfid_listener_active = True
        self._scan_ui_lock = threading.Lock()

        self.build_ui_frames()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.clock_active = True
        self.update_clock()

        self.after(2000, self._poll_auth_beacon)

        hb_ms = max(5000, int(config.HEARTBEAT_INTERVAL_SECONDS * 1000))
        self.after(hb_ms, self._schedule_heartbeat)

        self._rfid_thread = threading.Thread(target=self._rfid_listen_loop, daemon=True)
        self._rfid_thread.start()

    def _schedule_heartbeat(self):
        if not self.winfo_exists():
            return
        if self.api_session.is_approved:
            self.api_session.send_heartbeat()
        hb_ms = max(5000, int(config.HEARTBEAT_INTERVAL_SECONDS * 1000))
        self.after(hb_ms, self._schedule_heartbeat)

    def create_styles(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure(
            "Kiosk.TButton",
            font=(FONT_FAMILY, 13, "bold"),
            background=self.COLORS["card"],
            foreground=self.COLORS["text"],
            borderwidth=0,
            focuscolor="none",
            relief="flat",
            padding=15,
        )
        self.style.map(
            "Kiosk.TButton",
            background=[("active", self.COLORS["accent"])],
            foreground=[("active", "#ffffff")],
        )

    def build_ui_frames(self):
        self.header = tk.Frame(self, bg=self.COLORS["bg"], height=60)
        self.header.pack(side="top", fill="x", padx=20, pady=10)

        self.status_beacon = tk.Label(
            self.header,
            text="● CONNECTING…",
            font=(FONT_FAMILY, 9, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["warning"],
        )
        self.status_beacon.pack(side="left")

        self.body = tk.Frame(self, bg=self.COLORS["bg"])
        self.body.pack(expand=True, fill="both", padx=30)

        self.rest_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.rest_frame.pack(expand=True, fill="both")

        self.lbl_time = tk.Label(
            self.rest_frame,
            text="12:00:00",
            font=(FONT_FAMILY, 64, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        )
        self.lbl_time.pack(pady=(20, 0))

        self.lbl_date = tk.Label(
            self.rest_frame,
            text="Friday, 15. May 2026",
            font=(FONT_FAMILY, 16),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_date.pack(pady=(0, 30))

        self.lbl_instruction = tk.Label(
            self.rest_frame,
            text="👋 Tap Card to Clock In / Out",
            font=(FONT_FAMILY, 18, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["accent"],
        )
        self.lbl_instruction.pack(pady=10)

        self.overlay_frame = tk.Frame(self, bg=self.COLORS["bg"])

        self.lbl_overlay_title = tk.Label(
            self.overlay_frame,
            text="Waiting...",
            font=(FONT_FAMILY, 28, "bold"),
            bg=self.COLORS["bg"],
            fg="#ffffff",
        )
        self.lbl_overlay_title.pack(expand=True, pady=(40, 10))

        self.lbl_overlay_details = tk.Label(
            self.overlay_frame,
            text="Please scan your badge now.",
            font=(FONT_FAMILY, 16),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_overlay_details.pack(expand=True, pady=(0, 40))

        self.footer = tk.Frame(self, bg=self.COLORS["bg"], height=100)
        self.footer.pack(side="bottom", fill="x", padx=30, pady=(0, 30))

        ttk.Button(
            self.footer,
            text="🕒 State Info",
            style="Kiosk.TButton",
            command=lambda: self.action_button_clicked("CHECK_STATUS"),
        ).pack(side="left", expand=True, padx=10, fill="x")

        ttk.Button(
            self.footer,
            text="⚡ Flextime",
            style="Kiosk.TButton",
            command=lambda: self.action_button_clicked("FLEXTIME"),
        ).pack(side="left", expand=True, padx=10, fill="x")

        ttk.Button(
            self.footer,
            text="🌴 Holidays",
            style="Kiosk.TButton",
            command=lambda: self.action_button_clicked("HOLIDAY"),
        ).pack(side="left", expand=True, padx=10, fill="x")

        # Dev-only: click instruction to simulate a tap (still uploads to server)
        self.lbl_instruction.bind("<Button-1>", lambda e: self._on_card_uid("MOCK_CLICK_001"))

    def _poll_auth_beacon(self):
        if not self.winfo_exists():
            return
        if self.api_session.is_approved:
            self.status_beacon.config(
                text="● SERVER ONLINE",
                fg=self.COLORS["success"],
            )
        else:
            self.status_beacon.config(
                text="● SERVER OFFLINE / AUTH…",
                fg=self.COLORS["warning"],
            )
        self.after(2000, self._poll_auth_beacon)

    def _rfid_listen_loop(self):
        logger.info("RFID listener thread started.")
        while self.rfid_listener_active:
            while self.rfid_listener_active and not self.api_session.is_approved:
                self.api_session.authenticate()
                if not self.rfid_listener_active:
                    break
                if not self.api_session.is_approved:
                    logger.warning("Not authenticated; retry in 5s…")
                    time.sleep(5)

            if not self.rfid_listener_active:
                break

            try:
                card_id = self.reader.read_id()
            except KeyboardInterrupt:
                break
            except Exception as ex:
                logger.error("RFID read error: %s", ex)
                time.sleep(2.0)
                continue

            if card_id and self.rfid_listener_active:
                uid = str(card_id).strip()
                logger.info("*** RFID TAG SCANNED -> ID: %s ***", uid)
                self.after(0, lambda u=uid: self._on_card_uid(u))
                time.sleep(config.SCAN_COOLDOWN_SECONDS)

    def _on_card_uid(self, uid):
        if not self.winfo_exists():
            return
        if not self._scan_ui_lock.acquire(blocking=False):
            return

        pending_context = self.active_action

        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

        self.lbl_overlay_title.config(text="⏳ Sending…", fg=self.COLORS["accent"])
        self.lbl_overlay_details.config(
            text=f"UID: {uid}\nPosting to server…",
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, 16),
        )
        self.rest_frame.pack_forget()
        self.overlay_frame.pack(expand=True, fill="both")
        self.current_state = "UPLOADING"

        def worker():
            if pending_context == "CHECK_STATUS":
                result = self.api_session.send_status_query(uid, "status")
            elif pending_context == "FLEXTIME":
                result = self.api_session.send_status_query(uid, "flextime")
            elif pending_context == "HOLIDAY":
                result = self.api_session.send_status_query(uid, "holiday")
            else:
                local_ts = datetime.datetime.now().isoformat(timespec="seconds")
                result = self.api_session.send_scan(uid, client_local_time_iso=local_ts)
            self.after(0, lambda: self._after_scan_upload(uid, result, pending_context))

        threading.Thread(target=worker, daemon=True).start()

    def _after_scan_upload(self, uid, result, pending_context):
        try:
            self._scan_ui_lock.release()
        except RuntimeError:
            pass

        if not self.winfo_exists():
            return

        self.current_state = "CONFIRMATION"
        self.active_action = None

        ok = bool(result and result.get("success"))
        ev = (result or {}).get("event", "") if ok else ""
        terminal_msg = (result or {}).get("terminal_message", "") if ok else ""

        if ok and pending_context:
            title = {
                "CHECK_STATUS": "📋 Your status",
                "FLEXTIME": "⚡ Flextime (preview)",
                "HOLIDAY": "🌴 Holidays (preview)",
            }.get(pending_context, "Info")
            color = self.COLORS["accent"]
            details = terminal_msg or "No message from server."
        elif ok:
            if ev == "in":
                punch_line = "CHECK IN recorded."
                title_base = "🟢 CHECK IN"
            elif ev == "out":
                punch_line = "CHECK OUT recorded."
                title_base = "🔴 CHECK OUT"
            else:
                punch_line = "Punch saved."
                title_base = "✓ PUNCH SAVED"

            srv_t = (result or {}).get("server_time", "")
            loc_t = (result or {}).get("client_local_time", "")
            time_bits = f"Server: {srv_t}" if srv_t else ""
            if loc_t:
                time_bits += f"\nTerminal clock: {loc_t}" if time_bits else f"Terminal: {loc_t}"

            primary = terminal_msg or f"{title_base}\n{punch_line}"
            color = self.COLORS["success"] if ev == "in" else (self.COLORS["error"] if ev == "out" else self.COLORS["success"])
            title = title_base
            details = f"{primary}\n\nUID: {uid}\n{time_bits}\nDevice: {config.DEVICE_ID}"
        else:
            title = "✗ REQUEST FAILED"
            color = self.COLORS["error"]
            details = (
                f"UID: {uid}\nCould not reach server or request rejected.\n"
                f"Check network and DEVICE_SECRET."
            )

        self.lbl_overlay_title.config(text=title, fg=color)
        self.lbl_overlay_details.config(
            text=details,
            fg="#ffffff",
            font=(FONT_FAMILY, 16),
        )
        self.timeout_timer = self.after(4000, self.revert_to_rest)

    def action_button_clicked(self, action_type):
        self.current_state = "WAITING_CARD"
        self.active_action = action_type

        titles = {
            "CHECK_STATUS": "🔍 Current Account State",
            "FLEXTIME": "⚡ View Flextime Balance",
            "HOLIDAY": "🌴 Query Vacation Balances",
        }
        self.lbl_overlay_title.config(
            text=titles.get(action_type, "Checking…"),
            fg=self.COLORS["accent"],
        )
        self.lbl_overlay_details.config(
            text="TAP YOUR BADGE ON THE READER\n(Cancels automatically in 10s)",
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, 16),
        )
        self.rest_frame.pack_forget()
        self.overlay_frame.pack(expand=True, fill="both")

        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
        self.timeout_timer = self.after(10000, self.revert_to_rest)

    def revert_to_rest(self):
        self.current_state = "REST"
        self.active_action = None
        self.overlay_frame.pack_forget()
        self.rest_frame.pack(expand=True, fill="both")
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

    def update_clock(self):
        now = datetime.datetime.now()
        self.lbl_time.config(text=now.strftime("%H:%M:%S"))
        self.lbl_date.config(text=now.strftime("%A, %d. %B %Y"))
        if self.clock_active:
            self.after(1000, self.update_clock)

    def _on_close(self):
        logger.info("Shutting down kiosk…")
        self._closing = True
        self.clock_active = False
        self.rfid_listener_active = False
        thr = getattr(self, "_rfid_thread", None)
        if thr is not None and thr.is_alive():
            thr.join(timeout=3.0)
        if not getattr(self, "_is_mock_hw", True) and getattr(self, "_gpio", None) is not None:
            try:
                self._gpio.cleanup()
                logger.info("GPIO cleaned up.")
            except Exception as e:
                logger.error("GPIO cleanup failed: %s", e)
        self.destroy()


def run_kiosk(skip_boot_checks: bool = False):
    try:
        config.validate_config()
    except ValueError as err:
        logger.critical("Configuration error: %s", err)
        sys.exit(1)

    from boot_sequence import skip_boot_checks_requested

    defer_boot = not skip_boot_checks and not skip_boot_checks_requested()
    if defer_boot:
        app = KioskApp(defer_boot_sequence=True)
    else:
        if not skip_boot_checks:
            from boot_sequence import run_boot_checks

            run_boot_checks()
        app = KioskApp(defer_boot_sequence=False)
    app.mainloop()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="RFID kiosk GUI")
    p.add_argument(
        "--no-boot-check",
        action="store_true",
        help="Skip startup RFID/network/server checks",
    )
    a = p.parse_args()
    run_kiosk(skip_boot_checks=a.no_boot_check)
