"""
Touch-friendly kiosk UI for the ManageIO RFID terminal (Tkinter).

The main window shows a large clock, optional startup checks on first launch, and an overlay for scan
feedback. RFID blocking reads run on a background thread; HTTP calls run in short worker threads;
all widget updates are marshalled back onto the Tk main thread via ``after``.
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

# Fixed kiosk resolution (7" panels are often 800x480).
DISPLAY_W, DISPLAY_H = 800, 480
PAD_X = 16
PAD_Y = 8
FONT_CLOCK = 50
FONT_DATE = 13
FONT_INSTRUCTION = 15
FONT_HEADER_TITLE = 13
FONT_HEADER_STATUS = 10
FONT_OVERLAY_TITLE = 22
FONT_OVERLAY_BODY = 13
FONT_BOOT_TITLE = 20
FONT_BOOT_STEP = 14
FONT_BOOT_DETAIL = 12
FONT_BUTTON = 11
OVERLAY_WRAP = 720
FOOTER_PAYOUTSIDE = 12
FOOTER_PADBOTTOM = 14

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
    """
    Instantiate the RFID stack used by the kiosk (real ``SimpleMFRC522`` or :class:`mock_rfid.MockSimpleMFRC522`).

    Returns:
        tuple: ``(reader, is_mock: bool, gpio_module | None)`` — GPIO module is only set on real hardware.
    """
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
    """Root Tk window: boot splash (optional) + idle clock UI + scan overlay + footer actions."""

    def __init__(self, defer_boot_sequence: bool = False):
        """
        Build the shell.

        Args:
            defer_boot_sequence: If ``True``, show the three-line boot checklist and only construct
                the reader/session after it passes; if ``False``, go straight to the operational UI
                (used when boot checks were already run headlessly).
        """
        super().__init__()

        self.title("Time clock")
        self.geometry(f"{DISPLAY_W}x{DISPLAY_H}")
        self.minsize(DISPLAY_W, DISPLAY_H)
        self.maxsize(DISPLAY_W, DISPLAY_H)
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
        """Lay out the pre-flight checklist labels before hardware and network are touched."""
        wrap = tk.Frame(self, bg=self.COLORS["bg"])
        wrap.pack(expand=True, fill="both", padx=PAD_X * 2, pady=PAD_Y * 3)
        self._boot_wrap = wrap

        tk.Label(
            wrap,
            text="Starting…",
            font=(FONT_FAMILY, FONT_BOOT_TITLE, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        ).pack(pady=(0, 4))

        self._boot_subtitle = tk.Label(
            wrap,
            text="A quick check before the clock appears.",
            font=(FONT_FAMILY, FONT_BOOT_DETAIL),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self._boot_subtitle.pack(pady=(0, 16))

        rows = [
            ("hardware", "RFID reader"),
            ("network", "Network"),
            ("server", "Time server"),
        ]
        for key, title in rows:
            row = tk.Frame(wrap, bg=self.COLORS["bg"])
            row.pack(fill="x", pady=6)
            tk.Label(
                row,
                text=title,
                font=(FONT_FAMILY, FONT_BOOT_STEP, "bold"),
                anchor="w",
                bg=self.COLORS["bg"],
                fg=self.COLORS["text"],
            ).pack(fill="x")
            lbl = tk.Label(
                row,
                text="Waiting…",
                font=(FONT_FAMILY, FONT_BOOT_DETAIL),
                anchor="w",
                bg=self.COLORS["bg"],
                fg=self.COLORS["subtext"],
            )
            lbl.pack(fill="x", padx=(14, 0))
            self._boot_detail_labels[key] = lbl

    def _kickoff_boot_sequence(self):
        """Start :func:`boot_sequence.run_boot_checks` on a daemon thread; funnel UI updates through ``after``."""
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
        """Apply one ``on_step`` notification from the boot thread (must run on the Tk main thread)."""
        if self._closing or not self.winfo_exists():
            return
        lbl = self._boot_detail_labels.get(phase)
        if lbl:
            if state == "running":
                lbl.config(text="Checking…", fg=self.COLORS["warning"])
            elif state == "skip":
                lbl.config(text=detail or "Not needed on this setup.", fg=self.COLORS["subtext"])
            elif state == "ok":
                lbl.config(text=detail or "OK", fg=self.COLORS["success"])
            elif state == "error":
                lbl.config(text=detail or "Something went wrong.", fg=self.COLORS["error"])
            else:
                lbl.config(text=detail or state, fg=self.COLORS["subtext"])
        if self._boot_subtitle:
            subtitle = {
                "hardware": "Reader and wiring",
                "network": "This device online",
                "server": "Reaching the time server",
            }.get(phase, "One moment…")
            self._boot_subtitle.config(text=subtitle, fg=self.COLORS["subtext"])

    def _boot_sequence_ok(self):
        """Brief success message, then tear down the splash and mount the real kiosk chrome."""
        if self._closing or not self.winfo_exists():
            return
        if self._boot_subtitle:
            self._boot_subtitle.config(
                text="All good — opening the clock.",
                fg=self.COLORS["success"],
            )
        self.after(350, self._finalize_boot_transition)

    def _finalize_boot_transition(self):
        """Destroy boot frames and call :meth:`_complete_initialization_after_boot`."""
        if self._closing or not self.winfo_exists():
            return
        if self._boot_wrap is not None:
            self._boot_wrap.destroy()
            self._boot_wrap = None
        self._complete_initialization_after_boot()

    def _boot_sequence_failed(self, err: BaseException):
        """Show the captured exception on the splash; operator closes the window manually."""
        if self._closing or not self.winfo_exists():
            return
        msg = str(err)
        code = getattr(err, "exit_code", None)
        if code is not None:
            msg = f"[{code}] {msg}"
        if self._boot_subtitle:
            self._boot_subtitle.config(
                text="Startup stopped — this needs a fix",
                fg=self.COLORS["error"],
            )
        tk.Label(
            self._boot_wrap,
            text=msg,
            font=(FONT_FAMILY, FONT_BOOT_DETAIL),
            wraplength=OVERLAY_WRAP,
            justify="left",
            bg=self.COLORS["bg"],
            fg=self.COLORS["error"],
        ).pack(pady=(16, 0), anchor="w")
        tk.Label(
            self._boot_wrap,
            text="Close this window after fixing the issue, then start the app again.",
            font=(FONT_FAMILY, FONT_BOOT_DETAIL - 1),
            wraplength=OVERLAY_WRAP,
            justify="left",
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        ).pack(pady=(8, 0), anchor="w")

    def _complete_initialization_after_boot(self):
        """Wire session + reader, build widgets, and spawn the RFID polling thread."""
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
        """Recurring timer: send heartbeat while authenticated; reschedules itself."""
        if not self.winfo_exists():
            return
        if self.api_session.is_approved:
            self.api_session.send_heartbeat()
        hb_ms = max(5000, int(config.HEARTBEAT_INTERVAL_SECONDS * 1000))
        self.after(hb_ms, self._schedule_heartbeat)

    def create_styles(self):
        """Configure ttk styles for large-footprint kiosk buttons."""
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure(
            "Kiosk.TButton",
            font=(FONT_FAMILY, FONT_BUTTON, "bold"),
            background=self.COLORS["card"],
            foreground=self.COLORS["text"],
            borderwidth=0,
            focuscolor="none",
            relief="flat",
            padding=(12, 10),
        )
        self.style.map(
            "Kiosk.TButton",
            background=[("active", self.COLORS["accent"])],
            foreground=[("active", "#ffffff")],
        )

    def build_ui_frames(self):
        """Create header/body/footer structure, clock labels, overlay region, and footer buttons."""
        self.header = tk.Frame(self, bg=self.COLORS["bg"])
        self.header.pack(side="top", fill="x", padx=PAD_X, pady=(PAD_Y, 4))

        hdr_inner = tk.Frame(self.header, bg=self.COLORS["bg"])
        hdr_inner.pack(fill="x")

        tk.Label(
            hdr_inner,
            text="Time clock",
            font=(FONT_FAMILY, FONT_HEADER_TITLE, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        ).pack(side="left")

        self.status_beacon = tk.Label(
            hdr_inner,
            text="● Connecting…",
            font=(FONT_FAMILY, FONT_HEADER_STATUS, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["warning"],
        )
        self.status_beacon.pack(side="right")

        self.body = tk.Frame(self, bg=self.COLORS["bg"])
        self.body.pack(expand=True, fill="both", padx=PAD_X, pady=0)

        self.rest_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.rest_frame.pack(expand=True, fill="both")

        self.lbl_time = tk.Label(
            self.rest_frame,
            text="12:00:00",
            font=(FONT_FAMILY, FONT_CLOCK, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        )
        self.lbl_time.pack(pady=(8, 0))

        self.lbl_date = tk.Label(
            self.rest_frame,
            text="Friday, 15. May 2026",
            font=(FONT_FAMILY, FONT_DATE),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_date.pack(pady=(2, 12))

        self.lbl_instruction = tk.Label(
            self.rest_frame,
            text="Hold your badge on the reader to clock in or out.",
            font=(FONT_FAMILY, FONT_INSTRUCTION),
            wraplength=OVERLAY_WRAP,
            justify="center",
            bg=self.COLORS["bg"],
            fg=self.COLORS["accent"],
        )
        self.lbl_instruction.pack(pady=(4, 8))

        self.overlay_frame = tk.Frame(self, bg=self.COLORS["bg"])
        self.overlay_inner = tk.Frame(self.overlay_frame, bg=self.COLORS["bg"])
        self.overlay_inner.pack(expand=True, fill="both", padx=PAD_X, pady=PAD_Y)

        self.lbl_overlay_title = tk.Label(
            self.overlay_inner,
            text="…",
            font=(FONT_FAMILY, FONT_OVERLAY_TITLE, "bold"),
            bg=self.COLORS["bg"],
            fg="#ffffff",
            wraplength=OVERLAY_WRAP,
            justify="center",
        )
        self.lbl_overlay_title.pack(pady=(24, 8))

        self.lbl_overlay_details = tk.Label(
            self.overlay_inner,
            text="",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
            wraplength=OVERLAY_WRAP,
            justify="center",
        )
        self.lbl_overlay_details.pack(pady=(0, 16))

        self.footer = tk.Frame(self, bg=self.COLORS["bg"])
        self.footer.pack(side="bottom", fill="x", padx=FOOTER_PAYOUTSIDE, pady=(4, FOOTER_PADBOTTOM))

        btn_pad = 6
        ttk.Button(
            self.footer,
            text="My status",
            style="Kiosk.TButton",
            command=lambda: self.action_button_clicked("CHECK_STATUS"),
        ).pack(side="left", expand=True, padx=(0, btn_pad), fill="x")

        ttk.Button(
            self.footer,
            text="Flextime",
            style="Kiosk.TButton",
            command=lambda: self.action_button_clicked("FLEXTIME"),
        ).pack(side="left", expand=True, padx=(0, btn_pad), fill="x")

        ttk.Button(
            self.footer,
            text="Holidays",
            style="Kiosk.TButton",
            command=lambda: self.action_button_clicked("HOLIDAY"),
        ).pack(side="left", expand=True, padx=(0, 0), fill="x")

        # Dev-only: click instruction to simulate a tap (still uploads to server)
        self.lbl_instruction.bind("<Button-1>", lambda e: self._on_card_uid("MOCK_CLICK_001"))

    def _poll_auth_beacon(self):
        """Toggle the small header LED text between online (green) and retrying (amber)."""
        if not self.winfo_exists():
            return
        if self.api_session.is_approved:
            self.status_beacon.config(
                text="● Online",
                fg=self.COLORS["success"],
            )
        else:
            self.status_beacon.config(
                text="● Connecting…",
                fg=self.COLORS["warning"],
            )
        self.after(2000, self._poll_auth_beacon)

    def _rfid_listen_loop(self):
        """Background loop: wait for tags, push UIDs to :meth:`_on_card_uid` on the UI thread."""
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
        """
        Begin an HTTP round-trip for the given UID (punch or query depending on ``active_action``).

        Serialised with ``_scan_ui_lock`` so rapid taps do not interleave requests.
        """
        if not self.winfo_exists():
            return
        if not self._scan_ui_lock.acquire(blocking=False):
            return

        pending_context = self.active_action

        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

        self.lbl_overlay_title.config(text="One moment…", fg=self.COLORS["accent"])
        self.lbl_overlay_details.config(
            text=f"We’re sending this to the server.\n\nBadge: {uid}",
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
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
        """Populate the overlay with success or failure copy, then auto-return to idle after a delay."""
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
                "CHECK_STATUS": "Your status",
                "FLEXTIME": "Flextime",
                "HOLIDAY": "Holidays & time off",
            }.get(pending_context, "Information")
            color = self.COLORS["accent"]
            details = terminal_msg or "Nothing else from the server right now."
        elif ok:
            if ev == "in":
                punch_line = "You are checked in."
                title_base = "Checked in"
            elif ev == "out":
                punch_line = "You are checked out."
                title_base = "Checked out"
            else:
                punch_line = "Your time was recorded."
                title_base = "Recorded"

            srv_t = (result or {}).get("server_time", "")
            loc_t = (result or {}).get("client_local_time", "")
            time_bits = ""
            if srv_t:
                time_bits = f"Server time: {srv_t}"
            if loc_t:
                time_bits = f"{time_bits}\nClock on device: {loc_t}" if time_bits else f"Clock on device: {loc_t}"

            headline = terminal_msg or punch_line
            color = self.COLORS["success"] if ev == "in" else (self.COLORS["error"] if ev == "out" else self.COLORS["success"])
            title = title_base
            details = f"{headline}\n\nBadge: {uid}"
            if time_bits:
                details = f"{details}\n{time_bits}"
            details = f"{details}\nTerminal: {config.DEVICE_ID}"
        else:
            title = "Couldn’t complete that"
            color = self.COLORS["error"]
            details = (
                f"We couldn’t confirm this with the server.\n\n"
                f"Badge: {uid}\n\n"
                f"Check network cables or Wi‑Fi, then try again. "
                f"If it keeps happening, check the device password (DEVICE_SECRET) in your .env file."
            )

        self.lbl_overlay_title.config(text=title, fg=color)
        self.lbl_overlay_details.config(
            text=details,
            fg="#ffffff",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
        )
        self.timeout_timer = self.after(4000, self.revert_to_rest)

    def action_button_clicked(self, action_type):
        """
        Footer buttons: switch to “wait for card” mode with a contextual overlay title.

        Args:
            action_type: ``CHECK_STATUS`` | ``FLEXTIME`` | ``HOLIDAY`` — forwarded to the query API.
        """
        self.current_state = "WAITING_CARD"
        self.active_action = action_type

        titles = {
            "CHECK_STATUS": "My status",
            "FLEXTIME": "Flextime",
            "HOLIDAY": "Holidays",
        }
        self.lbl_overlay_title.config(
            text=titles.get(action_type, "Next step"),
            fg=self.COLORS["accent"],
        )
        self.lbl_overlay_details.config(
            text=(
                "Hold your badge on the reader.\n\n"
                "This screen closes in about 10 seconds if no badge is read."
            ),
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
        )
        self.rest_frame.pack_forget()
        self.overlay_frame.pack(expand=True, fill="both")

        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
        self.timeout_timer = self.after(10000, self.revert_to_rest)

    def revert_to_rest(self):
        """Hide overlay, show the clock screen again, and clear timers."""
        self.current_state = "REST"
        self.active_action = None
        self.overlay_frame.pack_forget()
        self.rest_frame.pack(expand=True, fill="both")
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

    def update_clock(self):
        """Refresh date/time labels once per second while ``clock_active`` is true."""
        now = datetime.datetime.now()
        self.lbl_time.config(text=now.strftime("%H:%M:%S"))
        self.lbl_date.config(text=now.strftime("%A, %d. %B %Y"))
        if self.clock_active:
            self.after(1000, self.update_clock)

    def _on_close(self):
        """Stop threads cleanly, release GPIO on real hardware, and destroy the Tk root."""
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
    """
    Entrypoint: validate ``.env``, optionally run boot checks, then open :class:`KioskApp`.

    Args:
        skip_boot_checks: Passed through to skip both inline and environment-driven boot sequences.
    """
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
    # Allow ``python gui_poc.py`` with optional ``--no-boot-check``.
    import argparse

    p = argparse.ArgumentParser(description="RFID kiosk GUI")
    p.add_argument(
        "--no-boot-check",
        action="store_true",
        help="Skip startup RFID/network/server checks",
    )
    a = p.parse_args()
    run_kiosk(skip_boot_checks=a.no_boot_check)
