"""Dashboard temps reel pour comparer la D6T et la prediction NanoEdge AI."""

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
from matplotlib.ticker import FuncFormatter
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
VALIDATION_DIRECTORY = Path(__file__).resolve().parents[1]
CSV_HEADER = (
    "elapsed_s",
    "d6t_temp_c",
    "predicted_temp_c",
    "signed_error_c",
    "absolute_error_c",
    "cumulative_mae_c",
)

COLORS = {
    "background": "#E9EDF1",
    "surface": "#FFFFFF",
    "surface_soft": "#F6F8FA",
    "ink": "#17212B",
    "muted": "#66717D",
    "border": "#D3DAE1",
    "header": "#17212B",
    "header_muted": "#AAB5C0",
    "actual": "#087F73",
    "predicted": "#2457D6",
    "green": "#168554",
    "blue": "#2868C7",
    "orange": "#D96716",
    "red": "#C83B3B",
    "neutral": "#66717D",
    "grid": "#DEE4E9",
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


ERROR_BANDS = (
    (0.5, ErrorBand("Excellent", COLORS["green"])),
    (1.0, ErrorBand("Bon", COLORS["blue"])),
    (1.5, ErrorBand("A surveiller", COLORS["orange"])),
)
RED_ERROR_BAND = ErrorBand("Ecart eleve", COLORS["red"])
NEUTRAL_ERROR_BAND = ErrorBand("En attente", COLORS["neutral"])


def parse_validation_line(raw_line: bytes | str) -> tuple[float, float]:
    """Parse une ligne firmware ``temperature_reelle;temperature_predite``."""
    if isinstance(raw_line, bytes):
        try:
            line = raw_line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("La trame contient des octets non ASCII.") from exc
    else:
        line = str(raw_line)

    fields = [field.strip() for field in line.strip().split(";")]
    if len(fields) != 2:
        raise ValueError(f"Deux valeurs attendues, {len(fields)} recues.")

    try:
        actual_c, predicted_c = (float(field) for field in fields)
    except ValueError as exc:
        raise ValueError("La trame contient une valeur non numerique.") from exc

    if not math.isfinite(actual_c) or not math.isfinite(predicted_c):
        raise ValueError("La trame contient NaN ou une valeur infinie.")

    return actual_c, predicted_c


def classify_error(absolute_error_c: float) -> ErrorBand:
    """Retourne la classe couleur correspondant a un ecart absolu en degres."""
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
    """Formate un nombre avec exactement une decimale et une virgule francaise."""
    if not math.isfinite(value):
        return "--,-"
    rounded = round(value, 1)
    if rounded == 0.0:
        rounded = 0.0
    return f"{rounded:.1f}".replace(".", ",")


def is_current_connection_event(event_generation: int, current_generation: int) -> bool:
    """Indique si un evenement appartient encore a la connexion serie active."""
    return event_generation == current_generation


def is_transient_serial_error(error: Exception) -> bool:
    """Identifie l'erreur Windows ClearCommError qui peut etre temporaire."""
    message = str(error).casefold()
    return "clearcommerror" in message or "does not recognize the command" in message


def csv_sample_row(sample: ValidationSample) -> tuple[str, ...]:
    return (
        f"{sample.elapsed_s:.3f}",
        f"{sample.actual_c:.6f}",
        f"{sample.predicted_c:.6f}",
        f"{sample.signed_error_c:.6f}",
        f"{sample.absolute_error_c:.6f}",
        f"{sample.cumulative_mae_c:.6f}",
    )


class CsvSessionRecorder:
    """Ecrit une session CSV progressivement pour limiter les pertes de donnees."""

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
    """Calcule les erreurs instantanee et moyenne absolue cumulee (MAE)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.count = 0
        self.total_absolute_error_c = 0.0
        self.started_at_s: float | None = None

    def add(self, timestamp_s: float, actual_c: float, predicted_c: float) -> ValidationSample:
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
        )


class TemperatureValidationApp(tk.Tk):
    """Interface principale de validation thermique temps reel."""

    def __init__(self, *, demo: bool = False, initial_port: str | None = None) -> None:
        super().__init__()

        self.title("Validation thermique NanoEdge AI")
        self.geometry("1320x860")
        self.minsize(1040, 780)
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
        self.status_var = tk.StringVar(value="Deconnecte")
        self.sample_count_var = tk.StringVar(value="0")
        self.rate_var = tk.StringVar(value="0,0 Hz")
        self.invalid_var = tk.StringVar(value="0")

        self.actual_value_label: tk.Label
        self.predicted_value_label: tk.Label
        self.instant_error_frame: tk.Frame
        self.instant_error_labels: list[tk.Label]
        self.instant_error_value_label: tk.Label
        self.cumulative_error_frame: tk.Frame
        self.cumulative_error_labels: list[tk.Label]
        self.cumulative_error_value_label: tk.Label

        self.setup_style()
        self.build_ui()
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
            padding=8,
            font=("Aptos", 10),
        )
        style.map(
            "Validation.TCombobox",
            fieldbackground=[("readonly", COLORS["surface"])],
            foreground=[("readonly", COLORS["ink"])],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["header"],
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(16, 10),
            font=("Aptos Display", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#263542"), ("disabled", "#9AA4AE")],
            foreground=[("disabled", "#E5E7EB")],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            padding=(13, 9),
            font=("Aptos", 10, "bold"),
        )
        style.map("Secondary.TButton", background=[("active", COLORS["surface_soft"])])

    def build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.build_header()
        self.build_toolbar()

        content = tk.Frame(self, bg=COLORS["background"])
        content.grid(row=2, column=0, sticky="nsew", padx=22, pady=(18, 20))
        content.grid_columnconfigure(0, weight=1, uniform="temperature")
        content.grid_columnconfigure(1, weight=1, uniform="temperature")
        content.grid_rowconfigure(2, weight=1)

        actual_panel, self.actual_value_label = self.build_temperature_panel(
            content,
            title="TEMPERATURE REELLE",
            subtitle="Capteur infrarouge D6T",
            accent=COLORS["actual"],
        )
        actual_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 9))

        predicted_panel, self.predicted_value_label = self.build_temperature_panel(
            content,
            title="TEMPERATURE ESTIMEE",
            subtitle="Regression NanoEdge AI embarquee",
            accent=COLORS["predicted"],
        )
        predicted_panel.grid(row=0, column=1, sticky="nsew", padx=(9, 0))

        error_row = tk.Frame(content, bg=COLORS["background"])
        error_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 16))
        error_row.grid_columnconfigure(0, weight=1, uniform="errors")
        error_row.grid_columnconfigure(1, weight=1, uniform="errors")

        (
            self.instant_error_frame,
            self.instant_error_value_label,
            self.instant_error_labels,
        ) = self.build_error_panel(
            error_row,
            title="ERREUR INSTANTANEE",
            subtitle="Ecart absolu sur la derniere mesure",
        )
        self.instant_error_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 9))

        (
            self.cumulative_error_frame,
            self.cumulative_error_value_label,
            self.cumulative_error_labels,
        ) = self.build_error_panel(
            error_row,
            title="ERREUR CUMULEE (MAE)",
            subtitle="Moyenne absolue depuis le debut",
        )
        self.cumulative_error_frame.grid(row=0, column=1, sticky="nsew", padx=(9, 0))

        self.build_threshold_legend(error_row).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 0),
        )

        self.build_chart(content).grid(row=2, column=0, columnspan=2, sticky="nsew")

    def build_header(self) -> None:
        header = tk.Frame(self, bg=COLORS["header"], height=86)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        title_block = tk.Frame(header, bg=COLORS["header"])
        title_block.grid(row=0, column=0, sticky="w", padx=24, pady=14)
        tk.Label(
            title_block,
            text="Validation thermique",
            bg=COLORS["header"],
            fg="#FFFFFF",
            font=("Georgia", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="Mesure D6T face a l'estimation NanoEdge AI",
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=("Aptos", 10),
        ).pack(anchor="w", pady=(2, 0))

        status = tk.Frame(header, bg=COLORS["header"])
        status.grid(row=0, column=1, sticky="e", padx=24)
        self.status_dot = tk.Canvas(
            status,
            width=14,
            height=14,
            bg=COLORS["header"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 8))
        self.status_dot_id = self.status_dot.create_oval(2, 2, 12, 12, fill=COLORS["neutral"], outline="")
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=COLORS["header"],
            fg="#FFFFFF",
            font=("Aptos Display", 11, "bold"),
        ).pack(side="left")

    def build_toolbar(self) -> None:
        toolbar = tk.Frame(self, bg=COLORS["surface"], height=72, highlightthickness=1, highlightbackground=COLORS["border"])
        toolbar.grid(row=1, column=0, sticky="ew")
        toolbar.grid_propagate(False)
        toolbar.grid_columnconfigure(1, weight=1)

        tk.Label(
            toolbar,
            text="PORT SERIE",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Aptos", 9, "bold"),
        ).grid(row=0, column=0, padx=(22, 10), pady=20)

        self.port_combo = ttk.Combobox(
            toolbar,
            textvariable=self.port_var,
            state="readonly",
            style="Validation.TCombobox",
            width=44,
        )
        self.port_combo.grid(row=0, column=1, sticky="w", pady=14)

        self.refresh_button = ttk.Button(
            toolbar,
            text="Actualiser",
            command=self.refresh_ports,
            style="Secondary.TButton",
        )
        self.refresh_button.grid(row=0, column=2, padx=8)

        self.connect_button = ttk.Button(
            toolbar,
            text="Connecter",
            command=self.toggle_connection,
            style="Primary.TButton",
        )
        self.connect_button.grid(row=0, column=3, padx=8)

        self.reset_button = ttk.Button(
            toolbar,
            text="Reinitialiser",
            command=self.reset_session,
            style="Secondary.TButton",
        )
        self.reset_button.grid(row=0, column=4, padx=8)

        self.export_button = ttk.Button(
            toolbar,
            text="Exporter CSV",
            command=self.export_csv,
            style="Secondary.TButton",
            state=tk.DISABLED,
        )
        self.export_button.grid(row=0, column=5, padx=(8, 22))

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
            height=185,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        panel.grid_propagate(False)
        panel.grid_columnconfigure(1, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        tk.Frame(panel, bg=accent, width=7).grid(row=0, column=0, rowspan=3, sticky="ns")
        tk.Label(
            panel,
            text=title,
            bg=COLORS["surface"],
            fg=accent,
            font=("Aptos Display", 11, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=24, pady=(18, 0))

        value_row = tk.Frame(panel, bg=COLORS["surface"])
        value_row.grid(row=1, column=1, sticky="w", padx=22)
        value_label = tk.Label(
            value_row,
            text="--,-",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Georgia", 48, "bold"),
        )
        value_label.pack(side="left")
        tk.Label(
            value_row,
            text=" °C",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Georgia", 19),
        ).pack(side="left", anchor="s", pady=(0, 10))

        tk.Label(
            panel,
            text=subtitle,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Aptos", 10),
        ).grid(row=2, column=1, sticky="w", padx=24, pady=(0, 16))
        return panel, value_label

    def build_error_panel(
        self,
        parent: tk.Widget,
        *,
        title: str,
        subtitle: str,
    ) -> tuple[tk.Frame, tk.Label, list[tk.Label]]:
        background = COLORS["neutral"]
        panel = tk.Frame(parent, bg=background, height=104)
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        title_label = tk.Label(
            panel,
            text=title,
            bg=background,
            fg="#FFFFFF",
            font=("Aptos Display", 10, "bold"),
        )
        title_label.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 0))

        value_label = tk.Label(
            panel,
            text="--,- °C",
            bg=background,
            fg="#FFFFFF",
            font=("Georgia", 23, "bold"),
        )
        value_label.grid(row=1, column=0, sticky="w", padx=18)

        subtitle_label = tk.Label(
            panel,
            text=subtitle,
            bg=background,
            fg="#EAF0F4",
            font=("Aptos", 9),
        )
        subtitle_label.grid(row=0, column=1, rowspan=2, sticky="e", padx=18)
        return panel, value_label, [title_label, value_label, subtitle_label]

    def build_threshold_legend(self, parent: tk.Widget) -> tk.Frame:
        legend = tk.Frame(parent, bg=COLORS["background"])
        entries = (
            (COLORS["green"], "< 0,5 °C"),
            (COLORS["blue"], "0,5 à < 1,0 °C"),
            (COLORS["orange"], "1,0 à 1,5 °C"),
            (COLORS["red"], "> 1,5 °C"),
        )
        for index, (color, text) in enumerate(entries):
            item = tk.Frame(legend, bg=COLORS["background"])
            item.pack(side="left", padx=(0 if index == 0 else 18, 0))
            tk.Frame(item, bg=color, width=12, height=12).pack(side="left", padx=(0, 6))
            tk.Label(
                item,
                text=text,
                bg=COLORS["background"],
                fg=COLORS["muted"],
                font=("Aptos", 9),
            ).pack(side="left")
        return legend

    def build_chart(self, parent: tk.Widget) -> tk.Frame:
        panel = tk.Frame(parent, bg=COLORS["surface"], highlightthickness=1, highlightbackground=COLORS["border"])
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        chart_header = tk.Frame(panel, bg=COLORS["surface"])
        chart_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 0))
        chart_header.grid_columnconfigure(0, weight=1)
        tk.Label(
            chart_header,
            text="Historique temps reel",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Georgia", 14, "bold"),
        ).grid(row=0, column=0, sticky="w")

        stats = tk.Frame(chart_header, bg=COLORS["surface"])
        stats.grid(row=0, column=1, sticky="e")
        self.build_stat(stats, "Echantillons", self.sample_count_var).pack(side="left", padx=10)
        self.build_stat(stats, "Cadence", self.rate_var).pack(side="left", padx=10)
        self.build_stat(stats, "Trames ignorees", self.invalid_var).pack(side="left", padx=(10, 0))

        self.figure = Figure(figsize=(10, 5), dpi=100, facecolor=COLORS["surface"])
        grid = self.figure.add_gridspec(4, 1, hspace=0.10)
        self.temperature_axis = self.figure.add_subplot(grid[:3, 0])
        self.error_axis = self.figure.add_subplot(grid[3, 0], sharex=self.temperature_axis)
        self.figure.subplots_adjust(left=0.070, right=0.985, top=0.965, bottom=0.125)

        self.actual_line, = self.temperature_axis.plot([], [], color=COLORS["actual"], linewidth=2.5, label="D6T reelle")
        self.predicted_line, = self.temperature_axis.plot([], [], color=COLORS["predicted"], linewidth=2.3, label="IA estimee")
        self.error_line, = self.error_axis.plot([], [], color=COLORS["ink"], linewidth=1.2, alpha=0.55)
        self.error_scatter = None
        self.error_fill = None

        self.configure_axes()

        self.canvas = FigureCanvasTkAgg(self.figure, master=panel)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))
        return panel

    def build_stat(self, parent: tk.Widget, title: str, variable: tk.StringVar) -> tk.Frame:
        frame = tk.Frame(parent, bg=COLORS["surface"])
        tk.Label(frame, text=title, bg=COLORS["surface"], fg=COLORS["muted"], font=("Aptos", 8)).pack(anchor="e")
        tk.Label(frame, textvariable=variable, bg=COLORS["surface"], fg=COLORS["ink"], font=("Aptos Display", 10, "bold")).pack(anchor="e")
        return frame

    def configure_axes(self) -> None:
        comma_formatter = FuncFormatter(lambda value, _position: f"{value:.1f}".replace(".", ","))

        for axis in (self.temperature_axis, self.error_axis):
            axis.set_facecolor(COLORS["surface"])
            axis.grid(True, color=COLORS["grid"], linewidth=0.8, alpha=0.8)
            axis.tick_params(colors=COLORS["muted"], labelsize=8)
            axis.yaxis.set_major_formatter(comma_formatter)
            for spine in axis.spines.values():
                spine.set_color(COLORS["border"])

        self.temperature_axis.tick_params(labelbottom=False)
        self.temperature_axis.set_ylabel("Temperature (°C)", color=COLORS["muted"], fontsize=9)
        self.error_axis.set_ylabel("|e| °C", color=COLORS["muted"], fontsize=8)
        self.error_axis.set_xlabel("Temps ecoule (s)", color=COLORS["muted"], fontsize=9)
        self.error_axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _position: f"{value:.0f}"))

        legend = self.temperature_axis.legend(loc="upper left", frameon=False, ncol=2, fontsize=9)
        for text in legend.get_texts():
            text.set_color(COLORS["ink"])

        self.error_axis.axhspan(0.0, 0.5, color=COLORS["green"], alpha=0.09)
        self.error_axis.axhspan(0.5, 1.0, color=COLORS["blue"], alpha=0.08)
        self.error_axis.axhspan(1.0, 1.5, color=COLORS["orange"], alpha=0.09)
        self.error_axis.axhspan(1.5, 10.0, color=COLORS["red"], alpha=0.07)
        for limit in (0.5, 1.0, 1.5):
            self.error_axis.axhline(limit, color=COLORS["border"], linewidth=0.8, linestyle="--")

    def refresh_ports(self) -> None:
        ports = list(list_ports.comports())
        values: list[str] = []
        preferred: list[str] = []
        self.port_display_to_device.clear()

        for port in ports:
            description = str(port.description or "Peripherique serie")
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
            messagebox.showerror("Port serie", "Aucun port COM n'est selectionne.")
            return

        self.set_status("Connexion...", COLORS["blue"])
        self.update_idletasks()

        try:
            connection = serial.Serial(
                port=device,
                baudrate=BAUD_RATE,
                timeout=SERIAL_TIMEOUT_S,
                write_timeout=1.0,
            )
            connection.reset_input_buffer()
        except Exception as exc:
            self.set_status("Echec connexion", COLORS["red"])
            messagebox.showerror("Connexion impossible", f"Impossible d'ouvrir {device} :\n{exc}")
            return

        self.reset_session()
        try:
            self.start_automatic_export()
        except OSError as exc:
            connection.close()
            self.set_status("Echec export automatique", COLORS["red"])
            messagebox.showerror(
                "Export automatique impossible",
                f"Impossible de creer le fichier CSV :\n{exc}",
            )
            return

        self.serial_connection = connection
        self.reader_stop_event = threading.Event()
        self.connection_generation += 1
        generation = self.connection_generation
        self.reader_thread = threading.Thread(
            target=self.serial_reader_loop,
            args=(connection, self.reader_stop_event, generation),
            daemon=True,
        )
        self.reader_thread.start()
        self.connect_button.configure(text="Deconnecter")
        self.port_combo.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.set_status("Connecte - attente du flux", COLORS["orange"])

    def disconnect_serial(self) -> None:
        connection = self.serial_connection
        self.serial_connection = None
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
        self.set_status("Deconnecte", COLORS["neutral"])

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
                try:
                    actual_c, predicted_c = parse_validation_line(raw_line)
                except ValueError as exc:
                    self.events.put(("invalid", (generation, str(exc))))
                    continue
                self.events.put(
                    ("sample", (generation, time.monotonic(), actual_c, predicted_c))
                )

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "sample":
                    generation, timestamp_s, actual_c, predicted_c = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.add_sample(float(timestamp_s), float(actual_c), float(predicted_c))
                elif event == "invalid":
                    generation, _message = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.invalid_frame_count += 1
                        self.invalid_var.set(str(self.invalid_frame_count))
                elif event == "serial_warning":
                    generation, _message = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.set_status("Perturbation serie - nouvelle tentative", COLORS["orange"])
                elif event == "serial_error":
                    generation, message = payload  # type: ignore[misc]
                    if is_current_connection_event(generation, self.connection_generation):
                        self.disconnect_serial()
                        self.set_status("Erreur serie", COLORS["red"])
                        messagebox.showerror("Erreur serie", str(message))
        except queue.Empty:
            pass
        self.after(QUEUE_REFRESH_MS, self.process_events)

    def add_sample(self, timestamp_s: float, actual_c: float, predicted_c: float) -> None:
        sample = self.accumulator.add(timestamp_s, actual_c, predicted_c)
        self.session_samples.append(sample)
        self.plot_samples.append(sample)
        self.recent_timestamps.append(timestamp_s)
        self.last_sample_monotonic = timestamp_s
        self.plot_dirty = True

        self.actual_value_label.configure(text=format_one_decimal(actual_c))
        self.predicted_value_label.configure(text=format_one_decimal(predicted_c))
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

        self.sample_count_var.set(str(self.accumulator.count))
        self.rate_var.set(f"{self.current_rate_hz():.1f} Hz".replace(".", ","))
        self.export_button.configure(state=tk.NORMAL)
        self.set_status("Acquisition active", COLORS["green"])
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
            self.set_status("Erreur export automatique", COLORS["red"])
            messagebox.showerror(
                "Export automatique interrompu",
                f"Impossible d'ecrire dans {failed_path.name} :\n{exc}",
            )

    def update_error_panel(
        self,
        frame: tk.Frame,
        labels: list[tk.Label],
        value_label: tk.Label,
        error_c: float,
    ) -> None:
        band = classify_error(error_c)
        frame.configure(bg=band.color)
        for label in labels:
            label.configure(bg=band.color)
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
            self.actual_value_label.configure(text="--,-")
            self.predicted_value_label.configure(text="--,-")
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
            self.rate_var.set("0,0 Hz")
            self.invalid_var.set("0")
            self.export_button.configure(state=tk.DISABLED)

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

        self.actual_line.set_data(xs, actual)
        self.predicted_line.set_data(xs, predicted)
        self.error_line.set_data(xs, errors)

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

        self.canvas.draw_idle()
        self.plot_dirty = False

    def refresh_connection_status(self) -> None:
        if self.demo_mode:
            self.set_status("Mode demonstration", COLORS["blue"])
        elif self.serial_connection is not None:
            if self.last_sample_monotonic is None:
                self.set_status("Connecte - attente du flux", COLORS["orange"])
            elif (time.monotonic() - self.last_sample_monotonic) > STALE_DATA_SECONDS:
                self.set_status("Flux interrompu", COLORS["orange"])
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
            filetypes=[("Fichier CSV", "*.csv")],
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
            messagebox.showerror("Export impossible", str(exc))
            return

        self.set_status(f"Export termine - {output_path.name}", COLORS["blue"])

    def start_demo_mode(self) -> None:
        self.port_combo.configure(state=tk.DISABLED)
        self.refresh_button.configure(state=tk.DISABLED)
        self.connect_button.configure(state=tk.DISABLED, text="Demonstration")
        self.reset_session()
        self.demo_started_at = time.monotonic()
        self.set_status("Mode demonstration", COLORS["blue"])
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
        self.add_sample(now, actual_c, predicted_c)
        self.after(100, self.demo_tick)

    def on_close(self) -> None:
        self.demo_mode = False
        self.disconnect_serial()
        self.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dashboard de validation thermique NanoEdge AI.")
    parser.add_argument("--port", help="Port serie a ouvrir automatiquement, par exemple COM5.")
    parser.add_argument("--demo", action="store_true", help="Affiche un flux simule sans carte.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = TemperatureValidationApp(demo=args.demo, initial_port=args.port)
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()