"""Real-time dashboard for comparing D6T measurements with NanoEdge AI predictions."""

from __future__ import annotations

import argparse
import csv
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator
import serial
from serial.tools import list_ports


BAUD_RATE = 115200
SERIAL_TIMEOUT_S = 1.0
SERIAL_RETRY_DELAY_S = 0.15
SERIAL_TRANSIENT_RETRY_LIMIT = 100
QUEUE_REFRESH_MS = 40
PLOT_REFRESH_MS = 250
STATUS_REFRESH_MS = 250
PLOT_WINDOW_SECONDS = 90.0
PLOT_MAX_POINTS = 1800
STALE_DATA_SECONDS = 2.0
MIN_LOAD_SETPOINT_A = 0.05
MAX_LOAD_SETPOINT_A = 0.25
PROFILE_COLLECT_ONLY = "Collect only"
PROFILE_STABLE = "Stable"
PROFILE_VARIABLE_LOAD = "Variable load"
PROFILE_VARIABLE_SPEED = "Variable speed"
PROFILE_ALL_VARIABLE = "All variable"
PROFILE_OPTIONS = (
    PROFILE_COLLECT_ONLY,
    PROFILE_STABLE,
    PROFILE_VARIABLE_LOAD,
    PROFILE_VARIABLE_SPEED,
    PROFILE_ALL_VARIABLE,
)
PROFILE_COMMAND_TOKENS = {
    PROFILE_STABLE: "STABLE",
    PROFILE_VARIABLE_LOAD: "VARIABLE_LOAD",
    PROFILE_VARIABLE_SPEED: "VARIABLE_SPEED",
    PROFILE_ALL_VARIABLE: "VARIABLE_ALL",
}
PROFILE_LABELS_BY_TOKEN = {
    token: label for label, token in PROFILE_COMMAND_TOKENS.items()
}
PROFILE_DESCRIPTIONS = {
    PROFILE_COLLECT_ONLY: "Observation uniquement : aucune commande moteur, le bouton physique B2 reste disponible.",
    PROFILE_STABLE: "2500 tr/min et 0,10 A, avec un démarrage protégé à 0,05 A.",
    PROFILE_VARIABLE_LOAD: "2500 tr/min, charge aléatoire de 0,05 à 0,25 A toutes les 2 à 5 s.",
    PROFILE_VARIABLE_SPEED: "Vitesse aléatoire toutes les 2 à 5 s, charge fixe de 0,10 A après le démarrage.",
    PROFILE_ALL_VARIABLE: "Vitesse et charge évoluent toutes les 2 à 5 s, comme avec le bouton B2.",
}
VALIDATION_DIRECTORY = Path(__file__).resolve().parents[1]
CSV_HEADER = (
    "elapsed_s",
    "d6t_temp_c",
    "predicted_temp_c",
    "signed_error_c",
    "absolute_error_c",
    "cumulative_mae_c",
    "load_setpoint_a",
)

COLORS = {
    "background": "#F3F6F9",
    "surface": "#FFFFFF",
    "surface_soft": "#F8FAFC",
    "surface_selected": "#EEF2FF",
    "ink": "#142033",
    "muted": "#66758A",
    "border": "#DDE4EA",
    "border_strong": "#CBD5E1",
    "header": "#0F1B2D",
    "header_soft": "#18263B",
    "header_muted": "#A8B6C8",
    "actual": "#00877B",
    "predicted": "#5368E8",
    "load": "#C97816",
    "purple": "#7C5CE7",
    "green": "#198754",
    "blue": "#4062D6",
    "orange": "#B7791F",
    "red": "#C63C52",
    "neutral": "#66758A",
    "grid": "#E8EDF2",
}

PROFILE_PRESENTATION = {
    PROFILE_COLLECT_ONLY: {
        "title": "Collecte seule",
        "spec": "Aucune commande moteur",
        "accent": COLORS["neutral"],
        "speed_variable": False,
        "load_variable": False,
    },
    PROFILE_STABLE: {
        "title": "Profil stable",
        "spec": "2500 tr/min  ·  0,10 A",
        "accent": COLORS["green"],
        "speed_variable": False,
        "load_variable": False,
    },
    PROFILE_VARIABLE_LOAD: {
        "title": "Charge variable",
        "spec": "2500 tr/min  ·  0,05–0,25 A",
        "accent": COLORS["load"],
        "speed_variable": False,
        "load_variable": True,
    },
    PROFILE_VARIABLE_SPEED: {
        "title": "Vitesse variable",
        "spec": "2000–4000 tr/min  ·  0,10 A",
        "accent": COLORS["blue"],
        "speed_variable": True,
        "load_variable": False,
    },
    PROFILE_ALL_VARIABLE: {
        "title": "Tout variable",
        "spec": "Vitesse + charge  ·  toutes les 2–5 s",
        "accent": COLORS["purple"],
        "speed_variable": True,
        "load_variable": True,
    },
}

ERROR_BAND_BACKGROUNDS = {
    COLORS["green"]: "#ECF8F2",
    COLORS["blue"]: "#EEF3FF",
    COLORS["orange"]: "#FFF7E8",
    COLORS["red"]: "#FFF0F2",
    COLORS["neutral"]: COLORS["surface_soft"],
}


@dataclass(frozen=True)
class ErrorBand:
    name: str
    color: str


@dataclass(frozen=True)
class ValidationSample:
    elapsed_s: float
    actual_c: float
    predicted_c: float
    signed_error_c: float
    absolute_error_c: float
    cumulative_mae_c: float
    load_setpoint_a: float = math.nan


ERROR_BANDS = (
    (0.5, ErrorBand("Excellent", COLORS["green"])),
    (1.0, ErrorBand("Good", COLORS["blue"])),
    (1.5, ErrorBand("Needs attention", COLORS["orange"])),
)
RED_ERROR_BAND = ErrorBand("High error", COLORS["red"])
NEUTRAL_ERROR_BAND = ErrorBand("Waiting", COLORS["neutral"])


def normalize_profile(value: str) -> str:
    """Return the canonical GUI label for a validation profile."""
    normalized = str(value).strip().casefold()
    for profile in PROFILE_OPTIONS:
        if normalized == profile.casefold():
            return profile
    raise ValueError(f"Unsupported motor profile: {value!r}")


def build_profile_command(profile: str) -> bytes | None:
    """Build a profile command, or no command for acquisition-only mode."""
    normalized_profile = normalize_profile(profile)
    token = PROFILE_COMMAND_TOKENS.get(normalized_profile)
    if token is None:
        return None
    return f"PROFILE,{token}\n".encode("ascii")


def parse_control_response(raw_line: bytes | str) -> tuple[str, str | None]:
    """Classify a firmware control response without interpreting telemetry."""
    if isinstance(raw_line, bytes):
        try:
            line = raw_line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("The control response contains non-ASCII bytes.") from exc
    else:
        line = str(raw_line)

    line = line.strip()
    if line.startswith("ERR,"):
        return "error", None
    if line == "ACK,STOP":
        return "stop_ack", None
    if line.startswith("ACK,PROFILE,"):
        token = line.removeprefix("ACK,PROFILE,").strip().upper()
        if token in PROFILE_LABELS_BY_TOKEN:
            return "profile_ack", token
    if line.startswith("ACK,"):
        return "ack", None
    return "info", None


def parse_validation_line(raw_line: bytes | str) -> tuple[float, float, float | None]:
    """Parse legacy two-field or load-aware three-field validation telemetry."""
    if isinstance(raw_line, bytes):
        try:
            line = raw_line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("The frame contains non-ASCII bytes.") from exc
    else:
        line = str(raw_line)

    fields = [field.strip() for field in line.strip().split(";")]
    if len(fields) not in {2, 3}:
        raise ValueError(f"Expected two or three values; received {len(fields)}.")

    try:
        actual_c = float(fields[0])
        predicted_c = float(fields[1])
        load_setpoint_a = float(fields[2]) if len(fields) == 3 else None
    except ValueError as exc:
        raise ValueError("The frame contains a nonnumeric value.") from exc

    if not math.isfinite(actual_c) or not math.isfinite(predicted_c):
        raise ValueError("The frame contains NaN or an infinite value.")
    if load_setpoint_a is not None:
        if not math.isfinite(load_setpoint_a):
            raise ValueError("The load setpoint contains NaN or an infinite value.")
        if not MIN_LOAD_SETPOINT_A <= load_setpoint_a <= MAX_LOAD_SETPOINT_A:
            raise ValueError("The load setpoint is outside the 0.05 A to 0.25 A range.")

    return actual_c, predicted_c, load_setpoint_a


def classify_error(absolute_error_c: float) -> ErrorBand:
    """Return the color band for an absolute error in degrees Celsius."""
    error = abs(float(absolute_error_c))
    if not math.isfinite(error):
        return NEUTRAL_ERROR_BAND
    for upper_bound, band in ERROR_BANDS:
        if error < upper_bound:
            return band
    if error <= 1.5:
        return ERROR_BANDS[-1][1]
    return RED_ERROR_BAND


def format_one_decimal(value: float) -> str:
    """Format a number with exactly one decimal place."""
    if not math.isfinite(value):
        return "--.-"
    rounded = round(value, 1)
    if rounded == 0.0:
        rounded = 0.0
    return f"{rounded:.1f}"


def is_current_connection_event(event_generation: int, current_generation: int) -> bool:
    """Return whether an event still belongs to the active serial connection."""
    return event_generation == current_generation


def is_transient_serial_error(error: Exception) -> bool:
    """Identify the potentially transient Windows ClearCommError."""
    message = str(error).casefold()
    return "clearcommerror" in message or "does not recognize the command" in message


def csv_sample_row(sample: ValidationSample) -> tuple[str, ...]:
    load_setpoint = (
        f"{sample.load_setpoint_a:.6f}"
        if math.isfinite(sample.load_setpoint_a)
        else "NaN"
    )
    return (
        f"{sample.elapsed_s:.3f}",
        f"{sample.actual_c:.6f}",
        f"{sample.predicted_c:.6f}",
        f"{sample.signed_error_c:.6f}",
        f"{sample.absolute_error_c:.6f}",
        f"{sample.cumulative_mae_c:.6f}",
        load_setpoint,
    )


class CsvSessionRecorder:
    """Write a CSV session incrementally to limit data loss."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream = path.open("x", newline="", encoding="utf-8")
        self.writer = csv.writer(self.stream, delimiter=";")
        self.writer.writerow(CSV_HEADER)
        self.stream.flush()

    def append(self, sample: ValidationSample) -> None:
        self.writer.writerow(csv_sample_row(sample))
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


class SessionAccumulator:
    """Calculate instantaneous errors and cumulative mean absolute error (MAE)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.count = 0
        self.total_absolute_error_c = 0.0
        self.started_at_s: float | None = None

    def add(
        self,
        timestamp_s: float,
        actual_c: float,
        predicted_c: float,
        load_setpoint_a: float = math.nan,
    ) -> ValidationSample:
        if self.started_at_s is None:
            self.started_at_s = timestamp_s

        signed_error_c = predicted_c - actual_c
        absolute_error_c = abs(signed_error_c)
        self.count += 1
        self.total_absolute_error_c += absolute_error_c

        return ValidationSample(
            elapsed_s=max(0.0, timestamp_s - self.started_at_s),
            actual_c=actual_c,
            predicted_c=predicted_c,
            signed_error_c=signed_error_c,
            absolute_error_c=absolute_error_c,
            cumulative_mae_c=self.total_absolute_error_c / self.count,
            load_setpoint_a=float(load_setpoint_a),
        )


class TemperatureValidationApp(tk.Tk):
    """Main interface for real-time temperature validation."""

    def __init__(self, *, demo: bool = False, initial_port: str | None = None) -> None:
        super().__init__()

        self.title("Validation thermique NanoEdge AI")
        self.geometry("1320x780")
        self.minsize(1040, 760)
        self.configure(bg=COLORS["background"])

        self.demo_mode = demo
        self.initial_port = initial_port
        self.serial_connection: serial.Serial | None = None
        self.reader_thread: threading.Thread | None = None
        self.reader_stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.connection_generation = 0
        self.automatic_export: CsvSessionRecorder | None = None

        self.accumulator = SessionAccumulator()
        self.session_samples: list[ValidationSample] = []
        self.plot_samples: deque[ValidationSample] = deque(maxlen=PLOT_MAX_POINTS)
        self.recent_timestamps: deque[float] = deque(maxlen=50)
        self.invalid_frame_count = 0
        self.last_sample_monotonic: float | None = None
        self.plot_dirty = True
        self.demo_started_at = time.monotonic()

        self.port_var = tk.StringVar()
        self.port_display_to_device: dict[str, str] = {}
        self.profile_var = tk.StringVar(value=PROFILE_COLLECT_ONLY)
        self.profile_description_var = tk.StringVar(
            value=PROFILE_DESCRIPTIONS[PROFILE_COLLECT_ONLY]
        )
        self.active_profile_token: str | None = None
        self.pending_control_command: str | None = None
        self.control_command_error: str | None = None
        self.gui_started_motor = False
        self.status_var = tk.StringVar(value="Prêt à connecter la carte")
        self.serial_state_var = tk.StringVar(value="Série · hors ligne")
        self.data_state_var = tk.StringVar(value="Données · en attente")
        self.motor_state_var = tk.StringVar(value="Moteur · non piloté")
        self.sample_count_var = tk.StringVar(value="0")
        self.rate_var = tk.StringVar(value="0.0 Hz")
        self.invalid_var = tk.StringVar(value="0")
        self.load_value_var = tk.StringVar(value="—")
        self.instant_error_detail_var = tk.StringVar(value="En attente de données")
        self.profile_context_var = tk.StringVar(
            value=PROFILE_DESCRIPTIONS[PROFILE_COLLECT_ONLY]
        )

        self.profile_cards: dict[str, dict[str, object]] = {}
        self.state_indicators: dict[str, tuple[tk.Canvas, int]] = {}

        self.actual_value_label: tk.Label
        self.predicted_value_label: tk.Label
        self.instant_error_frame: tk.Frame
        self.instant_error_labels: list[tk.Label]
        self.instant_error_value_label: tk.Label
        self.cumulative_error_frame: tk.Frame
        self.cumulative_error_labels: list[tk.Label]
        self.cumulative_error_value_label: tk.Label
        self.load_gauge: tk.Canvas

        self.setup_style()
        self.build_ui()
        self.profile_var.trace_add("write", self.on_profile_changed)
        self.update_profile_controls_state()
        self.refresh_ports()
        self.reset_session()

        self.after(QUEUE_REFRESH_MS, self.process_events)
        self.after(PLOT_REFRESH_MS, self.redraw_plot_periodic)
        self.after(STATUS_REFRESH_MS, self.refresh_connection_status)

        if self.demo_mode:
            self.start_demo_mode()
        elif self.initial_port:
            self.select_port(self.initial_port)
            self.after(300, self.connect_serial)

    def setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Validation.TCombobox",
            fieldbackground=COLORS["surface"],
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            arrowcolor=COLORS["ink"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=7,
            font=("Segoe UI", 10),
        )
        style.map(
            "Validation.TCombobox",
            fieldbackground=[("readonly", COLORS["surface"])],
            foreground=[("readonly", COLORS["ink"])],
            bordercolor=[("focus", COLORS["blue"])],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["blue"],
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(16, 10),
            font=("Segoe UI Semibold", 10),
            focuscolor=COLORS["blue"],
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#304DB8"), ("disabled", "#A8B4C5")],
            foreground=[("disabled", "#EEF2F7")],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=(13, 9),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", COLORS["surface_soft"]), ("disabled", "#F1F4F7")],
            foreground=[("disabled", "#A6B0BD")],
            bordercolor=[("focus", COLORS["blue"])],
        )
        style.configure(
            "Danger.TButton",
            background="#FFF4F5",
            foreground=COLORS["red"],
            bordercolor="#F2C8CF",
            lightcolor="#F2C8CF",
            darkcolor="#F2C8CF",
            borderwidth=1,
            padding=(16, 10),
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#FDE7EA"), ("disabled", "#F5F6F8")],
            foreground=[("disabled", "#B7BEC8")],
            bordercolor=[("focus", COLORS["red"])],
        )

    def build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.build_header()
        self.build_toolbar()

        content = tk.Frame(self, bg=COLORS["background"])
        content.grid(row=2, column=0, sticky="nsew", padx=18, pady=16)
        content.grid_columnconfigure(0, minsize=292)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.build_profile_selector(content).grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 14),
        )

        dashboard = tk.Frame(content, bg=COLORS["background"])
        dashboard.grid(row=0, column=1, sticky="nsew")
        self.dashboard_frame = dashboard
        dashboard.grid_rowconfigure(1, weight=1)
        for column in range(5):
            dashboard.grid_columnconfigure(column, weight=1, uniform="metric")

        actual_panel, self.actual_value_label = self.build_temperature_panel(
            dashboard,
            title="TEMP. MESURÉE",
            subtitle="Capteur infrarouge D6T",
            accent=COLORS["actual"],
        )
        actual_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        predicted_panel, self.predicted_value_label = self.build_temperature_panel(
            dashboard,
            title="PRÉDICTION IA",
            subtitle="Estimation NanoEdge AI",
            accent=COLORS["predicted"],
        )
        predicted_panel.grid(row=0, column=1, sticky="nsew", padx=5)

        (
            self.instant_error_frame,
            self.instant_error_value_label,
            self.instant_error_labels,
        ) = self.build_error_panel(
            dashboard,
            title="ERREUR |e|",
            subtitle_variable=self.instant_error_detail_var,
        )
        self.instant_error_frame.grid(row=0, column=2, sticky="nsew", padx=5)

        (
            self.cumulative_error_frame,
            self.cumulative_error_value_label,
            self.cumulative_error_labels,
        ) = self.build_error_panel(
            dashboard,
            title="MAE SESSION",
            subtitle="Moyenne depuis le départ",
        )
        self.cumulative_error_frame.grid(row=0, column=3, sticky="nsew", padx=5)

        load_panel = self.build_load_panel(dashboard)
        load_panel.grid(
            row=0,
            column=4,
            sticky="nsew",
            padx=(5, 0),
        )

        self.metric_panels = (
            actual_panel,
            predicted_panel,
            self.instant_error_frame,
            self.cumulative_error_frame,
            load_panel,
        )
        self.chart_panel = self.build_chart(dashboard)
        self.chart_panel.grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="nsew",
            pady=(12, 0),
        )
        self.compact_dashboard_layout: bool | None = None
        self.bind("<Configure>", self.on_window_resized, add="+")
        self.after_idle(lambda: self.apply_dashboard_layout(self.winfo_width()))

    def on_window_resized(self, event: tk.Event) -> None:
        if event.widget is self:
            self.apply_dashboard_layout(int(event.width))

    def apply_dashboard_layout(self, window_width: int) -> None:
        compact = window_width < 1120
        if compact == self.compact_dashboard_layout:
            return
        self.compact_dashboard_layout = compact

        dashboard = self.dashboard_frame
        for column in range(6):
            dashboard.grid_columnconfigure(column, weight=0, uniform="")
        for row in range(3):
            dashboard.grid_rowconfigure(row, weight=0)
        for panel in self.metric_panels:
            panel.grid_forget()
        self.chart_panel.grid_forget()

        if compact:
            for column in range(6):
                dashboard.grid_columnconfigure(column, weight=1, uniform="metric_compact")
            dashboard.grid_rowconfigure(2, weight=1)
            self.metric_panels[0].grid(
                row=0,
                column=0,
                columnspan=3,
                sticky="nsew",
                padx=(0, 5),
            )
            self.metric_panels[1].grid(
                row=0,
                column=3,
                columnspan=3,
                sticky="nsew",
                padx=(5, 0),
            )
            self.metric_panels[2].grid(
                row=1,
                column=0,
                columnspan=2,
                sticky="nsew",
                padx=(0, 5),
                pady=(10, 0),
            )
            self.metric_panels[3].grid(
                row=1,
                column=2,
                columnspan=2,
                sticky="nsew",
                padx=5,
                pady=(10, 0),
            )
            self.metric_panels[4].grid(
                row=1,
                column=4,
                columnspan=2,
                sticky="nsew",
                padx=(5, 0),
                pady=(10, 0),
            )
            self.chart_panel.grid(
                row=2,
                column=0,
                columnspan=6,
                sticky="nsew",
                pady=(12, 0),
            )
            return

        for column in range(5):
            dashboard.grid_columnconfigure(column, weight=1, uniform="metric")
        dashboard.grid_rowconfigure(1, weight=1)
        for column, panel in enumerate(self.metric_panels):
            panel.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0, 5) if column == 0 else (5, 0) if column == 4 else 5,
            )
        self.chart_panel.grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="nsew",
            pady=(12, 0),
        )

    def build_header(self) -> None:
        header = tk.Frame(self, bg=COLORS["header"], height=92)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        title_block = tk.Frame(header, bg=COLORS["header"])
        title_block.grid(row=0, column=0, sticky="w", padx=24, pady=8)
        tk.Label(
            title_block,
            text="VALIDATION EMBARQUÉE  ·  NANOEDGE AI",
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="Température moteur",
            bg=COLORS["header"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 20),
        ).pack(anchor="w", pady=(1, 0))

        event_line = tk.Frame(title_block, bg=COLORS["header"])
        event_line.pack(anchor="w", pady=(3, 0))
        self.status_dot = tk.Canvas(
            event_line,
            width=10,
            height=10,
            bg=COLORS["header"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_dot_id = self.status_dot.create_oval(
            1,
            1,
            9,
            9,
            fill=COLORS["neutral"],
            outline="",
        )
        tk.Label(
            event_line,
            textvariable=self.status_var,
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=("Segoe UI", 9),
        ).pack(side="left")

        indicators = tk.Frame(header, bg=COLORS["header"])
        indicators.grid(row=0, column=1, sticky="e", padx=24)
        self.build_state_chip(
            indicators,
            "serial",
            self.serial_state_var,
            COLORS["neutral"],
        ).pack(side="left", padx=(0, 6))
        self.build_state_chip(
            indicators,
            "data",
            self.data_state_var,
            COLORS["neutral"],
        ).pack(side="left", padx=6)
        self.build_state_chip(
            indicators,
            "motor",
            self.motor_state_var,
            COLORS["neutral"],
        ).pack(side="left", padx=(6, 0))

    def build_state_chip(
        self,
        parent: tk.Widget,
        key: str,
        variable: tk.StringVar,
        color: str,
    ) -> tk.Frame:
        chip = tk.Frame(
            parent,
            bg=COLORS["header_soft"],
            highlightthickness=1,
            highlightbackground="#263A55",
        )
        dot = tk.Canvas(
            chip,
            width=12,
            height=12,
            bg=COLORS["header_soft"],
            highlightthickness=0,
        )
        dot.pack(side="left", padx=(10, 6), pady=10)
        dot_id = dot.create_oval(2, 2, 10, 10, fill=color, outline="")
        tk.Label(
            chip,
            textvariable=variable,
            bg=COLORS["header_soft"],
            fg="#E8EEF6",
            font=("Segoe UI Semibold", 8),
        ).pack(side="left", padx=(0, 10), pady=8)
        self.state_indicators[key] = (dot, dot_id)
        return chip

    def build_toolbar(self) -> None:
        toolbar = tk.Frame(
            self,
            bg=COLORS["surface"],
            height=70,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        toolbar.grid(row=1, column=0, sticky="ew")
        toolbar.grid_propagate(False)
        toolbar.grid_columnconfigure(1, weight=1)

        tk.Label(
            toolbar,
            text="CONNEXION CARTE",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI Semibold", 8),
        ).grid(row=0, column=0, padx=(22, 12), pady=20)

        self.port_combo = ttk.Combobox(
            toolbar,
            textvariable=self.port_var,
            state="readonly",
            style="Validation.TCombobox",
            width=42,
        )
        self.port_combo.grid(row=0, column=1, sticky="ew", pady=13)

        self.refresh_button = ttk.Button(
            toolbar,
            text="Actualiser",
            command=self.refresh_ports,
            style="Secondary.TButton",
        )
        self.refresh_button.grid(row=0, column=2, padx=(10, 6))

        self.connect_button = ttk.Button(
            toolbar,
            text="Connecter",
            command=self.toggle_connection,
            style="Primary.TButton",
        )
        self.connect_button.grid(row=0, column=3, padx=6)

        tk.Frame(toolbar, bg=COLORS["border"], width=1, height=34).grid(
            row=0,
            column=4,
            padx=10,
        )

        self.reset_button = ttk.Button(
            toolbar,
            text="Réinitialiser",
            command=self.reset_session,
            style="Secondary.TButton",
        )
        self.reset_button.grid(row=0, column=5, padx=6)

        self.export_button = ttk.Button(
            toolbar,
            text="Exporter CSV",
            command=self.export_csv,
            style="Secondary.TButton",
            state=tk.DISABLED,
        )
        self.export_button.grid(row=0, column=6, padx=(6, 22))

    def build_profile_selector(self, parent: tk.Widget) -> tk.Frame:
        panel = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        heading = tk.Frame(panel, bg=COLORS["surface"])
        heading.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 8))
        tk.Label(
            heading,
            text="PROFIL DE TEST",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text="Sélectionnez un scénario, puis lancez-le explicitement.",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(3, 0))

        cards = tk.Frame(panel, bg=COLORS["surface"])
        cards.grid(row=1, column=0, sticky="nsew", padx=14)
        for profile in PROFILE_OPTIONS:
            self.build_profile_card(cards, profile).pack(fill="x", pady=(0, 5))

        context = tk.Frame(
            panel,
            bg=COLORS["surface_soft"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        context.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 7))
        tk.Label(
            context,
            textvariable=self.profile_context_var,
            bg=COLORS["surface_soft"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8),
            wraplength=245,
            justify="left",
        ).pack(anchor="w", padx=11, pady=7)

        safety = tk.Frame(panel, bg="#EEF8F4")
        safety.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        tk.Frame(safety, bg=COLORS["green"], width=4).pack(side="left", fill="y")
        tk.Label(
            safety,
            text="DÉMARRAGE SÉCURISÉ  ·  charge à 0,05 A avant RUN",
            bg="#EEF8F4",
            fg="#176B49",
            font=("Segoe UI Semibold", 7),
            wraplength=240,
            justify="left",
        ).pack(side="left", padx=9, pady=6)

        actions = tk.Frame(panel, bg=COLORS["surface"])
        actions.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 14))
        actions.grid_columnconfigure(0, weight=1)

        self.launch_profile_button = ttk.Button(
            actions,
            text="Démarrer ce profil",
            command=self.launch_selected_profile,
            style="Primary.TButton",
            state=tk.DISABLED,
        )
        self.launch_profile_button.grid(row=0, column=0, sticky="ew")

        self.stop_motor_button = ttk.Button(
            actions,
            text="Arrêter le moteur",
            command=self.stop_motor,
            style="Danger.TButton",
            state=tk.DISABLED,
        )
        self.stop_motor_button.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        return panel

    def build_profile_card(self, parent: tk.Widget, profile: str) -> tk.Frame:
        presentation = PROFILE_PRESENTATION[profile]
        card = tk.Frame(
            parent,
            bg=COLORS["surface"],
            height=56,
            cursor="hand2",
            takefocus=1,
            highlightthickness=2,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["blue"],
        )
        card.pack_propagate(False)

        accent_bar = tk.Frame(card, bg=str(presentation["accent"]), width=4)
        accent_bar.pack(side="left", fill="y")

        signal = tk.Canvas(
            card,
            width=50,
            height=34,
            bg=COLORS["surface"],
            highlightthickness=0,
        )
        signal.pack(side="left", padx=(8, 5), pady=7)

        text_block = tk.Frame(card, bg=COLORS["surface"])
        text_block.pack(side="left", fill="both", expand=True, pady=5)
        title = tk.Label(
            text_block,
            text=str(presentation["title"]),
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 9),
        )
        title.pack(anchor="w")
        spec = tk.Label(
            text_block,
            text=str(presentation["spec"]),
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 7),
        )
        spec.pack(anchor="w", pady=(2, 0))

        badge = tk.Label(
            card,
            text="",
            bg=COLORS["surface"],
            fg=COLORS["blue"],
            font=("Segoe UI Semibold", 6),
            width=9,
            anchor="e",
        )
        badge.pack(side="right", padx=(3, 9))

        for widget in (card, accent_bar, signal, text_block, title, spec, badge):
            widget.bind(
                "<Button-1>",
                lambda _event, selected=profile: self.select_profile_card(selected),
            )
        card.bind(
            "<Return>",
            lambda _event, selected=profile: self.select_profile_card(selected),
        )
        card.bind(
            "<space>",
            lambda _event, selected=profile: self.select_profile_card(selected),
        )
        card.bind(
            "<Enter>",
            lambda _event, selected=profile: self.set_profile_card_hover(selected, True),
        )
        card.bind(
            "<Leave>",
            lambda _event, selected=profile: self.set_profile_card_hover(selected, False),
        )

        self.profile_cards[profile] = {
            "card": card,
            "accent": accent_bar,
            "signal": signal,
            "text_block": text_block,
            "title": title,
            "spec": spec,
            "badge": badge,
        }
        self.draw_profile_signal(signal, profile, COLORS["surface"])
        return card

    def draw_profile_signal(
        self,
        canvas: tk.Canvas,
        profile: str,
        background: str,
    ) -> None:
        presentation = PROFILE_PRESENTATION[profile]
        color = str(presentation["accent"])
        canvas.configure(bg=background)
        canvas.delete("all")
        canvas.create_text(
            4,
            10,
            text="V",
            fill=COLORS["muted"],
            font=("Segoe UI", 6, "bold"),
        )
        canvas.create_text(
            4,
            28,
            text="C",
            fill=COLORS["muted"],
            font=("Segoe UI", 6, "bold"),
        )
        speed_points = (
            (11, 10, 19, 5, 27, 14, 36, 6, 47, 11)
            if bool(presentation["speed_variable"])
            else (11, 10, 47, 10)
        )
        load_points = (
            (11, 28, 18, 23, 26, 31, 35, 24, 47, 28)
            if bool(presentation["load_variable"])
            else (11, 28, 47, 28)
        )
        canvas.create_line(*speed_points, fill=color, width=2)
        canvas.create_line(*load_points, fill=color, width=2)

    def select_profile_card(self, profile: str) -> None:
        if self.demo_mode:
            return
        self.profile_var.set(profile)
        card = self.profile_cards.get(profile, {}).get("card")
        if isinstance(card, tk.Widget):
            card.focus_set()

    def set_profile_card_hover(self, profile: str, hovering: bool) -> None:
        if self.demo_mode:
            return
        card = self.profile_cards.get(profile, {}).get("card")
        if not isinstance(card, tk.Frame):
            return
        if hovering and profile != self.selected_profile():
            card.configure(highlightbackground=COLORS["border_strong"])
        else:
            self.refresh_profile_cards()

    def refresh_profile_cards(self) -> None:
        if not self.profile_cards:
            return

        selected = self.selected_profile()
        active_profile = (
            PROFILE_LABELS_BY_TOKEN.get(self.active_profile_token)
            if self.active_profile_token is not None
            else None
        )
        pending_profile: str | None = None
        if self.pending_control_command is not None and self.pending_control_command.startswith("PROFILE,"):
            pending_profile = PROFILE_LABELS_BY_TOKEN.get(
                self.pending_control_command.split(",", 1)[1]
            )

        for profile, widgets in self.profile_cards.items():
            presentation = PROFILE_PRESENTATION[profile]
            is_selected = profile == selected
            is_active = profile == active_profile
            is_pending = profile == pending_profile
            background = COLORS["surface_selected"] if is_selected else COLORS["surface"]
            if is_active and not is_selected:
                background = "#ECF8F2"

            border = COLORS["border"]
            if is_selected:
                border = str(presentation["accent"])
            if is_active:
                border = COLORS["green"]

            card = widgets["card"]
            assert isinstance(card, tk.Frame)
            card.configure(
                bg=background,
                highlightbackground=border,
                cursor="arrow" if self.demo_mode else "hand2",
                takefocus=0 if self.demo_mode else 1,
            )
            for key in ("text_block", "title", "spec"):
                widget = widgets[key]
                assert isinstance(widget, tk.Widget)
                widget.configure(bg=background)

            badge = widgets["badge"]
            title = widgets["title"]
            spec = widgets["spec"]
            signal = widgets["signal"]
            assert isinstance(badge, tk.Label)
            assert isinstance(title, tk.Label)
            assert isinstance(spec, tk.Label)
            assert isinstance(signal, tk.Canvas)
            badge.configure(bg=background)
            if is_pending:
                badge.configure(text="ENVOI…", fg=COLORS["orange"])
            elif is_active:
                badge.configure(text="ACTIF", fg=COLORS["green"])
            elif is_selected:
                badge.configure(text="SÉLECTION", fg=str(presentation["accent"]))
            else:
                badge.configure(text="")

            title.configure(fg=COLORS["muted"] if self.demo_mode else COLORS["ink"])
            spec.configure(fg="#A1ACB9" if self.demo_mode else COLORS["muted"])
            self.draw_profile_signal(signal, profile, background)

    def build_temperature_panel(
        self,
        parent: tk.Widget,
        *,
        title: str,
        subtitle: str,
        accent: str,
    ) -> tuple[tk.Frame, tk.Label]:
        panel = tk.Frame(
            parent,
            bg=COLORS["surface"],
            height=132,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        tk.Frame(panel, bg=accent, height=4).grid(row=0, column=0, sticky="ew")
        tk.Label(
            panel,
            text=title,
            bg=COLORS["surface"],
            fg=accent,
            font=("Segoe UI Semibold", 8),
        ).grid(row=1, column=0, sticky="w", padx=13, pady=(10, 0))

        value_row = tk.Frame(panel, bg=COLORS["surface"])
        value_row.grid(row=2, column=0, sticky="w", padx=12, pady=(2, 0))
        value_label = tk.Label(
            value_row,
            text="--.-",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 29),
        )
        value_label.pack(side="left")
        tk.Label(
            value_row,
            text=" °C",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 12),
        ).pack(side="left", anchor="s", pady=(0, 5))

        tk.Label(
            panel,
            text=subtitle,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 7),
        ).grid(row=3, column=0, sticky="w", padx=13, pady=(0, 10))
        return panel, value_label

    def build_error_panel(
        self,
        parent: tk.Widget,
        *,
        title: str,
        subtitle: str | None = None,
        subtitle_variable: tk.StringVar | None = None,
    ) -> tuple[tk.Frame, tk.Label, list[tk.Label]]:
        background = COLORS["surface_soft"]
        panel = tk.Frame(
            parent,
            bg=background,
            height=132,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        accent_bar = tk.Frame(panel, bg=COLORS["neutral"], height=4)
        accent_bar.grid(row=0, column=0, columnspan=2, sticky="ew")

        title_label = tk.Label(
            panel,
            text=title,
            bg=background,
            fg=COLORS["neutral"],
            font=("Segoe UI Semibold", 8),
        )
        title_label.grid(row=1, column=0, sticky="w", padx=13, pady=(10, 0))

        band_label = tk.Label(
            panel,
            text="EN ATTENTE",
            bg=background,
            fg=COLORS["neutral"],
            font=("Segoe UI Semibold", 6),
        )
        band_label.grid(row=1, column=1, sticky="e", padx=10, pady=(10, 0))

        value_label = tk.Label(
            panel,
            text="--.- °C",
            bg=background,
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 23),
        )
        value_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(3, 0))

        subtitle_label = tk.Label(
            panel,
            text=subtitle or "",
            textvariable=subtitle_variable,
            bg=background,
            fg=COLORS["muted"],
            font=("Segoe UI", 7),
            anchor="w",
        )
        subtitle_label.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=13,
            pady=(0, 9),
        )

        panel._accent_bar = accent_bar  # type: ignore[attr-defined]
        panel._band_label = band_label  # type: ignore[attr-defined]
        return panel, value_label, [title_label, value_label, subtitle_label, band_label]

    def build_load_panel(self, parent: tk.Widget) -> tk.Frame:
        panel = tk.Frame(
            parent,
            bg=COLORS["surface"],
            height=132,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        tk.Frame(panel, bg=COLORS["load"], height=4).grid(row=0, column=0, sticky="ew")
        tk.Label(
            panel,
            text="CHARGE TB-200S",
            bg=COLORS["surface"],
            fg=COLORS["load"],
            font=("Segoe UI Semibold", 8),
        ).grid(row=1, column=0, sticky="w", padx=13, pady=(10, 0))
        tk.Label(
            panel,
            textvariable=self.load_value_var,
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 23),
        ).grid(row=2, column=0, sticky="w", padx=12, pady=(3, 0))
        self.load_gauge = tk.Canvas(
            panel,
            width=118,
            height=20,
            bg=COLORS["surface"],
            highlightthickness=0,
        )
        self.load_gauge.grid(row=3, column=0, sticky="w", padx=13, pady=(0, 6))
        self.update_load_gauge(math.nan)
        return panel

    def update_load_gauge(self, load_a: float) -> None:
        if not hasattr(self, "load_gauge"):
            return
        canvas = self.load_gauge
        canvas.delete("all")
        x_start = 2
        x_end = 116
        canvas.create_rectangle(
            x_start,
            5,
            x_end,
            11,
            fill="#E6EBF0",
            outline="",
        )
        if math.isfinite(load_a):
            ratio = (load_a - MIN_LOAD_SETPOINT_A) / (
                MAX_LOAD_SETPOINT_A - MIN_LOAD_SETPOINT_A
            )
            ratio = max(0.0, min(1.0, ratio))
            canvas.create_rectangle(
                x_start,
                5,
                x_start + ((x_end - x_start) * ratio),
                11,
                fill=COLORS["load"],
                outline="",
            )
        canvas.create_text(
            x_start,
            17,
            text="0,05",
            anchor="w",
            fill=COLORS["muted"],
            font=("Segoe UI", 6),
        )
        canvas.create_text(
            x_end,
            17,
            text="0,25 A",
            anchor="e",
            fill=COLORS["muted"],
            font=("Segoe UI", 6),
        )

    def build_chart(self, parent: tk.Widget) -> tk.Frame:
        panel = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        chart_header = tk.Frame(panel, bg=COLORS["surface"])
        chart_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(11, 2))
        chart_header.grid_columnconfigure(0, weight=1)
        chart_title = tk.Frame(chart_header, bg=COLORS["surface"])
        chart_title.grid(row=0, column=0, sticky="w")
        tk.Label(
            chart_title,
            text="Historique temps réel",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")
        tk.Label(
            chart_title,
            text="D6T / IA, erreur absolue et consigne de charge · fenêtre de 90 secondes",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 7),
        ).pack(anchor="w", pady=(1, 0))

        stats = tk.Frame(chart_header, bg=COLORS["surface"])
        stats.grid(row=0, column=1, sticky="e")
        self.build_stat(stats, "ÉCHANTILLONS", self.sample_count_var).pack(side="left", padx=4)
        self.build_stat(stats, "FRÉQUENCE", self.rate_var).pack(side="left", padx=4)
        self.build_stat(stats, "TRAMES INVALIDES", self.invalid_var).pack(side="left", padx=(4, 0))

        self.figure = Figure(figsize=(10, 5), dpi=100, facecolor=COLORS["surface"])
        grid = self.figure.add_gridspec(
            6,
            1,
            hspace=0.10,
            height_ratios=(1.0, 1.0, 1.0, 1.0, 0.9, 0.9),
        )
        self.temperature_axis = self.figure.add_subplot(grid[:4, 0])
        self.error_axis = self.figure.add_subplot(grid[4, 0], sharex=self.temperature_axis)
        self.load_axis = self.figure.add_subplot(grid[5, 0], sharex=self.temperature_axis)
        self.figure.subplots_adjust(left=0.075, right=0.985, top=0.965, bottom=0.14)

        self.actual_line, = self.temperature_axis.plot(
            [],
            [],
            color=COLORS["actual"],
            linewidth=2.4,
            label="Mesure D6T",
        )
        self.predicted_line, = self.temperature_axis.plot(
            [],
            [],
            color=COLORS["predicted"],
            linewidth=2.1,
            linestyle=(0, (6, 3)),
            label="Prédiction IA",
        )
        self.error_line, = self.error_axis.plot(
            [],
            [],
            color=COLORS["ink"],
            linewidth=1.1,
            alpha=0.55,
        )
        self.load_line, = self.load_axis.step(
            [],
            [],
            where="post",
            color=COLORS["load"],
            linewidth=1.8,
        )
        self.error_scatter = None
        self.error_fill = None
        self.load_fill = None

        self.waiting_text = self.temperature_axis.text(
            0.5,
            0.50,
            "En attente de télémétrie",
            transform=self.temperature_axis.transAxes,
            ha="center",
            va="center",
            color=COLORS["muted"],
            fontsize=10,
        )
        self.load_empty_text = self.load_axis.text(
            0.5,
            0.5,
            "Charge non transmise",
            transform=self.load_axis.transAxes,
            ha="center",
            va="center",
            color=COLORS["muted"],
            fontsize=7,
        )

        self.configure_axes()

        self.canvas = FigureCanvasTkAgg(self.figure, master=panel)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=7, pady=(2, 7))
        return panel

    def build_stat(self, parent: tk.Widget, title: str, variable: tk.StringVar) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=COLORS["surface_soft"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        tk.Label(
            frame,
            text=title,
            bg=COLORS["surface_soft"],
            fg=COLORS["muted"],
            font=("Segoe UI Semibold", 6),
        ).pack(anchor="e", padx=9, pady=(4, 0))
        tk.Label(
            frame,
            textvariable=variable,
            bg=COLORS["surface_soft"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="e", padx=9, pady=(0, 4))
        return frame

    def configure_axes(self) -> None:
        decimal_formatter = FuncFormatter(lambda value, _position: f"{value:.1f}")

        for axis in (self.temperature_axis, self.error_axis, self.load_axis):
            axis.set_facecolor(COLORS["surface"])
            axis.grid(
                True,
                axis="y",
                color=COLORS["grid"],
                linewidth=0.8,
                alpha=0.85,
            )
            axis.tick_params(colors=COLORS["muted"], labelsize=8)
            axis.yaxis.set_major_formatter(decimal_formatter)
            axis.spines["top"].set_visible(False)
            axis.spines["right"].set_visible(False)
            axis.spines["left"].set_color(COLORS["border"])
            axis.spines["bottom"].set_color(COLORS["border"])

        self.temperature_axis.tick_params(labelbottom=False)
        self.error_axis.tick_params(labelbottom=False)
        self.temperature_axis.set_ylabel("Temp. (°C)", color=COLORS["muted"], fontsize=8)
        self.error_axis.set_ylabel("|e|", color=COLORS["muted"], fontsize=7)
        self.load_axis.set_ylabel("A", color=COLORS["muted"], fontsize=7)
        self.load_axis.set_xlabel("Temps écoulé (s)", color=COLORS["muted"], fontsize=8)
        self.error_axis.yaxis.set_major_locator(MaxNLocator(nbins=3, prune="upper"))
        self.load_axis.set_ylim(0.04, 0.26)
        self.load_axis.set_yticks((0.05, 0.15, 0.25))
        self.load_axis.yaxis.set_major_formatter(
            FuncFormatter(lambda value, _position: f"{value:.2f}")
        )
        self.load_axis.xaxis.set_major_formatter(
            FuncFormatter(lambda value, _position: f"{value:.0f}")
        )

        legend = self.temperature_axis.legend(
            loc="upper left",
            frameon=False,
            ncol=2,
            fontsize=8,
        )
        for text in legend.get_texts():
            text.set_color(COLORS["ink"])

        self.error_axis.axhspan(0.0, 0.5, color=COLORS["green"], alpha=0.09)
        self.error_axis.axhspan(0.5, 1.0, color=COLORS["blue"], alpha=0.08)
        self.error_axis.axhspan(1.0, 1.5, color=COLORS["orange"], alpha=0.09)
        self.error_axis.axhspan(1.5, 10.0, color=COLORS["red"], alpha=0.07)
        for limit in (0.5, 1.0, 1.5):
            self.error_axis.axhline(limit, color=COLORS["border"], linewidth=0.8, linestyle="--")
        self.load_axis.axhspan(
            MIN_LOAD_SETPOINT_A,
            MAX_LOAD_SETPOINT_A,
            color=COLORS["load"],
            alpha=0.055,
        )

    def refresh_ports(self) -> None:
        ports = list(list_ports.comports())
        values: list[str] = []
        preferred: list[str] = []
        self.port_display_to_device.clear()

        for port in ports:
            description = str(port.description or "Serial device")
            display = f"{port.device} - {description}"
            values.append(display)
            self.port_display_to_device[display] = port.device

            identity = " ".join(
                [
                    port.device,
                    description,
                    str(getattr(port, "manufacturer", "") or ""),
                    str(getattr(port, "hwid", "") or ""),
                ]
            ).lower()
            is_st_device = getattr(port, "vid", None) == 0x0483
            if is_st_device or any(
                token in identity for token in ("stmicroelectronics", "stlink", "st-link", "stm32")
            ):
                preferred.append(display)

        self.port_combo["values"] = values
        current_device = self.selected_port_device()
        current_display = next(
            (display for display, device in self.port_display_to_device.items() if device == current_device),
            "",
        )
        if current_display:
            self.port_var.set(current_display)
        elif preferred:
            self.port_var.set(preferred[0])
        elif values:
            self.port_var.set(values[0])
        else:
            self.port_var.set("")

    def select_port(self, device: str) -> None:
        for display, candidate in self.port_display_to_device.items():
            if candidate.casefold() == device.casefold():
                self.port_var.set(display)
                return
        self.port_var.set(device)

    def selected_port_device(self) -> str:
        display = self.port_var.get().strip()
        return self.port_display_to_device.get(display, display.split(" - ", 1)[0].strip())

    def selected_profile(self) -> str:
        """Return the currently selected canonical profile label."""
        return normalize_profile(self.profile_var.get())

    def update_profile_controls_state(self) -> None:
        """Keep profile actions consistent with the serial connection state."""
        connected = self.serial_connection is not None and not self.demo_mode
        profile = self.selected_profile()
        can_launch = (
            connected
            and self.pending_control_command is None
            and profile != PROFILE_COLLECT_ONLY
        )
        self.launch_profile_button.configure(
            state=tk.NORMAL if can_launch else tk.DISABLED,
            text=(
                "Commande en cours…"
                if self.pending_control_command is not None
                else "Aucune commande moteur"
                if profile == PROFILE_COLLECT_ONLY
                else "Appliquer ce profil"
                if self.gui_started_motor
                else "Démarrer ce profil"
            ),
        )
        self.stop_motor_button.configure(
            state=tk.NORMAL if connected and self.gui_started_motor else tk.DISABLED
        )

        context = PROFILE_DESCRIPTIONS[profile]
        if profile == PROFILE_COLLECT_ONLY and self.gui_started_motor:
            context += " Le moteur déjà commandé continue : utilisez « Arrêter le moteur » pour l'immobiliser."
        elif self.control_command_error is not None:
            context = f"Erreur firmware : {self.control_command_error}"
        elif self.pending_control_command is not None:
            context = "Commande envoyée. En attente de l'accusé de réception du firmware…"
        elif self.active_profile_token is not None:
            active_profile = PROFILE_LABELS_BY_TOKEN.get(self.active_profile_token)
            if active_profile is not None:
                active_title = PROFILE_PRESENTATION[active_profile]["title"]
                context = f"Profil actif : {active_title}. {context}"
        self.profile_context_var.set(context)
        self.refresh_profile_cards()
        self.update_system_indicators()

    def on_profile_changed(self, *_args: object) -> None:
        profile = self.selected_profile()
        self.profile_description_var.set(PROFILE_DESCRIPTIONS[profile])
        self.update_profile_controls_state()

    def set_state_indicator(self, key: str, text: str, color: str) -> None:
        variables = {
            "serial": self.serial_state_var,
            "data": self.data_state_var,
            "motor": self.motor_state_var,
        }
        variables[key].set(text)
        indicator = self.state_indicators.get(key)
        if indicator is not None:
            canvas, item = indicator
            canvas.itemconfigure(item, fill=color)

    def update_system_indicators(self) -> None:
        if not self.state_indicators:
            return

        if self.demo_mode:
            self.set_state_indicator("serial", "Série · mode démo", COLORS["blue"])
        elif self.serial_connection is not None:
            self.set_state_indicator("serial", "Série · connectée", COLORS["green"])
        else:
            self.set_state_indicator("serial", "Série · hors ligne", COLORS["neutral"])

        if self.demo_mode:
            self.set_state_indicator("data", "Données · simulées", COLORS["blue"])
        elif self.serial_connection is None:
            self.set_state_indicator("data", "Données · inactives", COLORS["neutral"])
        elif self.last_sample_monotonic is None:
            self.set_state_indicator("data", "Données · en attente", COLORS["orange"])
        elif (time.monotonic() - self.last_sample_monotonic) > STALE_DATA_SECONDS:
            self.set_state_indicator("data", "Données · flux en pause", COLORS["orange"])
        else:
            self.set_state_indicator(
                "data",
                f"Données · direct {self.current_rate_hz():.1f} Hz",
                COLORS["green"],
            )

        if self.demo_mode:
            self.set_state_indicator("motor", "Moteur · non piloté", COLORS["neutral"])
        elif self.control_command_error is not None:
            self.set_state_indicator("motor", "Moteur · erreur commande", COLORS["red"])
        elif self.pending_control_command == "STOP":
            self.set_state_indicator("motor", "Moteur · arrêt demandé", COLORS["orange"])
        elif self.pending_control_command is not None:
            self.set_state_indicator("motor", "Moteur · démarrage…", COLORS["orange"])
        elif self.active_profile_token is not None:
            active_profile = PROFILE_LABELS_BY_TOKEN.get(self.active_profile_token)
            active_title = (
                str(PROFILE_PRESENTATION[active_profile]["title"])
                if active_profile is not None
                else self.active_profile_token
            )
            self.set_state_indicator("motor", f"Moteur · {active_title}", COLORS["green"])
        elif self.gui_started_motor:
            self.set_state_indicator("motor", "Moteur · commande envoyée", COLORS["orange"])
        else:
            self.set_state_indicator("motor", "Moteur · non piloté", COLORS["neutral"])

    def send_control_command(self, command: bytes, pending_command: str) -> bool:
        """Send one explicit motor command over the active serial connection."""
        connection = self.serial_connection
        if connection is None:
            self.set_status("Connectez la carte avant d'envoyer une commande", COLORS["orange"])
            return False

        try:
            connection.write(command)
            connection.flush()
        except Exception as exc:
            self.pending_control_command = None
            self.control_command_error = str(exc)
            self.update_profile_controls_state()
            self.set_status("Échec de la commande moteur", COLORS["red"])
            messagebox.showerror("Échec de la commande moteur", str(exc))
            return False

        self.pending_control_command = pending_command
        self.control_command_error = None
        self.update_profile_controls_state()
        self.set_status("Commande envoyée · attente de l'ACK firmware", COLORS["orange"])
        return True

    def launch_selected_profile(self) -> None:
        """Launch the selected firmware-owned profile after an explicit click."""
        profile = self.selected_profile()
        command = build_profile_command(profile)
        if command is None:
            self.set_status("Collecte seule · aucune commande moteur envoyée", COLORS["blue"])
            return
        token = PROFILE_COMMAND_TOKENS[profile]
        if self.send_control_command(command, f"PROFILE,{token}"):
            self.gui_started_motor = True
            self.update_profile_controls_state()

    def stop_motor(self) -> None:
        """Request a safe motor stop without ending temperature acquisition."""
        self.send_control_command(b"STOP\n", "STOP")

    def toggle_connection(self) -> None:
        if self.serial_connection is None:
            self.connect_serial()
        else:
            self.disconnect_serial()

    def connect_serial(self) -> None:
        if self.demo_mode or self.serial_connection is not None:
            return

        device = self.selected_port_device()
        if not device:
            messagebox.showerror("Port série", "Aucun port COM n'est sélectionné.")
            return

        self.set_status("Connexion à la carte…", COLORS["blue"])
        self.update_idletasks()

        connection: serial.Serial | None = None
        try:
            connection = serial.Serial(
                port=device,
                baudrate=BAUD_RATE,
                timeout=SERIAL_TIMEOUT_S,
                write_timeout=1.0,
            )
            time.sleep(0.2)
            connection.reset_input_buffer()
        except Exception as exc:
            if connection is not None:
                connection.close()
            self.set_status("Échec de la connexion", COLORS["red"])
            messagebox.showerror("Échec de la connexion", f"Impossible d'ouvrir {device} :\n{exc}")
            return

        self.reset_session()
        try:
            self.start_automatic_export()
        except OSError as exc:
            connection.close()
            self.set_status("Échec de l'enregistrement automatique", COLORS["red"])
            messagebox.showerror(
                "Échec de l'enregistrement CSV",
                f"Impossible de créer le fichier CSV :\n{exc}",
            )
            return

        self.serial_connection = connection
        self.active_profile_token = None
        self.pending_control_command = None
        self.control_command_error = None
        self.gui_started_motor = False
        self.reader_stop_event = threading.Event()
        self.connection_generation += 1
        generation = self.connection_generation
        self.reader_thread = threading.Thread(
            target=self.serial_reader_loop,
            args=(connection, self.reader_stop_event, generation),
            daemon=True,
        )
        self.reader_thread.start()
        self.connect_button.configure(text="Déconnecter")
        self.port_combo.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.update_profile_controls_state()
        self.set_status("Carte connectée · attente des données", COLORS["orange"])

    def disconnect_serial(self, *, stop_owned_motor: bool = True) -> None:
        connection = self.serial_connection
        stop_failed = False
        if connection is not None and stop_owned_motor and self.gui_started_motor:
            try:
                connection.write(b"STOP\n")
                connection.flush()
            except Exception:
                stop_failed = True

        self.serial_connection = None
        self.active_profile_token = None
        self.pending_control_command = None
        self.control_command_error = None
        self.gui_started_motor = False
        self.connection_generation += 1
        self.reader_stop_event.set()
        self.stop_automatic_export()

        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

        self.connect_button.configure(text="Connecter")
        self.port_combo.configure(state="readonly")
        self.refresh_button.configure(state=tk.NORMAL)
        self.update_profile_controls_state()
        if stop_failed:
            self.set_status("Déconnecté · impossible d'envoyer STOP", COLORS["red"])
        else:
            self.set_status("Carte déconnectée", COLORS["neutral"])

    def serial_reader_loop(
        self,
        connection: serial.Serial,
        stop_event: threading.Event,
        generation: int,
    ) -> None:
        buffer = b""
        transient_error_count = 0
        while not stop_event.is_set():
            try:
                chunk = connection.read(512)
            except Exception as exc:
                if (
                    is_transient_serial_error(exc)
                    and transient_error_count < SERIAL_TRANSIENT_RETRY_LIMIT
                ):
                    transient_error_count += 1
                    if transient_error_count == 1:
                        self.events.put(("serial_warning", (generation, str(exc))))
                    if stop_event.wait(SERIAL_RETRY_DELAY_S):
                        break
                    continue
                if not stop_event.is_set():
                    self.events.put(("serial_error", (generation, str(exc))))
                break

            transient_error_count = 0
            if not chunk:
                continue

            buffer += chunk
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                stripped_line = raw_line.strip()
                if stripped_line.startswith((b"ACK,", b"ERR,", b"#")):
                    control_line = stripped_line.decode("ascii", errors="replace")
                    self.events.put(("control", (generation, control_line)))
                    continue
                try:
                    actual_c, predicted_c, load_setpoint_a = parse_validation_line(raw_line)
                except ValueError as exc:
                    self.events.put(("invalid", (generation, str(exc))))
                    continue
                self.events.put(
                    (
                        "sample",
                        (
                            generation,
                            time.monotonic(),
                            actual_c,
                            predicted_c,
                            load_setpoint_a,
                        ),
                    )
                )

    def handle_control_response(self, control_line: str) -> None:
        """Reflect PROFILE/STOP acknowledgements and firmware errors in the UI."""
        response_kind, token = parse_control_response(control_line)
        if response_kind == "error":
            self.pending_control_command = None
            self.control_command_error = control_line
            self.update_profile_controls_state()
            self.set_status(f"Firmware : {control_line}", COLORS["red"])
            return

        if response_kind == "profile_ack" and token is not None:
            self.pending_control_command = None
            self.control_command_error = None
            self.active_profile_token = token
            self.gui_started_motor = True
            self.update_profile_controls_state()
            profile_label = PROFILE_LABELS_BY_TOKEN[token]
            profile_title = PROFILE_PRESENTATION[profile_label]["title"]
            self.set_status(f"Profil actif · {profile_title}", COLORS["green"])
            return

        if response_kind == "stop_ack":
            self.pending_control_command = None
            self.control_command_error = None
            self.active_profile_token = None
            self.gui_started_motor = False
            self.update_profile_controls_state()
            self.set_status("Moteur arrêté · acquisition toujours active", COLORS["blue"])
            return

        if response_kind == "ack":
            self.pending_control_command = None
            self.control_command_error = None
            self.update_profile_controls_state()
            self.set_status(f"Firmware : {control_line}", COLORS["blue"])

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "sample":
                    (
                        generation,
                        timestamp_s,
                        actual_c,
                        predicted_c,
                        load_setpoint_a,
                    ) = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        if load_setpoint_a is None:
                            load_setpoint_a = math.nan
                        self.add_sample(
                            float(timestamp_s),
                            float(actual_c),
                            float(predicted_c),
                            float(load_setpoint_a),
                        )
                elif event == "control":
                    generation, control_line = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.handle_control_response(str(control_line))
                elif event == "invalid":
                    generation, _message = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.invalid_frame_count += 1
                        self.invalid_var.set(str(self.invalid_frame_count))
                elif event == "serial_warning":
                    generation, _message = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.set_status("Liaison série · nouvelle tentative", COLORS["orange"])
                elif event == "serial_error":
                    generation, message = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.disconnect_serial(stop_owned_motor=False)
                        self.set_status("Erreur de liaison série", COLORS["red"])
                        messagebox.showerror("Erreur de liaison série", str(message))
        except queue.Empty:
            pass
        self.after(QUEUE_REFRESH_MS, self.process_events)

    def add_sample(
        self,
        timestamp_s: float,
        actual_c: float,
        predicted_c: float,
        load_setpoint_a: float = math.nan,
    ) -> None:
        stream_was_stale = (
            self.last_sample_monotonic is None
            or (timestamp_s - self.last_sample_monotonic) > STALE_DATA_SECONDS
        )
        sample = self.accumulator.add(
            timestamp_s,
            actual_c,
            predicted_c,
            load_setpoint_a,
        )
        self.session_samples.append(sample)
        self.plot_samples.append(sample)
        self.recent_timestamps.append(timestamp_s)
        self.last_sample_monotonic = timestamp_s
        self.plot_dirty = True

        self.actual_value_label.configure(text=format_one_decimal(actual_c))
        self.predicted_value_label.configure(text=format_one_decimal(predicted_c))
        self.instant_error_detail_var.set(
            f"IA {sample.signed_error_c:+.1f} °C par rapport au D6T"
        )
        self.update_error_panel(
            self.instant_error_frame,
            self.instant_error_labels,
            self.instant_error_value_label,
            sample.absolute_error_c,
        )
        self.update_error_panel(
            self.cumulative_error_frame,
            self.cumulative_error_labels,
            self.cumulative_error_value_label,
            sample.cumulative_mae_c,
        )

        if math.isfinite(sample.load_setpoint_a):
            self.load_value_var.set(f"{sample.load_setpoint_a:.3f} A")
        else:
            self.load_value_var.set("—")
        self.update_load_gauge(sample.load_setpoint_a)

        self.sample_count_var.set(str(self.accumulator.count))
        self.rate_var.set(f"{self.current_rate_hz():.1f} Hz")
        self.export_button.configure(state=tk.NORMAL)
        if (
            stream_was_stale
            and self.control_command_error is None
            and self.pending_control_command is None
        ):
            if self.active_profile_token is None:
                self.set_status("Flux de télémétrie actif", COLORS["green"])
            else:
                profile_label = PROFILE_LABELS_BY_TOKEN[self.active_profile_token]
                profile_title = PROFILE_PRESENTATION[profile_label]["title"]
                self.set_status(f"Profil actif · {profile_title}", COLORS["green"])
        self.update_system_indicators()
        self.record_automatic_sample(sample)

    def start_automatic_export(self) -> None:
        self.stop_automatic_export()
        filename = f"validation_ia_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
        self.automatic_export = CsvSessionRecorder(VALIDATION_DIRECTORY / filename)

    def stop_automatic_export(self) -> None:
        if self.automatic_export is None:
            return
        self.automatic_export.close()
        self.automatic_export = None

    def record_automatic_sample(self, sample: ValidationSample) -> None:
        if self.automatic_export is None:
            return
        try:
            self.automatic_export.append(sample)
        except OSError as exc:
            failed_path = self.automatic_export.path
            self.stop_automatic_export()
            self.set_status("Erreur d'enregistrement automatique", COLORS["red"])
            messagebox.showerror(
                "Enregistrement CSV interrompu",
                f"Impossible d'écrire dans {failed_path.name} :\n{exc}",
            )

    def update_error_panel(
        self,
        frame: tk.Frame,
        labels: list[tk.Label],
        value_label: tk.Label,
        error_c: float,
    ) -> None:
        band = classify_error(error_c)
        background = ERROR_BAND_BACKGROUNDS.get(band.color, COLORS["surface_soft"])
        frame.configure(bg=background, highlightbackground=COLORS["border"])
        for label in labels:
            label.configure(bg=background)
        accent_bar = getattr(frame, "_accent_bar", None)
        if isinstance(accent_bar, tk.Frame):
            accent_bar.configure(bg=band.color)
        band_label = getattr(frame, "_band_label", None)
        if isinstance(band_label, tk.Label):
            french_band_names = {
                "Excellent": "EXCELLENT",
                "Good": "BON",
                "Needs attention": "ATTENTION",
                "High error": "ÉLEVÉ",
                "Waiting": "EN ATTENTE",
            }
            band_label.configure(
                text=french_band_names.get(band.name, band.name.upper()),
                fg=band.color,
            )
        if labels:
            labels[0].configure(fg=band.color)
        value_label.configure(fg=COLORS["ink"])
        value_label.configure(text=f"{format_one_decimal(error_c)} °C")

    def current_rate_hz(self) -> float:
        if len(self.recent_timestamps) < 2:
            return 0.0
        duration_s = self.recent_timestamps[-1] - self.recent_timestamps[0]
        if duration_s <= 0.0:
            return 0.0
        return (len(self.recent_timestamps) - 1) / duration_s

    def reset_session(self) -> None:
        self.accumulator.reset()
        self.session_samples.clear()
        self.plot_samples.clear()
        self.recent_timestamps.clear()
        self.invalid_frame_count = 0
        self.last_sample_monotonic = None
        self.plot_dirty = True

        if hasattr(self, "actual_value_label"):
            self.actual_value_label.configure(text="--.-")
            self.predicted_value_label.configure(text="--.-")
            self.instant_error_detail_var.set("En attente de données")
            self.load_value_var.set("—")
            self.update_load_gauge(math.nan)
            self.update_error_panel(
                self.instant_error_frame,
                self.instant_error_labels,
                self.instant_error_value_label,
                math.nan,
            )
            self.update_error_panel(
                self.cumulative_error_frame,
                self.cumulative_error_labels,
                self.cumulative_error_value_label,
                math.nan,
            )
            self.sample_count_var.set("0")
            self.rate_var.set("0.0 Hz")
            self.invalid_var.set("0")
            self.export_button.configure(state=tk.DISABLED)
            self.update_system_indicators()

    def redraw_plot_periodic(self) -> None:
        if self.plot_dirty:
            self.redraw_plot()
        self.after(PLOT_REFRESH_MS, self.redraw_plot_periodic)

    def redraw_plot(self) -> None:
        samples = list(self.plot_samples)
        xs = [sample.elapsed_s for sample in samples]
        actual = [sample.actual_c for sample in samples]
        predicted = [sample.predicted_c for sample in samples]
        errors = [sample.absolute_error_c for sample in samples]
        loads = [sample.load_setpoint_a for sample in samples]

        self.actual_line.set_data(xs, actual)
        self.predicted_line.set_data(xs, predicted)
        self.error_line.set_data(xs, errors)
        self.load_line.set_data(xs, loads)
        self.waiting_text.set_visible(not bool(xs))
        has_load_data = any(math.isfinite(load) for load in loads)
        self.load_empty_text.set_visible(not has_load_data)

        if self.error_fill is not None:
            self.error_fill.remove()
            self.error_fill = None
        if xs:
            self.error_fill = self.temperature_axis.fill_between(
                xs,
                actual,
                predicted,
                color=COLORS["predicted"],
                alpha=0.07,
                linewidth=0,
            )

        if self.error_scatter is not None:
            self.error_scatter.remove()
            self.error_scatter = None
        if xs:
            point_colors = [classify_error(error).color for error in errors]
            self.error_scatter = self.error_axis.scatter(
                xs,
                errors,
                c=point_colors,
                s=14,
                edgecolors="none",
                zorder=3,
            )

        if self.load_fill is not None:
            self.load_fill.remove()
            self.load_fill = None
        if xs and has_load_data:
            self.load_fill = self.load_axis.fill_between(
                xs,
                loads,
                MIN_LOAD_SETPOINT_A,
                step="post",
                where=[math.isfinite(load) for load in loads],
                color=COLORS["load"],
                alpha=0.12,
                linewidth=0,
            )

        if xs:
            x_max = xs[-1]
            x_min = max(0.0, x_max - PLOT_WINDOW_SECONDS)
            self.temperature_axis.set_xlim(x_min, max(PLOT_WINDOW_SECONDS * 0.08, x_max + 0.5))

            visible_temperatures = [
                value
                for x_value, pair in zip(xs, zip(actual, predicted))
                if x_value >= x_min
                for value in pair
            ]
            if visible_temperatures:
                y_min = min(visible_temperatures)
                y_max = max(visible_temperatures)
                margin = max(0.8, (y_max - y_min) * 0.16)
                self.temperature_axis.set_ylim(y_min - margin, y_max + margin)

            visible_errors = [error for x_value, error in zip(xs, errors) if x_value >= x_min]
            self.error_axis.set_ylim(0.0, max(2.0, max(visible_errors, default=0.0) * 1.18))
        else:
            self.temperature_axis.set_xlim(0.0, 10.0)
            self.temperature_axis.set_ylim(20.0, 40.0)
            self.error_axis.set_ylim(0.0, 2.0)
        self.load_axis.set_ylim(0.04, 0.26)

        self.canvas.draw_idle()
        self.plot_dirty = False

    def refresh_connection_status(self) -> None:
        if self.demo_mode:
            self.set_status("Mode démonstration", COLORS["blue"])
        elif self.serial_connection is not None:
            if self.control_command_error is not None:
                self.set_status(f"Firmware : {self.control_command_error}", COLORS["red"])
            elif self.pending_control_command is not None:
                self.set_status("Commande envoyée · attente de l'ACK firmware", COLORS["orange"])
            elif self.last_sample_monotonic is None:
                self.set_status("Carte connectée · attente des données", COLORS["orange"])
            elif (time.monotonic() - self.last_sample_monotonic) > STALE_DATA_SECONDS:
                self.set_status("Flux de données interrompu", COLORS["orange"])
        self.update_system_indicators()
        self.after(STATUS_REFRESH_MS, self.refresh_connection_status)

    def set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_dot.itemconfigure(self.status_dot_id, fill=color)

    def export_csv(self) -> None:
        if not self.session_samples:
            return

        default_name = f"validation_ia_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(
            title="Exporter la session",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("Fichiers CSV", "*.csv")],
        )
        if not path:
            return

        output_path = Path(path)
        try:
            with output_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(CSV_HEADER)
                for sample in self.session_samples:
                    writer.writerow(csv_sample_row(sample))
        except OSError as exc:
            messagebox.showerror("Échec de l'export", str(exc))
            return

        self.set_status("Export CSV terminé", COLORS["blue"])

    def start_demo_mode(self) -> None:
        self.port_combo.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.connect_button.configure(state=tk.DISABLED, text="Démo")
        self.reset_session()
        self.demo_started_at = time.monotonic()
        self.set_status("Mode démonstration", COLORS["blue"])
        self.after(100, self.demo_tick)

    def demo_tick(self) -> None:
        if not self.demo_mode:
            return
        now = time.monotonic()
        elapsed = now - self.demo_started_at
        actual_c = 34.0 + (3.8 * math.sin(elapsed / 16.0)) + (0.25 * math.sin(elapsed * 1.3))
        error_magnitude = 0.18 + (1.65 * (0.5 + 0.5 * math.sin(elapsed * 0.22)))
        error_sign = 1.0 if math.sin(elapsed * 0.11) >= 0.0 else -1.0
        predicted_c = actual_c + (error_sign * error_magnitude)
        demo_loads = (0.05, 0.10, 0.15, 0.20, 0.25, 0.10)
        load_setpoint_a = demo_loads[int(elapsed // 4.0) % len(demo_loads)]
        self.add_sample(now, actual_c, predicted_c, load_setpoint_a)
        self.after(100, self.demo_tick)

    def on_close(self) -> None:
        self.demo_mode = False
        self.disconnect_serial()
        self.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NanoEdge AI temperature validation dashboard.")
    parser.add_argument("--port", help="Serial port to open automatically, for example COM5.")
    parser.add_argument("--demo", action="store_true", help="Show a simulated stream without a board.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = TemperatureValidationApp(demo=args.demo, initial_port=args.port)
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
