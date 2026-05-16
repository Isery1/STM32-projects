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
import json
import logging
import threading
import tkinter as tk
from tkinter import ttk

import config
from auth import AuthenticatedSession
from offline_queue import OfflineScanQueue, local_now_iso

FONT_FAMILY = os.getenv("KIOSK_FONT_FAMILY", "Geist")

# Fixed kiosk resolution (7" panels are often 800x480).
DISPLAY_W, DISPLAY_H = 800, 480
PAD_X = 12
PAD_Y = 6
FONT_CLOCK = 58
FONT_DATE = 13
FONT_INSTRUCTION = 15
FONT_HEADER_TITLE = 13
FONT_HEADER_STATUS = 10
FONT_OVERLAY_TITLE = 22
FONT_OVERLAY_BODY = 12
FONT_BOOT_TITLE = 20
FONT_BOOT_STEP = 14
FONT_BOOT_DETAIL = 12
FONT_BUTTON = 11
OVERLAY_WRAP = 720
FOOTER_PAYOUTSIDE = 12
FOOTER_PADBOTTOM = 10
BUTTON_RADIUS = 10
IDLE_RETURN_MS = 60_000
SIDEBAR_W = 82

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


class RoundedButton(tk.Canvas):
    """Canvas-backed touch button with rounded corners to match the web dashboard style."""

    def __init__(
        self,
        parent,
        text: str,
        command,
        *,
        width: int,
        height: int,
        bg: str,
        fg: str,
        active_bg: str,
        border: str,
        font,
    ):
        super().__init__(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0, bd=0)
        self.command = command
        self.normal_bg = bg
        self.active_bg = active_bg
        self.fg = fg
        self.border = border
        self.radius = min(BUTTON_RADIUS, height // 2)
        self.font = font
        self.text = text
        self._draw(bg)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _rounded_rect(self, fill: str):
        w = int(self["width"])
        h = int(self["height"])
        r = self.radius
        self.create_rectangle(r, 1, w - r, h - 1, fill=fill, outline=fill)
        self.create_rectangle(1, r, w - 1, h - r, fill=fill, outline=fill)
        self.create_arc(1, 1, 2 * r, 2 * r, start=90, extent=90, fill=fill, outline=fill)
        self.create_arc(w - 2 * r, 1, w - 1, 2 * r, start=0, extent=90, fill=fill, outline=fill)
        self.create_arc(w - 2 * r, h - 2 * r, w - 1, h - 1, start=270, extent=90, fill=fill, outline=fill)
        self.create_arc(1, h - 2 * r, 2 * r, h - 1, start=180, extent=90, fill=fill, outline=fill)
        self.create_line(r, 1, w - r, 1, fill=self.border)
        self.create_line(w - 1, r, w - 1, h - r, fill=self.border)
        self.create_line(w - r, h - 1, r, h - 1, fill=self.border)
        self.create_line(1, h - r, 1, r, fill=self.border)

    def _draw(self, fill: str):
        self.delete("all")
        self._rounded_rect(fill)
        self.create_text(
            int(self["width"]) // 2,
            int(self["height"]) // 2,
            text=self.text,
            fill=self.fg,
            font=self.font,
        )

    def _press(self, _event):
        self._draw(self.active_bg)
        self.after(90, self.command)

    def _release(self, _event):
        self._draw(self.normal_bg)


class RoundedPanel(tk.Canvas):
    """Rounded panel with an embedded frame for child widgets."""

    def __init__(self, parent, *, bg: str, border: str, radius: int = 18, width: int = 100, height: int = 100):
        super().__init__(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0, bd=0)
        self.fill = bg
        self.border = border
        self.radius = radius
        self.inner = tk.Frame(self, bg=bg)
        self.window_id = self.create_window(0, 0, anchor="nw", window=self.inner)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        self.delete("panel")
        w = max(2, self.winfo_width())
        h = max(2, self.winfo_height())
        r = min(self.radius, w // 2, h // 2)
        self.create_rectangle(r, 1, w - r, h - 1, fill=self.fill, outline=self.fill, tags="panel")
        self.create_rectangle(1, r, w - 1, h - r, fill=self.fill, outline=self.fill, tags="panel")
        self.create_arc(1, 1, 2 * r, 2 * r, start=90, extent=90, fill=self.fill, outline=self.fill, tags="panel")
        self.create_arc(w - 2 * r, 1, w - 1, 2 * r, start=0, extent=90, fill=self.fill, outline=self.fill, tags="panel")
        self.create_arc(w - 2 * r, h - 2 * r, w - 1, h - 1, start=270, extent=90, fill=self.fill, outline=self.fill, tags="panel")
        self.create_arc(1, h - 2 * r, 2 * r, h - 1, start=180, extent=90, fill=self.fill, outline=self.fill, tags="panel")
        self.create_line(r, 1, w - r, 1, fill=self.border, tags="panel")
        self.create_line(w - 1, r, w - 1, h - r, fill=self.border, tags="panel")
        self.create_line(w - r, h - 1, r, h - 1, fill=self.border, tags="panel")
        self.create_line(1, h - r, 1, r, fill=self.border, tags="panel")
        self.tag_lower("panel")
        inset = max(8, r // 2)
        self.coords(self.window_id, inset, inset)
        self.itemconfigure(self.window_id, width=max(1, w - (inset * 2)), height=max(1, h - (inset * 2)))


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
        self.configure(bg="#0a0f1d")
        if config.KIOSK_FULLSCREEN:
            self.attributes("-fullscreen", True)
            self.attributes("-topmost", True)
            self.bind("<Control-q>", lambda _event: self._on_close())
        self._closing = False
        self.defer_boot_sequence = defer_boot_sequence

        self.COLORS = {
            "bg": "#0b1326",
            "sidebar": "#060e20",
            "surface": "#0b1326",
            "card": "#131b2e",
            "card_2": "#202a42",
            "border": "#31394d",
            "text": "#f8fafc",
            "accent": "#7c3aed",
            "accent_2": "#8b5cf6",
            "success": "#10b981",
            "warning": "#fbbf24",
            "subtext": "#94a3b8",
            "muted": "#64748b",
            "error": "#fb7185",
            "button": "#7c3aed",
            "button_active": "#8b5cf6",
            "button_alt": "#1f2937",
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
            ("time", "Clock time"),
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
                "time": "Checking clock time",
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
        self.scan_queue = OfflineScanQueue()
        self.reader, self._is_mock_hw, self._gpio = _create_reader()

        self.current_state = "REST"
        self.active_action = None
        self.timeout_timer = None
        self.admin_frame = None
        self.admin_summary_label = None
        self.user_panel_frame = None
        self.nav_items = {}
        self.rfid_listener_active = True
        self._scan_ui_lock = threading.Lock()
        self._sync_lock = threading.Lock()

        self.build_ui_frames()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.clock_active = True
        self.update_clock()

        self.after(2000, self._poll_auth_beacon)

        hb_ms = max(5000, int(config.HEARTBEAT_INTERVAL_SECONDS * 1000))
        self.after(1000, self._sync_pending_async)
        self.after(hb_ms, self._schedule_heartbeat)

        self._rfid_thread = threading.Thread(target=self._rfid_listen_loop, daemon=True)
        self._rfid_thread.start()

    def _schedule_heartbeat(self):
        """Recurring timer: send heartbeat and retry queued scans; reschedules itself."""
        if not self.winfo_exists():
            return
        self._sync_pending_async(send_heartbeat=True)
        hb_ms = max(5000, int(config.HEARTBEAT_INTERVAL_SECONDS * 1000))
        self.after(hb_ms, self._schedule_heartbeat)

    def _sync_pending_async(self, send_heartbeat: bool = False):
        """Run heartbeat/queue sync on a worker so the Tk thread stays responsive."""
        if self._closing or not self.winfo_exists():
            return
        if not self._sync_lock.acquire(blocking=False):
            return

        def worker():
            summary = {"uploaded": 0, "failed": 0, "pending": self.scan_queue.pending_count(), "last_error": ""}
            try:
                if send_heartbeat:
                    self.api_session.send_heartbeat()
                summary = self.scan_queue.sync_pending(self.api_session)
            finally:
                try:
                    self._sync_lock.release()
                except RuntimeError:
                    pass
            self.after(0, lambda s=summary: self._apply_sync_summary(s))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_sync_summary(self, summary):
        """Refresh small status copy after a background sync attempt."""
        if self._closing or not self.winfo_exists():
            return
        self._update_connection_copy(summary.get("pending", self.scan_queue.pending_count()))

    def create_styles(self):
        """Configure ttk styles for large-footprint kiosk buttons."""
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure(
            "Kiosk.TButton",
            font=(FONT_FAMILY, FONT_BUTTON, "bold"),
            background=self.COLORS["button"],
            foreground=self.COLORS["text"],
            borderwidth=1,
            focuscolor="none",
            relief="flat",
            padding=(12, 10),
        )
        self.style.map(
            "Kiosk.TButton",
            background=[("active", self.COLORS["button_active"]), ("pressed", self.COLORS["accent_2"])],
            foreground=[("active", "#ffffff")],
            relief=[("pressed", "sunken"), ("!pressed", "flat")],
        )
        self.style.configure(
            "Admin.TButton",
            font=(FONT_FAMILY, FONT_BUTTON, "bold"),
            background=self.COLORS["button_alt"],
            foreground=self.COLORS["subtext"],
            borderwidth=1,
            focuscolor="none",
            relief="flat",
            padding=(12, 10),
        )
        self.style.map(
            "Admin.TButton",
            background=[("active", self.COLORS["card_2"]), ("pressed", self.COLORS["accent_2"])],
            foreground=[("active", "#ffffff")],
        )

    def build_ui_frames(self):
        """Create header/body/footer structure, clock labels, overlay region, and footer buttons."""
        self.sidebar = tk.Frame(self, bg=self.COLORS["sidebar"], width=SIDEBAR_W)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self._add_nav_item("home", "⌂", "Home", self.revert_to_rest, active=True)
        self._add_nav_item("user", "♙", "User", self._open_user_panel)
        self._add_nav_item("admin", "🛡", "Admin", lambda: self.action_button_clicked("ADMIN"))
        tk.Frame(self.sidebar, bg=self.COLORS["sidebar"]).pack(expand=True, fill="both")
        self._add_nav_item("settings", "⚙", "Settings", self._open_settings_info)

        self.main_area = tk.Frame(self, bg=self.COLORS["bg"])
        self.main_area.pack(side="right", fill="both", expand=True)

        self.header = tk.Frame(self.main_area, bg=self.COLORS["surface"], highlightthickness=1, highlightbackground="#182036")
        self.header.pack(side="top", fill="x")

        hdr_inner = tk.Frame(self.header, bg=self.COLORS["surface"])
        hdr_inner.pack(fill="x", padx=18, pady=8)

        tk.Label(
            hdr_inner,
            text="manageIO",
            font=(FONT_FAMILY, FONT_HEADER_TITLE, "bold"),
            bg=self.COLORS["surface"],
            fg="#d8b4fe",
        ).pack(side="left")

        status_wrap = tk.Frame(hdr_inner, bg="#131b2e", highlightthickness=1, highlightbackground=self.COLORS["border"])
        status_wrap.pack(side="right")
        self.status_beacon = tk.Label(
            status_wrap,
            text="● ONLINE",
            font=(FONT_FAMILY, FONT_HEADER_STATUS, "bold"),
            bg="#131b2e",
            fg=self.COLORS["success"],
            padx=10,
            pady=3,
        )
        self.status_beacon.pack()

        self.body = tk.Frame(self.main_area, bg=self.COLORS["bg"])
        self.body.pack(side="top", expand=True, fill="both", padx=20, pady=(12, 12))

        self.rest_frame = tk.Frame(self.body, bg=self.COLORS["surface"])
        self.rest_frame.pack(expand=True, fill="both")

        self.clock_frame = tk.Frame(self.rest_frame, bg=self.COLORS["surface"])
        self.clock_frame.pack(fill="x", expand=True, pady=(48, 10))

        self.lbl_time = tk.Label(
            self.clock_frame,
            text="12:00:00",
            font=(FONT_FAMILY, FONT_CLOCK),
            bg=self.COLORS["surface"],
            fg="#d8b4fe",
        )
        self.lbl_time.pack()

        self.lbl_date = tk.Label(
            self.clock_frame,
            text="SATURDAY, 16. MAY 2026",
            font=(FONT_FAMILY, FONT_DATE),
            bg=self.COLORS["surface"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_date.pack(pady=(2, 0))

        self.action_row = tk.Frame(self.rest_frame, bg=self.COLORS["surface"])
        self.action_row.pack(side="bottom", fill="x", pady=(0, 26))

        self.prompt_panel = RoundedPanel(
            self.action_row,
            bg=self.COLORS["card"],
            border=self.COLORS["border"],
            width=300,
            height=78,
        )
        self.prompt_panel.pack(side="left", padx=(0, 14))
        self.prompt_card = self.prompt_panel.inner

        badge_row = tk.Frame(self.prompt_card, bg=self.COLORS["card"])
        badge_row.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(
            badge_row,
            text="▣",
            font=(FONT_FAMILY, 17),
            bg=self.COLORS["card_2"],
            fg="#d8b4fe",
            width=3,
            height=2,
        ).pack(side="left", padx=(0, 10))

        badge_text = tk.Frame(badge_row, bg=self.COLORS["card"])
        badge_text.pack(side="left", fill="both", expand=True)

        self.lbl_instruction = tk.Label(
            badge_text,
            text="Tap badge to clock in/out",
            font=(FONT_FAMILY, FONT_INSTRUCTION - 2),
            wraplength=220,
            justify="left",
            anchor="w",
            bg=self.COLORS["card"],
            fg=self.COLORS["text"],
        )
        self.lbl_instruction.pack(fill="x", pady=(6, 0))

        self.lbl_queue_status = tk.Label(
            badge_text,
            text="Ready to record your time.",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY - 2),
            wraplength=230,
            justify="left",
            anchor="w",
            bg=self.COLORS["card"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_queue_status.pack(fill="x", pady=(2, 0))

        RoundedButton(
            self.action_row,
            "ⓘ  Check my current status",
            self._check_status_clicked,
            width=315,
            height=78,
            bg=self.COLORS["accent"],
            fg="#ffffff",
            active_bg=self.COLORS["accent_2"],
            border="#8b5cf6",
            font=(FONT_FAMILY, FONT_BUTTON + 1),
        ).pack(side="left")

        self.overlay_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.overlay_panel = RoundedPanel(
            self.overlay_frame,
            bg=self.COLORS["surface"],
            border=self.COLORS["card_2"],
            radius=18,
        )
        self.overlay_panel.pack(expand=True, fill="both", padx=PAD_X, pady=PAD_Y * 2)
        self.overlay_inner = self.overlay_panel.inner

        self.lbl_overlay_title = tk.Label(
            self.overlay_inner,
            text="…",
            font=(FONT_FAMILY, FONT_OVERLAY_TITLE, "bold"),
            bg=self.COLORS["surface"],
            fg="#ffffff",
            wraplength=OVERLAY_WRAP,
            justify="center",
        )
        self.lbl_overlay_title.pack(pady=(18, 8))

        self.lbl_overlay_details = tk.Label(
            self.overlay_inner,
            text="",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
            bg=self.COLORS["surface"],
            fg=self.COLORS["subtext"],
            wraplength=OVERLAY_WRAP,
            justify="center",
        )
        self.lbl_overlay_details.pack(pady=(0, 12))

        self.overlay_return = RoundedButton(
            self.overlay_inner,
            "Return",
            self.revert_to_rest,
            width=260,
            height=44,
            bg=self.COLORS["button_alt"],
            fg=self.COLORS["subtext"],
            active_bg=self.COLORS["card_2"],
            border=self.COLORS["border"],
            font=(FONT_FAMILY, FONT_BUTTON + 1, "bold"),
        )
        self.overlay_return.pack(pady=(6, 0))

        self.footer = tk.Frame(self.main_area, bg=self.COLORS["bg"])

        # Dev-only: click instruction to simulate a tap (still uploads to server)
        self.lbl_instruction.bind("<Button-1>", lambda e: self._on_card_uid("MOCK_CLICK_001"))

    def _add_nav_item(self, key: str, icon: str, text: str, command, active: bool = False):
        """Add one compact sidebar item."""
        bg = self.COLORS["card_2"] if active else self.COLORS["sidebar"]
        fg = self.COLORS["text"] if active else self.COLORS["subtext"]
        item = tk.Frame(self.sidebar, bg=bg, cursor="hand2")
        item.pack(fill="x", pady=(12, 0), ipady=8)
        icon_label = tk.Label(
            item,
            text=icon,
            font=(FONT_FAMILY, 18),
            bg=bg,
            fg=fg,
            cursor="hand2",
        )
        icon_label.pack()
        text_label = tk.Label(
            item,
            text=text,
            font=(FONT_FAMILY, 8),
            bg=bg,
            fg=fg,
            cursor="hand2",
        )
        text_label.pack()
        for widget in (item, icon_label, text_label):
            widget.bind("<Button-1>", lambda _event, cmd=command: cmd())
        self.nav_items[key] = (item, icon_label, text_label)

    def _set_nav_active(self, active_key: str):
        """Highlight only the currently selected sidebar item."""
        for key, widgets in self.nav_items.items():
            is_active = key == active_key
            bg = self.COLORS["card_2"] if is_active else self.COLORS["sidebar"]
            fg = self.COLORS["text"] if is_active else self.COLORS["subtext"]
            for widget in widgets:
                widget.config(bg=bg, fg=fg) if isinstance(widget, tk.Label) else widget.config(bg=bg)

    def _check_status_clicked(self):
        """Open status badge prompt only when a server connection is available."""
        if not self.api_session.is_approved:
            self._show_info_screen(
                "Status unavailable",
                "No internet connection right now.\n\nPlease come back later to check your current status.",
                self.COLORS["warning"],
            )
            return
        self.action_button_clicked("CHECK_STATUS")

    def _open_settings_info(self):
        """Placeholder settings screen from the sidebar."""
        self._set_nav_active("settings")
        self._show_info_screen(
            "Settings",
            "Settings will be available here later.",
            self.COLORS["accent"],
        )

    def _show_info_screen(self, title: str, details: str, color: str):
        """Show a returnable message screen with the same overlay design."""
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None
        if self.admin_frame is not None:
            self.admin_frame.destroy()
            self.admin_frame = None
        self.rest_frame.pack_forget()
        self.current_state = "INFO"
        self.active_action = None
        self.lbl_overlay_title.config(text=title, fg=color)
        self.lbl_overlay_details.config(
            text=details,
            fg="#ffffff",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY + 2, "bold"),
        )
        self.overlay_frame.pack(expand=True, fill="both")
        self._schedule_idle_return()

    def _poll_auth_beacon(self):
        """Toggle the small header LED text between online (green) and retrying (amber)."""
        if not self.winfo_exists():
            return
        self._update_connection_copy(self.scan_queue.pending_count())
        self.after(2000, self._poll_auth_beacon)

    def _update_connection_copy(self, pending_count: int):
        """Show online/offline and queue state in worker-friendly language."""
        if self.api_session.is_approved:
            if pending_count:
                self.status_beacon.config(text=f"● Online - syncing {pending_count}", fg=self.COLORS["warning"])
                self.lbl_queue_status.config(
                    text=f"{pending_count} saved stamp(s) will be sent shortly.",
                    fg=self.COLORS["warning"],
                )
            else:
                self.status_beacon.config(text="● Online", fg=self.COLORS["success"])
                self.lbl_queue_status.config(text="Ready to record your time.", fg=self.COLORS["subtext"])
        else:
            if pending_count:
                self.status_beacon.config(text=f"● Offline - {pending_count} saved", fg=self.COLORS["warning"])
                self.lbl_queue_status.config(
                    text=f"No internet right now. {pending_count} stamp(s) are safely stored.",
                    fg=self.COLORS["warning"],
                )
            else:
                self.status_beacon.config(text="● Offline mode", fg=self.COLORS["warning"])
                self.lbl_queue_status.config(
                    text="No internet right now. You can still scan your badge.",
                    fg=self.COLORS["subtext"],
                )

    def _rfid_listen_loop(self):
        """Background loop: wait for tags, push UIDs to :meth:`_on_card_uid` on the UI thread."""
        logger.info("RFID listener thread started.")
        while self.rfid_listener_active:
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
        if self.current_state not in ("REST", "WAITING_CARD"):
            logger.info("Ignoring badge scan while screen is %s.", self.current_state)
            return
        if not self._scan_ui_lock.acquire(blocking=False):
            return

        pending_context = self.active_action

        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

        if pending_context:
            detail = "Checking your badge…"
        else:
            detail = "Saving your time stamp…"
        self.lbl_overlay_title.config(text="One moment…", fg=self.COLORS["accent"])
        self.lbl_overlay_details.config(
            text=detail,
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
        )
        self.rest_frame.pack_forget()
        self.overlay_frame.pack(expand=True, fill="both")
        self.current_state = "UPLOADING"

        def worker():
            if pending_context == "ADMIN":
                if uid in config.ADMIN_BADGE_UIDS:
                    result = {"success": True, "admin_access": True}
                else:
                    result = {"success": False, "admin_denied": True}
            elif pending_context == "CHECK_STATUS":
                result = self.api_session.send_status_query(uid, "status")
            elif pending_context == "FLEXTIME":
                result = self.api_session.send_status_query(uid, "flextime")
            elif pending_context == "HOLIDAY":
                result = self.api_session.send_status_query(uid, "holiday")
            else:
                queued = self.scan_queue.enqueue_scan(uid, client_local_time=local_now_iso())
                if self.api_session.is_approved and self._sync_lock.acquire(blocking=False):
                    try:
                        summary = self.scan_queue.sync_pending(self.api_session)
                    finally:
                        try:
                            self._sync_lock.release()
                        except RuntimeError:
                            pass
                else:
                    summary = {"pending": self.scan_queue.pending_count()}
                queued_after_sync = self.scan_queue.get_by_request_id(queued["request_id"])
                response_text = queued_after_sync.get("server_response") or ""
                if queued_after_sync.get("status") == "synced" and response_text:
                    try:
                        result = json.loads(response_text)
                    except ValueError:
                        result = {"success": True}
                    result["queue_pending"] = summary.get("pending", self.scan_queue.pending_count())
                else:
                    result = {
                        "success": True,
                        "offline_saved": True,
                        "request_id": queued["request_id"],
                        "client_local_time": queued["client_local_time"],
                        "queue_pending": summary.get("pending", self.scan_queue.pending_count()),
                    }
            if pending_context in ("CHECK_STATUS", "FLEXTIME", "HOLIDAY") and not result:
                result = {
                    "success": False,
                    "query_failed": True,
                    "query_kind": pending_context,
                }
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
        detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY)

        if ok and (result or {}).get("admin_access"):
            self._open_admin_page(uid)
            return
        if (result or {}).get("admin_denied"):
            title = "Admin access denied"
            color = self.COLORS["error"]
            details = "This badge is not allowed to open the admin page."
        elif (result or {}).get("query_failed"):
            title = {
                "CHECK_STATUS": "Status unavailable",
                "FLEXTIME": "Flextime unavailable",
                "HOLIDAY": "Holidays unavailable",
            }.get((result or {}).get("query_kind"), "Information unavailable")
            color = self.COLORS["warning"]
            details = "The server did not answer. Please try again when the terminal is online."
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 2, "bold")
        elif ok and (result or {}).get("offline_saved"):
            pending = int((result or {}).get("queue_pending", self.scan_queue.pending_count()))
            title = "Saved offline"
            color = self.COLORS["warning"]
            details = (
                "Your badge scan was saved on this terminal.\n\n"
                "It will sync automatically when the connection returns."
            )
            if pending:
                details = f"{details}\n\n{pending} saved stamp(s) are waiting."
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 2, "bold")
        elif ok and pending_context:
            title = {
                "CHECK_STATUS": "Your status",
                "FLEXTIME": "Flextime",
                "HOLIDAY": "Holidays & time off",
            }.get(pending_context, "Information")
            color = self.COLORS["accent"]
            state_raw = ((result or {}).get("state") or "").lower()
            state_label = {
                "in": "Clocked in",
                "out": "Clocked out",
            }.get(state_raw, "No status yet")
            since = (result or {}).get("since") or ""
            worked = (result or {}).get("worked_today_hm") or "0:00"
            status_lines = [f"Status: {state_label}", f"Worked today: {worked}"]
            if since:
                status_lines.append(f"Since: {since}")
            status_block = "\n".join(status_lines)

            if pending_context == "CHECK_STATUS":
                details = status_block
            elif pending_context == "FLEXTIME":
                extra = terminal_msg or "No flextime balance was returned yet."
                details = f"{status_block}\n\n{extra}"
            elif pending_context == "HOLIDAY":
                extra = terminal_msg or "No holiday balance was returned yet."
                details = f"{status_block}\n\n{extra}"
            else:
                details = terminal_msg or status_block
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 3, "bold")
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

            headline = terminal_msg or punch_line
            color = self.COLORS["success"] if ev == "in" else (self.COLORS["error"] if ev == "out" else self.COLORS["success"])
            title = title_base
            details = headline
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 4, "bold")
        else:
            title = "Couldn’t complete that"
            color = self.COLORS["error"]
            details = (
                "We couldn’t confirm this with the server.\n\n"
                "Please try again in a moment. If it keeps happening, ask an admin to check the terminal."
            )
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 1, "bold")

        self.lbl_overlay_title.config(text=title, fg=color)
        self.lbl_overlay_details.config(
            text=details,
            fg="#ffffff",
            font=detail_font,
        )
        self._update_connection_copy(self.scan_queue.pending_count())
        self._schedule_idle_return()

    def _schedule_idle_return(self):
        """Return to the default clock screen after one minute without a new action."""
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
        self.timeout_timer = self.after(IDLE_RETURN_MS, self.revert_to_rest)

    def _open_user_panel(self):
        """Show employee self-service choices before asking for a badge."""
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None
        self.current_state = "USER_PANEL"
        self.active_action = None
        self._set_nav_active("user")
        if self.admin_frame is not None:
            self.admin_frame.destroy()
            self.admin_frame = None
        self.overlay_frame.pack_forget()
        self.rest_frame.pack_forget()
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()

        self.user_panel_frame = RoundedPanel(
            self.body,
            bg=self.COLORS["surface"],
            border=self.COLORS["card_2"],
            radius=18,
        )
        self.user_panel_frame.pack(expand=True, fill="both")
        user_panel = self.user_panel_frame.inner

        tk.Label(
            user_panel,
            text="What would you like to check?",
            font=(FONT_FAMILY, FONT_OVERLAY_TITLE),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(pady=(26, 6))
        tk.Label(
            user_panel,
            text=(
                "Choose an option, then scan your badge."
                if self.api_session.is_approved
                else "No internet connection right now. Please come back later."
            ),
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
            bg=self.COLORS["surface"],
            fg=self.COLORS["subtext"] if self.api_session.is_approved else self.COLORS["warning"],
        ).pack(pady=(0, 20))

        if self.api_session.is_approved:
            choices = tk.Frame(user_panel, bg=self.COLORS["surface"])
            choices.pack(fill="x", padx=36)
            RoundedButton(
                choices,
                "Flextime",
                lambda: self.action_button_clicked("FLEXTIME"),
                width=330,
                height=76,
                bg=self.COLORS["card"],
                fg=self.COLORS["text"],
                active_bg=self.COLORS["card_2"],
                border=self.COLORS["border"],
                font=(FONT_FAMILY, FONT_BUTTON + 4, "bold"),
            ).pack(side="left", expand=True, padx=(0, 10))
            RoundedButton(
                choices,
                "Holidays",
                lambda: self.action_button_clicked("HOLIDAY"),
                width=330,
                height=76,
                bg=self.COLORS["card"],
                fg=self.COLORS["text"],
                active_bg=self.COLORS["card_2"],
                border=self.COLORS["border"],
                font=(FONT_FAMILY, FONT_BUTTON + 4, "bold"),
            ).pack(side="left", expand=True, padx=(10, 0))

        RoundedButton(
            user_panel,
            "Return",
            self.revert_to_rest,
            width=300,
            height=48,
            bg=self.COLORS["button_alt"],
            fg=self.COLORS["subtext"],
            active_bg=self.COLORS["card_2"],
            border=self.COLORS["border"],
            font=(FONT_FAMILY, FONT_BUTTON + 1, "bold"),
        ).pack(pady=(22, 0))
        self._schedule_idle_return()

    def _open_admin_page(self, admin_uid: str):
        """Show local queue/database status after an admin badge unlocks it."""
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None
        self.current_state = "ADMIN"
        self.active_action = None
        self._set_nav_active("admin")
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None
        self.overlay_frame.pack_forget()
        self.rest_frame.pack_forget()
        if self.admin_frame is not None:
            self.admin_frame.destroy()

        self.admin_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.admin_frame.pack(expand=True, fill="both")

        header_panel = RoundedPanel(
            self.admin_frame,
            bg=self.COLORS["surface"],
            border=self.COLORS["card_2"],
            radius=16,
            height=48,
        )
        header_panel.pack(fill="x", pady=(0, 8))
        header = header_panel.inner
        tk.Label(
            header,
            text="Admin overview",
            font=(FONT_FAMILY, FONT_HEADER_TITLE + 2),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(side="left", padx=10, pady=6)
        tk.Label(
            header,
            text="Unlocked",
            font=(FONT_FAMILY, FONT_HEADER_STATUS),
            bg=self.COLORS["surface"],
            fg=self.COLORS["success"],
        ).pack(side="right", padx=10)

        self.admin_summary_label = tk.Label(
            self.admin_frame,
            text="",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
            wraplength=OVERLAY_WRAP,
            justify="left",
            anchor="w",
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self.admin_summary_label.pack(fill="x", pady=(0, 8))

        tk.Label(
            self.admin_frame,
            text="Stamps that need attention",
            font=(FONT_FAMILY, FONT_OVERLAY_BODY, "bold"),
            anchor="w",
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        ).pack(fill="x")

        rows_panel = RoundedPanel(
            self.admin_frame,
            bg=self.COLORS["card"],
            border=self.COLORS["card_2"],
            radius=16,
        )
        rows_panel.pack(fill="both", expand=True, pady=(4, 8))
        self.admin_rows_frame = rows_panel.inner

        controls = tk.Frame(self.admin_frame, bg=self.COLORS["bg"])
        controls.pack(fill="x")
        ttk.Button(controls, text="Retry sync", style="Kiosk.TButton", command=self._admin_retry_sync).pack(
            side="left", expand=True, fill="x", padx=(0, 6)
        )
        ttk.Button(controls, text="Refresh", style="Kiosk.TButton", command=self._refresh_admin_page).pack(
            side="left", expand=True, fill="x", padx=(0, 6)
        )
        ttk.Button(controls, text="Return", style="Kiosk.TButton", command=self._close_admin_page).pack(
            side="left", expand=True, fill="x"
        )

        self._refresh_admin_page()
        self._schedule_idle_return()

    def _refresh_admin_page(self):
        """Reload local SQLite queue rows into the admin page."""
        if self.admin_frame is None or not self.admin_frame.winfo_exists():
            return
        counts = self.scan_queue.status_counts()
        pending = counts.get("pending", 0)
        failed = counts.get("failed", 0)
        synced = counts.get("synced", 0)
        self.admin_summary_label.config(
            text=(
                f"Waiting to send: {pending}    Needs attention: {failed}    Sent: {synced}\n"
                "Saved stamps stay on this terminal until the server accepts them."
            )
        )

        for child in self.admin_rows_frame.winfo_children():
            child.destroy()

        rows = self.scan_queue.issue_scans(limit=6)
        if not rows:
            tk.Label(
                self.admin_rows_frame,
                text="Everything looks good. No saved stamps need attention.",
                font=(FONT_FAMILY, FONT_OVERLAY_BODY),
                bg=self.COLORS["card"],
                fg=self.COLORS["success"],
            ).pack(expand=True)
            return

        for row in rows:
            status_color = self.COLORS["error"] if row["status"] == "failed" else self.COLORS["warning"]
            label = "Needs retry" if row["status"] == "failed" else "Waiting to send"
            line = (
                f"{label}  {row['client_local_time']}\n"
                f"Attempts: {row['retry_count']}  {row['last_sync_error'] or 'Will try again automatically.'}"
            )
            tk.Label(
                self.admin_rows_frame,
                text=line,
                font=(FONT_FAMILY, FONT_OVERLAY_BODY - 1),
                wraplength=OVERLAY_WRAP,
                justify="left",
                anchor="w",
                bg=self.COLORS["card"],
                fg=status_color,
            ).pack(fill="x", padx=8, pady=4)

    def _admin_retry_sync(self):
        """Admin button: trigger a sync attempt and refresh rows afterwards."""
        if self.admin_summary_label:
            self.admin_summary_label.config(text="Sending saved stamps now…")
        self._schedule_idle_return()

        def worker():
            if self.api_session.is_approved or self.api_session.authenticate(silent=True):
                self.scan_queue.sync_pending(self.api_session)
            self.after(0, self._refresh_admin_page)

        threading.Thread(target=worker, daemon=True).start()

    def _close_admin_page(self):
        """Return from admin page to the normal clock screen."""
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None
        if self.admin_frame is not None:
            self.admin_frame.destroy()
            self.admin_frame = None
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None
        self.rest_frame.pack(expand=True, fill="both")
        self.current_state = "REST"
        self._update_connection_copy(self.scan_queue.pending_count())

    def action_button_clicked(self, action_type):
        """
        Footer buttons: switch to “wait for card” mode with a contextual overlay title.

        Args:
            action_type: ``CHECK_STATUS`` | ``FLEXTIME`` | ``HOLIDAY`` — forwarded to the query API.
        """
        if action_type in ("CHECK_STATUS", "FLEXTIME", "HOLIDAY") and not self.api_session.is_approved:
            self._show_info_screen(
                "Not available offline",
                "No internet connection right now.\n\nPlease come back later.",
                self.COLORS["warning"],
            )
            return
        self.current_state = "WAITING_CARD"
        self.active_action = action_type
        if action_type == "ADMIN":
            self._set_nav_active("admin")
        if self.admin_frame is not None:
            self.admin_frame.destroy()
            self.admin_frame = None
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None

        titles = {
            "CHECK_STATUS": "My status",
            "FLEXTIME": "Flextime",
            "HOLIDAY": "Holidays",
            "ADMIN": "Admin check",
        }
        self.lbl_overlay_title.config(
            text=titles.get(action_type, "Next step"),
            fg=self.COLORS["accent"],
        )
        prompt = (
            "Scan an admin badge to open the local database page.\n\n"
            "This screen returns automatically after one minute."
            if action_type == "ADMIN"
            else "Hold your badge on the reader.\n\nThis screen returns automatically after one minute."
        )
        self.lbl_overlay_details.config(
            text=prompt,
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
        )
        self.rest_frame.pack_forget()
        if self.user_panel_frame is not None:
            self.user_panel_frame.pack_forget()
        self.overlay_frame.pack(expand=True, fill="both")

        self._schedule_idle_return()

    def revert_to_rest(self):
        """Hide overlay, show the clock screen again, and clear timers."""
        self.current_state = "REST"
        self.active_action = None
        self._set_nav_active("home")
        self.overlay_frame.pack_forget()
        if self.admin_frame is not None:
            self.admin_frame.destroy()
            self.admin_frame = None
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None
        self.rest_frame.pack(expand=True, fill="both")
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

    def update_clock(self):
        """Refresh date/time labels once per second while ``clock_active`` is true."""
        now = datetime.datetime.now()
        self.lbl_time.config(text=now.strftime("%H:%M:%S"))
        self.lbl_date.config(text=now.strftime("%A, %d. %B %Y").upper())
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
