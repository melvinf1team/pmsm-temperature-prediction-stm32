"""Live D6T pixel map for choosing the pixel logged as ``d6t_temp_c``.

The tool polls ``D6T_FRAME`` from ``firmware_acquisition`` and draws the 4x4
matrix of the Omron D6T-44L-06 as a heat map. Click the pixel that sees the
motor, then write it to ``D6TIR_SELECTED_PIXEL_INDEX`` in both firmware
projects.

Usage::

    python tests/bench/d6t_calibration.py --port COM5
    python tests/bench/d6t_calibration.py --demo
"""

from __future__ import annotations

import argparse
import math
import queue
import random
import re
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

import serial
from serial.tools import list_ports

from check_wiring import BAUD_RATE, COMMAND_CHAR_DELAY_S, VALIDATION_FIRMWARE_LINE, default_port


PROJECT_ROOT = Path(__file__).resolve().parents[2]

GRID_SIZE = 4
PIXEL_COUNT = GRID_SIZE * GRID_SIZE
DEFAULT_PERIOD_MS = 250
SERIAL_READ_TIMEOUT_S = 0.05
REPLY_TIMEOUT_S = 1.0
MISSED_REPLIES_WARNING = 3
MAX_BUFFER_BYTES = 4096
HISTORY_SECONDS = 5.0
STALE_AFTER_S = 2.0
MIN_SCALE_SPAN_C = 2.0
DEFAULT_SCALE_C = (20.0, 40.0)


@dataclass(frozen=True)
class FirmwareTarget:
    name: str
    path: Path


FIRMWARE_TARGETS = (
    FirmwareTarget(
        "firmware_acquisition",
        PROJECT_ROOT / "firmware_acquisition" / "tets_motor_dewalt" / "STM32CubeIDE" / "Application" / "User" / "d6t_ir.c",
    ),
    FirmwareTarget(
        "firmware_validation",
        PROJECT_ROOT / "firmware_validation" / "STM32CubeIDE" / "Application" / "User" / "d6t_ir.c",
    ),
)

PIXEL_DEFINE = re.compile(r"^(#define[ \t]+D6TIR_SELECTED_PIXEL_INDEX[ \t]+)(\d+)(U?)", re.MULTILINE)
PIXEL_COMMENT = re.compile(r"(d6t_temp_c : )\d+ = ligne \d+, colonne \d+")

COLORS = {
    "bg": "#070A0F",
    "panel": "#10151E",
    "panel_soft": "#0C111A",
    "panel_lift": "#182232",
    "border": "#2B384B",
    "text": "#F2F6FF",
    "muted": "#9AA8BA",
    "accent": "#2DD4BF",
    "cyan": "#38BDF8",
    "lime": "#A3E635",
    "warning": "#FBBF24",
    "danger": "#FB7185",
    "violet": "#C084FC",
    "input": "#0A1019",
    "empty": "#1A2332",
    "ink_dark": "#061016",
}

FONT_UI = "Segoe UI"
FONT_TITLE = "Segoe UI Semibold"
FONT_VALUE = "Bahnschrift SemiBold"

# Magma-like scale: dark violet for the coldest pixel, pale yellow for the hottest.
HEAT_STOPS = (
    (0.00, (0x14, 0x0B, 0x34)),
    (0.20, (0x3B, 0x0F, 0x70)),
    (0.40, (0x8C, 0x29, 0x81)),
    (0.60, (0xDE, 0x49, 0x68)),
    (0.80, (0xFE, 0x9F, 0x6D)),
    (1.00, (0xFC, 0xFD, 0xBF)),
)

BUTTON_KINDS = {
    "primary": (COLORS["accent"], COLORS["ink_dark"], "#5EEAD4"),
    "secondary": (COLORS["panel_lift"], COLORS["text"], "#223047"),
    "danger": ("#3A1018", COLORS["text"], "#5A1A27"),
}


# ---------------------------------------------------------------------------
# Frames, colours, and orientation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameReply:
    """Parsed ``D6T_FRAME`` answer; temperatures in degrees Celsius."""

    ok: bool
    selected: Optional[int]
    ptat_c: float = math.nan
    pixels_c: tuple[float, ...] = ()


def parse_frame_reply(line: str) -> Optional[FrameReply]:
    """Parse ``D6T_FRAME,ok=1,selected=10,ptat=253,px=241:...`` (tenths of a degree)."""
    line = line.strip()
    if not line.startswith("D6T_FRAME,"):
        return None
    fields = {}
    for part in line.split(",")[1:]:
        key, separator, value = part.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    try:
        selected = int(fields["selected"]) if "selected" in fields else None
        if fields.get("ok") != "1":
            return FrameReply(False, selected)
        pixels = tuple(int(value) / 10.0 for value in fields["px"].split(":"))
        ptat = int(fields["ptat"]) / 10.0
    except (KeyError, ValueError):
        return None
    if len(pixels) != PIXEL_COUNT:
        return None
    return FrameReply(True, selected, ptat, pixels)


def heat_rgb(value: float, low: float, high: float) -> tuple[int, int, int]:
    span = high - low
    ratio = 0.5 if span <= 0 else min(max((value - low) / span, 0.0), 1.0)
    for (start, start_rgb), (end, end_rgb) in zip(HEAT_STOPS, HEAT_STOPS[1:]):
        if ratio <= end:
            local = (ratio - start) / (end - start)
            return tuple(round(a + (b - a) * local) for a, b in zip(start_rgb, end_rgb))
    return HEAT_STOPS[-1][1]


def heat_color(value: float, low: float, high: float) -> str:
    if not math.isfinite(value):
        return COLORS["empty"]
    red, green, blue = heat_rgb(value, low, high)
    return f"#{red:02X}{green:02X}{blue:02X}"


def contrast_text(color: str) -> str:
    red, green, blue = (int(color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return COLORS["ink_dark"] if luminance > 0.55 else COLORS["text"]


def pixel_row_col(index: int) -> tuple[int, int]:
    """Zero-based row and column of a pixel in the sensor's own order."""
    return divmod(index, GRID_SIZE)


def view_cell(index: int, rotation: int, mirrored: bool) -> tuple[int, int]:
    """Screen cell of a pixel after mirroring then rotating by ``rotation`` x 90 degrees clockwise."""
    row, col = pixel_row_col(index)
    if mirrored:
        col = GRID_SIZE - 1 - col
    for _ in range(rotation % 4):
        row, col = col, GRID_SIZE - 1 - row
    return row, col


def pixel_at(view_row: int, view_col: int, rotation: int, mirrored: bool) -> int:
    for index in range(PIXEL_COUNT):
        if view_cell(index, rotation, mirrored) == (view_row, view_col):
            return index
    raise ValueError(f"No pixel at ({view_row}, {view_col})")


def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


class PixelHistory:
    """Frames received during the last ``seconds``."""

    def __init__(self, seconds: float = HISTORY_SECONDS) -> None:
        self.seconds = seconds
        self.samples: deque[tuple[float, tuple[float, ...]]] = deque()

    def add(self, timestamp: float, pixels: tuple[float, ...]) -> None:
        self.samples.append((timestamp, pixels))
        while self.samples and self.samples[0][0] < timestamp - self.seconds:
            self.samples.popleft()

    def clear(self) -> None:
        self.samples.clear()

    def values(self, index: int) -> list[float]:
        return [pixels[index] for _time, pixels in self.samples if math.isfinite(pixels[index])]

    def mean(self, index: int) -> float:
        values = self.values(index)
        return sum(values) / len(values) if values else math.nan

    def span(self, index: int) -> tuple[float, float]:
        values = self.values(index)
        return (min(values), max(values)) if values else (math.nan, math.nan)

    def overall_range(self) -> tuple[float, float]:
        values = [value for _time, pixels in self.samples for value in pixels if math.isfinite(value)]
        return (min(values), max(values)) if values else (math.nan, math.nan)

    def means(self) -> list[float]:
        return [self.mean(index) for index in range(PIXEL_COUNT)]

    def rate_hz(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        elapsed = self.samples[-1][0] - self.samples[0][0]
        return (len(self.samples) - 1) / elapsed if elapsed > 0 else 0.0


# ---------------------------------------------------------------------------
# Firmware sources
# ---------------------------------------------------------------------------


def read_source(path: Path) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as handle:
        return handle.read()


def display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def read_firmware_pixel(path: Path) -> Optional[int]:
    """Return ``D6TIR_SELECTED_PIXEL_INDEX`` from a firmware source, or ``None``."""
    try:
        match = PIXEL_DEFINE.search(read_source(path))
    except OSError:
        return None
    return int(match.group(2)) if match else None


def write_firmware_pixel(path: Path, index: int) -> bool:
    """Set ``D6TIR_SELECTED_PIXEL_INDEX`` and its comment; return ``True`` if the file changed."""
    if not 0 <= index < PIXEL_COUNT:
        raise ValueError(f"Pixel index {index} outside 0-{PIXEL_COUNT - 1}")
    text = read_source(path)
    if PIXEL_DEFINE.search(text) is None:
        raise ValueError(f"D6TIR_SELECTED_PIXEL_INDEX not found in {path}")
    row, col = pixel_row_col(index)
    updated = PIXEL_DEFINE.sub(lambda match: f"{match.group(1)}{index}U", text, count=1)
    updated = PIXEL_COMMENT.sub(lambda match: f"{match.group(1)}{index} = ligne {row + 1}, colonne {col + 1}", updated, count=1)
    if updated == text:
        return False
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        handle.write(updated)
    return True


# ---------------------------------------------------------------------------
# Frame sources (worker threads)
# ---------------------------------------------------------------------------


class FrameSource(threading.Thread):
    """Base class: posts ``(source_id, kind, payload)`` events to the GUI queue."""

    def __init__(self, source_id: int, events: queue.Queue, period_s: float) -> None:
        super().__init__(daemon=True)
        self.source_id = source_id
        self.events = events
        self.period_s = period_s
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def post(self, kind: str, payload=None) -> None:
        self.events.put((self.source_id, kind, payload))


class SerialFrameSource(FrameSource):
    """Requests ``D6T_FRAME`` from ``firmware_acquisition`` at a fixed period."""

    def __init__(self, source_id: int, events: queue.Queue, period_s: float, port: str, baud: int) -> None:
        super().__init__(source_id, events, period_s)
        self.port = port
        self.baud = baud
        self.buffer = b""
        self.validation_seen = False

    def run(self) -> None:
        try:
            connection = serial.Serial(self.port, self.baud, timeout=SERIAL_READ_TIMEOUT_S)
        except (serial.SerialException, OSError) as exc:
            self.post("closed", f"Cannot open {self.port}: {exc}. Close the dashboard or any terminal using it.")
            return

        missed = 0
        try:
            with connection:
                connection.reset_input_buffer()
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    self.send(connection, "D6T_FRAME")
                    reply = self.wait_reply(connection)
                    if reply is not None:
                        missed = 0
                        self.post("frame", reply)
                    elif not self.stop_event.is_set():
                        missed += 1
                        if missed == MISSED_REPLIES_WARNING:
                            self.post("warning", self.silence_message())
                    self.stop_event.wait(max(0.0, self.period_s - (time.monotonic() - started)))
        except (serial.SerialException, OSError) as exc:
            self.post("closed", f"Connection lost: {exc}")
            return
        self.post("closed", None)

    def silence_message(self) -> str:
        if self.validation_seen:
            return "firmware_validation is flashed: flash firmware_acquisition to calibrate the D6T."
        return "No answer to D6T_FRAME: flash the current firmware_acquisition, or check the COM port."

    def send(self, connection, command: str) -> None:
        # Same pacing as the dashboard: USART1 has no RX FIFO enabled.
        for byte in (command + "\n").encode("ascii"):
            connection.write(bytes((byte,)))
            connection.flush()
            time.sleep(COMMAND_CHAR_DELAY_S)

    def wait_reply(self, connection) -> Optional[FrameReply]:
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while time.monotonic() < deadline and not self.stop_event.is_set():
            chunk = connection.read(256)
            if not chunk:
                continue
            self.buffer = (self.buffer + chunk)[-MAX_BUFFER_BYTES:]
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                line = raw.decode("ascii", errors="replace").strip()
                reply = parse_frame_reply(line)
                if reply is not None:
                    return reply
                if VALIDATION_FIRMWARE_LINE.match(line):
                    self.validation_seen = True
        return None


class DemoFrameSource(FrameSource):
    """Simulated warm spot drifting slowly around pixel 9."""

    def run(self) -> None:
        rng = random.Random(7)
        start = time.monotonic()
        while not self.stop_event.is_set():
            elapsed = time.monotonic() - start
            hot_row = 2.0 + 0.35 * math.sin(elapsed / 6.0)
            hot_col = 1.2 + 0.35 * math.cos(elapsed / 8.0)
            heat = 14.0 + 3.0 * math.sin(elapsed / 15.0)
            pixels = []
            for index in range(PIXEL_COUNT):
                row, col = pixel_row_col(index)
                distance2 = (row - hot_row) ** 2 + (col - hot_col) ** 2
                pixels.append(round(24.0 + heat * math.exp(-distance2 / 1.1) + rng.gauss(0.0, 0.12), 1))
            self.post("frame", FrameReply(True, None, round(25.3 + rng.gauss(0.0, 0.05), 1), tuple(pixels)))
            self.stop_event.wait(self.period_s)
        self.post("closed", None)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs) -> int:
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


def make_button(parent, text: str, command, kind: str = "secondary", **kwargs) -> tk.Button:
    button = tk.Button(
        parent,
        text=text,
        command=command,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        font=(FONT_TITLE, 10),
        padx=14,
        pady=8,
        disabledforeground=COLORS["muted"],
        **kwargs,
    )
    set_button_kind(button, kind)
    button.bind("<Enter>", lambda _event: button.configure(bg=button.hover_bg) if button["state"] != tk.DISABLED else None)
    button.bind("<Leave>", lambda _event: button.configure(bg=button.base_bg))
    return button


def set_button_kind(button: tk.Button, kind: str, enabled: bool = True) -> None:
    background, foreground, hover = BUTTON_KINDS[kind]
    if not enabled:
        background, hover = COLORS["panel_lift"], COLORS["panel_lift"]
    button.base_bg = background
    button.hover_bg = hover
    button.configure(
        bg=background,
        fg=foreground,
        activebackground=hover,
        activeforeground=foreground,
        state=tk.NORMAL if enabled else tk.DISABLED,
        cursor="hand2" if enabled else "arrow",
    )


class D6tCalibrationApp(tk.Tk):
    """Live heat map of the D6T and writer for ``D6TIR_SELECTED_PIXEL_INDEX``."""

    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = BAUD_RATE,
        period_ms: int = DEFAULT_PERIOD_MS,
        demo: bool = False,
    ) -> None:
        super().__init__()
        self.title("D6T Pixel Calibration")
        self.geometry("1240x800")
        self.minsize(1080, 720)
        self.configure(bg=COLORS["bg"])

        self.baud = baud
        self.period_s = period_ms / 1000.0
        self.events: queue.Queue = queue.Queue()
        self.source: Optional[FrameSource] = None
        self.source_id = 0
        self.source_label = ""
        self.stopped_sources: list[FrameSource] = []
        self.after_ids: list[str] = []

        self.history = PixelHistory()
        self.latest: Optional[FrameReply] = None
        self.last_frame_time = 0.0
        self.flashed_pixel: Optional[int] = None
        self.firmware_pixels: dict[str, Optional[int]] = {}
        self.rotation = 0
        self.mirrored = False
        self.locked_range: Optional[tuple[float, float]] = None
        self.hover_index: Optional[int] = None
        self.cell_boxes: dict[int, tuple[float, float, float, float]] = {}

        self.port_var = tk.StringVar(value=port or default_port() or "")
        self.status_var = tk.StringVar(value="Disconnected")
        self.status_detail_var = tk.StringVar(value="Select the ST-LINK port, then connect.")
        self.orientation_var = tk.StringVar()
        self.pixel_title_var = tk.StringVar()
        self.pixel_position_var = tk.StringVar()
        self.pixel_live_var = tk.StringVar(value="--")
        self.pixel_mean_var = tk.StringVar(value="--")
        self.pixel_range_var = tk.StringVar(value="--")
        self.pixel_delta_var = tk.StringVar(value="--")
        self.pixel_rank_var = tk.StringVar(value="--")
        self.board_pixel_var = tk.StringVar()
        self.firmware_hint_var = tk.StringVar()

        self.reload_firmware_pixels()
        known = [value for value in self.firmware_pixels.values() if value is not None]
        self.selected_index = known[0] if known else 0

        self.setup_style()
        self.build_ui()
        self.bind_keys()
        self.refresh_ports()
        self.refresh_all()
        self.set_status("idle", "Disconnected", f"Needs firmware_acquisition at {self.baud} baud.")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.schedule(50, self.poll_events)
        self.schedule(500, self.check_stale)
        if demo:
            self.schedule(100, self.start_demo)
        elif port:
            self.schedule(100, self.connect)

    # -- Style and layout -------------------------------------------------

    def setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["input"],
            background=COLORS["input"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["accent"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=7,
        )
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["input"])], foreground=[("readonly", COLORS["text"])])
        self.option_add("*TCombobox*Listbox.background", COLORS["input"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", COLORS["ink_dark"])

    def label(self, parent, text="", variable=None, size=10, font=FONT_UI, color="text", bg="panel", **kwargs) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            textvariable=variable,
            bg=COLORS[bg],
            fg=COLORS[color],
            font=(font, size),
            **kwargs,
        )

    def card(self, parent, title: str, accent: str = COLORS["accent"]) -> tuple[tk.Frame, tk.Frame, tk.Frame]:
        outer = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        head = tk.Frame(outer, bg=COLORS["panel"])
        head.pack(fill=tk.X, padx=18, pady=(14, 8))
        tk.Frame(head, bg=accent, width=8, height=8).pack(side=tk.LEFT, padx=(0, 10))
        self.label(head, title.upper(), size=11, font=FONT_TITLE).pack(side=tk.LEFT)
        content = tk.Frame(outer, bg=COLORS["panel"])
        content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 14))
        return outer, head, content

    def build_ui(self) -> None:
        root = tk.Frame(self, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=18, pady=14)
        self.build_header(root)

        body = tk.Frame(root, bg=COLORS["bg"])
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=380)
        body.grid_rowconfigure(0, weight=1)

        self.build_map_card(body).grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        side = tk.Frame(body, bg=COLORS["bg"])
        side.grid(row=0, column=1, sticky="nsew")
        self.build_pixel_card(side).pack(fill=tk.X)
        self.build_firmware_card(side).pack(fill=tk.X, pady=(14, 0))

    def build_header(self, parent) -> None:
        header = tk.Frame(parent, bg=COLORS["panel_soft"], highlightbackground=COLORS["border"], highlightthickness=1)
        header.pack(fill=tk.X)
        tk.Frame(header, bg=COLORS["accent"], width=5).pack(side=tk.LEFT, fill=tk.Y)

        title_block = tk.Frame(header, bg=COLORS["panel_soft"])
        title_block.pack(side=tk.LEFT, padx=(22, 16), pady=16)
        self.label(title_block, "D6T Pixel Calibration", size=20, font=FONT_TITLE, bg="panel_soft").pack(anchor="w")
        self.label(
            title_block,
            "Omron D6T-44L-06 live 4x4 map  \u00b7  choose the pixel logged as d6t_temp_c",
            size=9,
            color="muted",
            bg="panel_soft",
        ).pack(anchor="w", pady=(2, 0))

        panel = tk.Frame(header, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        panel.pack(side=tk.RIGHT, padx=16, pady=10)

        status = tk.Frame(panel, bg=COLORS["panel"])
        status.pack(fill=tk.X, padx=12, pady=(10, 6))
        self.status_dot = tk.Canvas(status, width=14, height=14, bg=COLORS["panel"], highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))
        self.status_dot_id = self.status_dot.create_oval(2, 2, 12, 12, fill=COLORS["muted"], outline="")
        self.label(status, variable=self.status_var, size=10, font=FONT_TITLE).pack(side=tk.LEFT)
        self.label(status, variable=self.status_detail_var, size=9, color="muted", anchor="w", wraplength=300, justify="left").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0)
        )

        controls = tk.Frame(panel, bg=COLORS["panel"])
        controls.pack(fill=tk.X, padx=12, pady=(0, 10))
        self.port_combo = ttk.Combobox(controls, textvariable=self.port_var, state="readonly", width=12, font=(FONT_UI, 10))
        self.port_combo.pack(side=tk.LEFT, padx=(0, 8))
        make_button(controls, "Refresh", self.refresh_ports).pack(side=tk.LEFT, padx=(0, 8))
        self.connect_button = make_button(controls, "Connect", self.toggle_connection, "primary", width=10)
        self.connect_button.pack(side=tk.LEFT, padx=(0, 8))
        self.demo_button = make_button(controls, "Demo", self.start_demo)
        self.demo_button.pack(side=tk.LEFT)

    def build_map_card(self, parent) -> tk.Frame:
        outer, head, content = self.card(parent, "Thermal map")

        tools = tk.Frame(head, bg=COLORS["panel"])
        tools.pack(side=tk.RIGHT)
        self.lock_button = make_button(tools, "Lock scale", self.toggle_lock)
        self.lock_button.pack(side=tk.RIGHT)
        make_button(tools, "Mirror", self.toggle_mirror).pack(side=tk.RIGHT, padx=(0, 8))
        make_button(tools, "Rotate 90\u00b0", self.rotate_view).pack(side=tk.RIGHT, padx=(0, 8))
        make_button(tools, "Select hottest", self.select_hottest).pack(side=tk.RIGHT, padx=(0, 8))
        self.label(head, variable=self.orientation_var, size=9, color="muted").pack(side=tk.RIGHT, padx=(0, 14))

        self.map_canvas = tk.Canvas(content, bg=COLORS["panel"], highlightthickness=0, cursor="hand2")
        self.map_canvas.pack(fill=tk.BOTH, expand=True)
        self.map_canvas.bind("<Configure>", lambda _event: self.draw_map())
        self.map_canvas.bind("<Motion>", self.on_map_motion)
        self.map_canvas.bind("<Leave>", self.on_map_leave)
        self.map_canvas.bind("<Button-1>", self.on_map_click)

        self.legend_canvas = tk.Canvas(content, height=50, bg=COLORS["panel"], highlightthickness=0)
        self.legend_canvas.pack(fill=tk.X, pady=(10, 0))
        self.legend_canvas.bind("<Configure>", lambda _event: self.draw_legend())

        self.label(
            content,
            "Click a pixel to select it   \u00b7   arrow keys move   \u00b7   H hottest   \u00b7   R rotate   \u00b7   M mirror",
            size=9,
            color="muted",
        ).pack(pady=(6, 0))
        return outer

    def stat_tile(self, parent, title: str, variable: tk.StringVar, row: int, col: int) -> None:
        tile = tk.Frame(parent, bg=COLORS["panel_lift"])
        tile.grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 4, 4 if col == 0 else 0), pady=4)
        self.label(tile, title, size=8, color="muted", bg="panel_lift").pack(anchor="w", padx=12, pady=(7, 0))
        self.label(tile, variable=variable, size=12, font=FONT_VALUE, bg="panel_lift").pack(anchor="w", padx=12, pady=(0, 7))

    def build_pixel_card(self, parent) -> tk.Frame:
        outer, _head, content = self.card(parent, "Selected pixel", COLORS["cyan"])

        title_row = tk.Frame(content, bg=COLORS["panel"])
        title_row.pack(fill=tk.X)
        self.label(title_row, variable=self.pixel_title_var, size=18, font=FONT_TITLE).pack(side=tk.LEFT)
        self.label(title_row, variable=self.pixel_position_var, size=10, color="muted").pack(side=tk.RIGHT, pady=(8, 0))

        live_row = tk.Frame(content, bg=COLORS["panel"])
        live_row.pack(fill=tk.X, pady=(2, 6))
        self.swatch = tk.Canvas(live_row, width=30, height=30, bg=COLORS["panel"], highlightthickness=0)
        self.swatch.pack(side=tk.LEFT, padx=(0, 12))
        self.label(live_row, variable=self.pixel_live_var, size=24, font=FONT_VALUE).pack(side=tk.LEFT)
        self.label(live_row, "live", size=9, color="muted").pack(side=tk.LEFT, padx=(8, 0), pady=(12, 0))

        stats = tk.Frame(content, bg=COLORS["panel"])
        stats.pack(fill=tk.X)
        stats.grid_columnconfigure(0, weight=1, uniform="stats")
        stats.grid_columnconfigure(1, weight=1, uniform="stats")
        self.stat_tile(stats, f"MEAN ({HISTORY_SECONDS:.0f} s)", self.pixel_mean_var, 0, 0)
        self.stat_tile(stats, f"RANGE ({HISTORY_SECONDS:.0f} s)", self.pixel_range_var, 0, 1)
        self.stat_tile(stats, "VS. SENSOR AMBIENT (PTAT)", self.pixel_delta_var, 1, 0)
        self.stat_tile(stats, "RANK (BY MEAN)", self.pixel_rank_var, 1, 1)
        return outer

    def build_firmware_card(self, parent) -> tk.Frame:
        outer, _head, content = self.card(parent, "Firmware", COLORS["lime"])

        self.firmware_badges: dict[str, tk.Label] = {}
        for target in FIRMWARE_TARGETS:
            row = tk.Frame(content, bg=COLORS["panel"])
            row.pack(fill=tk.X, pady=(0, 6))
            self.label(row, target.name, size=10, font=FONT_TITLE).pack(side=tk.LEFT)
            badge = self.label(row, "", size=10, font=FONT_TITLE, bg="panel_lift", padx=10, pady=3)
            badge.pack(side=tk.RIGHT)
            self.firmware_badges[target.name] = badge
        self.label(content, "D6TIR_SELECTED_PIXEL_INDEX in Application/User/d6t_ir.c", size=8, color="muted").pack(anchor="w")

        self.label(content, variable=self.board_pixel_var, size=9, color="muted", wraplength=330, justify="left").pack(anchor="w", pady=(8, 8))
        self.write_button = make_button(content, "", self.write_to_firmwares, "primary")
        self.write_button.pack(fill=tk.X)
        self.label(content, variable=self.firmware_hint_var, size=9, color="muted", wraplength=330, justify="left").pack(
            anchor="w", pady=(8, 0)
        )
        return outer

    def bind_keys(self) -> None:
        for key, (d_row, d_col) in {"<Left>": (0, -1), "<Right>": (0, 1), "<Up>": (-1, 0), "<Down>": (1, 0)}.items():
            self.bind(key, lambda event, d=(d_row, d_col): self.on_key_move(event, *d))
        for key, action in (("h", self.select_hottest), ("r", self.rotate_view), ("m", self.toggle_mirror)):
            self.bind(f"<KeyPress-{key}>", lambda event, run=action: None if self.focus_in_combo() else run())
            self.bind(f"<KeyPress-{key.upper()}>", lambda event, run=action: None if self.focus_in_combo() else run())

    def focus_in_combo(self) -> bool:
        return isinstance(self.focus_get(), ttk.Combobox)

    # -- Scheduling and lifecycle ---------------------------------------

    def schedule(self, delay_ms: int, callback) -> None:
        self.after_ids.append(self.after(delay_ms, callback))
        if len(self.after_ids) > 32:
            self.after_ids = self.after_ids[-32:]

    def destroy(self) -> None:
        for after_id in self.after_ids:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        if self.source is not None:
            self.source.stop()
            self.source.join(timeout=REPLY_TIMEOUT_S + self.period_s)
            self.source = None
        super().destroy()

    # -- Sources ----------------------------------------------------------

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        self.port_combo.configure(values=ports)
        if self.port_var.get() not in ports:
            self.port_var.set(default_port() or (ports[0] if ports else ""))

    def start_source(self, source_factory, label: str) -> None:
        self.stop_source()
        for old in self.stopped_sources:
            old.join(timeout=REPLY_TIMEOUT_S + self.period_s)
        self.stopped_sources = []
        self.source_id += 1
        self.source = source_factory(self.source_id)
        self.source_label = label
        self.history.clear()
        self.latest = None
        self.last_frame_time = 0.0
        self.flashed_pixel = None
        self.source.start()
        self.refresh_connection_buttons()
        self.refresh_all()

    def stop_source(self) -> None:
        if self.source is None:
            return
        self.source.stop()
        self.stopped_sources.append(self.source)
        self.source = None
        self.source_id += 1
        self.refresh_connection_buttons()

    def connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            self.set_status("warning", "No port selected", "Plug in the B-G473E-ZEST1S, click Refresh, and select its port.")
            return
        self.start_source(
            lambda source_id: SerialFrameSource(source_id, self.events, self.period_s, port, self.baud),
            port,
        )
        self.set_status("waiting", "Connecting", f"Requesting D6T_FRAME on {port}...")

    def start_demo(self) -> None:
        self.start_source(lambda source_id: DemoFrameSource(source_id, self.events, self.period_s), "Simulated frames")
        self.set_status("demo", "Demo", "Simulated frames, no board needed.")

    def toggle_connection(self) -> None:
        if self.source is not None:
            self.stop_source()
            self.set_status("idle", "Disconnected", "Connection closed.")
            self.refresh_all()
        else:
            self.connect()

    def refresh_connection_buttons(self) -> None:
        connected = self.source is not None
        if not connected:
            label = "Connect"
        elif isinstance(self.source, DemoFrameSource):
            label = "Stop demo"
        else:
            label = "Disconnect"
        self.connect_button.configure(text=label)
        set_button_kind(self.connect_button, "danger" if connected else "primary")
        self.port_combo.configure(state=tk.DISABLED if connected else "readonly")

    # -- Events -------------------------------------------------------------

    def poll_events(self) -> None:
        self.drain_events()
        self.schedule(50, self.poll_events)

    def drain_events(self) -> None:
        frame_received = False
        while True:
            try:
                source_id, kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if source_id != self.source_id:
                continue
            if kind == "frame":
                frame_received |= self.handle_frame(payload)
            elif kind == "warning":
                self.set_status("warning", "No data", payload)
            elif kind == "closed":
                self.source = None
                self.refresh_connection_buttons()
                if payload:
                    self.set_status("error", "Disconnected", payload)
                else:
                    self.set_status("idle", "Disconnected", "Connection closed.")
        if frame_received:
            self.refresh_all()

    def handle_frame(self, reply: FrameReply) -> bool:
        now = time.monotonic()
        self.flashed_pixel = reply.selected
        if not reply.ok:
            self.set_status(
                "warning",
                "Sensor silent",
                "The board answers but the D6T does not: run check_wiring.py to locate the fault.",
            )
            self.refresh_firmware_card()
            return False
        self.latest = reply
        self.last_frame_time = now
        self.history.add(now, reply.pixels_c)
        state = "demo" if isinstance(self.source, DemoFrameSource) else "live"
        self.set_status(
            state,
            "Demo" if state == "demo" else "Live",
            f"{self.source_label}  \u00b7  {self.history.rate_hz():.1f} frames/s  \u00b7  PTAT {reply.ptat_c:.1f} \u00b0C",
        )
        return True

    def check_stale(self) -> None:
        if (
            self.source is not None
            and self.last_frame_time > 0
            and time.monotonic() - self.last_frame_time > STALE_AFTER_S
        ):
            self.set_status("warning", "Waiting for data", f"No frame for {STALE_AFTER_S:.0f} s on {self.source_label}.")
        self.schedule(500, self.check_stale)

    def set_status(self, state: str, title: str, detail: str) -> None:
        colors = {
            "idle": COLORS["muted"],
            "waiting": COLORS["cyan"],
            "live": COLORS["lime"],
            "demo": COLORS["violet"],
            "warning": COLORS["warning"],
            "error": COLORS["danger"],
        }
        self.status_var.set(title)
        self.status_detail_var.set(detail)
        self.status_dot.itemconfigure(self.status_dot_id, fill=colors[state])

    # -- Selection and view -----------------------------------------------

    def select_pixel(self, index: int) -> None:
        self.selected_index = index
        self.refresh_all()

    def hottest_index(self) -> Optional[int]:
        means = self.history.means()
        finite = [(value, index) for index, value in enumerate(means) if math.isfinite(value)]
        return max(finite)[1] if finite else None

    def select_hottest(self) -> None:
        index = self.hottest_index()
        if index is not None:
            self.select_pixel(index)

    def on_key_move(self, _event, d_row: int, d_col: int) -> None:
        if self.focus_in_combo():
            return
        row, col = view_cell(self.selected_index, self.rotation, self.mirrored)
        row = min(max(row + d_row, 0), GRID_SIZE - 1)
        col = min(max(col + d_col, 0), GRID_SIZE - 1)
        self.select_pixel(pixel_at(row, col, self.rotation, self.mirrored))

    def rotate_view(self) -> None:
        self.rotation = (self.rotation + 1) % 4
        self.refresh_all()

    def toggle_mirror(self) -> None:
        self.mirrored = not self.mirrored
        self.refresh_all()

    def toggle_lock(self) -> None:
        self.locked_range = None if self.locked_range else self.current_range()
        self.lock_button.configure(text="Unlock scale" if self.locked_range else "Lock scale")
        set_button_kind(self.lock_button, "primary" if self.locked_range else "secondary")
        self.refresh_all()

    def current_range(self) -> tuple[float, float]:
        if self.locked_range:
            return self.locked_range
        low, high = self.history.overall_range()
        if not (math.isfinite(low) and math.isfinite(high)):
            return DEFAULT_SCALE_C
        if high - low < MIN_SCALE_SPAN_C:
            middle = (low + high) / 2
            low, high = middle - MIN_SCALE_SPAN_C / 2, middle + MIN_SCALE_SPAN_C / 2
        return low, high

    def index_at(self, x: float, y: float) -> Optional[int]:
        for index, (x1, y1, x2, y2) in self.cell_boxes.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return index
        return None

    def on_map_motion(self, event) -> None:
        index = self.index_at(event.x, event.y)
        if index != self.hover_index:
            self.hover_index = index
            self.draw_map()

    def on_map_leave(self, _event) -> None:
        if self.hover_index is not None:
            self.hover_index = None
            self.draw_map()

    def on_map_click(self, event) -> None:
        self.map_canvas.focus_set()
        index = self.index_at(event.x, event.y)
        if index is not None:
            self.select_pixel(index)

    # -- Firmware -----------------------------------------------------------

    def reload_firmware_pixels(self) -> None:
        self.firmware_pixels = {target.name: read_firmware_pixel(target.path) for target in FIRMWARE_TARGETS}

    def write_to_firmwares(self) -> None:
        index = self.selected_index
        row, col = pixel_row_col(index)
        files = "\n".join(f"  \u2022 {display_path(target.path)}" for target in FIRMWARE_TARGETS)
        confirmed = messagebox.askyesno(
            "Write pixel to both firmwares",
            f"Set D6TIR_SELECTED_PIXEL_INDEX to {index} (row {row + 1}, column {col + 1}) in:\n\n{files}\n\n"
            "Rebuild and flash both projects afterwards. Logs recorded and models trained "
            "with another pixel will no longer match the new d6t_temp_c.",
            parent=self,
        )
        if not confirmed:
            return
        errors = []
        for target in FIRMWARE_TARGETS:
            try:
                write_firmware_pixel(target.path, index)
            except (OSError, ValueError) as exc:
                errors.append(f"{target.name}: {exc}")
        self.reload_firmware_pixels()
        self.refresh_all()
        if errors:
            messagebox.showerror("Firmware not updated", "\n".join(errors), parent=self)
        else:
            self.firmware_hint_var.set(f"Pixel {index} written: rebuild and flash both projects.")

    # -- Rendering ----------------------------------------------------------

    def refresh_all(self) -> None:
        suffix = "  \u00b7  mirrored" if self.mirrored else ""
        self.orientation_var.set(f"View {self.rotation * 90}\u00b0{suffix}")
        self.draw_map()
        self.draw_legend()
        self.refresh_pixel_card()
        self.refresh_firmware_card()

    def draw_map(self) -> None:
        canvas = self.map_canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 60 or height < 60:
            return

        size = min(width, height) - 8
        gap = max(6.0, size / 70)
        cell = (size - gap * (GRID_SIZE - 1)) / GRID_SIZE
        x0, y0 = (width - size) / 2, (height - size) / 2
        pixels = self.latest.pixels_c if self.latest else ()
        low, high = self.current_range()
        hottest = self.hottest_index()
        firmware_pixel = self.firmware_pixels.get(FIRMWARE_TARGETS[0].name)
        self.cell_boxes = {}

        for index in range(PIXEL_COUNT):
            row, col = view_cell(index, self.rotation, self.mirrored)
            x1, y1 = x0 + col * (cell + gap), y0 + row * (cell + gap)
            x2, y2 = x1 + cell, y1 + cell
            self.cell_boxes[index] = (x1, y1, x2, y2)

            value = pixels[index] if pixels else math.nan
            fill = heat_color(value, low, high)
            ink = contrast_text(fill)
            radius = cell * 0.09

            if index == self.selected_index:
                rounded_rect(canvas, x1 - 5, y1 - 5, x2 + 5, y2 + 5, radius + 4, fill="", outline=COLORS["cyan"], width=3)
            outline = COLORS["text"] if index == self.hover_index else COLORS["border"]
            rounded_rect(canvas, x1, y1, x2, y2, radius, fill=fill, outline=outline, width=2 if index == self.hover_index else 1)

            canvas.create_text(
                x1 + cell * 0.08, y1 + cell * 0.07, text=f"#{index}", anchor="nw", fill=ink, font=(FONT_TITLE, -max(11, int(cell * 0.10)))
            )
            text = f"{value:.1f}\u00b0" if math.isfinite(value) else "--"
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=ink, font=(FONT_VALUE, -max(16, int(cell * 0.24))))

            if index == hottest and pixels:
                self.draw_pill(canvas, x2 - cell * 0.07, y1 + cell * 0.07, "HOTTEST", COLORS["warning"], anchor="ne", cell=cell)
            if index == firmware_pixel:
                self.draw_pill(canvas, x1 + cell * 0.07, y2 - cell * 0.07, "FIRMWARE", COLORS["lime"], anchor="sw", cell=cell)

        if self.latest is None:
            message = "Connect to the board or start the demo" if self.source is None else "Waiting for the first frame..."
            text_id = canvas.create_text(width / 2, height / 2, text=message, fill=COLORS["text"], font=(FONT_TITLE, 13))
            bx1, by1, bx2, by2 = canvas.bbox(text_id)
            box = rounded_rect(canvas, bx1 - 22, by1 - 14, bx2 + 22, by2 + 14, 14, fill=COLORS["panel_lift"], outline=COLORS["border"])
            canvas.tag_raise(text_id, box)

    def draw_pill(self, canvas: tk.Canvas, x: float, y: float, text: str, color: str, anchor: str, cell: float) -> None:
        font_size = -max(9, int(cell * 0.075))
        text_id = canvas.create_text(x, y, text=text, anchor=anchor, fill=COLORS["ink_dark"], font=(FONT_TITLE, font_size))
        bx1, by1, bx2, by2 = canvas.bbox(text_id)
        shift_x = -6 if "e" in anchor else 6
        shift_y = 3 if "n" in anchor else -3
        canvas.move(text_id, shift_x, shift_y)
        box = rounded_rect(canvas, bx1 - 6 + shift_x, by1 - 3 + shift_y, bx2 + 6 + shift_x, by2 + 3 + shift_y, 8, fill=color, outline="")
        canvas.tag_raise(text_id, box)

    def draw_legend(self) -> None:
        canvas = self.legend_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 200:
            return
        low, high = self.current_range()
        x1, x2, y1, y2 = 90.0, width - 90.0, 8.0, 22.0
        steps = 120
        for step in range(steps):
            left = x1 + (x2 - x1) * step / steps
            right = x1 + (x2 - x1) * (step + 1) / steps + 1
            value = low + (high - low) * step / (steps - 1)
            canvas.create_rectangle(left, y1, right, y2, fill=heat_color(value, low, high), outline="")
        canvas.create_text(x1 - 12, (y1 + y2) / 2, text=f"{low:.1f} \u00b0C", anchor="e", fill=COLORS["text"], font=(FONT_VALUE, 11))
        canvas.create_text(x2 + 12, (y1 + y2) / 2, text=f"{high:.1f} \u00b0C", anchor="w", fill=COLORS["text"], font=(FONT_VALUE, 11))
        mode = "Locked scale" if self.locked_range else f"Auto scale over the last {HISTORY_SECONDS:.0f} s"
        canvas.create_text(width / 2, y2 + 15, text=mode, fill=COLORS["muted"], font=(FONT_UI, 9))

    def refresh_pixel_card(self) -> None:
        index = self.selected_index
        row, col = pixel_row_col(index)
        self.pixel_title_var.set(f"Pixel {index}")
        self.pixel_position_var.set(f"Row {row + 1}  \u00b7  Column {col + 1}")

        live = self.latest.pixels_c[index] if self.latest else math.nan
        mean = self.history.mean(index)
        span_low, span_high = self.history.span(index)
        self.pixel_live_var.set(f"{live:.1f} \u00b0C" if math.isfinite(live) else "--")
        self.pixel_mean_var.set(f"{mean:.1f} \u00b0C" if math.isfinite(mean) else "--")
        self.pixel_range_var.set(f"{span_high - span_low:.1f} \u00b0C" if math.isfinite(span_low) else "--")
        if self.latest and math.isfinite(live):
            self.pixel_delta_var.set(f"{live - self.latest.ptat_c:+.1f} \u00b0C")
        else:
            self.pixel_delta_var.set("--")

        means = self.history.means()
        if math.isfinite(means[index]):
            rank = 1 + sum(1 for value in means if math.isfinite(value) and value > means[index])
            self.pixel_rank_var.set("Hottest" if rank == 1 else f"{ordinal(rank)} of {PIXEL_COUNT}")
        else:
            self.pixel_rank_var.set("--")

        low, high = self.current_range()
        self.swatch.delete("all")
        rounded_rect(self.swatch, 2, 2, 28, 28, 7, fill=heat_color(live, low, high), outline=COLORS["border"])

    def refresh_firmware_card(self) -> None:
        index = self.selected_index
        for name, badge in self.firmware_badges.items():
            value = self.firmware_pixels.get(name)
            badge.configure(
                text=f"Pixel {value}" if value is not None else "Not found",
                fg=COLORS["lime"] if value == index else COLORS["warning"],
            )

        source_pixel = self.firmware_pixels.get(FIRMWARE_TARGETS[0].name)
        if self.flashed_pixel is None:
            self.board_pixel_var.set("Board: connect to read the flashed pixel.")
        elif self.flashed_pixel != source_pixel:
            self.board_pixel_var.set(f"Board: logs pixel {self.flashed_pixel}; flash firmware_acquisition to apply {source_pixel}.")
        else:
            self.board_pixel_var.set(f"Board: logs pixel {self.flashed_pixel}.")

        values = list(self.firmware_pixels.values())
        missing = [name for name, value in self.firmware_pixels.items() if value is None]
        up_to_date = all(value == index for value in values)
        self.write_button.configure(text=f"Write pixel {index} to both firmwares")
        set_button_kind(self.write_button, "primary", enabled=not up_to_date and not missing)
        if missing:
            self.firmware_hint_var.set("D6TIR_SELECTED_PIXEL_INDEX not found in " + ", ".join(missing) + ".")
        elif up_to_date:
            if not self.firmware_hint_var.get().startswith(f"Pixel {index} written"):
                self.firmware_hint_var.set(f"Both sources already log pixel {index}.")
        else:
            self.firmware_hint_var.set("Then rebuild and flash both projects.")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live D6T pixel map to choose the pixel logged as d6t_temp_c.")
    parser.add_argument("--port", help="ST-LINK virtual COM port, for example COM5; connects at startup.")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--demo", action="store_true", help="Start with simulated frames, without a board.")
    parser.add_argument("--period-ms", type=int, default=DEFAULT_PERIOD_MS, help="Frame request period in ms (100-2000).")
    args = parser.parse_args(argv)
    if not 100 <= args.period_ms <= 2000:
        parser.error("--period-ms must be between 100 and 2000")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    app = D6tCalibrationApp(port=args.port, baud=args.baud, period_ms=args.period_ms, demo=args.demo)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
