"""PMSM data logging GUI for STM32 thermal prediction.

This module provides a Tkinter application for controlling an STM32
B-G473E-ZEST1S with an STDES-LVHP01 power board, running a simple motor
sequence, receiving UART measurements, recording CSV, and displaying live
values. The final CSV is written to ``datalogging/logs`` and keeps only the STM32
columns needed for NanoEdge AI preprocessing, without a PC timestamp. Default
paths can be overridden by command-line options, YAML, or environment
variables through ConfigArgParse.

The expected firmware serial protocol is line-oriented text::

    SYNC
    CFG,<rpm>,<iq_limit>,<hard_limit>,<accel>,<datalog_ms>,<ds18b20_ms>
    START
    ACQ_START,<datalog_ms>,<ds18b20_ms>
    STOP
    ACK,<command>
    ERR,<reason>
    #CSV_HEADER,<column_1>,<column_2>,...
    DATA,<value_1>,<value_2>,...
"""

import csv
import json
import math
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import configargparse
import serial
import serial.tools.list_ports

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False


# Paramètres moteur et communication par défaut.
POLE_PAIRS_DEFAULT = 2
BAUD_DEFAULT = 115200
DS18B20_MIN_MS = 750
PLOT_MAX_POINTS = 1500
PLOT_REFRESH_MS = 250
LOG_MAX_LINES = 3000
LOG_TRIM_LINES = 300
CSV_FLUSH_EVERY_ROWS = 10
CSV_FLUSH_INTERVAL_S = 1.0
IQ_WARNING_RATIO = 0.90
MIN_TARGET_SPEED_RPM = 100.0
MAX_TARGET_SPEED_RPM = 4500.0
MAX_IQ_LIMIT_A = 30.0
MAX_HARD_LIMIT_A = 30.0
MAX_ACCEL_ELEC_HZ_S = 50.0
MAX_DATALOG_MS = 10000
MAX_DS18B20_MS = 10000

ACQUISITION_MODE_MOTOR = "Moteur + collecte"
ACQUISITION_MODE_IDLE = "Collecte seule (moteur arrêté)"
ACQUISITION_MODES = (ACQUISITION_MODE_MOTOR, ACQUISITION_MODE_IDLE)

# Colonnes effectivement écrites dans le CSV final.
# La carte peut recevoir davantage de colonnes pour l'affichage live, mais seules
# celles-ci sont conservées dans le fichier de datalogging.
D6T_TEMPERATURE_COLUMN = "d6t_temp_c"
D6T_TEMPERATURE_COLUMNS = {D6T_TEMPERATURE_COLUMN}

CSV_OUTPUT_COLUMNS = [
    "stm32_time_ms",
    D6T_TEMPERATURE_COLUMN,
    "ds18b20_temp_c",
    "motor_ud_v",
    "motor_uq_v",
    "motor_speed_mech_rpm",
    "motor_id_a",
    "motor_iq_a",
]

NON_PLOT_FIELDS = {"stm32_time_ms", "motor_speed_elec_hz", "motor_vbus_v"}


# Palette centrale utilisée par toute l'interface graphique.
COLORS = {
    "bg": "#070A0F",
    "panel": "#10151E",
    "panel_2": "#151D29",
    "panel_3": "#1D2B3D",
    "border": "#2B384B",
    "text": "#F2F6FF",
    "muted": "#9AA8BA",
    "accent": "#2DD4BF",
    "accent_2": "#A3E635",
    "danger": "#FB7185",
    "warning": "#FBBF24",
    "input": "#0A1019",
    "line_grid": "#273447",
    "panel_soft": "#0C111A",
    "panel_lift": "#182232",
    "cyan": "#38BDF8",
    "lime": "#A3E635",
    "rose": "#FB7185",
    "amber": "#FBBF24",
    "violet": "#C084FC",
}


@dataclass
class MotorProfile:
    """Motor configuration profile that can be saved.

    Attributes:
        name: Readable name displayed in the profile list.
        speed_value: Speed setpoint expressed in ``speed_unit``.
        speed_unit: Unit of ``speed_value``; supported values are ``"rpm"`` and
            ``"elec_hz"``.
        iq_limit_a: ``Iq`` current limit used as a setpoint or software guard.
        hard_limit_a: Maximum current threshold above which firmware may
            trigger a safety shutdown.
        accel_elec_hz_s: Acceleration ramp in electrical hertz per second.
        datalog_ms: Firmware ``DATA`` transmission period.
        ds18b20_ms: DS18B20 sensor refresh period.
    """
    name: str
    speed_value: float
    speed_unit: str  # "rpm" ou "elec_hz"
    iq_limit_a: float
    hard_limit_a: float
    accel_elec_hz_s: float
    datalog_ms: int
    ds18b20_ms: int


# Profil minimal embarqué ; les profils utilisateur sont chargés depuis motor_profiles.json.
PROFILES = {
    "Personnalisé": MotorProfile(
        name="Personnalisé",
        speed_value=600.0,
        speed_unit="rpm",
        iq_limit_a=2.0,
        hard_limit_a=6.0,
        accel_elec_hz_s=5.0,
        datalog_ms=100,
        ds18b20_ms=1000,
    ),
}


BUILTIN_PROFILE_NAMES = set(PROFILES.keys())
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent
DEFAULT_LOG_DIR = DASHBOARD_DIR / "logs"
DEFAULT_PROFILE_STORE_PATH = DASHBOARD_DIR / "motor_profiles.json"

# Fichiers YAML optionnels lus automatiquement par ConfigArgParse s'ils existent.
DEFAULT_CONFIG_FILES = [
    PROJECT_ROOT / "dashboard_config.yaml",
    DASHBOARD_DIR / "dashboard_config.yaml",
]


@dataclass
class DashboardPaths:
    """Configurable paths used by the Tkinter dashboard."""

    log_dir: Path
    profile_store_path: Path
    csv_path: Optional[Path]


def path_from_arg(value):
    """Normalize a path supplied by CLI, configuration, or environment variable."""
    path = Path(os.path.expandvars(str(value))).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def parse_dashboard_args(argv=None):
    """Read configurable dashboard paths with ConfigArgParse."""
    parser = configargparse.ArgParser(
        description="STM32 PMSM dashboard with configurable paths.",
        default_config_files=[str(path) for path in DEFAULT_CONFIG_FILES],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add_argument(
        "-c",
        "--config",
        is_config_file=True,
        help="Optional YAML configuration file.",
    )
    parser.add_argument(
        "--log-dir",
        type=path_from_arg,
        default=DEFAULT_LOG_DIR,
        env_var="PMSM_DATALOG_LOG_DIR",
        help="Directory suggested for new data logging CSV files.",
    )
    parser.add_argument(
        "--profile-store",
        type=path_from_arg,
        default=DEFAULT_PROFILE_STORE_PATH,
        env_var="PMSM_DATALOG_PROFILE_STORE",
        help="JSON file containing custom motor profiles.",
    )
    parser.add_argument(
        "--csv-path",
        type=path_from_arg,
        default=None,
        env_var="PMSM_DATALOG_CSV_PATH",
        help="Initial CSV path suggested in the output field.",
    )

    args, _unknown_args = parser.parse_known_args(argv)
    paths = DashboardPaths(
        log_dir=path_from_arg(args.log_dir),
        profile_store_path=path_from_arg(args.profile_store),
        csv_path=path_from_arg(args.csv_path) if args.csv_path else None,
    )

    if paths.log_dir.exists() and not paths.log_dir.is_dir():
        raise NotADirectoryError(f"Le chemin des logs n'est pas un dossier : {paths.log_dir}")
    if paths.profile_store_path.exists() and not paths.profile_store_path.is_file():
        raise IsADirectoryError(f"Le chemin des profils n'est pas un fichier : {paths.profile_store_path}")
    if paths.csv_path is not None and paths.csv_path.exists() and paths.csv_path.is_dir():
        raise IsADirectoryError(f"Le chemin CSV initial pointe vers un dossier : {paths.csv_path}")

    return paths


# Métadonnées des colonnes CSV reconnues par les cartes live et le graphe.
KNOWN_FIELDS = {
    "stm32_time_ms": ("STM32", "ms"),
    "ds18b20_temp_c": ("Temp ext", "°C"),
    "d6t_temp_c": ("Temp int", "°C"),
    "motor_ud_v": ("Ud", "V"),
    "motor_uq_v": ("Uq", "V"),
    "motor_speed_elec_hz": ("Vitesse elec", "Hz"),
    "motor_speed_mech_rpm": ("Vitesse mech", "rpm"),
    "motor_id_a": ("Id", "A"),
    "motor_iq_a": ("Iq", "A"),
}

DEFAULT_LIVE_FIELDS = [
    "stm32_time_ms",
    "ds18b20_temp_c",
    "d6t_temp_c",
    "motor_ud_v",
    "motor_uq_v",
    "motor_speed_mech_rpm",
    "motor_id_a",
    "motor_iq_a",
]

DEFAULT_PLOT_FIELDS = {
    "ds18b20_temp_c": "Temp ext (°C)",
    "d6t_temp_c": "Temp int IR (°C)",
    "motor_ud_v": "Ud (V)",
    "motor_uq_v": "Uq (V)",
    "motor_speed_mech_rpm": "Vitesse mech (rpm)",
    "motor_id_a": "Id (A)",
    "motor_iq_a": "Iq (A)",
}

DEFAULT_PLOT_SELECTION = {"motor_speed_mech_rpm", "motor_iq_a", "ds18b20_temp_c", "d6t_temp_c"}

PLOT_COLORS = {
    "ds18b20_temp_c": "#FBBF24",
    "d6t_temp_c": "#FB923C",
    "motor_ud_v": "#38BDF8",
    "motor_uq_v": "#C084FC",
    "motor_speed_mech_rpm": "#A3E635",
    "motor_id_a": "#2DD4BF",
    "motor_iq_a": "#F472B6",
}

FIELD_ACCENTS = {
    "stm32_time_ms": "#94A3B8",
    "ds18b20_temp_c": "#FBBF24",
    "d6t_temp_c": "#FB923C",
    "motor_ud_v": "#38BDF8",
    "motor_uq_v": "#C084FC",
    "motor_speed_elec_hz": "#2DD4BF",
    "motor_speed_mech_rpm": "#A3E635",
    "motor_id_a": "#22D3EE",
    "motor_iq_a": "#F472B6",
}


class MotorDatalogGui(tk.Tk):
    """Tkinter application for motor control and data logging.

    This class manages the interface, serial connection, communication threads,
    CSV creation, and live chart. Direct Tkinter access stays on the main thread;
    serial threads communicate with the interface through ``queue.Queue`` objects.

    Attributes:
        serial_obj: Open PySerial object, or ``None``.
        gui_queue: Queue used by threads to send events to the Tkinter thread.
        ack_queue: Queue dedicated to firmware ``ACK`` and ``ERR`` responses.
        profiles: Dictionary of available motor profiles.
        plot_fields: Variables currently available for plotting.
        live_vars: Tkinter variables bound to live-value cards.
    """
    def __init__(self, paths=None):
        """Initialize the application, internal state, and graphical interface.

        Prepare Tkinter variables, load profiles, build interface panels, discover
        available COM ports, and start periodic queue-processing and chart-refresh
        loops. Application paths are read through ConfigArgParse before display.
        """
        super().__init__()

        self.title("STM32 PMSM Dark Bench Dashboard")
        self.geometry("1540x960")
        self.minsize(1120, 720)
        self.configure(bg=COLORS["bg"])
        self.paths = paths or parse_dashboard_args()

        self.serial_obj = None
        self.serial_lock = threading.Lock()
        self.reader_thread = None
        self.launch_thread = None
        self.stop_thread = None

        self.stop_event = threading.Event()
        self.gui_queue = queue.Queue()
        self.ack_queue = queue.Queue()

        self.csv_file = None
        self.csv_writer = None
        self.csv_columns = []
        self.csv_output_columns = []
        self.csv_path = None
        self.csv_pending_rows = 0
        self.csv_last_flush_s = time.monotonic()

        self.is_running = False
        self.is_launching = False
        self.is_stopping = False
        self.data_before_header_count = 0
        self.log_line_count = 0
        self.close_requested = False

        self._applying_profile = False
        self._updating_speed_link = False
        self._user_edit_ready = False
        self.profile_load_errors = []

        self.profiles = self.load_profiles()
        initial_profile = self.get_initial_profile_name()

        self.port_var = tk.StringVar()
        self.port_display_to_device = {}
        self.baud_var = tk.StringVar(value=str(BAUD_DEFAULT))
        self.profile_var = tk.StringVar(value=initial_profile)
        self.acquisition_mode_var = tk.StringVar(value=ACQUISITION_MODE_MOTOR)
        self.speed_rpm_var = tk.StringVar()
        self.speed_hz_var = tk.StringVar()
        self.pole_pairs_var = tk.StringVar(value=str(POLE_PAIRS_DEFAULT))
        self.iq_limit_var = tk.StringVar()
        self.hard_limit_var = tk.StringVar()
        self.accel_var = tk.StringVar()
        self.datalog_ms_var = tk.StringVar()
        self.ds18b20_ms_var = tk.StringVar()
        self.csv_path_var = tk.StringVar(value=str(self.paths.csv_path or self.default_csv_path()))
        self.status_var = tk.StringVar(value="Prêt")
        self.status_detail_var = tk.StringVar(value="Sélectionne un port COM et une configuration.")
        self.warning_var = tk.StringVar(value="")
        self.active_acquisition_mode = ACQUISITION_MODE_MOTOR

        self.live_fields = list(DEFAULT_LIVE_FIELDS)
        self.live_vars = {key: tk.StringVar(value=self.default_live_value(key)) for key in self.live_fields}
        self.live_card_frames = {}
        self.live_value_labels = {}
        self.live_frame = None

        self.left_canvas = None
        self.left_window_id = None
        self.left_scroll_enabled = False

        self.plot_fields = dict(DEFAULT_PLOT_FIELDS)
        self.plot_enabled_vars = {
            key: tk.BooleanVar(value=(key in DEFAULT_PLOT_SELECTION))
            for key in self.plot_fields
        }
        self.plot_checkbuttons = {}

        self.plot_x = deque(maxlen=PLOT_MAX_POINTS)
        self.plot_data = {key: deque(maxlen=PLOT_MAX_POINTS) for key in self.plot_fields}
        self.plot_t0_s = None
        self.plot_dirty = False

        self.figure = None
        self.ax = None
        self.canvas = None

        self.entry_widgets = {}
        self.combo_widgets = {}
        self.motor_profile_widgets = []

        self.setup_style()
        self.build_ui()
        for error in self.profile_load_errors:
            self.log(error)
        self.refresh_ports()
        self.apply_profile(self.profiles[initial_profile])
        self._user_edit_ready = True
        self.update_acquisition_mode_ui()
        self.validate_form()
        self.after(50, self.process_gui_queue)
        self.after(PLOT_REFRESH_MS, self.redraw_plot_periodic)

    def default_csv_path(self):
        """Build the default CSV path for a new acquisition.

        Returns:
            str: Path to a ``daq_log_YYYYMMDD_HHMMSS.csv`` file in the configured
            dashboard log directory.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(self.paths.log_dir / f"daq_log_{timestamp}.csv")

    @staticmethod
    def csv_path_from_text(value):
        """Convert and validate a CSV path entered in the interface."""
        raw = str(value).strip()
        if not raw:
            raise ValueError("Aucun fichier CSV sélectionné.")
        path = path_from_arg(raw)
        if path.exists() and path.is_dir():
            raise ValueError(f"Le chemin CSV pointe vers un dossier : {path}")
        return path

    def setup_style(self):
        """Configure a compact, readable dark theme for the motor test bench."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        font_ui = "Segoe UI"
        font_title = "Segoe UI Semibold"
        font_value = "Bahnschrift SemiBold"

        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["panel"], relief="flat")
        style.configure("SubCard.TFrame", background=COLORS["panel_2"], relief="flat")
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=(font_ui, 10))
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=(font_ui, 9))
        style.configure("Card.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=(font_ui, 10))
        style.configure("CardMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=(font_ui, 9))
        style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=(font_title, 12))
        style.configure("Section.TLabel", background=COLORS["panel"], foreground=COLORS["accent"], font=(font_title, 9))
        style.configure("Hero.TLabel", background=COLORS["panel_soft"], foreground=COLORS["text"], font=(font_title, 24))
        style.configure("HeroSub.TLabel", background=COLORS["panel_soft"], foreground=COLORS["muted"], font=(font_ui, 10))
        style.configure("Value.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=(font_value, 21))
        style.configure("Unit.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=(font_ui, 9))
        style.configure("Warn.TLabel", background=COLORS["panel"], foreground=COLORS["warning"], font=(font_title, 9))
        style.configure("Status.TLabel", background=COLORS["panel_lift"], foreground=COLORS["text"], font=(font_title, 10))

        style.configure("TEntry", fieldbackground=COLORS["input"], background=COLORS["input"], foreground=COLORS["text"], insertcolor=COLORS["text"], bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], padding=8)
        style.configure("Invalid.TEntry", fieldbackground="#3A1018", background="#3A1018", foreground=COLORS["text"], insertcolor=COLORS["text"], bordercolor=COLORS["danger"], lightcolor=COLORS["danger"], darkcolor=COLORS["danger"], padding=8)
        style.configure("TCombobox", fieldbackground=COLORS["input"], background=COLORS["input"], foreground=COLORS["text"], arrowcolor=COLORS["accent"], bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], padding=7)
        style.configure("Invalid.TCombobox", fieldbackground="#3A1018", background="#3A1018", foreground=COLORS["text"], arrowcolor=COLORS["danger"], bordercolor=COLORS["danger"], lightcolor=COLORS["danger"], darkcolor=COLORS["danger"], padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["input"])], foreground=[("readonly", COLORS["text"])])
        style.map("Invalid.TCombobox", fieldbackground=[("readonly", "#3A1018")], foreground=[("readonly", COLORS["text"])])
        style.configure("TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"], font=(font_ui, 9))
        style.map("TCheckbutton", background=[("active", COLORS["panel"])], foreground=[("active", COLORS["accent"])])

    def build_ui(self):
        """Build the main structure of the dark graphical interface."""
        root = tk.Frame(self, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)

        self.build_header(root)

        body = tk.Frame(root, bg=COLORS["bg"])
        body.pack(fill=tk.BOTH, expand=True, pady=(16, 0))
        body.grid_columnconfigure(0, minsize=430, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left_shell = tk.Frame(body, bg=COLORS["bg"], width=430)
        left_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left_shell.grid_propagate(False)
        left_shell.grid_rowconfigure(0, weight=1)
        left_shell.grid_columnconfigure(0, weight=1)

        left = self.build_left_scroll_area(left_shell)

        right = tk.Frame(body, bg=COLORS["bg"])
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(2, weight=0)

        self.build_left_panel(left)
        self.build_live_cards(right)
        self.build_plot_panel(right)
        self.build_log_panel(right)

        self.bind_traces()

    def build_left_scroll_area(self, parent):
        """Create an adaptive left column with discreet scrolling.

        Cards keep their natural size to avoid clipped widgets. When available height
        is insufficient, the mouse wheel scrolls only this column without a visible
        scrollbar.

        Args:
            parent: Tkinter container in which to create the left canvas.

        Returns:
            tk.Frame: Inner frame holding the configuration cards.
        """
        self.left_canvas = tk.Canvas(
            parent,
            bg=COLORS["bg"],
            bd=0,
            highlightthickness=0,
            relief="flat",
        )
        self.left_canvas.grid(row=0, column=0, sticky="nsew")

        left = tk.Frame(self.left_canvas, bg=COLORS["bg"])
        left.grid_columnconfigure(0, weight=1)
        self.left_window_id = self.left_canvas.create_window((0, 0), window=left, anchor="nw")

        left.bind("<Configure>", self.on_left_content_configure)
        self.left_canvas.bind("<Configure>", self.on_left_canvas_configure)
        self.bind_all("<MouseWheel>", self.on_left_mousewheel, add="+")
        self.bind_all("<Button-4>", self.on_left_mousewheel, add="+")
        self.bind_all("<Button-5>", self.on_left_mousewheel, add="+")
        return left

    def on_left_content_configure(self, _event=None):
        """Update the scrollable region when left-column content changes.

        Args:
            _event: Unused Tkinter ``<Configure>`` event.
        """
        if self.left_canvas is None:
            return
        bbox = self.left_canvas.bbox("all")
        if bbox is not None:
            self.left_canvas.configure(scrollregion=bbox)
        self.update_left_scroll_state()

    def on_left_canvas_configure(self, event):
        """Match left-content width to the canvas.

        Args:
            event: Tkinter event containing the new available width.
        """
        if self.left_canvas is None or self.left_window_id is None:
            return
        self.left_canvas.itemconfigure(self.left_window_id, width=event.width)
        self.update_left_scroll_state()

    def update_left_scroll_state(self):
        """Enable or disable scrolling in the left column.

        Scrolling is enabled only when content height exceeds visible canvas height.
        """
        if self.left_canvas is None:
            return
        bbox = self.left_canvas.bbox("all")
        if bbox is None:
            self.left_scroll_enabled = False
            return
        content_height = bbox[3] - bbox[1]
        canvas_height = max(1, self.left_canvas.winfo_height())
        self.left_scroll_enabled = content_height > canvas_height + 2
        if not self.left_scroll_enabled:
            self.left_canvas.yview_moveto(0)

    def pointer_is_over_left_panel(self):
        """Report whether the pointer is over the left panel.

        Returns:
            bool: ``True`` over the left region, otherwise ``False``.
        """
        if self.left_canvas is None:
            return False
        pointer_x = self.winfo_pointerx()
        pointer_y = self.winfo_pointery()
        left_x = self.left_canvas.winfo_rootx()
        left_y = self.left_canvas.winfo_rooty()
        left_w = self.left_canvas.winfo_width()
        left_h = self.left_canvas.winfo_height()
        return left_x <= pointer_x <= left_x + left_w and left_y <= pointer_y <= left_y + left_h

    def on_left_mousewheel(self, event):
        """Handle mouse-wheel or touchpad scrolling for the left panel.

        Args:
            event: Windows/macOS or Linux wheel event.

        Returns:
            str | None: ``"break"`` if consumed by the left column, otherwise ``None``.
        """
        if self.left_canvas is None or not self.left_scroll_enabled:
            return None
        if not self.pointer_is_over_left_panel():
            return None

        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        else:
            direction = -1 if event.delta > 0 else 1

        self.left_canvas.yview_scroll(direction * 3, "units")
        return "break"

    def build_header(self, parent):
        """Build the motor test bench command header."""
        header = tk.Frame(parent, bg=COLORS["panel_soft"], highlightbackground=COLORS["border"], highlightthickness=1)
        header.pack(fill=tk.X)
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)
        header.grid_columnconfigure(2, weight=0)

        accent = tk.Frame(header, bg=COLORS["accent"], width=5)
        accent.grid(row=0, column=0, sticky="nsw")

        title_block = tk.Frame(header, bg=COLORS["panel_soft"])
        title_block.grid(row=0, column=0, sticky="w", padx=(24, 16), pady=18)
        ttk.Label(title_block, text="STM32 PMSM Bench", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(title_block, text="Pilotage moteur, acquisition CSV et supervision temps reel", style="HeroSub.TLabel").pack(anchor="w", pady=(3, 0))

        status_card = tk.Frame(header, bg=COLORS["panel_lift"], highlightbackground=COLORS["border"], highlightthickness=1)
        status_card.grid(row=0, column=1, sticky="e", padx=(8, 14), pady=14)
        status_card.grid_columnconfigure(1, weight=1)

        self.status_dot = tk.Canvas(status_card, width=20, height=20, bg=COLORS["panel_lift"], highlightthickness=0)
        self.status_dot.grid(row=0, column=0, rowspan=2, padx=(14, 9), pady=12)
        self.status_dot_id = self.status_dot.create_oval(4, 4, 16, 16, fill=COLORS["warning"], outline="")
        ttk.Label(status_card, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 18), pady=(10, 0))
        ttk.Label(status_card, textvariable=self.status_detail_var, style="CardMuted.TLabel").grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(0, 10))

        control_card = tk.Frame(header, bg=COLORS["panel_soft"])
        control_card.grid(row=0, column=2, sticky="e", padx=(0, 16), pady=14)
        control_card.grid_columnconfigure(0, weight=1)
        control_card.grid_columnconfigure(1, weight=1)

        self.start_button = self.action_button(control_card, "LANCER", self.start_run, COLORS["lime"], "#062712")
        self.start_button.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.stop_button = self.action_button(control_card, "ARRETER", self.stop_run, COLORS["danger"], "#3A1018")
        self.stop_button.configure(state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, sticky="nsew")

    def action_button(self, parent, text, command, accent, base_bg):
        """Create a large motor-control button."""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=base_bg,
            fg=COLORS["text"],
            activebackground=accent,
            activeforeground="#061016",
            relief="flat",
            padx=24,
            pady=16,
            cursor="hand2",
            font=("Segoe UI Semibold", 11),
            bd=0,
            highlightthickness=1,
            highlightbackground=accent,
        )

    def small_button(self, parent, text, command, accent=None):
        """Create a compact button for configuration cards."""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["panel_3"],
            fg=COLORS["text"],
            activebackground=accent or COLORS["accent"],
            activeforeground="#061016",
            relief="flat",
            padx=12,
            pady=9,
            cursor="hand2",
            font=("Segoe UI Semibold", 9),
            bd=0,
        )

    def card(self, parent, title=None, padx=16, pady=14, accent=None):
        """Create a dark card without a colored top bar."""
        outer = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        if title:
            title_row = tk.Frame(outer, bg=COLORS["panel"])
            title_row.pack(fill=tk.X, padx=padx, pady=(pady, 6))
            if accent:
                tk.Frame(title_row, bg=accent, width=8, height=8).pack(side=tk.LEFT, padx=(0, 8), pady=(4, 0))
            ttk.Label(title_row, text=title.upper(), style="CardTitle.TLabel").pack(side=tk.LEFT, anchor="w")
        content = tk.Frame(outer, bg=COLORS["panel"])
        content.pack(fill=tk.BOTH, expand=True, padx=padx, pady=(0 if title else pady, pady))
        return outer, content

    def form_label(self, parent, text, row, col, padx=(0, 8)):
        """Place a compact form label."""
        ttk.Label(parent, text=text, style="CardMuted.TLabel").grid(row=row, column=col, sticky="w", padx=padx, pady=(0, 4))

    def form_row(self, parent, label, widget, row, col=0):
        """Add a label-and-widget form row to a grid.

        Args:
            parent: Target container.
            label: Label text.
            widget: Input widget placed below the label.
            row: Starting grid row.
            col: Starting grid column.
        """
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", pady=(0, 2))
        widget.grid(row=row + 1, column=col, sticky="ew", pady=(0, 6), padx=(0, 8))

    def compact_form_row(self, parent, left_label, left_widget, right_label, right_widget, row):
        """Add two compact form fields on one row.

        Args:
            parent: Target container.
            left_label: Label for the left field.
            left_widget: Widget for the left field.
            right_label: Label for the right field.
            right_widget: Widget for the right field.
            row: Grid row.
        """
        ttk.Label(parent, text=left_label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 6), pady=(0, 6))
        left_widget.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=(0, 6))
        ttk.Label(parent, text=right_label, style="Card.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 6), pady=(0, 6))
        right_widget.grid(row=row, column=3, sticky="ew", pady=(0, 6))

    def build_left_panel(self, parent):
        """Build the left configuration rail."""
        parent.grid_columnconfigure(0, weight=1)
        for row in range(4):
            parent.grid_rowconfigure(row, weight=0)

        conn_card, conn = self.card(parent, "Connexion carte", padx=14, pady=12, accent=COLORS["cyan"])
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        conn.grid_columnconfigure(0, weight=1)
        conn.grid_columnconfigure(1, weight=0)

        self.form_label(conn, "Port COM", 0, 0)
        self.port_combo = ttk.Combobox(conn, textvariable=self.port_var, state="readonly", width=28)
        self.combo_widgets["port"] = self.port_combo
        self.port_combo.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))
        self.port_combo.bind("<<ComboboxSelected>>", self.on_port_selected)

        self.refresh_button = self.small_button(conn, "Scanner", self.refresh_ports, COLORS["cyan"])
        self.refresh_button.grid(row=1, column=1, sticky="ew", pady=(0, 10))

        self.form_label(conn, "Baudrate", 2, 0)
        self.baud_entry = ttk.Entry(conn, textvariable=self.baud_var)
        self.entry_widgets["baud"] = self.baud_entry
        self.baud_entry.grid(row=3, column=0, columnspan=2, sticky="ew")

        profile_card, profile = self.card(parent, "Profil moteur", padx=14, pady=12, accent=COLORS["lime"])
        profile_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        profile.grid_columnconfigure(0, weight=0)
        profile.grid_columnconfigure(1, weight=1, minsize=76)
        profile.grid_columnconfigure(2, weight=0)
        profile.grid_columnconfigure(3, weight=1, minsize=76)

        self.profile_combo = ttk.Combobox(profile, textvariable=self.profile_var, values=list(self.profiles.keys()), state="readonly")
        self.combo_widgets["profile"] = self.profile_combo
        self.profile_combo.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_changed)

        self.speed_rpm_entry = ttk.Entry(profile, textvariable=self.speed_rpm_var, width=8)
        self.entry_widgets["speed_rpm"] = self.speed_rpm_entry
        self.speed_hz_entry = ttk.Entry(profile, textvariable=self.speed_hz_var, width=8)
        self.entry_widgets["speed_hz"] = self.speed_hz_entry
        self.compact_form_row(profile, "rpm", self.speed_rpm_entry, "Hz elec", self.speed_hz_entry, 1)

        self.pole_entry = ttk.Entry(profile, textvariable=self.pole_pairs_var, width=8)
        self.entry_widgets["pole_pairs"] = self.pole_entry
        self.accel_entry = ttk.Entry(profile, textvariable=self.accel_var, width=8)
        self.entry_widgets["accel"] = self.accel_entry
        self.compact_form_row(profile, "Paires", self.pole_entry, "Accel", self.accel_entry, 2)

        self.iq_entry = ttk.Entry(profile, textvariable=self.iq_limit_var, width=8)
        self.entry_widgets["iq_limit"] = self.iq_entry
        self.hard_entry = ttk.Entry(profile, textvariable=self.hard_limit_var, width=8)
        self.entry_widgets["hard_limit"] = self.hard_entry
        self.compact_form_row(profile, "Iq lim.", self.iq_entry, "Hard", self.hard_entry, 3)

        self.save_profile_button = self.small_button(profile, "Enregistrer le profil", self.save_current_profile, COLORS["lime"])
        self.save_profile_button.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.save_profile_button.grid_remove()

        self.motor_profile_widgets = [
            self.profile_combo,
            self.speed_rpm_entry,
            self.speed_hz_entry,
            self.pole_entry,
            self.accel_entry,
            self.iq_entry,
            self.hard_entry,
        ]

        csv_card, csv_box = self.card(parent, "Sortie CSV", padx=14, pady=12, accent=COLORS["amber"])
        csv_card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        csv_box.grid_columnconfigure(0, weight=1)
        csv_box.grid_columnconfigure(1, weight=0)

        self.form_label(csv_box, "Fichier de sortie", 0, 0)
        self.csv_entry = ttk.Entry(csv_box, textvariable=self.csv_path_var)
        self.entry_widgets["csv_path"] = self.csv_entry
        self.csv_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.small_button(csv_box, "Choisir", self.choose_csv_file, COLORS["amber"]).grid(row=1, column=1, sticky="ew")

        acq_card, acq = self.card(parent, "Acquisition", padx=14, pady=12, accent=COLORS["violet"])
        acq_card.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for i in range(2):
            acq.grid_columnconfigure(i, weight=1)

        self.form_label(acq, "Mode de session", 0, 0)
        self.acquisition_mode_combo = ttk.Combobox(
            acq,
            textvariable=self.acquisition_mode_var,
            values=ACQUISITION_MODES,
            state="readonly",
        )
        self.combo_widgets["acquisition_mode"] = self.acquisition_mode_combo
        self.acquisition_mode_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.datalog_entry = ttk.Entry(acq, textvariable=self.datalog_ms_var)
        self.entry_widgets["datalog_ms"] = self.datalog_entry
        self.form_row(acq, "Periode DATA (ms)", self.datalog_entry, 2, 0)

        self.ds18b20_entry = ttk.Entry(acq, textvariable=self.ds18b20_ms_var)
        self.entry_widgets["ds18b20_ms"] = self.ds18b20_entry
        self.form_row(acq, "Periode DS18B20 (ms)", self.ds18b20_entry, 2, 1)

        self.warning_label = ttk.Label(acq, textvariable=self.warning_var, style="Warn.TLabel", wraplength=340)
        self.warning_label.configure(wraplength=380)
        self.warning_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))


    def build_live_cards(self, parent):
        """Build the live-value cards."""
        self.live_frame = tk.Frame(parent, bg=COLORS["bg"])
        self.live_frame.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        for i in range(4):
            self.live_frame.grid_columnconfigure(i, weight=1, uniform="live")

        self.live_card_frames = {}
        for key in self.live_fields:
            self.add_live_card(key)

    def add_live_card(self, key):
        """Dynamically add a live-value card."""
        if self.live_frame is None or key in self.live_card_frames:
            return

        if key not in self.live_vars:
            self.live_vars[key] = tk.StringVar(value=self.default_live_value(key))
            self.live_fields.append(key)

        idx = len(self.live_card_frames)
        title, unit = KNOWN_FIELDS.get(key, (key, ""))
        accent = FIELD_ACCENTS.get(key, COLORS["accent"])

        frame = tk.Frame(self.live_frame, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        frame.grid(row=idx // 4, column=idx % 4, sticky="ew", padx=6, pady=6)
        frame.grid_columnconfigure(0, weight=1)
        self.live_card_frames[key] = frame

        top = tk.Frame(frame, bg=COLORS["panel"])
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 0))
        top.grid_columnconfigure(0, weight=1)
        ttk.Label(top, text=title.upper(), style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        tk.Frame(top, bg=accent, width=10, height=10).grid(row=0, column=1, sticky="e", padx=(8, 0))

        value_line = tk.Frame(frame, bg=COLORS["panel"])
        value_line.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 12))
        value_label = ttk.Label(value_line, textvariable=self.live_vars[key], style="Value.TLabel")
        value_label.pack(side=tk.LEFT)
        self.live_value_labels[key] = value_label
        ttk.Label(value_line, text=f" {unit}", style="Unit.TLabel").pack(side=tk.LEFT, pady=(9, 0))

    def build_plot_panel(self, parent):
        """Build the live-chart panel."""
        plot_card, plot_content = self.card(parent, "Graphique dynamique", padx=14, pady=12, accent=COLORS["accent"])
        plot_card.grid(row=1, column=0, sticky="nsew", pady=(0, 14))
        plot_card.grid_rowconfigure(0, weight=1)
        plot_card.grid_columnconfigure(0, weight=1)
        plot_content.grid_columnconfigure(0, weight=0)
        plot_content.grid_columnconfigure(1, weight=1)
        plot_content.grid_rowconfigure(0, weight=1)

        selector = tk.Frame(plot_content, bg=COLORS["panel"])
        selector.grid(row=0, column=0, sticky="nsw", padx=(0, 16))

        ttk.Label(selector, text="VARIABLES", style="Section.TLabel").pack(anchor="w", pady=(0, 9))
        self.plot_checks_frame = tk.Frame(selector, bg=COLORS["panel"])
        self.plot_checks_frame.pack(anchor="w", fill=tk.X)
        for key in self.plot_fields:
            self.add_plot_checkbox(key)

        self.small_button(selector, "Effacer", self.clear_plot, COLORS["accent"]).pack(fill=tk.X, pady=(16, 0))
        ttk.Label(
            selector,
            text="Plusieurs unites : affichage relatif 0-100, min/max dans la legende.",
            style="CardMuted.TLabel",
            wraplength=220,
        ).pack(anchor="w", pady=(14, 0))

        chart_area = tk.Frame(plot_content, bg=COLORS["panel"])
        chart_area.grid(row=0, column=1, sticky="nsew")
        chart_area.grid_rowconfigure(0, weight=1)
        chart_area.grid_columnconfigure(0, weight=1)

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(8.5, 5.1), dpi=100, facecolor=COLORS["panel"])
            self.ax = self.figure.add_subplot(111)
            self.style_axis()
            self.canvas = FigureCanvasTkAgg(self.figure, master=chart_area)
            self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
            self.canvas.draw_idle()
        else:
            tk.Label(
                chart_area,
                text="Matplotlib n'est pas installe.\nInstalle-le avec : pip install matplotlib",
                bg=COLORS["panel"],
                fg=COLORS["warning"],
                font=("Segoe UI Semibold", 12),
                justify="center",
            ).grid(row=0, column=0, sticky="nsew")

    def register_csv_fields_for_live(self, columns):
        """Add known CSV columns discovered at runtime to the live cards.

        Args:
            columns: Columns received from the ``#CSV_HEADER`` line.
        """
        for key in columns:
            if key in KNOWN_FIELDS and key not in self.live_card_frames:
                self.add_live_card(key)

    def add_plot_checkbox(self, key):
        """Add a checkbox to show a variable in the chart."""
        if self.plot_checks_frame is None or key in self.plot_checkbuttons:
            return
        label = self.plot_fields.get(key, key)
        cb = ttk.Checkbutton(
            self.plot_checks_frame,
            text=label,
            variable=self.plot_enabled_vars[key],
            command=self.mark_plot_dirty,
        )
        cb.pack(anchor="w", pady=4)
        self.plot_checkbuttons[key] = cb

    def register_csv_fields_for_plot(self, columns):
        """Register new CSV columns as plottable variables.

        Args:
            columns: Columns received from the ``#CSV_HEADER`` line.
        """
        added = []
        for key in columns:
            if key in NON_PLOT_FIELDS or key in self.plot_fields:
                continue
            label, unit = KNOWN_FIELDS.get(key, (key, ""))
            self.plot_fields[key] = f"{label} ({unit})" if unit else label
            self.plot_enabled_vars[key] = tk.BooleanVar(value=False)
            self.plot_data[key] = deque(maxlen=PLOT_MAX_POINTS)
            self.add_plot_checkbox(key)
            added.append(key)
        if added:
            self.log("Variables CSV ajoutées au graphe : " + ", ".join(added))

    def build_log_panel(self, parent):
        """Build the TX/RX text log."""
        log_card, log_content = self.card(parent, "Journal UART", padx=14, pady=12, accent=COLORS["rose"])
        log_card.grid(row=2, column=0, sticky="nsew")
        log_card.grid_rowconfigure(0, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        log_content.grid_rowconfigure(0, weight=1)
        log_content.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_content,
            height=7,
            wrap="none",
            bg="#05080D",
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            padx=12,
            pady=10,
            font=("Cascadia Mono", 9),
            bd=0,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_content, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def bind_traces(self):
        """Connect Tkinter variables to validation callbacks.

        Traces keep rpm and electrical Hz consistent and revalidate settings whenever
        a user field changes.
        """
        self.speed_rpm_var.trace_add("write", self.on_speed_rpm_changed)
        self.speed_hz_var.trace_add("write", self.on_speed_hz_changed)
        self.pole_pairs_var.trace_add("write", self.on_pole_pairs_changed)
        self.acquisition_mode_var.trace_add("write", self.on_acquisition_mode_changed)

        for var in [
            self.iq_limit_var,
            self.hard_limit_var,
            self.accel_var,
            self.datalog_ms_var,
            self.ds18b20_ms_var,
        ]:
            var.trace_add("write", self.on_user_config_changed)

        for var in [self.baud_var, self.csv_path_var]:
            var.trace_add("write", lambda *_args: self.validate_form())

    def load_profiles(self):
        """Load built-in and custom motor profiles.

        Read the JSON file configured through ConfigArgParse when present. Ignore
        invalid profiles and save errors in ``profile_load_errors`` for the log.

        Returns:
            dict[str, MotorProfile]: Available profiles.
        """
        profiles = dict(PROFILES)
        profile_store_path = self.paths.profile_store_path

        if profile_store_path.exists():
            try:
                if not profile_store_path.is_file():
                    raise ValueError("Le chemin des profils pointe vers un dossier.")

                raw = json.loads(profile_store_path.read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    raise ValueError("Le fichier doit contenir une liste de profils.")

                for index, item in enumerate(raw, start=1):
                    if not isinstance(item, dict):
                        self.profile_load_errors.append(f"Profil #{index} ignoré : entrée JSON invalide.")
                        continue

                    name = str(item.get("name", "")).strip()
                    if not name or name == "Personnalisé":
                        self.profile_load_errors.append(f"Profil #{index} ignoré : nom vide ou réservé.")
                        continue

                    speed_unit = str(item.get("speed_unit", "rpm")).strip() or "rpm"
                    if speed_unit not in {"rpm", "elec_hz"}:
                        self.profile_load_errors.append(f"Profil '{name}' ignoré : speed_unit invalide.")
                        continue

                    profiles[name] = MotorProfile(
                        name=name,
                        speed_value=float(item.get("speed_value", 600.0)),
                        speed_unit=speed_unit,
                        iq_limit_a=float(item.get("iq_limit_a", 2.0)),
                        hard_limit_a=float(item.get("hard_limit_a", 6.0)),
                        accel_elec_hz_s=float(item.get("accel_elec_hz_s", 5.0)),
                        datalog_ms=int(item.get("datalog_ms", 100)),
                        ds18b20_ms=int(item.get("ds18b20_ms", 1000)),
                    )
            except Exception as exc:
                self.profile_load_errors.append(f"Impossible de charger {profile_store_path.name} : {exc}")

        if "Personnalisé" not in profiles:
            profiles["Personnalisé"] = PROFILES["Personnalisé"]

        return profiles

    def get_initial_profile_name(self):
        """Select the profile shown at startup.

        Returns:
        str: First available custom profile, or ``"Personnalisé"`` ("Custom")
        if none was loaded.
        """
        for name in self.profiles:
            if name != "Personnalisé":
                return name
        return "Personnalisé"

    def save_profiles_to_disk(self):
        """Save custom profiles to the configured JSON file.

        Do not export built-in profiles so the JSON file contains user-created
        profiles only.
        """
        custom_profiles = []
        for name, profile in self.profiles.items():
            if name in BUILTIN_PROFILE_NAMES or name == "Personnalisé":
                continue
            custom_profiles.append(asdict(profile))

        profile_store_path = self.paths.profile_store_path
        profile_store_path.parent.mkdir(parents=True, exist_ok=True)
        profile_store_path.write_text(
            json.dumps(custom_profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def format_float(value):
        """Format a float for an input field.

        Args:
            value: Value convertible to ``float``.

        Returns:
            str: Up to three decimal places without trailing zeroes, or an empty
            string if conversion fails.
        """
        try:
            value = float(value)
        except Exception:
            return ""
        text = f"{value:.3f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def default_live_value(key):
        """Return the placeholder shown until a reading arrives."""
        return "—"

    @staticmethod
    def csv_value_for_column(row, column):
        """Return a CSV value, using NaN for missing infrared readings."""
        value = row.get(column, "")
        if column in D6T_TEMPERATURE_COLUMNS and str(value).strip() == "":
            return "NaN"
        return value

    def reset_live_values(self):
        """Reset live cards before a new acquisition."""
        for key, var in self.live_vars.items():
            var.set(self.default_live_value(key))
        self.update_iq_warning_color()

    def get_pole_pairs_safe(self):
        """Read the pole-pair count with a fallback.

        Returns:
            int: Valid pole-pair count, or ``POLE_PAIRS_DEFAULT`` for an empty or
            invalid field.
        """
        try:
            pole_pairs = int(self.pole_pairs_var.get().strip())
            if pole_pairs > 0:
                return pole_pairs
        except Exception:
            pass
        return POLE_PAIRS_DEFAULT

    def rpm_to_elec_hz(self, rpm):
        """Convert mechanical speed to electrical frequency.

        Args:
            rpm: Mechanical speed in revolutions per minute.

        Returns:
            float: Electrical frequency in hertz.
        """
        return (float(rpm) * float(self.get_pole_pairs_safe())) / 60.0

    def elec_hz_to_rpm(self, elec_hz):
        """Convert electrical frequency to mechanical speed.

        Args:
            elec_hz: Electrical frequency in hertz.

        Returns:
            float: Mechanical speed in rpm.
        """
        return (float(elec_hz) * 60.0) / float(self.get_pole_pairs_safe())

    def profile_to_rpm(self, profile):
        """Convert a profile speed to mechanical rpm.

        Args:
            profile: Motor profile to interpret.

        Returns:
            float: Equivalent mechanical speed in rpm.
        """
        if profile.speed_unit == "elec_hz":
            return self.elec_hz_to_rpm(profile.speed_value)
        return float(profile.speed_value)

    def update_save_profile_button(self):
        """Show or hide the profile-save button.

        The button is visible only for the ``"Personnalisé"`` ("Custom") profile.
        """
        if not hasattr(self, "save_profile_button"):
            return
        if (self.profile_var.get() == "Personnalisé" and
                not self.is_idle_acquisition_mode()):
            self.save_profile_button.grid()
        else:
            self.save_profile_button.grid_remove()

    def is_idle_acquisition_mode(self):
        """Return whether the requested session only acquires data with the motor stopped."""
        return self.acquisition_mode_var.get() == ACQUISITION_MODE_IDLE

    def update_acquisition_mode_ui(self):
        """Adapt fields and actions to motor or acquisition-only mode."""
        if not hasattr(self, "acquisition_mode_combo"):
            return

        controls_locked = self.is_running or self.is_launching or self.is_stopping
        idle_mode = self.is_idle_acquisition_mode()

        self.acquisition_mode_combo.configure(state="disabled" if controls_locked else "readonly")

        for widget in self.motor_profile_widgets:
            if widget is self.profile_combo:
                widget.configure(state="disabled" if (idle_mode or controls_locked) else "readonly")
            else:
                widget.configure(state="disabled" if (idle_mode or controls_locked) else "normal")

        if idle_mode:
            self.save_profile_button.grid_remove()
            self.start_button.configure(text="LANCER COLLECTE")
        else:
            self.update_save_profile_button()
            self.start_button.configure(text="LANCER")

    def on_acquisition_mode_changed(self, *_args):
        """Handle switching between motor control and stopped-motor acquisition."""
        self.update_acquisition_mode_ui()
        self.validate_form()

    def switch_to_custom_due_to_edit(self):
        """Switch to the ``"Personnalisé"`` ("Custom") profile after editing.

        Skip this switch while applying a profile or automatically updating rpm/Hz,
        to avoid accidental profile changes.
        """
        if not self._user_edit_ready or self._applying_profile or self._updating_speed_link:
            return
        if self.profile_var.get() != "Personnalisé":
            self.profile_var.set("Personnalisé")
            self.update_save_profile_button()

    def on_user_config_changed(self, *_args):
        """Handle a changed configuration field.

        Args:
            *_args: Unused arguments supplied by ``trace_add``.
        """
        if not self.is_idle_acquisition_mode():
            self.switch_to_custom_due_to_edit()
        self.validate_form()

    def on_speed_rpm_changed(self, *_args):
        """Update electrical Hz after an rpm change.

        Args:
            *_args: Unused arguments supplied by ``trace_add``.
        """
        if not self._user_edit_ready or self._applying_profile or self._updating_speed_link:
            self.validate_form()
            return

        self._updating_speed_link = True
        try:
            rpm = float(self.speed_rpm_var.get().replace(",", "."))
            self.speed_hz_var.set(self.format_float(self.rpm_to_elec_hz(rpm)))
        except Exception:
            pass
        finally:
            self._updating_speed_link = False

        self.switch_to_custom_due_to_edit()
        self.validate_form()

    def on_speed_hz_changed(self, *_args):
        """Update rpm after an electrical Hz change.

        Args:
            *_args: Unused arguments supplied by ``trace_add``.
        """
        if not self._user_edit_ready or self._applying_profile or self._updating_speed_link:
            self.validate_form()
            return

        self._updating_speed_link = True
        try:
            elec_hz = float(self.speed_hz_var.get().replace(",", "."))
            self.speed_rpm_var.set(self.format_float(self.elec_hz_to_rpm(elec_hz)))
        except Exception:
            pass
        finally:
            self._updating_speed_link = False

        self.switch_to_custom_due_to_edit()
        self.validate_form()

    def on_pole_pairs_changed(self, *_args):
        """Recalculate electrical Hz when the pole-pair count changes.

        Args:
            *_args: Unused arguments supplied by ``trace_add``.
        """
        if not self._user_edit_ready or self._applying_profile or self._updating_speed_link:
            self.validate_form()
            return

        self._updating_speed_link = True
        try:
            rpm = float(self.speed_rpm_var.get().replace(",", "."))
            self.speed_hz_var.set(self.format_float(self.rpm_to_elec_hz(rpm)))
        except Exception:
            pass
        finally:
            self._updating_speed_link = False

        self.switch_to_custom_due_to_edit()
        self.validate_form()

    def save_current_profile(self):
        """Create or replace a custom profile from current fields.

        Validate settings, request a name, check for collisions, then write the
        profile to ``motor_profiles.json``.
        """
        if not self.validate_form():
            messagebox.showerror("Profil invalide", self.status_detail_var.get())
            return

        name = simpledialog.askstring(
            "Enregistrer le profil",
            "Nom du nouveau profil :",
            parent=self,
        )
        if name is None:
            return

        name = name.strip()
        if not name:
            messagebox.showerror("Nom invalide", "Le nom du profil ne peut pas être vide.")
            return
        if name == "Personnalisé":
            messagebox.showerror("Nom invalide", "Le nom 'Personnalisé' est réservé.")
            return
        if name in BUILTIN_PROFILE_NAMES:
            messagebox.showerror("Nom invalide", "Ce nom correspond à un profil prédéfini.")
            return
        if name in self.profiles:
            overwrite = messagebox.askyesno(
                "Profil existant",
                f"Le profil '{name}' existe déjà. Le remplacer ?",
            )
            if not overwrite:
                return

        if not messagebox.askyesno(
            "Confirmer l'enregistrement",
            f"Enregistrer le profil personnalisé sous le nom :\n\n{name}\n\nConfirmer ?",
        ):
            return

        cfg = self.parse_config()
        profile = MotorProfile(
            name=name,
            speed_value=cfg["target_rpm"],
            speed_unit="rpm",
            iq_limit_a=cfg["iq_limit"],
            hard_limit_a=cfg["hard_limit"],
            accel_elec_hz_s=cfg["accel"],
            datalog_ms=cfg["datalog_ms"],
            ds18b20_ms=cfg["ds18b20_ms"],
        )

        self.profiles[name] = profile
        self.profile_combo["values"] = list(self.profiles.keys())
        self.profile_var.set(name)
        self.update_save_profile_button()

        try:
            self.save_profiles_to_disk()
            self.log(f"Profil enregistré : {name}")
            messagebox.showinfo("Profil enregistré", f"Le profil '{name}' a été enregistré.")
        except Exception as exc:
            messagebox.showerror("Erreur sauvegarde", f"Impossible d'enregistrer le profil :\n{exc}")

    def set_status(self, state, detail=""):
        """Update status text and indicator color.

        Args:
            state: Primary status label.
            detail: Secondary message shown below the status.
        """
        self.status_var.set(state)
        self.status_detail_var.set(detail)
        color = COLORS["warning"]
        if state.lower().startswith("prêt") or state.lower().startswith("configuration"):
            color = COLORS["accent_2"]
        elif state.lower().startswith("lancement"):
            color = COLORS["warning"]
        elif (state.lower().startswith("datalogging") or
              state.lower().startswith("moteur") or
              state.lower().startswith("collecte")):
            color = COLORS["accent_2"]
        elif state.lower().startswith("arrêt"):
            color = COLORS["muted"]
        elif state.lower().startswith("erreur") or state.lower().startswith("échec"):
            color = COLORS["danger"]
        self.status_dot.itemconfig(self.status_dot_id, fill=color)

    def log(self, msg):
        """Add a timestamped line to the interface log.

        Args:
            msg: Message to display.
        """
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.insert(tk.END, f"[{stamp}] {msg}\n")
        self.log_line_count += 1

        if self.log_line_count > LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{LOG_TRIM_LINES + 1}.0")
            self.log_line_count -= LOG_TRIM_LINES

        self.log_text.see(tk.END)

    def format_port_display(self, port_info):
        """Format a serial port for display in the combobox.

        Args:
            port_info: Object returned by ``serial.tools.list_ports.comports``.

        Returns:
            str: Text such as ``COMx — description``.
        """
        description = str(port_info.description or "Périphérique série")
        return f"{port_info.device} — {description}"

    def get_selected_port_device(self):
        """Get the system name of the selected COM port.

        Returns:
            str: Port name usable by PySerial, such as ``COM5`` or ``/dev/ttyACM0``.
        """
        display = self.port_var.get().strip()
        if display in self.port_display_to_device:
            return self.port_display_to_device[display]
        if " — " in display:
            return display.split(" — ", 1)[0].strip()
        return display

    def on_port_selected(self, _event=None):
        """Handle COM port selection.

        Args:
            _event: Unused Tkinter selection event.
        """
        self.validate_form()

    def refresh_ports(self):
        """Refresh the list of available serial ports.

        Prioritize STMicroelectronics/ST-LINK ports when their hardware ID or
        description identifies them.
        """
        ports = list(serial.tools.list_ports.comports())

        self.port_display_to_device = {}
        values = []
        st_displays = []

        for p in ports:
            display = self.format_port_display(p)
            manufacturer = str(getattr(p, "manufacturer", "") or "")
            hwid = str(getattr(p, "hwid", "") or "")

            values.append(display)
            self.port_display_to_device[display] = p.device

            haystack = " ".join([p.device, p.description or "", manufacturer, hwid]).lower()
            if any(token in haystack for token in ["stmicroelectronics", "stlink", "st-link", "stm32", "stmicro"]):
                st_displays.append(display)

        self.port_combo["values"] = values

        current = self.port_var.get().strip()
        if st_displays:
            self.port_var.set(st_displays[0])
            self.log(f"Port STMicroelectronics détecté : {st_displays[0]}")
        elif values and current not in values:
            self.port_var.set(values[0])
        elif not values:
            self.port_var.set("")

        self.validate_form()

    def choose_csv_file(self):
        """Open a dialog to choose the output CSV file."""
        try:
            initial = self.csv_path_from_text(self.csv_path_var.get())
        except ValueError:
            initial = Path(self.default_csv_path())

        initialdir_path = initial.parent if initial.parent.exists() else self.paths.log_dir
        if not initialdir_path.exists():
            initialdir_path = Path.cwd()

        filename = filedialog.asksaveasfilename(
            title="Choisir le fichier CSV",
            initialdir=str(initialdir_path),
            initialfile=initial.name,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tous les fichiers", "*.*")],
        )
        if filename:
            self.csv_path_var.set(filename)

    def on_profile_changed(self, _event=None):
        """Apply the profile selected in the combobox.

        Args:
            _event: Unused Tkinter selection event.
        """
        profile_name = self.profile_var.get()
        profile = self.profiles.get(profile_name)
        if profile is not None:
            self.apply_profile(profile)

    def apply_profile(self, profile):
        """Copy profile settings into the interface fields.

        Args:
            profile: Motor profile to apply.
        """
        self._applying_profile = True
        try:
            target_rpm = self.profile_to_rpm(profile)
            elec_hz = self.rpm_to_elec_hz(target_rpm)

            self.speed_rpm_var.set(self.format_float(target_rpm))
            self.speed_hz_var.set(self.format_float(elec_hz))
            self.iq_limit_var.set(str(profile.iq_limit_a))
            self.hard_limit_var.set(str(profile.hard_limit_a))
            self.accel_var.set(str(profile.accel_elec_hz_s))
            self.datalog_ms_var.set(str(profile.datalog_ms))
            self.ds18b20_ms_var.set(str(profile.ds18b20_ms))
        finally:
            self._applying_profile = False

        self.update_save_profile_button()
        self.validate_form()

    def parse_config(self):
        """Read and validate current interface settings.

        Returns:
            dict[str, object]: Configuration ready to open the serial port and send
            ``CFG`` or ``ACQ_START`` for the selected mode.

        Raises:
            ValueError: If a required value is missing or inconsistent.
        """
        acquisition_mode = self.acquisition_mode_var.get()
        if acquisition_mode not in ACQUISITION_MODES:
            raise ValueError("Mode d'acquisition invalide.")

        port = self.get_selected_port_device()
        baud = int(self.baud_var.get().strip())
        datalog_ms = int(self.datalog_ms_var.get().strip())
        ds18b20_ms = int(self.ds18b20_ms_var.get().strip())
        csv_path = self.csv_path_from_text(self.csv_path_var.get())

        if not port:
            raise ValueError("Aucun port COM sélectionné.")
        if baud <= 0:
            raise ValueError("Le baudrate doit être > 0.")
        if not 1 <= datalog_ms <= MAX_DATALOG_MS:
            raise ValueError(f"La période DATA doit être comprise entre 1 et {MAX_DATALOG_MS} ms.")
        if not DS18B20_MIN_MS <= ds18b20_ms <= MAX_DS18B20_MS:
            raise ValueError(
                f"La période DS18B20 doit être comprise entre {DS18B20_MIN_MS} et {MAX_DS18B20_MS} ms."
            )

        config = {
            "acquisition_mode": acquisition_mode,
            "port": port,
            "baud": baud,
            "datalog_ms": datalog_ms,
            "ds18b20_ms": ds18b20_ms,
            "csv_path": csv_path,
        }

        if acquisition_mode == ACQUISITION_MODE_MOTOR:
            target_rpm = float(self.speed_rpm_var.get().replace(",", "."))
            speed_hz = float(self.speed_hz_var.get().replace(",", "."))
            pole_pairs = int(self.pole_pairs_var.get().strip())
            iq_limit = float(self.iq_limit_var.get().replace(",", "."))
            hard_limit = float(self.hard_limit_var.get().replace(",", "."))
            accel = float(self.accel_var.get().replace(",", "."))

            if not all(math.isfinite(value) for value in (
                    target_rpm, speed_hz, iq_limit, hard_limit, accel)):
                raise ValueError("Les paramètres moteur doivent être des nombres finis.")
            if not MIN_TARGET_SPEED_RPM <= target_rpm <= MAX_TARGET_SPEED_RPM:
                raise ValueError(
                    f"La vitesse doit être comprise entre {MIN_TARGET_SPEED_RPM:g} "
                    f"et {MAX_TARGET_SPEED_RPM:g} rpm."
                )
            if pole_pairs <= 0:
                raise ValueError("Le nombre de paires de pôles doit être > 0.")
            expected_hz = (target_rpm * pole_pairs) / 60.0
            if abs(expected_hz - speed_hz) > max(0.05, abs(expected_hz) * 0.01):
                raise ValueError("La vitesse Hz ne correspond pas aux rpm et aux paires de pôles.")
            if not 0 < iq_limit <= MAX_IQ_LIMIT_A:
                raise ValueError(f"Iq limite doit être compris entre 0 et {MAX_IQ_LIMIT_A:g} A.")
            if not 0 < hard_limit <= MAX_HARD_LIMIT_A:
                raise ValueError(f"Hard stop doit être compris entre 0 et {MAX_HARD_LIMIT_A:g} A.")
            if accel <= 0:
                raise ValueError("L'accélération doit être > 0.")
            if accel > MAX_ACCEL_ELEC_HZ_S:
                raise ValueError(
                    f"L'accélération doit être <= {MAX_ACCEL_ELEC_HZ_S:g} Hz électriques/s."
                )

            config.update({
                "target_rpm": target_rpm,
                "iq_limit": iq_limit,
                "hard_limit": hard_limit,
                "accel": accel,
            })

        return config

    def set_field_invalid(self, key, invalid=True):
        """Apply invalid styling to an input field.

        Args:
            key: Widget identifier in ``entry_widgets``.
            invalid: ``True`` to show an error, ``False`` to restore normal styling.
        """
        widget = self.entry_widgets.get(key)
        if widget is not None:
            try:
                widget.configure(style="Invalid.TEntry" if invalid else "TEntry")
            except Exception:
                pass

    def set_combo_invalid(self, key, invalid=True):
        """Apply invalid styling to a combobox.

        Args:
            key: Widget identifier in ``combo_widgets``.
            invalid: ``True`` to show an error, ``False`` to restore normal styling.
        """
        widget = self.combo_widgets.get(key)
        if widget is not None:
            try:
                widget.configure(style="Invalid.TCombobox" if invalid else "TCombobox")
            except Exception:
                pass

    def reset_field_styles(self):
        """Reset visual styling of all validated fields."""
        for key in list(self.entry_widgets.keys()):
            self.set_field_invalid(key, False)
        for key in list(self.combo_widgets.keys()):
            self.set_combo_invalid(key, False)

    def validate_form(self):
        """Validate all user configuration fields.

        Update field styles, warning message, start button, and overall status.

        Returns:
            bool: ``True`` if configuration is usable, otherwise ``False``.
        """
        self.reset_field_styles()
        warnings = []
        errors = []

        def parse_float(key, var, label, min_value=None, allow_zero=False):
            """Convert and validate a float field.

            Args:
                key: Widget identifier to flag on error.
                var: Tkinter variable holding the text value.
                label: Readable name used in error messages.
                min_value: Optional minimum value.
                allow_zero: Allow a value equal to ``min_value`` when ``True``.

            Returns:
                float | None: Converted value, or ``None`` if invalid.
            """
            raw = var.get().strip()
            if raw == "":
                self.set_field_invalid(key, True)
                errors.append(f"{label} vide.")
                return None
            try:
                value = float(raw.replace(",", "."))
                if not math.isfinite(value):
                    raise ValueError
            except Exception:
                self.set_field_invalid(key, True)
                errors.append(f"{label} invalide.")
                return None
            if min_value is not None:
                if allow_zero:
                    bad = value < min_value
                else:
                    bad = value <= min_value
                if bad:
                    self.set_field_invalid(key, True)
                    errors.append(f"{label} doit être > {min_value}.")
            return value

        def parse_int(key, var, label, min_value=None, allow_zero=False):
            """Convert and validate an integer field.

            Args:
                key: Widget identifier to flag on error.
                var: Tkinter variable holding the text value.
                label: Readable name used in error messages.
                min_value: Optional minimum value.
                allow_zero: Allow a value equal to ``min_value`` when ``True``.

            Returns:
                int | None: Converted value, or ``None`` if invalid.
            """
            raw = var.get().strip()
            if raw == "":
                self.set_field_invalid(key, True)
                errors.append(f"{label} vide.")
                return None
            try:
                value = int(raw)
            except Exception:
                self.set_field_invalid(key, True)
                errors.append(f"{label} invalide.")
                return None
            if min_value is not None:
                if allow_zero:
                    bad = value < min_value
                else:
                    bad = value <= min_value
                if bad:
                    self.set_field_invalid(key, True)
                    errors.append(f"{label} doit être > {min_value}.")
            return value

        port = self.get_selected_port_device()
        if not port:
            self.set_combo_invalid("port", True)
            errors.append("Aucun port COM sélectionné.")

        baud = parse_int("baud", self.baud_var, "Baudrate", 0)
        idle_mode = self.is_idle_acquisition_mode()

        target_rpm = None
        speed_hz = None
        pole_pairs = None
        iq_limit = None
        hard_limit = None
        accel = None

        if not idle_mode:
            target_rpm = parse_float("speed_rpm", self.speed_rpm_var, "Vitesse rpm", 0)
            speed_hz = parse_float("speed_hz", self.speed_hz_var, "Vitesse Hz", 0)
            pole_pairs = parse_int("pole_pairs", self.pole_pairs_var, "Paires de pôles", 0)
            iq_limit = parse_float("iq_limit", self.iq_limit_var, "Iq limite", 0)
            hard_limit = parse_float("hard_limit", self.hard_limit_var, "Hard stop", 0)
            accel = parse_float("accel", self.accel_var, "Accélération", 0)

        datalog_ms = parse_int("datalog_ms", self.datalog_ms_var, "Période DATA", 0)
        ds18b20_ms = parse_int("ds18b20_ms", self.ds18b20_ms_var, "Période DS18B20", 0)

        try:
            self.csv_path_from_text(self.csv_path_var.get())
        except ValueError as exc:
            self.set_field_invalid("csv_path", True)
            errors.append(str(exc))

        if pole_pairs is not None and target_rpm is not None and speed_hz is not None:
            expected_hz = (target_rpm * pole_pairs) / 60.0
            if abs(expected_hz - speed_hz) > max(0.05, abs(expected_hz) * 0.01):
                self.set_field_invalid("speed_hz", True)
                errors.append("La vitesse Hz ne correspond pas au rpm/paires de pôles.")
                warnings.append("La vitesse Hz ne correspond pas exactement au rpm. Elle sera recalculée automatiquement à la prochaine édition.")

        if target_rpm is not None and target_rpm < MIN_TARGET_SPEED_RPM:
            self.set_field_invalid("speed_rpm", True)
            self.set_field_invalid("speed_hz", True)
            errors.append(f"La vitesse minimale autorisée est {MIN_TARGET_SPEED_RPM:g} rpm.")
        elif target_rpm is not None and target_rpm > MAX_TARGET_SPEED_RPM:
            self.set_field_invalid("speed_rpm", True)
            self.set_field_invalid("speed_hz", True)
            errors.append(f"La vitesse maximale autorisée est {MAX_TARGET_SPEED_RPM:g} rpm.")

        if iq_limit is not None and iq_limit > MAX_IQ_LIMIT_A:
            self.set_field_invalid("iq_limit", True)
            errors.append(f"La limite Iq maximale autorisée est {MAX_IQ_LIMIT_A:g} A.")

        if hard_limit is not None and hard_limit > MAX_HARD_LIMIT_A:
            self.set_field_invalid("hard_limit", True)
            errors.append(f"Le hard stop maximal autorisé est {MAX_HARD_LIMIT_A:g} A.")

        if accel is not None and accel > MAX_ACCEL_ELEC_HZ_S:
            self.set_field_invalid("accel", True)
            errors.append(
                f"L'accélération maximale autorisée est {MAX_ACCEL_ELEC_HZ_S:g} Hz électriques/s."
            )

        if datalog_ms is not None and datalog_ms > MAX_DATALOG_MS:
            self.set_field_invalid("datalog_ms", True)
            errors.append(f"La période DATA doit être <= {MAX_DATALOG_MS} ms.")

        if ds18b20_ms is not None and ds18b20_ms > MAX_DS18B20_MS:
            self.set_field_invalid("ds18b20_ms", True)
            errors.append(f"La période DS18B20 doit être <= {MAX_DS18B20_MS} ms.")

        if datalog_ms is not None and datalog_ms < DS18B20_MIN_MS:
            warnings.append(
                "Attention : le DS18B20 ne peut pas fournir une nouvelle mesure sous 750 ms. "
                "Le CSV utilisera la dernière température valide, tandis que les données moteur suivront la période DATA."
            )

        if ds18b20_ms is not None and ds18b20_ms < DS18B20_MIN_MS:
            self.set_field_invalid("ds18b20_ms", True)
            errors.append("La période DS18B20 doit être >= 750 ms.")
            warnings.append("Période DS18B20 inférieure à 750 ms : impossible pour une vraie nouvelle mesure DS18B20.")

        self.warning_var.set("\n".join(warnings))
        is_valid = len(errors) == 0

        if not self.is_running and not self.is_launching and not self.is_stopping:
            if is_valid:
                self.start_button.configure(state=tk.NORMAL, bg=COLORS["accent_2"], fg="white")
                if idle_mode:
                    self.set_status("Configuration valide", "Prêt à collecter avec le moteur à l'arrêt.")
                else:
                    self.set_status("Configuration valide", "Prêt à lancer le moteur et la collecte.")
            else:
                self.start_button.configure(state=tk.DISABLED, bg=COLORS["panel_3"], fg=COLORS["muted"])
                self.set_status("Erreur configuration", errors[0])

        self.update_iq_warning_color()
        return is_valid

    def start_run(self):
        """Start a motor and acquisition session, or acquire with the motor stopped.

        Validate settings, prepare the CSV, open serial, start the reader thread, and run the mode-specific startup sequence in a worker thread.
        """
        if self.is_running or self.is_launching:
            return
        if not self.validate_form():
            messagebox.showerror("Configuration invalide", self.status_detail_var.get())
            return

        cfg = self.parse_config()
        csv_path = cfg["csv_path"]
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        if csv_path.exists():
            overwrite = messagebox.askyesno("Fichier existant", f"Le fichier existe déjà :\n{csv_path}\n\nL'écraser ?")
            if not overwrite:
                return

        try:
            self.serial_obj = serial.Serial(port=cfg["port"], baudrate=cfg["baud"], timeout=0.05, write_timeout=1.0)
            time.sleep(0.2)
            try:
                self.serial_obj.reset_input_buffer()
                self.serial_obj.reset_output_buffer()
            except Exception:
                pass
        except Exception as exc:
            messagebox.showerror("Erreur série", f"Impossible d'ouvrir {cfg['port']} :\n{exc}")
            return

        self.csv_path = str(csv_path)
        self.csv_file = None
        self.csv_writer = None
        self.csv_columns = []
        self.csv_output_columns = []
        self.csv_pending_rows = 0
        self.csv_last_flush_s = time.monotonic()
        self.data_before_header_count = 0
        self.reset_live_values()
        self.clear_plot()
        self.clear_ack_queue()

        self.stop_event.clear()
        self.reader_thread = threading.Thread(target=self.serial_reader_loop, daemon=True)
        self.reader_thread.start()

        self.is_launching = True
        self.is_running = False
        self.is_stopping = False
        self.active_acquisition_mode = cfg["acquisition_mode"]

        self.start_button.configure(state=tk.DISABLED, bg="#14532D", fg="white")
        self.stop_button.configure(state=tk.NORMAL)
        self.update_acquisition_mode_ui()
        if cfg["acquisition_mode"] == ACQUISITION_MODE_IDLE:
            self.set_status("Lancement", "Préparation de la collecte moteur arrêté...")
        else:
            self.set_status("Lancement", "Synchronisation et démarrage moteur...")
        self.log("Port série ouvert.")

        self.launch_thread = threading.Thread(target=self.launch_sequence_thread, args=(cfg,), daemon=True)
        self.launch_thread.start()

    def launch_sequence_thread(self, cfg):
        """Run the UART startup sequence in a worker thread.

        Args:
            cfg: Validated configuration returned by ``parse_config``.
        """
        try:
            idle_mode = cfg["acquisition_mode"] == ACQUISITION_MODE_IDLE

            self.clear_ack_queue()
            self.send_command("SYNC\n", char_delay=0.002)
            try:
                sync_ok = self.wait_for_ack(
                    "SYNC",
                    timeout=2.0,
                    ignored_errors={"ERR,UNKNOWN_CMD", "ERR,BAD_CFG", "ERR,NO_VALID_CFG"},
                )
                if not sync_ok:
                    next_command = "ACQ_START" if idle_mode else "CFG"
                    self.gui_queue.put(("log", f"SYNC non supporté/ignoré par la carte, poursuite avec {next_command}."))
            except TimeoutError:
                next_command = "ACQ_START" if idle_mode else "CFG"
                self.gui_queue.put(("log", f"Pas de réponse SYNC, poursuite avec {next_command}."))

            time.sleep(0.1)

            if idle_mode:
                acq_cmd = f"ACQ_START,{cfg['datalog_ms']},{cfg['ds18b20_ms']}\n"
                self.clear_ack_queue()
                self.send_command(acq_cmd, char_delay=0.002)
                self.wait_for_ack("ACQ_START", timeout=5.0)
            else:
                cfg_cmd = (
                    f"CFG,{cfg['target_rpm']:.3f},{cfg['iq_limit']:.3f},{cfg['hard_limit']:.3f},"
                    f"{cfg['accel']:.3f},{cfg['datalog_ms']},{cfg['ds18b20_ms']}\n"
                )

                self.clear_ack_queue()
                self.send_command(cfg_cmd, char_delay=0.003)
                self.wait_for_ack("CFG", timeout=5.0)

                time.sleep(0.1)

                self.clear_ack_queue()
                self.send_command("START\n", char_delay=0.002)
                self.wait_for_ack("START", timeout=5.0)

            self.gui_queue.put(("launch_success", cfg["acquisition_mode"]))
        except Exception as exc:
            self.gui_queue.put(("launch_failed", str(exc)))

    def stop_run(self):
        """Stop the motor if needed, then end acquisition with ``STOP``."""
        if self.is_stopping:
            return
        if self.serial_obj is None:
            self.close_resources()
            return

        self.is_stopping = True
        self.stop_button.configure(state=tk.DISABLED)
        self.set_status("Arrêt", "Commande STOP en cours...")
        self.log("Arrêt demandé.")
        self.stop_thread = threading.Thread(target=self.stop_sequence_thread, daemon=True)
        self.stop_thread.start()

    def stop_sequence_thread(self):
        """Send ``STOP`` and close resources after an ACK or timeout."""
        try:
            self.clear_ack_queue()
            self.send_command("STOP\n", char_delay=0.002)
            try:
                self.wait_for_ack("STOP", timeout=3.0)
            except Exception as exc:
                self.gui_queue.put(("log", f"ACK,STOP non reçu, fermeture quand même : {exc}"))
        except Exception as exc:
            self.gui_queue.put(("log", f"Erreur envoi STOP : {exc}"))
        self.gui_queue.put(("close_resources", None))

    def close_resources(self):
        """Close the CSV and serial port cleanly and return the GUI to idle."""
        self.stop_event.set()

        if self.csv_file:
            try:
                self.flush_csv(force=True)
                self.csv_file.close()
                self.log(f"CSV fermé : {self.csv_path}")
            except Exception as exc:
                self.log(f"Erreur fermeture CSV : {exc}")

        self.csv_file = None
        self.csv_writer = None
        self.csv_columns = []
        self.csv_output_columns = []
        self.csv_pending_rows = 0

        if self.serial_obj:
            try:
                if self.serial_obj.is_open:
                    self.serial_obj.close()
                self.log("Port série fermé.")
            except Exception as exc:
                self.log(f"Erreur fermeture série : {exc}")

        self.serial_obj = None
        self.is_running = False
        self.is_launching = False
        self.is_stopping = False
        self.stop_button.configure(state=tk.DISABLED)
        self.update_acquisition_mode_ui()
        self.validate_form()
        self.set_status("Arrêté", "Session terminée.")

        if self.close_requested:
            self.destroy()

    def send_command(self, command, char_delay=0.0):
        """Send an ASCII command to the STM32 firmware.

        Args:
            command: Full command to send, usually ending with a newline.
            char_delay: Optional delay between characters for firmware that handles
                UART bursts poorly.

        Raises:
            RuntimeError: If no open serial port is available.
        """
        if self.serial_obj is None or not self.serial_obj.is_open:
            raise RuntimeError("Port série non ouvert.")

        display = command.replace("\r", "\\r").replace("\n", "\\n")
        self.gui_queue.put(("log", f"TX → {display}"))

        with self.serial_lock:
            if char_delay > 0.0:
                for ch in command:
                    self.serial_obj.write(ch.encode("ascii"))
                    self.serial_obj.flush()
                    time.sleep(char_delay)
            else:
                self.serial_obj.write(command.encode("ascii"))
                self.serial_obj.flush()

    def clear_ack_queue(self):
        """Clear all pending ACK/ERR responses."""
        try:
            while True:
                self.ack_queue.get_nowait()
        except queue.Empty:
            pass

    def wait_for_ack(self, name, timeout=5.0, ignored_errors=None):
        """Wait for an ``ACK,<name>`` response or a firmware error.

        Args:
            name: Command name expected after ``ACK,``.
            timeout: Maximum wait time in seconds.
            ignored_errors: Optional set of nonblocking ``ERR,...`` responses.

        Returns:
            bool: ``True`` for the expected ACK; ``False`` for an ignored error.

        Raises:
            TimeoutError: If no usable response arrives before the timeout.
            RuntimeError: If firmware returns an error that is not ignored.
        """
        if ignored_errors is None:
            ignored_errors = set()
        expected = f"ACK,{name}"
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timeout en attente de {expected}")
            try:
                line = self.ack_queue.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"Timeout en attente de {expected}")
            if line == expected:
                return True
            if line.startswith("ERR,"):
                if line in ignored_errors:
                    return False
                raise RuntimeError(line)

    def serial_reader_loop(self):
        """Read the serial port continuously in a worker thread.

        Complete lines go to the GUI thread through ``gui_queue``. ``ACK`` and ``ERR``
        responses are also copied to ``ack_queue`` for sequence threads.
        """
        buffer = b""
        while not self.stop_event.is_set():
            try:
                if self.serial_obj is None:
                    break
                data = self.serial_obj.read(512)
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.gui_queue.put(("log", f"Erreur lecture série : {exc}"))
                break
            if not data:
                continue
            buffer += data
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                line = raw_line.decode(errors="replace").replace("\x00", "").strip()
                if not line:
                    continue
                if line.startswith("ACK,") or line.startswith("ERR,"):
                    self.ack_queue.put(line)
                self.gui_queue.put(("line", line))
        self.gui_queue.put(("reader_stopped", None))

    def process_gui_queue(self):
        """Handle events produced by worker threads.

        Called periodically through ``after`` so all Tkinter updates stay on the main thread.
        """
        try:
            while True:
                kind, payload = self.gui_queue.get_nowait()
                if kind == "line":
                    self.handle_serial_line(payload)
                elif kind == "log":
                    self.log(payload)
                elif kind == "launch_success":
                    self.is_launching = False
                    self.is_running = True
                    self.is_stopping = False
                    self.update_acquisition_mode_ui()
                    if payload == ACQUISITION_MODE_IDLE:
                        self.set_status("Collecte active", "Moteur arrêté, réception des températures et mesures nulles.")
                    else:
                        self.set_status("Moteur lancé", "Datalogging en attente/actif.")
                    self.start_button.configure(state=tk.DISABLED, bg="#14532D", fg="white")
                    self.stop_button.configure(state=tk.NORMAL)
                elif kind == "launch_failed":
                    self.log(f"Échec lancement : {payload}")
                    self.set_status("Échec lancement", payload)
                    messagebox.showerror("Échec lancement", payload)
                    try:
                        if self.serial_obj and self.serial_obj.is_open:
                            self.send_command("STOP\n", char_delay=0.001)
                    except Exception:
                        pass
                    self.close_resources()
                elif kind == "close_resources":
                    self.close_resources()
                elif kind == "reader_stopped":
                    pass
        except queue.Empty:
            pass
        self.after(50, self.process_gui_queue)

    @staticmethod
    def clean_fields(fields):
        """Strip whitespace around a list of CSV/UART fields.

        Args:
            fields: Text fields to clean.

        Returns:
            list[str]: Fields without leading or trailing whitespace.
        """
        return [x.strip() for x in fields]

    @staticmethod
    def safe_float(value):
        """Convert a value to a finite float or ``NaN``.

        Args:
            value: Text or numeric value to convert.

        Returns:
            float: Converted value if finite, otherwise ``math.nan``.
        """
        try:
            v = float(str(value).strip().replace(",", "."))
            if math.isfinite(v):
                return v
        except Exception:
            pass
        return math.nan

    def open_csv_with_header(self, columns):
        """Open the CSV after receiving the firmware header.

        Args:
            columns: Columns announced by the ``#CSV_HEADER`` line.
        """
        self.csv_file = open(self.csv_path, mode="w", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.csv_file, delimiter=";")
        self.csv_columns = columns
        self.csv_output_columns = list(CSV_OUTPUT_COLUMNS)

        missing_columns = [col for col in CSV_OUTPUT_COLUMNS if col not in columns]
        extra_columns = [col for col in columns if col not in CSV_OUTPUT_COLUMNS]
        if missing_columns:
            self.log("Colonnes CSV attendues absentes du firmware : " + ", ".join(missing_columns))
            for key in D6T_TEMPERATURE_COLUMNS:
                if key in missing_columns and key in self.live_vars:
                    self.live_vars[key].set("NaN")
        if extra_columns:
            self.log("Colonnes reçues non écrites dans le CSV final : " + ", ".join(extra_columns))

        self.csv_writer.writerow(self.csv_output_columns)
        self.csv_pending_rows = 0
        self.csv_last_flush_s = time.monotonic()
        self.flush_csv(force=True)
        self.register_csv_fields_for_live(columns)
        self.register_csv_fields_for_plot(columns)
        self.log(f"CSV créé : {self.csv_path}")

    def flush_csv(self, force=False):
        """Flush the CSV now or on its schedule.

        Args:
            force: If ``True``, flush immediately even before row or time thresholds.
        """
        if self.csv_file is None:
            return

        now = time.monotonic()
        should_flush = (
            force
            or self.csv_pending_rows >= CSV_FLUSH_EVERY_ROWS
            or (self.csv_pending_rows > 0 and (now - self.csv_last_flush_s) >= CSV_FLUSH_INTERVAL_S)
        )
        if should_flush:
            self.csv_file.flush()
            self.csv_pending_rows = 0
            self.csv_last_flush_s = now

    def handle_serial_line(self, line):
        """Process a line received over UART.

        Route CSV headers, ``DATA`` rows, ACK/ERR responses, and debug messages. Record
        valid data in the CSV, then forward it to live cards and the chart.

        Args:
            line: Decoded and stripped UART line.
        """
        self.log(f"RX ← {line}")

        if line.startswith("#CSV_HEADER,"):
            columns = self.clean_fields(line.split(",")[1:])
            if not columns:
                self.log("Header CSV vide ignoré.")
                return
            if self.csv_writer is None:
                self.open_csv_with_header(columns)
                self.set_status("Datalogging actif", "Réception DATA en cours.")
            elif columns != self.csv_columns:
                self.log("Header différent reçu, ignoré.")
            return

        if line.startswith("DATA,"):
            if self.csv_writer is None:
                self.data_before_header_count += 1
                if self.data_before_header_count <= 3:
                    self.log(f"DATA reçue avant header, ignorée : {line}")
                elif self.data_before_header_count == 4:
                    self.log("Autres DATA avant header ignorées sans affichage.")
                return

            parts = self.clean_fields(line.split(","))
            values = parts[1:]
            if len(values) != len(self.csv_columns):
                self.log(f"DATA invalide ({len(values)} valeurs pour {len(self.csv_columns)} colonnes) : {line}")
                return

            row = dict(zip(self.csv_columns, values))
            self.csv_writer.writerow([self.csv_value_for_column(row, col) for col in self.csv_output_columns])
            self.csv_pending_rows += 1
            self.flush_csv()

            self.update_live_values(row)
            self.append_plot_row(row)
            return

        if line.startswith("ACK,") or line.startswith("ERR,") or line.startswith("#"):
            return

        return

    def update_live_values(self, row):
        """Update live cards with values from a DATA row.

        Electrical speed is displayed but not logged, so the interface recalculates it
        from mechanical speed and the configured pole-pair count.

        Args:
            row: ``column -> value`` dictionary built from the CSV row.
        """
        for key, var in self.live_vars.items():
            if key in row:
                var.set(self.csv_value_for_column(row, key))
            elif key in D6T_TEMPERATURE_COLUMNS:
                var.set("NaN")

        rpm = self.safe_float(row.get("motor_speed_mech_rpm", math.nan))
        if math.isfinite(rpm) and "motor_speed_elec_hz" in self.live_vars:
            self.live_vars["motor_speed_elec_hz"].set(self.format_float(self.rpm_to_elec_hz(rpm)))

        self.update_iq_warning_color()

    def get_iq_limit_safe(self):
        """Return the configured Iq limit, or ``NaN`` if invalid.

        Returns:
            float: Positive Iq limit, otherwise ``math.nan``.
        """
        try:
            value = float(self.iq_limit_var.get().strip().replace(",", "."))
            if math.isfinite(value) and value > 0.0:
                return value
        except Exception:
            pass
        return math.nan

    def update_iq_warning_color(self):
        """Color Iq according to its proximity to the configured limit."""
        label = self.live_value_labels.get("motor_iq_a")
        if label is None:
            return

        if self.is_idle_acquisition_mode():
            try:
                label.configure(foreground=COLORS["text"])
            except Exception:
                pass
            return

        iq_value = self.safe_float(self.live_vars.get("motor_iq_a", tk.StringVar(value="")).get())
        iq_limit = self.get_iq_limit_safe()
        color = COLORS["text"]

        if math.isfinite(iq_value) and math.isfinite(iq_limit) and iq_limit > 0.0:
            ratio = abs(iq_value) / iq_limit
            if ratio >= 1.0:
                color = COLORS["danger"]
            elif ratio >= IQ_WARNING_RATIO:
                color = COLORS["warning"]

        try:
            label.configure(foreground=color)
        except Exception:
            pass

    def append_plot_row(self, row):
        """Add a data row to the chart buffers.

        Args:
            row: ``column -> value`` dictionary built from the CSV row.
        """
        t_ms = self.safe_float(row.get("stm32_time_ms", math.nan))
        if math.isfinite(t_ms):
            t_s = t_ms / 1000.0
        else:
            t_s = time.monotonic()

        if self.plot_t0_s is None:
            self.plot_t0_s = t_s

        x = t_s - self.plot_t0_s
        self.plot_x.append(x)

        for key in self.plot_fields:
            self.plot_data[key].append(self.safe_float(row.get(key, math.nan)))

        self.plot_dirty = True

    def clear_plot(self):
        """Reset all live-chart buffers."""
        self.plot_x.clear()
        for data in self.plot_data.values():
            data.clear()
        self.plot_t0_s = None
        self.plot_dirty = True
        if MATPLOTLIB_AVAILABLE and self.ax is not None:
            self.redraw_plot(force=True)

    def mark_plot_dirty(self):
        """Mark the chart for redrawing."""
        self.plot_dirty = True

    def style_axis(self, ax=None, title=False):
        """Apply the dashboard's dark style to a Matplotlib axis."""
        if ax is None:
            ax = self.ax
        if ax is None:
            return
        ax.set_facecolor(COLORS["panel"])
        ax.tick_params(colors=COLORS["muted"], labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(COLORS["border"])
        ax.grid(True, color=COLORS["line_grid"], alpha=0.42, linewidth=0.8)
        ax.set_xlabel("Temps (s)", color=COLORS["muted"])
        if title:
            ax.set_title("Donnees temps reel", color=COLORS["text"], fontsize=12, pad=12)

    def redraw_plot_periodic(self):
        """Periodic callback to refresh the chart."""
        if self.plot_dirty:
            self.redraw_plot()
        self.after(PLOT_REFRESH_MS, self.redraw_plot_periodic)

    def redraw_plot(self, force=False):
        """Redraw the live chart when needed.

        Args:
            force: If ``True``, redraw even when no data is marked as changed.
        """
        if not MATPLOTLIB_AVAILABLE or self.figure is None or self.canvas is None:
            return
        if not force and not self.plot_dirty:
            return

        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        ax = self.ax
        self.style_axis(ax, title=True)
        self.figure.subplots_adjust(left=0.075, right=0.985, top=0.88, bottom=0.16)

        xs = list(self.plot_x)
        selected = [key for key, var in self.plot_enabled_vars.items() if var.get()]

        if xs and selected:
            lines = []
            labels = []
            multi_scale = len(selected) > 1

            for key in selected:
                ys = list(self.plot_data[key])
                pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(y)]
                if not pairs:
                    continue

                x_clean, y_clean = zip(*pairs)
                color = PLOT_COLORS.get(key, None)

                if multi_scale:
                    y_min = min(y_clean)
                    y_max = max(y_clean)
                    latest = y_clean[-1]
                    if abs(y_max - y_min) < 1e-9:
                        y_plot = [50.0 for _ in y_clean]
                    else:
                        y_plot = [((y - y_min) / (y_max - y_min)) * 100.0 for y in y_clean]
                    label = f"{self.plot_fields[key]} | {y_min:.3g}→{y_max:.3g} | act. {latest:.3g}"
                else:
                    y_plot = y_clean
                    label = self.plot_fields[key]

                line, = ax.plot(x_clean, y_plot, linewidth=2.0, label=label, color=color)
                lines.append(line)
                labels.append(label)

            if lines:
                if multi_scale:
                    ax.set_ylabel("Échelle relative par variable (%)", color=COLORS["muted"])
                    ax.set_ylim(-5, 105)
                else:
                    ax.set_ylabel(labels[0], color=COLORS["muted"])

                legend = ax.legend(lines, labels, loc="upper left", fontsize=8, frameon=True)
                if legend:
                    legend.get_frame().set_facecolor(COLORS["panel_2"])
                    legend.get_frame().set_edgecolor(COLORS["border"])
                    for text in legend.get_texts():
                        text.set_color(COLORS["text"])
        else:
            ax.text(
                0.5,
                0.5,
                "En attente de données...",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color=COLORS["muted"],
                fontsize=12,
            )

        self.canvas.draw_idle()
        self.plot_dirty = False

    def on_close(self):
        """Handle closing the main window.

        If a test is running, ask for confirmation and complete the motor shutdown
        sequence before destroying the window.
        """
        if self.is_running or self.is_launching:
            if not messagebox.askyesno("Quitter", "Une session est en cours. L'arrêter et quitter ?"):
                return
            self.close_requested = True
            self.stop_run()
        else:
            self.destroy()


if __name__ == "__main__":
    app = MotorDatalogGui(parse_dashboard_args())
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
