"""App window: camera on the left; on the right, the session view or the settings
page. Sounds the alarm (and buzzes the phone) when the eyes stay closed too long."""

import json
import re
import secrets
import threading
import time
import tkinter as tk
from datetime import date, datetime

import customtkinter as ctk
import qrcode
import requests
from PIL import Image, ImageDraw, ImageTk

import config
from alerts import PhoneAlert, SoundAlarm
from eye_tracker import EyeTracker

REFRESH_MS = 33  # ~30 updates per second
SETTINGS_PATH = config.PROJECT_DIR / "settings.json"
SESSIONS_PATH = config.PROJECT_DIR / "sessions.json"
MIN_ALARM_DELAY, MAX_ALARM_DELAY = 1, 60  # seconds
MIN_SAVED_SESSION = 10  # seconds; shorter sessions aren't recorded

# Layout (pixels). The camera area has a fixed size so the window never jumps.
CAM_W, CAM_H = 720, 540   # 4:3 preview
CAM_INSET = 10            # gap between the camera card's border and the video
SIDEBAR_W = 360
SETTINGS_TEXT_W = 300  # wrap width for text in the settings columns
QR_SIZE = 160
GAP = 20

# Palette
BG = "#0e1014"
CARD = "#171a21"
CARD_BORDER = "#262a33"
RAISED = "#20242d"
RAISED_HOVER = "#2a2f3a"
TEXT = "#f1f3f7"
MUTED = "#8b93a3"
ACCENT = "#7c83ff"
ACCENT_HOVER = "#6970f0"
GREEN = "#34d399"
GREEN_HOVER = "#10b981"
AMBER = "#fbbf24"
RED = "#f87171"

# Status pill styles: (text color, tinted background)
PILL = {
    "idle": (MUTED, RAISED),
    "info": (ACCENT, "#1f2240"),
    "good": (GREEN, "#123028"),
    "warn": (AMBER, "#33290f"),
    "alarm": (RED, "#3a1717"),
}


def format_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def format_short(seconds):
    """Compact duration for stat tiles: 45s, 12m, 1h 05m."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def qr_image(text):
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(text)
    return qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


def font(size, weight="normal"):
    return ctk.CTkFont(size=size, weight=weight)


class StudyApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.title("No-Sleep Study")
        self.resizable(False, False)

        self.settings = load_json(SETTINGS_PATH, {})
        if "phone_topic" not in self.settings:
            # Random so strangers can't guess it (anyone who knows a topic can read it).
            self.settings["phone_topic"] = f"no-sleep-{secrets.token_hex(6)}"
            self._save_settings()
        self.alarm = SoundAlarm(config.ALARM_SOUND, config.ALARM_VOLUME_BOOST,
                                self.settings.get("alarm_volume", config.ALARM_SYSTEM_VOLUME))
        self.phone = PhoneAlert(config.NTFY_SERVER, self.settings["phone_topic"],
                                config.PHONE_ALERT_INTERVAL)
        self.alarm_delay = self.settings.get("alarm_delay", config.CLOSED_SECONDS_BEFORE_ALARM)
        self.closed_ratio = self.settings.get("closed_ratio", config.CLOSED_RATIO)

        self.tracker = None
        self.alarm_ringing = False
        self.session_started = None
        self.awake_since = None
        self.wake_ups = 0
        self._status = None

        # The preview is one PhotoImage we paste each new frame into.
        self._photo = ImageTk.PhotoImage(Image.new("RGB", (CAM_W, CAM_H), CARD))
        self._mask = rounded_mask((CAM_W, CAM_H), 12)
        self._card_fill = Image.new("RGB", (CAM_W, CAM_H), CARD)

        self._build_camera_card()
        self._build_sidebar()
        self._show_placeholder("Camera is off", "Start a session and the camera will appear here.")
        self._set_status("Ready to study", "idle")
        self._refresh_today()

        self.bind("<Command-Return>", lambda e: self._toggle_session())
        self.bind("<Command-comma>", lambda e: self._open_settings())
        self.bind("<Escape>", lambda e: self._close_settings())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick()

    def _save_settings(self):
        save_json(SETTINGS_PATH, self.settings)

    # ---------- layout: camera ----------

    def _build_camera_card(self):
        self.camera_card = ctk.CTkFrame(
            self, width=CAM_W + 2 * CAM_INSET, height=CAM_H + 2 * CAM_INSET,
            corner_radius=18, fg_color=CARD, border_width=3, border_color=CARD_BORDER,
        )
        self.camera_card.grid(row=0, column=0, padx=(GAP, GAP // 2), pady=GAP)
        self.camera_card.grid_propagate(False)

        self.video = tk.Label(self.camera_card, image=self._photo, bd=0,
                              highlightthickness=0, bg=CARD)

        self.placeholder = ctk.CTkFrame(self.camera_card, fg_color="transparent")
        ctk.CTkLabel(self.placeholder, text="◉", font=font(44), text_color=CARD_BORDER).pack()
        self.placeholder_title = ctk.CTkLabel(self.placeholder, text="", font=font(22, "bold"),
                                              text_color=TEXT)
        self.placeholder_title.pack(pady=(6, 2))
        self.placeholder_body = ctk.CTkLabel(self.placeholder, text="", font=font(14),
                                             text_color=MUTED, wraplength=CAM_W - 120)
        self.placeholder_body.pack()

    # ---------- layout: sidebar ----------

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_W, height=CAM_H + 2 * CAM_INSET,
                                    corner_radius=18, fg_color=CARD, border_width=1,
                                    border_color=CARD_BORDER)
        self.sidebar.grid(row=0, column=1, padx=(GAP // 2, GAP), pady=GAP)
        self.sidebar.pack_propagate(False)

        self.session_view = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.session_view.pack(fill="both", expand=True, padx=24, pady=18)
        self._build_session_view(self.session_view)
        self._build_settings_page()

    def _open_settings(self):
        # Covers the whole window; tracking and the alarm keep running underneath.
        self.settings_page.place(x=0, y=0, relwidth=1, relheight=1)
        self.settings_page.lift()

    def _close_settings(self):
        self.settings_page.place_forget()

    def _build_session_view(self, view):
        header = ctk.CTkFrame(view, fg_color="transparent")
        header.pack(fill="x")
        title = ctk.CTkFrame(header, fg_color="transparent")
        title.pack(side="left")
        ctk.CTkLabel(title, text="No-Sleep Study", font=font(18, "bold"), text_color=TEXT,
                     height=24).pack(anchor="w")
        self.clock_label = ctk.CTkLabel(title, text="", font=font(12), text_color=MUTED, height=16)
        self.clock_label.pack(anchor="w")
        self._icon_button(header, "⚙", self._open_settings).pack(side="right")

        self._section_label(view, "SESSION").pack(anchor="w", pady=(14, 0))
        self.timer_label = ctk.CTkLabel(view, text=format_duration(0), text_color=TEXT,
                                        font=ctk.CTkFont(family="Helvetica Neue", size=48,
                                                         weight="bold"))
        self.timer_label.pack(anchor="w")

        self.status_pill = ctk.CTkLabel(view, text="", height=38, corner_radius=19,
                                        font=font(14, "bold"))
        self.status_pill.pack(fill="x", pady=(6, 0))

        closed_row = ctk.CTkFrame(view, fg_color="transparent")
        closed_row.pack(fill="x", pady=(10, 2))
        ctk.CTkLabel(closed_row, text="Eyes closed", font=font(12),
                     text_color=MUTED).pack(side="left")
        self.closed_label = ctk.CTkLabel(closed_row, text="", font=font(12), text_color=MUTED)
        self.closed_label.pack(side="right")
        self.closed_bar = ctk.CTkProgressBar(view, height=6, corner_radius=3,
                                             fg_color=RAISED, progress_color=AMBER)
        self.closed_bar.set(0)
        self.closed_bar.pack(fill="x")

        stats = ctk.CTkFrame(view, fg_color="transparent")
        stats.pack(fill="x", pady=(14, 0))
        stats.columnconfigure((0, 1), weight=1, uniform="stats")
        self.wakeups_value = self._stat_tile(stats, 0, 0, "Wake-ups")
        self.awake_value = self._stat_tile(stats, 0, 1, "Awake for")
        self.today_value = self._stat_tile(stats, 1, 0, "Studied today")
        self.openness_value = self._stat_tile(stats, 1, 1, "Eye openness")

        self.session_button = ctk.CTkButton(view, text="", height=46, corner_radius=12,
                                            font=font(15, "bold"), command=self._toggle_session)
        self.session_button.pack(side="bottom", fill="x")
        ctk.CTkLabel(view, text="⌘ Return to start or end", font=font(11), text_color=MUTED,
                     height=14).pack(side="bottom", pady=(0, 6))
        self._style_session_button(active=False)

    def _build_settings_page(self):
        page = self.settings_page = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.pack(fill="x", padx=GAP, pady=(GAP, 14))
        ctk.CTkLabel(header, text="Settings", font=font(24, "bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkButton(header, text="Done", width=84, height=34, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BG,
                      font=font(13, "bold"), command=self._close_settings).pack(side="right")
        # Mirrors the session status so you can still keep an eye on it from here.
        self.settings_status = ctk.CTkLabel(header, text="", height=30, corner_radius=15,
                                            font=font(12, "bold"))
        self.settings_status.pack(side="right", padx=(0, 12))

        columns = ctk.CTkFrame(page, fg_color="transparent")
        columns.pack(fill="both", expand=True, padx=GAP, pady=(0, GAP))
        columns.columnconfigure((0, 1, 2), weight=1, uniform="settings")
        columns.rowconfigure(0, weight=1)

        # Alarm
        alarm = self._settings_card(columns, 0, "Alarm", "What happens when you doze off.")
        self.volume_value = self._slider_row(
            alarm, "Volume", 0, 100, 100, self.alarm.system_volume or 0, self._on_volume_change)
        self.delay_value = self._slider_row(
            alarm, "Ring after eyes closed for", MIN_ALARM_DELAY, MAX_ALARM_DELAY,
            MAX_ALARM_DELAY - MIN_ALARM_DELAY, self.alarm_delay, self._on_delay_change)
        self._on_volume_change(self.alarm.system_volume or 0, save=False)
        self._on_delay_change(self.alarm_delay, save=False)
        self._secondary_button(alarm, "▶  Test sound", self.alarm.play_once).pack(
            fill="x", pady=(12, 0))

        # Phone alerts
        phone = self._settings_card(columns, 1, "Phone alerts", "Buzz your phone, too.")
        switch_row = ctk.CTkFrame(phone, fg_color="transparent")
        switch_row.pack(fill="x")
        ctk.CTkLabel(switch_row, text="Buzz my phone", font=font(13),
                     text_color=TEXT).pack(side="left")
        self.phone_enabled_var = tk.BooleanVar(value=self.settings.get("phone_enabled", True))
        ctk.CTkSwitch(switch_row, text="", width=46, variable=self.phone_enabled_var,
                      progress_color=GREEN, command=self._on_phone_toggle).pack(side="right")
        self._hint(phone, "Sends a notification every second while the alarm rings. "
                          "Uses the free ntfy app.").pack(anchor="w", pady=(2, 10))

        setup = ctk.CTkFrame(phone, fg_color="transparent")
        setup.pack(fill="x")
        self.qr_label = ctk.CTkLabel(setup, text="", width=QR_SIZE, height=QR_SIZE)
        self.qr_label.pack(side="left", anchor="n")
        steps = ctk.CTkFrame(setup, fg_color="transparent")
        steps.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._step(steps, "1", "Install ntfy (App Store / Google Play)")
        self._step(steps, "2", "Scan this code with your phone camera, then tap Subscribe")

        ctk.CTkLabel(phone, text="Or subscribe manually to topic:", font=font(12),
                     text_color=MUTED, height=16).pack(anchor="w", pady=(10, 4))
        topic_row = ctk.CTkFrame(phone, fg_color="transparent")
        topic_row.pack(fill="x")
        self.topic_var = tk.StringVar(value=self.phone.topic)
        topic_entry = ctk.CTkEntry(topic_row, textvariable=self.topic_var, height=32,
                                   fg_color=RAISED, border_color=CARD_BORDER, text_color=TEXT,
                                   font=ctk.CTkFont(family="Menlo", size=12))
        topic_entry.pack(side="left", fill="x", expand=True)
        topic_entry.bind("<Return>", self._on_topic_change)
        topic_entry.bind("<FocusOut>", self._on_topic_change)
        self.copy_button = self._secondary_button(topic_row, "Copy", self._copy_topic, width=60)
        self.copy_button.pack(side="left", padx=(8, 0))

        self.phone_test_button = ctk.CTkButton(
            phone, text="Send test notification", height=34, corner_radius=10,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BG, font=font(13, "bold"),
            command=self._send_phone_test)
        self.phone_test_button.pack(fill="x", pady=(12, 0))
        self.phone_test_result = ctk.CTkLabel(phone, text="", font=font(12), text_color=MUTED,
                                              height=16)
        self.phone_test_result.pack(anchor="w", pady=(4, 0))
        self._refresh_qr()

        # Detection
        detection = self._settings_card(columns, 2, "Detection", "How eyes-closed is judged.")
        self.ratio_value = self._slider_row(
            detection, "Sensitivity", 0.5, 0.8, 6, self.closed_ratio, self._on_ratio_change)
        self.ratio_hint = self._hint(detection, "")
        self.ratio_hint.pack(anchor="w", pady=(6, 0))
        self._on_ratio_change(self.closed_ratio, save=False)
        self.recalibrate_button = self._secondary_button(
            detection, "↻  Recalibrate my eyes", self._recalibrate)
        self.recalibrate_button.pack(fill="x", pady=(12, 0))
        self._hint(detection, "Available during a session. Useful after moving seats or "
                              "if the lighting changes.").pack(anchor="w", pady=(4, 0))
        self.recalibrate_button.configure(state="disabled")

    # ---------- widget helpers ----------

    def _section_label(self, parent, text):
        return ctk.CTkLabel(parent, text=text, font=font(11, "bold"), text_color=MUTED, height=16)

    def _hint(self, parent, text):
        return ctk.CTkLabel(parent, text=text, font=font(12), text_color=MUTED,
                            wraplength=SETTINGS_TEXT_W, justify="left")

    def _icon_button(self, parent, text, command):
        return ctk.CTkButton(parent, text=text, width=34, height=34, corner_radius=10,
                             fg_color=RAISED, hover_color=RAISED_HOVER, text_color=TEXT,
                             font=font(16), command=command)

    def _secondary_button(self, parent, text, command, width=140):
        return ctk.CTkButton(parent, text=text, width=width, height=32, corner_radius=10,
                             fg_color=RAISED, hover_color=RAISED_HOVER, text_color=TEXT,
                             text_color_disabled=MUTED, font=font(13), command=command)

    def _settings_card(self, parent, column, title, subtitle):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=18, border_width=1,
                            border_color=CARD_BORDER)
        # Every column gets 12px of padding in total so the three cards end up equal width.
        card.grid(row=0, column=column, sticky="nsew", padx=[(0, 12), (6, 6), (12, 0)][column])
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=18)
        ctk.CTkLabel(inner, text=title, font=font(16, "bold"), text_color=TEXT,
                     height=22).pack(anchor="w")
        ctk.CTkLabel(inner, text=subtitle, font=font(12), text_color=MUTED,
                     height=16).pack(anchor="w", pady=(0, 14))
        return inner

    def _stat_tile(self, parent, row, column, title):
        tile = ctk.CTkFrame(parent, fg_color=RAISED, corner_radius=12)
        tile.grid(row=row, column=column, sticky="ew", pady=(0, 10) if row == 0 else 0,
                  padx=(0, 5) if column == 0 else (5, 0))
        ctk.CTkLabel(tile, text=title, font=font(11), text_color=MUTED,
                     height=14).pack(anchor="w", padx=12, pady=(8, 0))
        value = ctk.CTkLabel(tile, text="—", font=font(20, "bold"), text_color=TEXT, height=28)
        value.pack(anchor="w", padx=12, pady=(0, 8))
        return value

    def _slider_row(self, parent, title, low, high, steps, initial, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(row, text=title, font=font(13), text_color=TEXT, height=20).pack(side="left")
        value = ctk.CTkLabel(row, text="", font=font(13, "bold"), text_color=ACCENT, height=20)
        value.pack(side="right")
        slider = ctk.CTkSlider(parent, from_=low, to=high, number_of_steps=steps,
                               height=16, fg_color=RAISED, progress_color=ACCENT,
                               button_color=TEXT, button_hover_color="#ffffff",
                               command=command)
        slider.set(initial)
        slider.pack(fill="x", pady=(4, 6))
        return value

    def _step(self, parent, number, text):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text=number, width=22, height=22, corner_radius=11, fg_color=RAISED,
                     text_color=ACCENT, font=font(11, "bold")).pack(side="left", anchor="n")
        ctk.CTkLabel(row, text=text, font=font(12), text_color=TEXT, wraplength=112,
                     justify="left").pack(side="left", padx=(8, 0))

    def _set_status(self, text, style):
        if (text, style) == self._status:
            return  # skip redundant redraws; this runs ~30 times a second
        self._status = (text, style)
        color, background = PILL[style]
        self.status_pill.configure(text=text, text_color=color, fg_color=background)
        self.settings_status.configure(text=f"  {text}  ", text_color=color, fg_color=background)

    def _set_border(self, color):
        if color != self.camera_card.cget("border_color"):
            self.camera_card.configure(border_color=color)

    def _set_text(self, label, text):
        if label.cget("text") != text:
            label.configure(text=text)

    def _style_session_button(self, active):
        if active:
            self.session_button.configure(text="End Session", fg_color=RAISED,
                                          hover_color=RAISED_HOVER, text_color=RED)
        else:
            self.session_button.configure(text="Start Study Session", fg_color=GREEN,
                                          hover_color=GREEN_HOVER, text_color=BG)

    def _show_placeholder(self, title, body):
        self.video.place_forget()
        self.placeholder_title.configure(text=title)
        self.placeholder_body.configure(text=body)
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _show_video(self, rgb_frame):
        # Crop the frame to the preview's 4:3 shape, scale it, and round the corners.
        h, w = rgb_frame.shape[:2]
        crop_w = min(w, h * CAM_W // CAM_H)
        crop_h = crop_w * CAM_H // CAM_W
        x, y = (w - crop_w) // 2, (h - crop_h) // 2
        image = Image.fromarray(rgb_frame[y:y + crop_h, x:x + crop_w]).resize(
            (CAM_W, CAM_H), Image.BILINEAR)
        self._photo.paste(Image.composite(image, self._card_fill, self._mask))
        if not self.video.winfo_ismapped():
            self.placeholder.place_forget()
            self.video.place(relx=0.5, rely=0.5, anchor="center")

    # ---------- settings handlers ----------

    def _on_volume_change(self, value, save=True):
        volume = int(round(float(value)))
        self.volume_value.configure(text=f"{volume}%")
        self.alarm.system_volume = volume
        if save:
            self.settings["alarm_volume"] = volume
            self._save_settings()

    def _on_delay_change(self, value, save=True):
        delay = max(MIN_ALARM_DELAY, min(MAX_ALARM_DELAY, int(round(float(value)))))
        self.alarm_delay = delay
        self.delay_value.configure(text=f"{delay} sec")
        if save:
            self.settings["alarm_delay"] = delay
            self._save_settings()

    def _on_ratio_change(self, value, save=True):
        ratio = round(float(value), 2)
        self.closed_ratio = ratio
        self.ratio_value.configure(text=f"{ratio:.0%}")
        self.ratio_hint.configure(
            text=f"Your eyes count as closed when they're less than {ratio:.0%} as open as "
                 "normal. Raise it if the alarm misses you dozing off; lower it if it rings "
                 "while you're awake.")
        if self.tracker:
            self.tracker.closed_ratio = ratio
        if save:
            self.settings["closed_ratio"] = ratio
            self._save_settings()

    def _recalibrate(self):
        if self.tracker:
            self.tracker.recalibrate()
            self._close_settings()  # so you can watch it calibrate

    def _on_phone_toggle(self):
        enabled = self.phone_enabled_var.get()
        self.settings["phone_enabled"] = enabled
        self._save_settings()
        if not enabled:
            self.phone.stop()

    def _on_topic_change(self, event=None):
        topic = self.topic_var.get().strip()
        if topic == self.phone.topic:
            return
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
            self.topic_var.set(self.phone.topic)  # invalid: put back the old topic
            return
        self.phone.topic = topic
        self.settings["phone_topic"] = topic
        self._save_settings()
        self._refresh_qr()

    def _refresh_qr(self):
        image = qr_image(f"{config.NTFY_SERVER}/{self.phone.topic}")
        self._qr = ctk.CTkImage(image, size=(QR_SIZE, QR_SIZE))
        self.qr_label.configure(image=self._qr)

    def _copy_topic(self):
        self.clipboard_clear()
        self.clipboard_append(self.phone.topic)
        self.copy_button.configure(text="Copied")
        self.after(1500, lambda: self.copy_button.configure(text="Copy"))

    def _send_phone_test(self):
        self._on_topic_change()
        self.phone_test_button.configure(state="disabled")
        self.phone_test_result.configure(text="Sending…", text_color=MUTED)
        result = {}

        def send():
            try:
                self.phone.send("Test from No-Sleep Study", "Phone alerts are working.")
                result["ok"] = True
            except requests.RequestException as e:
                result["error"] = str(e)

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        self._wait_for_phone_test(thread, result)

    def _wait_for_phone_test(self, thread, result):
        # Tk widgets must only be touched from the main thread, so poll for the result.
        if thread.is_alive():
            self.after(100, self._wait_for_phone_test, thread, result)
            return
        self.phone_test_button.configure(state="normal")
        if result.get("ok"):
            self.phone_test_result.configure(text="✓ Sent — check your phone", text_color=GREEN)
        else:
            self.phone_test_result.configure(text="✗ Couldn't send — check your internet",
                                             text_color=RED)

    # ---------- session ----------

    def _toggle_session(self):
        if self.tracker:
            self._end_session()
        else:
            self._start_session()

    def _start_session(self):
        self.session_started = self.awake_since = time.monotonic()
        self.wake_ups = 0
        self.tracker = EyeTracker(config.MODEL_PATH, config.CAMERA_INDEX,
                                  config.CALIBRATION_SECONDS, self.closed_ratio)
        self.tracker.start()
        if self.settings.get("phone_enabled", True):
            self.phone.warm_up()
        self._style_session_button(active=True)
        self.recalibrate_button.configure(state="normal")
        self._set_status("Starting camera…", "info")
        self._show_placeholder("Starting camera…", "Look at the screen with your eyes open.")
        self.wakeups_value.configure(text="0")

    def _end_session(self):
        studied = time.monotonic() - self.session_started
        self.tracker.stop()
        self.tracker = None
        self._stop_alarm()
        self.session_started = self.awake_since = None
        if studied >= MIN_SAVED_SESSION:
            sessions = load_json(SESSIONS_PATH, [])
            sessions.append({"started": datetime.now().isoformat(timespec="seconds"),
                             "seconds": int(studied), "wake_ups": self.wake_ups})
            save_json(SESSIONS_PATH, sessions)

        self._style_session_button(active=False)
        self.recalibrate_button.configure(state="disabled")
        self._set_status("Ready to study", "idle")
        self.timer_label.configure(text=format_duration(0))
        self.closed_bar.set(0)
        self.closed_label.configure(text="")
        for label in (self.openness_value, self.awake_value):
            label.configure(text="—")
        self._set_border(CARD_BORDER)
        self._refresh_today()
        times = "time" if self.wake_ups == 1 else "times"
        self._show_placeholder("Session complete",
                               f"You studied for {format_duration(studied)} and were woken up "
                               f"{self.wake_ups} {times}.")

    def _refresh_today(self):
        """Studied-today total = saved sessions from today + the current session."""
        today = date.today().isoformat()
        total = sum(s["seconds"] for s in load_json(SESSIONS_PATH, [])
                    if s["started"].startswith(today))
        self._today_saved = total
        self.today_value.configure(text=format_short(total) if total else "—")

    def _tick(self):
        self._set_text(self.clock_label, datetime.now().strftime("%a, %b %-d · %-I:%M %p"))
        if self.tracker:
            elapsed = time.monotonic() - self.session_started
            self._set_text(self.timer_label, format_duration(elapsed))
            self._set_text(self.today_value, format_short(self._today_saved + elapsed))
            self._update_tracking(self.tracker.get_state())
        self.after(REFRESH_MS, self._tick)

    def _update_tracking(self, state):
        if state.error:
            self._set_status("Camera problem", "alarm")
            self._show_placeholder("Can't use the camera", state.error)
            return

        if state.frame is not None:
            self._show_video(state.frame)

        alarm = state.closed_seconds >= self.alarm_delay
        if alarm and not self.alarm_ringing:
            self.wake_ups += 1
            self._start_alarm()
        elif not alarm and self.alarm_ringing:
            self._stop_alarm()
            self.awake_since = time.monotonic()

        if alarm:
            self._set_status("WAKE UP!", "alarm")
            flash_on = int(time.monotonic() * 3) % 2 == 0
            border = RED if flash_on else CARD_BORDER
        elif not state.face_found:
            self._set_status("Can't see your face", "idle")
            border = CARD_BORDER
        elif state.calibrating:
            self._set_status("Calibrating — keep your eyes open", "info")
            border = ACCENT
        elif state.eyes_closed:
            remaining = self.alarm_delay - state.closed_seconds
            self._set_status(f"Eyes closed — alarm in {remaining:.1f}s", "warn")
            border = AMBER
        else:
            self._set_status("Eyes open — you're awake", "good")
            border = GREEN
        self._set_border(border)

        self.closed_bar.configure(progress_color=RED if alarm else AMBER)
        self.closed_bar.set(min(state.closed_seconds / self.alarm_delay, 1.0))
        self._set_text(self.closed_label,
                       f"{state.closed_seconds:.1f}s / {self.alarm_delay}s"
                       if state.closed_seconds else "")

        self._set_text(self.wakeups_value, str(self.wake_ups))
        self._set_text(self.awake_value,
                       "—" if alarm else format_short(time.monotonic() - self.awake_since))
        if state.face_found and not state.calibrating and state.open_ear:
            self._set_text(self.openness_value, f"{min(state.ear / state.open_ear, 1.0):.0%}")
        else:
            self._set_text(self.openness_value, "—")

    def _start_alarm(self):
        self.alarm_ringing = True
        self.alarm.start()
        phone_on = self.settings.get("phone_enabled", True)
        print(f"[alarm] ringing (phone alerts {'on, topic ' + self.phone.topic if phone_on else 'off'})")
        if phone_on:
            self.phone.start()

    def _stop_alarm(self):
        if self.alarm_ringing:
            print("[alarm] stopped")
        self.alarm_ringing = False
        self.alarm.stop()
        self.phone.stop()

    def _on_close(self):
        if self.tracker:
            self._end_session()  # also records the session
        self._stop_alarm()
        self.destroy()
