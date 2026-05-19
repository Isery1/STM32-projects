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
import math
import socket
import subprocess
import tkinter as tk
from tkinter import ttk

import config
from auth import AuthenticatedSession
from i18n import Translator
from offline_queue import OfflineScanQueue, local_now_iso

FONT_FAMILY = "DejaVu Sans"

# Fixed kiosk resolution (7" panels are often 800x480).
DISPLAY_W, DISPLAY_H = 800, 480
PAD_X = 12
PAD_Y = 6
FONT_CLOCK = 68
FONT_DATE = 13
FONT_INSTRUCTION = 13
FONT_HEADER_TITLE = 15
FONT_HEADER_STATUS = 12
FONT_OVERLAY_TITLE = 26
FONT_OVERLAY_BODY = 14
FONT_BOOT_TITLE = 20
FONT_BOOT_STEP = 14
FONT_BOOT_DETAIL = 12
FONT_BUTTON = 13
STATUS_BUTTON_W = 260
STATUS_BUTTON_H = 50
OVERLAY_WRAP = 680
FOOTER_PAYOUTSIDE = 0
FOOTER_PADBOTTOM = 0
BUTTON_RADIUS = 10
IDLE_RETURN_MS = 10_000
SIDEBAR_W = 72
SETTINGS_LANG_EN = "English"
SETTINGS_LANG_DE = "Deutsch"

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
        icon_type: str = None,
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
        self.icon_type = icon_type
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
        w = int(self["width"])
        h = int(self["height"])
        cy = h // 2

        if self.icon_type == "info":
            icon_x = 20
            self.create_oval(icon_x - 9, cy - 9, icon_x + 9, cy + 9, outline=self.fg, width=2)
            self.create_text(icon_x, cy, text="i", fill=self.fg, font=(FONT_FAMILY, 10, "bold"))
            self.create_text(icon_x + 26, cy, text=self.text, fill=self.fg, font=self.font, anchor="w")
        else:
            self.create_text(w // 2, cy, text=self.text, fill=self.fg, font=self.font)

    def _press(self, _event):
        self._draw(self.active_bg)
        self.after(90, self.command)

    def _release(self, _event):
        self._draw(self.normal_bg)


def _blend_hex(fg: str, bg: str, t: float) -> str:
    """Linear blend between two #RRGGBB colors (t=0 → fg, t=1 → bg)."""
    fg = fg.lstrip("#")
    bg = bg.lstrip("#")
    fr, fg_g, fb = (int(fg[i : i + 2], 16) for i in (0, 2, 4))
    br, bg_g, bb = (int(bg[i : i + 2], 16) for i in (0, 2, 4))
    r = int(fr * (1 - t) + br * t)
    g = int(fg_g * (1 - t) + bg_g * t)
    b = int(fb * (1 - t) + bb * t)
    return f"#{r:02x}{g:02x}{b:02x}"


class PulsingDot(tk.Canvas):
    """Animated status dot with a soft expanding ring."""

    def __init__(self, parent, color: str, *, size: int = 4, pulse: int = 5, bg: str = None):
        bg = bg or parent["bg"]
        max_r = size + 2 + pulse
        w = max_r * 2 + 6
        h = max_r * 2 + 2
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        self.dot_color = color
        self.canvas_bg = bg
        self.core = size
        self._pulse = pulse
        self.cx = w // 2
        self.cy = h // 2
        self._phase = 0.0
        self._tick()

    def set_color(self, color: str):
        self.dot_color = color

    def _tick(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        ring_r = self.core + 2 + pulse * self._pulse
        ring_w = max(1, int(1 + pulse * 2))
        ring_color = _blend_hex(self.dot_color, self.canvas_bg, 0.35 + 0.45 * pulse)
        self.create_oval(
            self.cx - ring_r,
            self.cy - ring_r,
            self.cx + ring_r,
            self.cy + ring_r,
            outline=ring_color,
            width=ring_w,
        )
        self.create_oval(
            self.cx - self.core,
            self.cy - self.core,
            self.cx + self.core,
            self.cy + self.core,
            fill=self.dot_color,
            outline=self.dot_color,
        )
        self._phase += 0.14
        self.after(55, self._tick)


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
        # Draw main filled area
        self.create_rectangle(r, 1, w - r, h - 1, fill=self.fill, outline=self.fill, tags="panel")
        self.create_rectangle(1, r, w - 1, h - r, fill=self.fill, outline=self.fill, tags="panel")
        # Draw border outline (cleaner approach using arcs and lines)
        self.create_arc(1, 1, 2 * r, 2 * r, start=90, extent=90, outline=self.border, tags="panel", style="arc", width=1)
        self.create_arc(w - 2 * r, 1, w - 1, 2 * r, start=0, extent=90, outline=self.border, tags="panel", style="arc", width=1)
        self.create_arc(w - 2 * r, h - 2 * r, w - 1, h - 1, start=270, extent=90, outline=self.border, tags="panel", style="arc", width=1)
        self.create_arc(1, h - 2 * r, 2 * r, h - 1, start=180, extent=90, outline=self.border, tags="panel", style="arc", width=1)
        
        self.create_line(r, 1, w - r, 1, fill=self.border, tags="panel", width=1)
        self.create_line(w - 1, r, w - 1, h - r, fill=self.border, tags="panel", width=1)
        self.create_line(w - r, h - 1, r, h - 1, fill=self.border, tags="panel", width=1)
        self.create_line(1, h - r, 1, r, fill=self.border, tags="panel", width=1)
        self.tag_lower("panel")
        inset = max(8, r // 2)
        self.coords(self.window_id, inset, inset)
        self.itemconfigure(self.window_id, width=max(1, w - (inset * 2)), height=max(1, h - (inset * 2)))

class ScrollableRoundedPanel(RoundedPanel):
    """A RoundedPanel that supports vertical scrolling for its inner content."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.v_scroll = tk.Scrollbar(self, orient="vertical", command=self.yview)
        self.v_scroll.pack(side="right", fill="y", padx=2)
        self.configure(yscrollcommand=self.v_scroll.set)
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.inner.bind("<Configure>", lambda e: self.configure(scrollregion=self.bbox("all")))

    def _on_mousewheel(self, event):
        if self.winfo_ismapped():
            self.yview_scroll(int(-1*(event.delta/120)), "units")

    def _redraw(self, _event=None):
        super()._redraw(_event)
        inset = max(8, self.radius // 2)
        # Use a fixed width for the inner window to allow vertical scrolling
        self.itemconfigure(self.window_id, width=max(1, self.winfo_width() - (inset * 2) - 25))


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

        self.title("")  # set after translator loads
        self.geometry(f"{DISPLAY_W}x{DISPLAY_H}")
        self.minsize(DISPLAY_W, DISPLAY_H)
        self.maxsize(DISPLAY_W, DISPLAY_H)
        self.resizable(False, False)
        self.configure(bg="#0a0f1d")
        if config.KIOSK_FULLSCREEN:
            self.attributes("-fullscreen", True)
            self.attributes("-topmost", True)
        self._closing = False
        self._bind_exit_shortcuts()
        self.defer_boot_sequence = defer_boot_sequence

        self.COLORS = {
            "bg": "#111318",
            "sidebar": "#0d0f13",
            "sidebar_active": "#281850",
            "surface": "#111318",
            "card": "#1c1f28",
            "card_border": "#23252b",
            "text": "#e6e6eb",
            "accent": "#7c3aed",
            "success": "#4ade80",
            "success_dim": "#143c23",
            "warning": "#fbbf24",
            "warning_dim": "#3c2d0a",
            "subtext": "#a0a0a8",
            "muted": "#64646c",
            "error": "#f87171",
            "error_dim": "#461414",
            "button": "#7c3aed",
            "status_bg": "#143c23",
        }

        # Session-persistent settings (load from file)
        self.settings_path = os.path.join(os.path.dirname(__file__), "app_settings.json")
        self.settings = self._load_settings()

        self.current_lang = self.settings.get("lang", "EN")
        self.translator = Translator(self.current_lang)
        self.IDLE_TIMEOUT_MS = self.settings.get("timeout_ms", 60000)
        self.sync_interval_ms = self.settings.get("sync_interval_ms", 60000)
        self.title(self.t("window_title"))

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

    def _bind_exit_shortcuts(self):
        """Bind emergency exit shortcuts globally so focused child widgets cannot swallow them."""
        def close_from_shortcut(_event):
            self._on_close()
            return "break"

        for sequence in ("<Control-q>", "<Control-Q>"):
            self.bind_all(sequence, close_from_shortcut)

    def _build_boot_screen(self):
        """Lay out the pre-flight checklist labels before hardware and network are touched."""
        wrap = tk.Frame(self, bg=self.COLORS["bg"])
        wrap.pack(expand=True, fill="both", padx=PAD_X * 2, pady=PAD_Y * 3)
        self._boot_wrap = wrap

        tk.Label(
            wrap,
            text=self.t("boot_starting"),
            font=(FONT_FAMILY, FONT_BOOT_TITLE, "bold"),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        ).pack(pady=(0, 4))

        self._boot_subtitle = tk.Label(
            wrap,
            text=self.t("boot_subtitle"),
            font=(FONT_FAMILY, FONT_BOOT_DETAIL),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self._boot_subtitle.pack(pady=(0, 16))

        rows = [
            ("hardware", self.t("boot_step_hardware")),
            ("network", self.t("boot_step_network")),
            ("time", self.t("boot_step_time")),
            ("server", self.t("boot_step_server")),
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
                text=self.t("boot_waiting"),
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
                lbl.config(text=self.t("boot_checking"), fg=self.COLORS["warning"])
            elif state == "skip":
                lbl.config(text=detail or self.t("boot_not_needed"), fg=self.COLORS["subtext"])
            elif state == "ok":
                lbl.config(text=detail or self.t("boot_ok"), fg=self.COLORS["success"])
            elif state == "error":
                lbl.config(text=detail or self.t("boot_failed"), fg=self.COLORS["error"])
            else:
                lbl.config(text=detail or state, fg=self.COLORS["subtext"])
        if self._boot_subtitle:
            subtitle = {
                "hardware": self.t("boot_phase_hardware"),
                "network": self.t("boot_phase_network"),
                "time": self.t("boot_phase_time"),
                "server": self.t("boot_phase_server"),
            }.get(phase, self.t("boot_phase_default"))
            self._boot_subtitle.config(text=subtitle, fg=self.COLORS["subtext"])

    def _boot_sequence_ok(self):
        """Brief success message, then tear down the splash and mount the real kiosk chrome."""
        if self._closing or not self.winfo_exists():
            return
        if self._boot_subtitle:
            self._boot_subtitle.config(
                text=self.t("boot_all_good"),
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
                text=self.t("boot_stopped"),
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
            text=self.t("boot_close_hint"),
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
        self.settings_frame = None
        self.nav_items = {}
        self.rfid_listener_active = True
        self._scan_ui_lock = threading.Lock()
        self._sync_lock = threading.Lock()

        self.build_ui_frames()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.clock_active = True
        self.update_clock()

        self.after(2000, self._poll_auth_beacon)
        self.after(2500, self._send_backend_boot_check)

        hb_ms = max(5000, int(config.HEARTBEAT_INTERVAL_SECONDS * 1000))
        self.after(1000, self._sync_pending_async)
        self.after(hb_ms, self._schedule_heartbeat)

        self._rfid_thread = threading.Thread(target=self._rfid_listen_loop, daemon=True)
        self._rfid_thread.start()

    def _send_backend_boot_check(self):
        """Report successful kiosk boot to the authenticated backend without blocking the UI."""
        if self._closing or not self.winfo_exists():
            return

        def worker():
            self.api_session.send_boot_check(
                hardware={
                    "rfid_reader": "mock" if self._is_mock_hw else "mfrc522",
                    "mode": "kiosk",
                    "display": f"{DISPLAY_W}x{DISPLAY_H}",
                }
            )

        threading.Thread(target=worker, daemon=True).start()

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
                    self.api_session.send_heartbeat(pending_offline_stamps=self.scan_queue.pending_count())
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

    def t(self, key: str, **kwargs) -> str:
        return self.translator.t(key, **kwargs)

    @staticmethod
    def _get_local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return ""

    def _destroy_settings_page(self):
        self._settings_canvas = None
        if self.settings_frame is not None:
            try:
                self.settings_frame.destroy()
            except tk.TclError:
                pass
            self.settings_frame = None

    def _bind_settings_scroll(self, canvas: tk.Canvas, *widgets):
        """Bind wheel / Linux scroll buttons to the settings canvas and its content."""

        def scroll_units(direction: int):
            canvas.yview_scroll(direction, "units")

        def on_wheel(event):
            if event.delta:
                scroll_units(int(-1 * (event.delta / 120)))
            return "break"

        def on_linux_scroll(event):
            scroll_units(-1 if event.num == 4 else 1)
            return "break"

        for widget in (canvas, *widgets):
            widget.bind("<MouseWheel>", on_wheel)
            widget.bind("<Button-4>", on_linux_scroll)
            widget.bind("<Button-5>", on_linux_scroll)

    def _settings_scroll_page(self, direction: int):
        canvas = getattr(self, "_settings_canvas", None)
        if canvas is not None and canvas.winfo_exists():
            canvas.yview_scroll(direction, "units")

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
            background=[("active", self.COLORS["accent"]), ("pressed", self.COLORS["accent"])],
            foreground=[("active", "#ffffff")],
            relief=[("pressed", "sunken"), ("!pressed", "flat")],
        )
        self.style.configure(
            "Admin.TButton",
            font=(FONT_FAMILY, FONT_BUTTON, "bold"),
            background=self.COLORS["sidebar_active"],
            foreground=self.COLORS["subtext"],
            borderwidth=1,
            focuscolor="none",
            relief="flat",
            padding=(12, 10),
        )
        self.style.map(
            "Admin.TButton",
            background=[("active", self.COLORS["card"]), ("pressed", self.COLORS["accent"])],
            foreground=[("active", "#ffffff")],
        )
        self._configure_settings_widget_styles()

    def _configure_settings_widget_styles(self):
        """Dark-theme ttk styles for the settings scroll bar and comboboxes."""
        c = self.COLORS
        self.style.configure(
            "Settings.TCombobox",
            font=(FONT_FAMILY, 11),
            padding=(12, 8),
            fieldbackground=c["card"],
            background=c["card_border"],
            foreground=c["text"],
            arrowcolor=c["subtext"],
            bordercolor=c["card_border"],
            lightcolor=c["card_border"],
            darkcolor=c["card_border"],
        )
        self.style.map(
            "Settings.TCombobox",
            fieldbackground=[("readonly", c["card"]), ("disabled", c["sidebar"])],
            foreground=[("readonly", c["text"])],
            arrowcolor=[("active", c["accent"]), ("readonly", c["subtext"])],
        )
        self.style.configure(
            "Settings.Vertical.TScrollbar",
            background=c["card_border"],
            troughcolor=c["bg"],
            bordercolor=c["bg"],
            arrowcolor=c["subtext"],
            darkcolor=c["bg"],
            lightcolor=c["bg"],
            gripcount=0,
            width=14,
        )
        self.style.map(
            "Settings.Vertical.TScrollbar",
            background=[("active", c["accent"]), ("pressed", c["button"])],
            arrowcolor=[("active", c["text"]), ("disabled", c["muted"])],
        )
        self.option_add("*TCombobox*Listbox.background", c["card"])
        self.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["sidebar_active"])
        self.option_add("*TCombobox*Listbox.selectForeground", c["text"])
        self.option_add("*TCombobox*Listbox.font", (FONT_FAMILY, 11))

    @staticmethod
    def _lang_from_combo_label(label: str) -> str:
        return "DE" if label == SETTINGS_LANG_DE else "EN"

    @staticmethod
    def _combo_label_for_lang(lang: str) -> str:
        return SETTINGS_LANG_DE if lang == "DE" else SETTINGS_LANG_EN

    def build_ui_frames(self):
        """Create header/body/footer structure, clock labels, overlay region, and footer buttons."""
        self.sidebar = tk.Frame(self, bg=self.COLORS["sidebar"], width=SIDEBAR_W)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Logo mark at top of sidebar
        logo_bar = tk.Frame(self.sidebar, bg=self.COLORS["sidebar"], height=48)
        logo_bar.pack(fill="x")
        logo_bar.pack_propagate(False)
        tk.Label(
            logo_bar, text="M",
            font=(FONT_FAMILY, 16, "bold"),
            bg=self.COLORS["sidebar"],
            fg="#a78bfa",
        ).pack(expand=True)
        # Thin divider
        tk.Frame(self.sidebar, bg="#23252b", height=1).pack(fill="x")

        self._add_nav_item("home", "home", self.t("nav_home"), self.revert_to_rest, active=True, pady=(10, 0))
        self._add_nav_item("user", "user", self.t("nav_user"), self._open_user_panel, pady=(10, 0))
        self._add_nav_item("admin", "admin", self.t("nav_admin"), lambda: self.action_button_clicked("ADMIN"), pady=(10, 0))

        tk.Frame(self.sidebar, bg=self.COLORS["sidebar"]).pack(expand=True, fill="both")

        self._add_nav_item("settings", "settings", self.t("nav_settings"), self._open_settings_info, pady=(0, 20))

        self.main_area = tk.Frame(self, bg=self.COLORS["bg"])
        self.main_area.pack(side="right", fill="both", expand=True)

        self.header = tk.Frame(self.main_area, bg=self.COLORS["surface"], highlightthickness=1, highlightbackground="#23252b")
        self.header.pack(side="top", fill="x")

        hdr_inner = tk.Frame(self.header, bg=self.COLORS["surface"])
        hdr_inner.pack(fill="x", padx=18, pady=0)
        hdr_inner.configure(height=48)
        hdr_inner.pack_propagate(False)

        tk.Label(
            hdr_inner,
            text="manageIO",
            font=(FONT_FAMILY, FONT_HEADER_TITLE, "bold"),
            bg=self.COLORS["surface"],
            fg="#e6e6eb",
        ).pack(side="left", pady=12)

        # Online / offline label (no background box; pulsing dot only)
        hdr_bg = self.COLORS["surface"]
        self.status_row = tk.Frame(hdr_inner, bg=hdr_bg)
        self.status_row.pack(side="right", pady=12)
        self.status_dot = PulsingDot(self.status_row, self.COLORS["success"], bg=hdr_bg)
        self.status_dot.pack(side="left", anchor="center")
        self.status_beacon = tk.Label(
            self.status_row,
            text=self.t("status_online"),
            font=(FONT_FAMILY, FONT_HEADER_STATUS, "bold"),
            bg=hdr_bg,
            fg=self.COLORS["success"],
        )
        self.status_beacon.pack(side="left", padx=(4, 0), anchor="center")

        self.body = tk.Frame(self.main_area, bg=self.COLORS["bg"])
        self.body.pack(side="top", expand=True, fill="both", padx=16, pady=(8, 8))

        self.rest_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.rest_frame.pack(expand=True, fill="both")

        self.home_center = tk.Frame(self.rest_frame, bg=self.COLORS["bg"])
        self.home_center.pack(expand=True)

        # Clock (no panel background — sits directly on the home screen)
        self.clock_panel = tk.Frame(self.home_center, bg=self.COLORS["bg"])
        self.clock_panel.pack(pady=(8, 0))

        self.clock_frame = tk.Frame(self.clock_panel, bg=self.COLORS["bg"])
        self.clock_frame.pack()

        self.lbl_time = tk.Label(
            self.clock_frame,
            text="12:00:00",
            font=(FONT_FAMILY, FONT_CLOCK),
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        )
        self.lbl_time.pack()

        self.lbl_date = tk.Label(
            self.clock_frame,
            text="SATURDAY, 16. MAY 2026",
            font=(FONT_FAMILY, FONT_DATE),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_date.pack(pady=(4, 0))

        # Action row
        self.action_row = tk.Frame(self.home_center, bg=self.COLORS["bg"])
        self.action_row.pack(pady=(14, 0))

        self.btn_status_wrap = tk.Frame(self.action_row, bg=self.COLORS["bg"])
        self.btn_status_wrap.pack()
        self.btn_status = RoundedButton(
            self.btn_status_wrap,
            self.t("btn_status"),
            self._check_status_clicked,
            width=STATUS_BUTTON_W,
            height=STATUS_BUTTON_H,
            bg=self.COLORS["button"],
            fg="#ffffff",
            active_bg="#6d28d9",
            border=self.COLORS["button"],
            font=(FONT_FAMILY, FONT_BUTTON, "bold"),
        )
        self.btn_status.pack()

        # Footer instruction pinned to bottom
        self.footer_instruction = tk.Frame(self.rest_frame, bg=self.COLORS["bg"])
        self.footer_instruction.pack(side="bottom", fill="x", pady=(0, 12))

        # Tap-hint strip
        hint_strip = tk.Frame(self.footer_instruction, bg=self.COLORS["bg"])
        hint_strip.pack()
        tk.Frame(hint_strip, bg="#23252b", width=48, height=1).pack(side="left", padx=(0, 12), pady=8)
        self.lbl_instruction = tk.Label(
            hint_strip,
            text=self.t("clock_instr"),
            font=(FONT_FAMILY, FONT_INSTRUCTION),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
        )
        self.lbl_instruction.pack(side="left")
        tk.Frame(hint_strip, bg="#23252b", width=48, height=1).pack(side="left", padx=(12, 0), pady=8)

        self.overlay_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.overlay_panel = RoundedPanel(
            self.overlay_frame,
            bg=self.COLORS["card"],
            border=self.COLORS["card_border"],
            radius=18,
        )
        self.overlay_panel.pack(expand=True, fill="both", padx=PAD_X, pady=PAD_Y * 2)
        self.overlay_inner = self.overlay_panel.inner

        self.lbl_overlay_title = tk.Label(
            self.overlay_inner,
            text="…",
            font=(FONT_FAMILY, FONT_OVERLAY_TITLE, "bold"),
            bg=self.COLORS["card"],
            fg=self.COLORS["text"],
            wraplength=OVERLAY_WRAP,
            justify="center",
        )
        self.lbl_overlay_title.pack(pady=(30, 10))

        self.lbl_overlay_details = tk.Label(
            self.overlay_inner,
            text="",
            font=(FONT_FAMILY, 14),
            bg=self.COLORS["bg"],
            fg=self.COLORS["subtext"],
            wraplength=OVERLAY_WRAP,
            justify="center",
        )
        self.lbl_overlay_details.pack(pady=(0, 20))

        self.footer = tk.Frame(self.main_area, bg=self.COLORS["bg"])

        # Dev-only: click instruction to simulate a tap (still uploads to server)
        self.lbl_instruction.bind("<Button-1>", lambda e: self._on_card_uid("MOCK_CLICK_001"))

    def _add_nav_item(self, key: str, icon_type: str, text: str, command, active: bool = False, pady=(0, 0)):
        """Add one compact sidebar item with a high-res Canvas icon."""
        bg = self.COLORS["card"] if active else self.COLORS["sidebar"]
        fg = self.COLORS["text"] if active else self.COLORS["subtext"]
        item = tk.Frame(self.sidebar, bg=bg, cursor="hand2")
        item.pack(fill="x", pady=pady, ipady=10)
        
        icon_canvas = tk.Canvas(item, width=40, height=40, bg=bg, highlightthickness=0, bd=0)
        icon_canvas.pack(pady=(2, 0))
        self._draw_sidebar_icon(icon_canvas, icon_type, fg)
        
        text_label = tk.Label(
            item,
            text=text.upper(),
            font=(FONT_FAMILY, 8, "bold"),
            bg=bg,
            fg=fg,
            cursor="hand2",
        )
        text_label.pack()
        
        for widget in (item, icon_canvas, text_label):
            widget.bind("<Button-1>", lambda _event, cmd=command: cmd())
        self.nav_items[key] = (item, icon_canvas, text_label)

    def _draw_sidebar_icon(self, canvas, icon_type, color):
        """Draw bold, high-res minimalist icons."""
        canvas.delete("all")
        bg = canvas["bg"]
        if icon_type == "home":
            canvas.create_polygon(20, 8, 8, 20, 32, 20, fill="", outline=color, width=3)
            canvas.create_rectangle(12, 20, 28, 32, fill="", outline=color, width=3)
        elif icon_type == "user":
            canvas.create_oval(14, 10, 26, 22, fill="", outline=color, width=3)
            canvas.create_arc(8, 26, 32, 42, start=0, extent=180, fill="", outline=color, width=3)
        elif icon_type == "admin":
            canvas.create_polygon(20, 8, 32, 12, 30, 26, 20, 34, 10, 26, 8, 12, fill="", outline=color, width=3)
            canvas.create_oval(18, 16, 22, 20, fill=color, outline=color)
            canvas.create_line(20, 20, 20, 26, fill=color, width=3)
        elif icon_type == "settings": # Placeholder for now
            canvas.create_oval(15, 15, 25, 25, outline=color, width=3)

    def _set_nav_active(self, active_key: str):
        """Highlight only the currently selected sidebar item and redraw its icon."""
        for key, widgets in self.nav_items.items():
            is_active = key == active_key
            bg = self.COLORS["sidebar_active"] if is_active else self.COLORS["sidebar"]
            fg = self.COLORS["text"] if is_active else self.COLORS["subtext"]
            
            # widgets is (item, icon_canvas, text_label)
            item, canvas, lbl = widgets
            item.config(bg=bg)
            canvas.config(bg=bg)
            lbl.config(bg=bg, fg=fg)
            
            # Determine icon type from key
            icon_type = "settings" if key == "settings" else key
            self._draw_sidebar_icon(canvas, icon_type, fg)

    def _check_status_clicked(self):
        """Open status badge prompt only when a server connection is available."""
        if not self.api_session.is_approved and not self.api_session.authenticate(silent=True):
            self._show_info_screen(
                self.t("status_unavailable_title"),
                self.t("status_unavailable_body"),
                self.COLORS["warning"],
            )
            return
        self.action_button_clicked("CHECK_STATUS")

    def _open_settings_info(self):
        """Require admin badge to open settings."""
        self.action_button_clicked("SETTINGS")

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
        # Preserve active_action if we are coming from a 'waiting for card' state
        if self.current_state != "WAITING_CARD":
            self.active_action = None
        
        self.rest_frame.pack_forget()
        self.current_state = "INFO"
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

    def _set_status_indicator(self, dot_fg: str, beacon_fg: str, beacon_text: str):
        self.status_dot.set_color(dot_fg)
        self.status_beacon.config(text=beacon_text, fg=beacon_fg)

    def _update_connection_copy(self, pending_count: int):
        """Show online/offline and queue state in worker-friendly language."""
        if self.api_session.is_approved:
            if pending_count:
                self._set_status_indicator(
                    self.COLORS["warning"],
                    self.COLORS["warning"],
                    self.t("status_syncing", count=pending_count),
                )
                self.lbl_instruction.config(text=self.t("clock_instr_syncing", count=pending_count))
            else:
                self._set_status_indicator(
                    self.COLORS["success"],
                    self.COLORS["success"],
                    self.t("status_online"),
                )
                self.lbl_instruction.config(text=self.t("clock_instr"))
        else:
            self._set_status_indicator(
                self.COLORS["error"],
                self.COLORS["error"],
                self.t("status_offline"),
            )
            if pending_count:
                self.lbl_instruction.config(text=self.t("clock_instr_offline_saved", count=pending_count))
            else:
                self.lbl_instruction.config(text=self.t("clock_instr_offline"))

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
        if self.current_state not in ("REST", "WAITING_CARD", "INFO"):
            logger.info("Ignoring badge scan while screen is %s.", self.current_state)
            return
        if not self._scan_ui_lock.acquire(blocking=False):
            return

        pending_context = self.active_action

        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
            self.timeout_timer = None

        if pending_context:
            detail = self.t("overlay_checking_badge")
        else:
            detail = self.t("overlay_saving_stamp")
        self.lbl_overlay_title.config(text=self.t("overlay_one_moment"), fg=self.COLORS["accent"])
        self.lbl_overlay_details.config(
            text=detail,
            fg=self.COLORS["subtext"],
            font=(FONT_FAMILY, FONT_OVERLAY_BODY),
        )
        self.rest_frame.pack_forget()
        self.overlay_frame.pack(expand=True, fill="both")
        self.current_state = "UPLOADING"

        def worker():
            if pending_context in ("ADMIN", "SETTINGS"):
                if uid in config.ADMIN_BADGE_UIDS:
                    result = {"success": True, "admin_access": True, "target": "settings" if pending_context == "SETTINGS" else "admin"}
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
                elif queued_after_sync.get("status") == "rejected" and response_text:
                    try:
                        result = json.loads(response_text)
                    except ValueError:
                        result = {
                            "success": False,
                            "message": queued_after_sync.get("last_sync_error") or "Server rejected this scan.",
                        }
                    result["queue_pending"] = summary.get("pending", self.scan_queue.pending_count())
                else:
                    result = {
                        "success": True,
                        "offline_saved": True,
                        "request_id": queued["request_id"],
                        "client_local_time": queued["client_local_time"],
                        "queue_pending": summary.get("pending", self.scan_queue.pending_count()),
                    }
            if pending_context in ("CHECK_STATUS", "FLEXTIME", "HOLIDAY") and result:
                result["query_kind"] = pending_context
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
        terminal_msg = (result or {}).get("terminal_message") or (result or {}).get("message") or ""
        detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY)

        if ok and (result or {}).get("admin_access"):
            if (result or {}).get("target") == "settings":
                self._open_settings_page()
            else:
                self._open_admin_page(uid)
            return
        if (result or {}).get("admin_denied"):
            title = self.t("admin_denied_title")
            color = self.COLORS["error"]
            details = self.t("admin_denied_body")
        elif (result or {}).get("query_failed"):
            title = {
                "CHECK_STATUS": self.t("query_unavailable_status"),
                "FLEXTIME": self.t("query_unavailable_flextime"),
                "HOLIDAY": self.t("query_unavailable_holiday"),
            }.get((result or {}).get("query_kind"), self.t("query_unavailable_default"))
            if (result or {}).get("http_status") == 404:
                title = self.t("badge_not_registered")
                color = self.COLORS["error"]
                details = terminal_msg or self.t("badge_not_assigned")
            else:
                color = self.COLORS["warning"]
                details = terminal_msg or self.t("server_no_answer")
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 2, "bold")
        elif ok and (result or {}).get("offline_saved"):
            pending = int((result or {}).get("queue_pending", self.scan_queue.pending_count()))
            title = self.t("saved_offline_title")
            color = self.COLORS["warning"]
            details = self.t("saved_offline_body")
            if pending:
                details = f"{details}\n\n{self.t('saved_offline_pending', count=pending)}"
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 2, "bold")
        elif ok and pending_context:
            title = {
                "CHECK_STATUS": self.t("query_title_status"),
                "FLEXTIME": self.t("query_title_flextime"),
                "HOLIDAY": self.t("query_title_holiday"),
            }.get(pending_context, self.t("query_title_default"))
            color = self.COLORS["accent"]
            state_raw = ((result or {}).get("state") or "").lower()
            state_label = {
                "in": self.t("state_in"),
                "out": self.t("state_out"),
            }.get(state_raw, self.t("state_none"))
            since = (result or {}).get("since") or ""
            worked = (result or {}).get("worked_today_hm") or "0:00"
            status_lines = [
                self.t("status_line_status", state=state_label),
                self.t("status_line_worked", worked=worked),
            ]
            if since:
                status_lines.append(self.t("status_line_since", since=since))
            status_block = "\n".join(status_lines)

            if pending_context == "CHECK_STATUS":
                details = status_block
            elif pending_context == "FLEXTIME":
                extra = terminal_msg or self.t("flextime_no_balance")
                details = f"{status_block}\n\n{extra}"
            elif pending_context == "HOLIDAY":
                extra = terminal_msg or self.t("holiday_no_balance")
                details = f"{status_block}\n\n{extra}"
            else:
                details = terminal_msg or status_block
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 3, "bold")
        elif ok:
            # Successful stamp: simplified and large
            if ev == "in":
                title = self.t("checked_in_title")
                details = self.t("checked_in_body")
            elif ev == "out":
                title = self.t("checked_out_title")
                details = self.t("checked_out_body")
            else:
                title = self.t("stamp_success_title")
                details = self.t("stamp_success_body")
                
            color = self.COLORS["success"] if ev == "in" else (self.COLORS["error"] if ev == "out" else self.COLORS["success"])
            detail_font = (FONT_FAMILY, 32, "bold")
            
            # Short auto-return for successful stamps (5 seconds)
            if self.timeout_timer:
                self.after_cancel(self.timeout_timer)
            self.timeout_timer = self.after(5000, self.revert_to_rest)
        else:
            if (result or {}).get("http_status") == 404:
                title = self.t("badge_not_registered")
                details = terminal_msg or self.t("badge_not_assigned")
            else:
                title = self.t("action_failed_title")
                details = terminal_msg or self.t("action_failed_body")
            color = self.COLORS["error"]
            detail_font = (FONT_FAMILY, FONT_OVERLAY_BODY + 1, "bold")

        self.lbl_overlay_title.config(text=title, fg=color)
        self.lbl_overlay_details.config(
            text=details,
            fg="#ffffff",
            font=detail_font,
        )
        self._update_connection_copy(self.scan_queue.pending_count())
        
        # All scan results (info, status, stamps, failures) return to home in 5 seconds
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
        self.timeout_timer = self.after(5000, self.revert_to_rest)

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
            bg=self.COLORS["bg"],
            border=self.COLORS["card_border"],
            radius=18,
        )
        self.user_panel_frame.pack(expand=True, fill="both", padx=20, pady=20)
        user_panel = self.user_panel_frame.inner

        tk.Label(
            user_panel,
            text=self.t("user_title"),
            font=(FONT_FAMILY, FONT_OVERLAY_TITLE),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(pady=(26, 6))
        tk.Label(
            user_panel,
            text=(
                self.t("user_subtitle_online")
                if self.api_session.is_approved
                else self.t("user_subtitle_offline")
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
                self.t("btn_flextime"),
                lambda: self.action_button_clicked("FLEXTIME"),
                width=260,
                height=70,
                bg=self.COLORS["card"],
                fg=self.COLORS["text"],
                active_bg=self.COLORS["card"],
                border=self.COLORS["card_border"],
                font=(FONT_FAMILY, FONT_BUTTON + 4, "bold"),
            ).pack(side="left", expand=True, padx=(0, 8))
            RoundedButton(
                choices,
                self.t("btn_holidays"),
                lambda: self.action_button_clicked("HOLIDAY"),
                width=260,
                height=70,
                bg=self.COLORS["card"],
                fg=self.COLORS["text"],
                active_bg=self.COLORS["card"],
                border=self.COLORS["card_border"],
                font=(FONT_FAMILY, FONT_BUTTON + 4, "bold"),
            ).pack(side="left", expand=True, padx=(8, 0))
        
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
        if self.settings_frame is not None:
            self.settings_frame.destroy()
            self.settings_frame = None
        self.overlay_frame.pack_forget()
        self.rest_frame.pack_forget()
        if self.admin_frame is not None:
            self.admin_frame.destroy()

        self.admin_frame = tk.Frame(self.body, bg=self.COLORS["bg"])
        self.admin_frame.pack(expand=True, fill="both")

        # Admin panel timeout is much longer (1 minute)
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
        self.timeout_timer = self.after(60000, self.revert_to_rest)

        header_panel = RoundedPanel(
            self.admin_frame,
            bg=self.COLORS["surface"],
            border=self.COLORS["card"],
            radius=16,
            height=48,
        )
        header_panel.pack(fill="x", pady=(0, 8))
        header = header_panel.inner
        tk.Label(
            header,
            text=self.t("admin_title"),
            font=(FONT_FAMILY, FONT_HEADER_TITLE + 2),
            bg=self.COLORS["surface"],
            fg=self.COLORS["text"],
        ).pack(side="left", padx=10, pady=6)
        tk.Label(
            header,
            text=self.t("admin_unlocked"),
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
            text=self.t("admin_stamps_title"),
            font=(FONT_FAMILY, FONT_OVERLAY_BODY, "bold"),
            anchor="w",
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
        ).pack(fill="x")

        rows_panel = RoundedPanel(
            self.admin_frame,
            bg=self.COLORS["bg"],
            border=self.COLORS["card_border"],
            radius=16,
        )
        rows_panel.pack(fill="both", expand=True, pady=(4, 8))
        self.admin_rows_frame = rows_panel.inner

        controls = tk.Frame(self.admin_frame, bg=self.COLORS["bg"])
        controls.pack(fill="x", pady=10)
        
        RoundedButton(
            controls,
            self.t("admin_sync_btn"),
            self._admin_retry_sync,
            width=170,
            height=48,
            bg=self.COLORS["sidebar_active"],
            fg=self.COLORS["text"],
            active_bg=self.COLORS["sidebar_active"],
            border=self.COLORS["card_border"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(side="left", expand=True, padx=5)

        RoundedButton(
            controls,
            self.t("admin_refresh_btn"),
            self._refresh_admin_page,
            width=170,
            height=48,
            bg=self.COLORS["sidebar_active"],
            fg=self.COLORS["text"],
            active_bg=self.COLORS["sidebar_active"],
            border=self.COLORS["card_border"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(side="left", expand=True, padx=5)

        RoundedButton(
            controls,
            self.t("admin_clear_btn"),
            self._admin_clear_local_stamps,
            width=170,
            height=48,
            bg=self.COLORS["error"],
            fg="#ffffff",
            active_bg="#b91c1c",
            border=self.COLORS["error"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(side="left", expand=True, padx=5)

        self._refresh_admin_page()
        self._schedule_idle_return()

    def _open_settings_page(self):
        """Admin-only settings panel UI."""
        if self.timeout_timer:
            self.after_cancel(self.timeout_timer)
        self.current_state = "SETTINGS"
        self._set_nav_active("settings")

        if self.admin_frame:
            self.admin_frame.destroy()
            self.admin_frame = None
        if self.user_panel_frame:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None
        self.overlay_frame.pack_forget()
        self.rest_frame.pack_forget()

        self._settings_draft_lang = self.current_lang
        self._settings_draft_sync_ms = self.sync_interval_ms
        self._settings_suppress_events = True

        self._destroy_settings_page()

        c = self.COLORS
        panel_bg = c["bg"]
        card_bg = c["card"]
        row_alt = c["bg"]
        border = c["card_border"]

        self.settings_frame = tk.Frame(self.body, bg=panel_bg)
        self.settings_frame.pack(expand=True, fill="both", padx=30, pady=20)
        self.settings_frame.grid_rowconfigure(1, weight=1)
        self.settings_frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            self.settings_frame,
            text=self.t("settings_title"),
            font=(FONT_FAMILY, 18, "bold"),
            bg=panel_bg,
            fg=c["text"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        scroll_outer = tk.Frame(self.settings_frame, bg=panel_bg)
        scroll_outer.grid(row=1, column=0, sticky="nsew")
        scroll_outer.grid_rowconfigure(0, weight=1)
        scroll_outer.grid_columnconfigure(0, weight=1)

        scroll_border = tk.Frame(scroll_outer, bg=border, padx=1, pady=1)
        scroll_border.grid(row=0, column=0, sticky="nsew")
        scroll_border.grid_rowconfigure(0, weight=1)
        scroll_border.grid_columnconfigure(0, weight=1)

        scroll_wrap = tk.Frame(scroll_border, bg=card_bg)
        scroll_wrap.grid(row=0, column=0, sticky="nsew")
        scroll_wrap.grid_rowconfigure(0, weight=1)
        scroll_wrap.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_wrap, bg=card_bg, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(scroll_wrap, orient="vertical", command=canvas.yview, style="Settings.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 2))
        self._settings_canvas = canvas

        inner = tk.Frame(canvas, bg=card_bg)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfigure(inner_id, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        self._bind_settings_scroll(canvas, inner, scroll_wrap, scroll_border)

        scroll_btns = tk.Frame(scroll_outer, bg=panel_bg)
        scroll_btns.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        for label, direction in (("\u25b2", -3), ("\u25bc", 3)):
            btn = tk.Button(
                scroll_btns,
                text=label,
                font=(FONT_FAMILY, 13, "bold"),
                width=3,
                height=1,
                bg=card_bg,
                fg=c["subtext"],
                activebackground=c["sidebar_active"],
                activeforeground=c["text"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=border,
                highlightcolor=c["accent"],
                bd=0,
                cursor="hand2",
                command=lambda d=direction: self._settings_scroll_page(d),
            )
            btn.pack(pady=4, ipady=4)

        def add_setting(label_text, **combo_kwargs):
            row = tk.Frame(
                inner,
                bg=card_bg,
                height=56,
                highlightthickness=1,
                highlightbackground=border,
                highlightcolor=border,
            )
            row.pack(fill="x", pady=8, padx=16)
            row.pack_propagate(False)
            tk.Label(
                row,
                text=label_text,
                font=(FONT_FAMILY, 12, "bold"),
                bg=card_bg,
                fg=c["text"],
            ).pack(side="left", padx=(4, 8))
            combo_kwargs.setdefault("style", "Settings.TCombobox")
            w = ttk.Combobox(row, **combo_kwargs)
            w.pack(side="right", padx=4, ipady=2)
            return w

        def add_section_title(title: str):
            sec = tk.Frame(inner, bg=card_bg, height=28)
            sec.pack(fill="x", padx=16, pady=(12, 4))
            sec.pack_propagate(False)
            tk.Label(sec, text=title, font=(FONT_FAMILY, 12, "bold"), bg=card_bg, fg=c["subtext"]).pack(side="left")

        def add_detail_row(label_text, value_text, value_color):
            row = tk.Frame(inner, bg=row_alt, height=42, highlightthickness=1, highlightbackground=border)
            row.pack(fill="x", pady=3, padx=16)
            row.pack_propagate(False)
            tk.Label(row, text=label_text, font=(FONT_FAMILY, 10), bg=row_alt, fg=c["subtext"]).pack(side="left", padx=10)
            tk.Label(row, text=value_text, font=(FONT_FAMILY, 10, "bold"), bg=row_alt, fg=value_color).pack(side="right", padx=10)

        self._settings_sync_map = {"30s": 30000, "1 min": 60000, "5 min": 300000}
        sync_rev = {v: k for k, v in self._settings_sync_map.items()}

        self._settings_lang_cb = add_setting(
            self.t("settings_lang"),
            values=[SETTINGS_LANG_EN, SETTINGS_LANG_DE],
            state="readonly",
            width=14,
        )
        self._settings_lang_cb.set(self._combo_label_for_lang(self._settings_draft_lang))
        self._settings_lang_cb.bind("<<ComboboxSelected>>", self._on_settings_lang_changed)

        self._settings_sync_cb = add_setting(
            self.t("settings_sync"),
            values=list(self._settings_sync_map.keys()),
            state="readonly",
            width=10,
        )
        self._settings_sync_cb.set(sync_rev.get(self._settings_draft_sync_ms, "1 min"))
        self._settings_sync_cb.bind("<<ComboboxSelected>>", self._on_settings_sync_changed)

        add_section_title(self.t("settings_network"))
        for label_key, value, value_color in self._get_network_info():
            add_detail_row(self.t(label_key), value, value_color)

        add_section_title(self.t("settings_info"))
        counts = self.scan_queue.status_counts()
        for lbl_key, val_txt, col in [
            ("info_version", "1.0.0", c["subtext"]),
            ("info_total_sent", str(counts.get("synced", 0)), c["success"]),
            ("info_pending_sync", str(counts.get("pending", 0)), c["warning"]),
            ("info_failed", str(counts.get("failed", 0)), c["error"]),
        ]:
            add_detail_row(self.t(lbl_key), val_txt, col)

        tk.Frame(inner, bg=card_bg, height=8).pack(fill="x")

        self._settings_suppress_events = False
        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

        save_footer = tk.Frame(self.settings_frame, bg=panel_bg)
        save_footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        RoundedButton(
            save_footer,
            self.t("settings_save"),
            self._commit_settings,
            width=280,
            height=50,
            bg=self.COLORS["success"],
            fg="#ffffff",
            active_bg="#059669",
            border=self.COLORS["success"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack()

    def _load_settings(self):
        try:
            if os.path.exists(self.settings_path):
                with open(self.settings_path, "r") as f:
                    return json.load(f)
        except: pass
        return {"lang": "EN", "sync_interval_ms": 60000, "timeout_ms": 60000}

    def _read_settings_draft_from_widgets(self):
        """Read unsaved combobox values into draft fields (does not apply to the running app)."""
        if hasattr(self, "_settings_lang_cb") and self._settings_lang_cb.winfo_exists():
            val = self._settings_lang_cb.get()
            self._settings_draft_lang = self._lang_from_combo_label(val)
        if hasattr(self, "_settings_sync_cb") and self._settings_sync_cb.winfo_exists():
            key = self._settings_sync_cb.get()
            self._settings_draft_sync_ms = self._settings_sync_map.get(key, self._settings_draft_sync_ms)

    def _on_settings_lang_changed(self, _event=None):
        """Remember language choice locally; home screen stays unchanged until Save."""
        if getattr(self, "_settings_suppress_events", False):
            return
        self._read_settings_draft_from_widgets()

    def _on_settings_sync_changed(self, _event=None):
        """Remember sync interval locally; heartbeat interval unchanged until Save."""
        if getattr(self, "_settings_suppress_events", False):
            return
        self._read_settings_draft_from_widgets()

    def _commit_settings(self):
        """Apply draft settings to the running app and persist to disk."""
        self._read_settings_draft_from_widgets()
        self.current_lang = self._settings_draft_lang
        self.sync_interval_ms = self._settings_draft_sync_ms
        self.translator.set_lang(self.current_lang)
        self.title(self.t("window_title"))
        self.settings = {
            "lang": self.current_lang,
            "sync_interval_ms": self.sync_interval_ms,
            "timeout_ms": self.IDLE_TIMEOUT_MS,
        }
        try:
            with open(self.settings_path, "w") as f:
                json.dump(self.settings, f, indent=2)
            self._apply_translations()
            self._show_info_screen("\u2713", self.t("save_success"), self.COLORS["success"])
        except Exception as e:
            self._show_info_screen(
                self.t("save_error_title"),
                self.t("save_error_body", error=e),
                self.COLORS["error"],
            )

    def _save_settings_to_disk(self):
        """Legacy alias — use :meth:`_commit_settings`."""
        self._commit_settings()

    def _apply_translations(self):
        self.lbl_instruction.config(text=self.t("clock_instr"))
        if hasattr(self, "nav_items"):
            for key, widgets in self.nav_items.items():
                _, _, lbl = widgets
                lbl.config(text=self.t(f"nav_{key}"))
        if hasattr(self, "btn_status"):
            self.btn_status.text = self.t("btn_status")
            self.btn_status._draw(self.btn_status.normal_bg)
        if hasattr(self, "status_beacon") and hasattr(self, "scan_queue"):
            self._update_connection_copy(self.scan_queue.pending_count())

    def _get_network_info(self):
        """Return list of (label_key, value, color) tuples for the network settings section."""
        rows = []
        ok_c = self.COLORS["success"]
        err_c = self.COLORS["error"]
        sub_c = self.COLORS["subtext"]
        none = self.t("settings_ip_none")

        local_ip = self._get_local_ip()
        if local_ip:
            rows.append(("settings_ip", local_ip, ok_c))
        else:
            rows.append(("settings_ip", none, sub_c))

        if local_ip:
            rows.append(("net_status", self.t("net_online"), ok_c))
        else:
            rows.append(("net_status", self.t("net_offline"), err_c))

        try:
            rows.append(("net_hostname", socket.gethostname(), sub_c))
        except OSError:
            pass

        try:
            gw_out = subprocess.check_output(
                ["ip", "route", "show", "default"], timeout=2, stderr=subprocess.DEVNULL
            ).decode().strip()
            parts = gw_out.split()
            gw_ip = parts[2] if len(parts) > 2 else none
            gw_iface = parts[4] if len(parts) > 4 else ""
            rows.append(("net_gateway", f"{gw_ip}  ({gw_iface})", sub_c))
        except (OSError, subprocess.SubprocessError, IndexError):
            pass

        try:
            with open("/etc/resolv.conf", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("nameserver"):
                        rows.append(("net_dns", line.split()[1], sub_c))
                        break
        except OSError:
            pass

        try:
            ssid_out = subprocess.check_output(
                ["iwgetid", "-r"], timeout=2, stderr=subprocess.DEVNULL
            ).decode().strip()
            if ssid_out:
                rows.append(("net_wifi_ssid", ssid_out, sub_c))
        except (OSError, subprocess.SubprocessError):
            pass

        try:
            iwconfig_out = subprocess.check_output(
                ["iwconfig"], timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            for line in iwconfig_out.splitlines():
                if "Signal level" in line:
                    import re
                    m = re.search(r"Signal level=(-\d+) dBm", line)
                    if m:
                        dbm = int(m.group(1))
                        if dbm > -50:
                            quality = self.t("signal_excellent")
                        elif dbm > -65:
                            quality = self.t("signal_good")
                        elif dbm > -75:
                            quality = self.t("signal_fair")
                        else:
                            quality = self.t("signal_weak")
                        rows.append((
                            "net_signal",
                            f"{dbm} dBm  ({quality})",
                            ok_c if dbm > -65 else self.COLORS["warning"],
                        ))
                        break
        except (OSError, subprocess.SubprocessError):
            pass

        return rows

    def _update_brightness(self, val):
        self.current_brightness = int(float(val))
        level = str(int(self.current_brightness * 2.55))   # scale 0-100 -> 0-255
        paths = [
            "/sys/class/backlight/rpi_backlight/brightness",
            "/sys/class/backlight/soc:backlight/brightness",
        ]
        for p in paths:
            if os.path.exists(p):
                # Try direct write first (works if running as root)
                try:
                    with open(p, "w") as f:
                        f.write(level)
                    break
                except PermissionError:
                    pass
                # Fall back to: echo LEVEL | sudo tee /sys/...
                # Requires the sudoers rule from install_sudoers.sh
                try:
                    subprocess.run(
                        ["sudo", "tee", p],
                        input=level.encode(),
                        stdout=subprocess.DEVNULL,
                        timeout=1,
                    )
                    break
                except Exception:
                    pass

    def _refresh_admin_page(self):
        """Reload local SQLite queue rows into the admin page."""
        if self.admin_frame is None or not self.admin_frame.winfo_exists():
            return
        counts = self.scan_queue.status_counts()
        pending = counts.get("pending", 0)
        failed = counts.get("failed", 0)
        synced = counts.get("synced", 0)
        self.admin_summary_label.config(
            text=self.t("admin_summary", pending=pending, failed=failed, synced=synced)
        )

        for child in self.admin_rows_frame.winfo_children():
            child.destroy()

        rows = self.scan_queue.issue_scans(limit=6)
        if not rows:
            tk.Label(
                self.admin_rows_frame,
                text=self.t("admin_all_good"),
                font=(FONT_FAMILY, FONT_OVERLAY_BODY),
                bg=self.COLORS["card"],
                fg=self.COLORS["success"],
            ).pack(expand=True)
            return

        for row in rows:
            status_color = self.COLORS["error"] if row["status"] == "failed" else self.COLORS["warning"]
            label = self.t("admin_row_retry") if row["status"] == "failed" else self.t("admin_row_waiting")
            detail = row["last_sync_error"] or self.t("admin_row_retry_auto")
            line = (
                f"{label}  {row['client_local_time']}\n"
                f"{self.t('admin_row_attempts', count=row['retry_count'], detail=detail)}"
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

    def _admin_clear_local_stamps(self):
        """Debug button: delete local SQLite scan rows only; server-side stamps are untouched."""
        removed = self.scan_queue.clear_local_scans()
        if self.admin_summary_label:
            self.admin_summary_label.config(text=f"Deleted {removed} local stamp row(s).")
        self._refresh_admin_page()
        self._schedule_idle_return()

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
        if self.settings_frame is not None:
            self.settings_frame.destroy()
            self.settings_frame = None
        self.rest_frame.pack(expand=True, fill="both")
        self.current_state = "REST"
        self._update_connection_copy(self.scan_queue.pending_count())

    def action_button_clicked(self, action_type):
        """
        Footer buttons: switch to “wait for card” mode with a contextual overlay title.

        Args:
            action_type: ``CHECK_STATUS`` | ``FLEXTIME`` | ``HOLIDAY`` — forwarded to the query API.
        """
        if action_type in ("CHECK_STATUS", "FLEXTIME", "HOLIDAY") and not self.api_session.is_approved and not self.api_session.authenticate(silent=True):
            self._show_info_screen(
                self.t("offline_not_available_title"),
                self.t("offline_not_available_body"),
                self.COLORS["warning"],
            )
            return
        self.current_state = "WAITING_CARD"
        self.active_action = action_type
        self._set_nav_active(action_type.lower() if action_type in ("ADMIN", "SETTINGS") else "home")

        if action_type == "ADMIN":
            self._show_info_screen(self.t("auth_title"), self.t("auth_admin"), self.COLORS["accent"])
        elif action_type == "SETTINGS":
            self._show_info_screen(self.t("auth_title"), self.t("auth_settings"), self.COLORS["accent"])
        else:
            wait_title = {
                "CHECK_STATUS": self.t("overlay_wait_status"),
                "FLEXTIME": self.t("overlay_wait_flextime"),
                "HOLIDAY": self.t("overlay_wait_holiday"),
            }.get(action_type, self.t("query_title_default"))
            self.lbl_overlay_title.config(text=wait_title, fg=self.COLORS["accent"])
            self.lbl_overlay_details.config(
                text=self.t("overlay_hold_badge"),
                fg=self.COLORS["subtext"],
                font=(FONT_FAMILY, FONT_OVERLAY_BODY),
            )
            self.rest_frame.pack_forget()
            self.overlay_frame.pack(expand=True, fill="both")

        if self.admin_frame is not None:
            self.admin_frame.destroy()
            self.admin_frame = None
        if self.user_panel_frame is not None:
            self.user_panel_frame.destroy()
            self.user_panel_frame = None
        if self.settings_frame is not None:
            self.settings_frame.destroy()
            self.settings_frame = None

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
        if self.settings_frame is not None:
            self.settings_frame.destroy()
            self.settings_frame = None
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
