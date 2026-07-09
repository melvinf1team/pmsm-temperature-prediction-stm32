"""Interface graphique de datalogging moteur PMSM pour STM32.

Ce module fournit une application Tkinter permettant de piloter une carte
STM32 B-G473E-ZEST1S associée à une power board STDES-LVHP01, de lancer une
séquence moteur simple, de recevoir les mesures UART, d'enregistrer un CSV et
d'afficher les grandeurs en temps réel. Le CSV final conserve uniquement les
colonnes STM32 utiles au prétraitement NanoEdge AI, sans timestamp PC.

Le protocole série attendu côté firmware est volontairement textuel et basé sur
une ligne par message::

    SYNC
    CFG,<rpm>,<iq_limit>,<hard_limit>,<accel>,<datalog_ms>,<ds18b20_ms>
    START
    STOP
    ACK,<commande>
    ERR,<raison>
    #CSV_HEADER,<colonne_1>,<colonne_2>,...
    DATA,<valeur_1>,<valeur_2>,...

Le style de documentation utilisé suit les conventions Google docstring pour
faciliter une génération future avec Sphinx ou Read the Docs.
"""

import csv
import json
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

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
    """Profil de configuration moteur sauvegardable.

    Attributes:
        name: Nom lisible affiché dans la liste des profils.
        speed_value: Valeur de consigne de vitesse, exprimée selon ``speed_unit``.
        speed_unit: Unité de ``speed_value``. Les valeurs supportées sont
            ``"rpm"`` et ``"elec_hz"``.
        iq_limit_a: Limite de courant ``Iq`` utilisée comme consigne ou garde-fou
            logiciel.
        hard_limit_a: Seuil de courant maximal au-delà duquel le firmware peut
            déclencher un arrêt de sécurité.
        accel_elec_hz_s: Rampe d'accélération exprimée en Hz électriques par
            seconde.
        datalog_ms: Période d'envoi des lignes ``DATA`` par le firmware.
        ds18b20_ms: Période de rafraîchissement du capteur DS18B20.
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
PROFILE_STORE_PATH = Path(__file__).with_name("motor_profiles.json")


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
    """Application Tkinter de contrôle moteur et datalogging.

    La classe centralise l'interface utilisateur, la connexion série, les threads
    de communication, la création du fichier CSV et le graphique dynamique. Les
    accès directs à Tkinter restent dans le thread principal ; les threads série ne
    communiquent avec l'interface qu'au travers de files ``queue.Queue``.

    Attributes:
        serial_obj: Objet série PySerial actuellement ouvert, ou ``None``.
        gui_queue: File utilisée par les threads pour envoyer des événements au
            thread Tkinter.
        ack_queue: File dédiée aux réponses ``ACK`` et ``ERR`` du firmware.
        profiles: Dictionnaire des profils moteur disponibles.
        plot_fields: Variables actuellement disponibles pour le graphique.
        live_vars: Variables Tkinter liées aux cartes de valeurs instantanées.
    """
    def __init__(self):
        """Initialise l'application, l'état interne et l'interface graphique.

        L'initialisation prépare les variables Tkinter, charge les profils, construit
        les panneaux de l'interface, détecte les ports COM disponibles et démarre les
        boucles périodiques de traitement des files et de rafraîchissement du graphe.
        """
        super().__init__()

        self.title("STM32 PMSM Dark Bench Dashboard")
        self.geometry("1540x960")
        self.minsize(1120, 720)
        self.configure(bg=COLORS["bg"])

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
        self.speed_rpm_var = tk.StringVar()
        self.speed_hz_var = tk.StringVar()
        self.pole_pairs_var = tk.StringVar(value=str(POLE_PAIRS_DEFAULT))
        self.iq_limit_var = tk.StringVar()
        self.hard_limit_var = tk.StringVar()
        self.accel_var = tk.StringVar()
        self.datalog_ms_var = tk.StringVar()
        self.ds18b20_ms_var = tk.StringVar()
        self.csv_path_var = tk.StringVar(value=self.default_csv_path())
        self.status_var = tk.StringVar(value="Prêt")
        self.status_detail_var = tk.StringVar(value="Sélectionne un port COM et une configuration.")
        self.warning_var = tk.StringVar(value="")

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

        self.setup_style()
        self.build_ui()
        for error in self.profile_load_errors:
            self.log(error)
        self.refresh_ports()
        self.apply_profile(self.profiles[initial_profile])
        self._user_edit_ready = True
        self.validate_form()
        self.after(50, self.process_gui_queue)
        self.after(PLOT_REFRESH_MS, self.redraw_plot_periodic)

    @staticmethod
    def default_csv_path():
        """Construit le chemin CSV par défaut pour une nouvelle acquisition.

        Returns:
            str: Chemin vers un fichier ``daq_log_YYYYMMDD_HHMMSS.csv`` dans le dossier
            ``logs`` du répertoire courant.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(Path.cwd() / "logs" / f"daq_log_{timestamp}.csv")

    def setup_style(self):
        """Configure un thème sombre dense et lisible pour le banc moteur."""
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
        """Construit la structure principale de l'interface graphique sombre."""
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
        """Crée la colonne gauche adaptative avec défilement discret.

        La zone garde les cartes à leur taille naturelle pour éviter les widgets
        coupés. Quand la hauteur disponible est insuffisante, la molette permet de
        faire défiler uniquement cette colonne sans afficher une barre de défilement
        visible.

        Args:
            parent: Conteneur Tkinter dans lequel créer le canvas de gauche.

        Returns:
            tk.Frame: Frame interne qui reçoit les cartes de configuration.
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
        """Met à jour la zone défilable quand le contenu gauche change.

        Args:
            _event: Événement Tkinter ``<Configure>`` non utilisé directement.
        """
        if self.left_canvas is None:
            return
        bbox = self.left_canvas.bbox("all")
        if bbox is not None:
            self.left_canvas.configure(scrollregion=bbox)
        self.update_left_scroll_state()

    def on_left_canvas_configure(self, event):
        """Ajuste la largeur du contenu gauche à celle du canvas.

        Args:
            event: Événement Tkinter contenant la nouvelle largeur disponible.
        """
        if self.left_canvas is None or self.left_window_id is None:
            return
        self.left_canvas.itemconfigure(self.left_window_id, width=event.width)
        self.update_left_scroll_state()

    def update_left_scroll_state(self):
        """Active ou désactive le défilement de la colonne gauche.

        Le défilement est activé uniquement lorsque la hauteur réelle du contenu dépasse
        la hauteur visible du canvas.
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
        """Indique si le pointeur de souris est au-dessus du panneau gauche.

        Returns:
            bool: ``True`` si le pointeur est dans la zone gauche, sinon ``False``.
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
        """Gère la molette souris/touchpad pour le panneau gauche.

        Args:
            event: Événement de molette Windows/macOS ou Linux.

        Returns:
            str | None: ``"break"`` quand l'événement est consommé par la colonne
            gauche, sinon ``None``.
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
        """Construit l'en-tête de commande du banc moteur."""
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
        """Crée un gros bouton de commande moteur."""
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
        """Crée un bouton compact pour les cartes de configuration."""
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
        """Crée une carte sombre sans barre colorée supérieure."""
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
        """Place un label compact de formulaire."""
        ttk.Label(parent, text=text, style="CardMuted.TLabel").grid(row=row, column=col, sticky="w", padx=padx, pady=(0, 4))

    def form_row(self, parent, label, widget, row, col=0):
        """Ajoute une ligne de formulaire label + widget en grille.

        Args:
            parent: Conteneur cible.
            label: Texte du label.
            widget: Widget de saisie à placer sous le label.
            row: Ligne de départ dans la grille.
            col: Colonne de départ dans la grille.
        """
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", pady=(0, 2))
        widget.grid(row=row + 1, column=col, sticky="ew", pady=(0, 6), padx=(0, 8))

    def compact_form_row(self, parent, left_label, left_widget, right_label, right_widget, row):
        """Ajoute deux champs de formulaire compacts sur une même ligne.

        Args:
            parent: Conteneur cible.
            left_label: Label du champ gauche.
            left_widget: Widget du champ gauche.
            right_label: Label du champ droit.
            right_widget: Widget du champ droit.
            row: Ligne de placement dans la grille.
        """
        ttk.Label(parent, text=left_label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 6), pady=(0, 6))
        left_widget.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=(0, 6))
        ttk.Label(parent, text=right_label, style="Card.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 6), pady=(0, 6))
        right_widget.grid(row=row, column=3, sticky="ew", pady=(0, 6))

    def build_left_panel(self, parent):
        """Construit le rail gauche de configuration."""
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

        self.datalog_entry = ttk.Entry(acq, textvariable=self.datalog_ms_var)
        self.entry_widgets["datalog_ms"] = self.datalog_entry
        self.form_row(acq, "Periode DATA (ms)", self.datalog_entry, 0, 0)

        self.ds18b20_entry = ttk.Entry(acq, textvariable=self.ds18b20_ms_var)
        self.entry_widgets["ds18b20_ms"] = self.ds18b20_entry
        self.form_row(acq, "Periode DS18B20 (ms)", self.ds18b20_entry, 0, 1)

        self.warning_label = ttk.Label(acq, textvariable=self.warning_var, style="Warn.TLabel", wraplength=340)
        self.warning_label.configure(wraplength=380)
        self.warning_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))


    def build_live_cards(self, parent):
        """Construit les cartes de valeurs instantanées."""
        self.live_frame = tk.Frame(parent, bg=COLORS["bg"])
        self.live_frame.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        for i in range(4):
            self.live_frame.grid_columnconfigure(i, weight=1, uniform="live")

        self.live_card_frames = {}
        for key in self.live_fields:
            self.add_live_card(key)

    def add_live_card(self, key):
        """Ajoute dynamiquement une carte de valeur instantanée."""
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
        """Construit le panneau du graphique dynamique."""
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
        """Ajoute aux cartes live les colonnes CSV connues découvertes à l'exécution.

        Args:
            columns: Liste des colonnes reçues depuis la ligne ``#CSV_HEADER``.
        """
        for key in columns:
            if key in KNOWN_FIELDS and key not in self.live_card_frames:
                self.add_live_card(key)

    def add_plot_checkbox(self, key):
        """Ajoute une case à cocher pour afficher une variable dans le graphe."""
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
        """Enregistre de nouvelles colonnes CSV comme variables traçables.

        Args:
            columns: Liste des colonnes reçues depuis la ligne ``#CSV_HEADER``.
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
        """Construit le journal texte TX/RX."""
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
        """Relie les variables Tkinter aux callbacks de validation.

        Les traces maintiennent la cohérence rpm/Hz électriques et revalident la
        configuration dès qu'un champ utilisateur est modifié.
        """
        self.speed_rpm_var.trace_add("write", self.on_speed_rpm_changed)
        self.speed_hz_var.trace_add("write", self.on_speed_hz_changed)
        self.pole_pairs_var.trace_add("write", self.on_pole_pairs_changed)

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
        """Charge les profils moteur intégrés et personnalisés.

        Le fichier ``motor_profiles.json`` est lu s'il existe dans le même dossier que
        le script. Les profils invalides sont ignorés et les erreurs sont stockées dans
        ``profile_load_errors`` pour affichage dans le journal.

        Returns:
            dict[str, MotorProfile]: Dictionnaire des profils disponibles.
        """
        profiles = dict(PROFILES)

        if PROFILE_STORE_PATH.exists():
            try:
                raw = json.loads(PROFILE_STORE_PATH.read_text(encoding="utf-8"))
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
                self.profile_load_errors.append(f"Impossible de charger {PROFILE_STORE_PATH.name} : {exc}")

        if "Personnalisé" not in profiles:
            profiles["Personnalisé"] = PROFILES["Personnalisé"]

        return profiles

    def get_initial_profile_name(self):
        """Sélectionne le profil à afficher au démarrage.

        Returns:
            str: Premier profil personnalisé disponible, ou ``"Personnalisé"`` si aucun
            profil externe n'a été chargé.
        """
        for name in self.profiles:
            if name != "Personnalisé":
                return name
        return "Personnalisé"

    def save_profiles_to_disk(self):
        """Sauvegarde les profils personnalisés dans ``motor_profiles.json``.

        Les profils intégrés ne sont pas exportés afin de conserver le fichier JSON
        centré sur les profils créés par l'utilisateur.
        """
        custom_profiles = []
        for name, profile in self.profiles.items():
            if name in BUILTIN_PROFILE_NAMES or name == "Personnalisé":
                continue
            custom_profiles.append(asdict(profile))

        PROFILE_STORE_PATH.write_text(
            json.dumps(custom_profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def format_float(value):
        """Formate un nombre flottant pour affichage dans un champ de saisie.

        Args:
            value: Valeur convertible en ``float``.

        Returns:
            str: Nombre avec trois décimales maximum, sans zéros inutiles. Retourne une
            chaîne vide si la conversion échoue.
        """
        try:
            value = float(value)
        except Exception:
            return ""
        text = f"{value:.3f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def default_live_value(key):
        """Retourne la valeur affichée tant qu'aucune mesure n'est reçue."""
        return "—"

    @staticmethod
    def csv_value_for_column(row, column):
        """Retourne la valeur CSV en remplissant les mesures IR absentes par NaN."""
        value = row.get(column, "")
        if column in D6T_TEMPERATURE_COLUMNS and str(value).strip() == "":
            return "NaN"
        return value

    def reset_live_values(self):
        """Réinitialise les cartes live avant une nouvelle acquisition."""
        for key, var in self.live_vars.items():
            var.set(self.default_live_value(key))
        self.update_iq_warning_color()

    def get_pole_pairs_safe(self):
        """Lit le nombre de paires de pôles avec une valeur de secours.

        Returns:
            int: Nombre de paires de pôles valide, ou ``POLE_PAIRS_DEFAULT`` en cas de
            champ vide/invalide.
        """
        try:
            pole_pairs = int(self.pole_pairs_var.get().strip())
            if pole_pairs > 0:
                return pole_pairs
        except Exception:
            pass
        return POLE_PAIRS_DEFAULT

    def rpm_to_elec_hz(self, rpm):
        """Convertit une vitesse mécanique en fréquence électrique.

        Args:
            rpm: Vitesse mécanique en tours par minute.

        Returns:
            float: Fréquence électrique en hertz.
        """
        return (float(rpm) * float(self.get_pole_pairs_safe())) / 60.0

    def elec_hz_to_rpm(self, elec_hz):
        """Convertit une fréquence électrique en vitesse mécanique.

        Args:
            elec_hz: Fréquence électrique en hertz.

        Returns:
            float: Vitesse mécanique en tours par minute.
        """
        return (float(elec_hz) * 60.0) / float(self.get_pole_pairs_safe())

    def profile_to_rpm(self, profile):
        """Convertit la vitesse d'un profil en rpm mécaniques.

        Args:
            profile: Profil moteur à interpréter.

        Returns:
            float: Vitesse mécanique équivalente en rpm.
        """
        if profile.speed_unit == "elec_hz":
            return self.elec_hz_to_rpm(profile.speed_value)
        return float(profile.speed_value)

    def update_save_profile_button(self):
        """Affiche ou masque le bouton d'enregistrement de profil.

        Le bouton est visible uniquement lorsque le profil courant est
        ``"Personnalisé"``.
        """
        if not hasattr(self, "save_profile_button"):
            return
        if self.profile_var.get() == "Personnalisé":
            self.save_profile_button.grid()
        else:
            self.save_profile_button.grid_remove()

    def switch_to_custom_due_to_edit(self):
        """Bascule automatiquement sur le profil ``Personnalisé`` après édition.

        La bascule est ignorée pendant l'application d'un profil ou la mise à jour
        automatique rpm/Hz afin d'éviter les changements de profil parasites.
        """
        if not self._user_edit_ready or self._applying_profile or self._updating_speed_link:
            return
        if self.profile_var.get() != "Personnalisé":
            self.profile_var.set("Personnalisé")
            self.update_save_profile_button()

    def on_user_config_changed(self, *_args):
        """Callback déclenché quand un champ de configuration est modifié.

        Args:
            *_args: Arguments fournis par ``trace_add`` et non utilisés.
        """
        self.switch_to_custom_due_to_edit()
        self.validate_form()

    def on_speed_rpm_changed(self, *_args):
        """Synchronise le champ Hz électriques après modification des rpm.

        Args:
            *_args: Arguments fournis par ``trace_add`` et non utilisés.
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
        """Synchronise le champ rpm après modification des Hz électriques.

        Args:
            *_args: Arguments fournis par ``trace_add`` et non utilisés.
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
        """Recalcule les Hz électriques lorsque les paires de pôles changent.

        Args:
            *_args: Arguments fournis par ``trace_add`` et non utilisés.
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
        """Crée ou remplace un profil personnalisé depuis les champs actuels.

        La méthode valide la configuration, demande un nom, vérifie les collisions puis
        écrit le profil dans ``motor_profiles.json``.
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
        """Met à jour le statut textuel et la couleur du témoin.

        Args:
            state: Libellé principal du statut.
            detail: Message secondaire affiché sous le statut.
        """
        self.status_var.set(state)
        self.status_detail_var.set(detail)
        color = COLORS["warning"]
        if state.lower().startswith("prêt") or state.lower().startswith("configuration"):
            color = COLORS["accent_2"]
        elif state.lower().startswith("lancement"):
            color = COLORS["warning"]
        elif state.lower().startswith("datalogging") or state.lower().startswith("moteur"):
            color = COLORS["accent_2"]
        elif state.lower().startswith("arrêt"):
            color = COLORS["muted"]
        elif state.lower().startswith("erreur") or state.lower().startswith("échec"):
            color = COLORS["danger"]
        self.status_dot.itemconfig(self.status_dot_id, fill=color)

    def log(self, msg):
        """Ajoute une ligne horodatée dans le journal de l'interface.

        Args:
            msg: Message à afficher.
        """
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.insert(tk.END, f"[{stamp}] {msg}\n")
        self.log_line_count += 1

        if self.log_line_count > LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{LOG_TRIM_LINES + 1}.0")
            self.log_line_count -= LOG_TRIM_LINES

        self.log_text.see(tk.END)

    def format_port_display(self, port_info):
        """Formate un port série pour l'affichage dans la combobox.

        Args:
            port_info: Objet retourné par ``serial.tools.list_ports.comports``.

        Returns:
            str: Texte du type ``COMx — description``.
        """
        description = str(port_info.description or "Périphérique série")
        return f"{port_info.device} — {description}"

    def get_selected_port_device(self):
        """Récupère le nom système du port COM sélectionné.

        Returns:
            str: Nom du port utilisable par PySerial, par exemple ``COM5`` ou
            ``/dev/ttyACM0``.
        """
        display = self.port_var.get().strip()
        if display in self.port_display_to_device:
            return self.port_display_to_device[display]
        if " — " in display:
            return display.split(" — ", 1)[0].strip()
        return display

    def on_port_selected(self, _event=None):
        """Callback appelé après sélection d'un port COM.

        Args:
            _event: Événement Tkinter de sélection non utilisé directement.
        """
        self.validate_form()

    def refresh_ports(self):
        """Rafraîchit la liste des ports série disponibles.

        Les ports STMicroelectronics/ST-LINK sont priorisés automatiquement lorsque
        l'identifiant matériel ou la description le permet.
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
        """Ouvre une boîte de dialogue pour choisir le fichier CSV de sortie."""
        initial = Path(self.csv_path_var.get())
        initialdir = str(initial.parent) if initial.parent.exists() else str(Path.cwd())
        filename = filedialog.asksaveasfilename(
            title="Choisir le fichier CSV",
            initialdir=initialdir,
            initialfile=initial.name,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tous les fichiers", "*.*")],
        )
        if filename:
            self.csv_path_var.set(filename)

    def on_profile_changed(self, _event=None):
        """Applique le profil sélectionné dans la combobox.

        Args:
            _event: Événement Tkinter de sélection non utilisé directement.
        """
        profile_name = self.profile_var.get()
        profile = self.profiles.get(profile_name)
        if profile is not None:
            self.apply_profile(profile)

    def apply_profile(self, profile):
        """Copie les paramètres d'un profil dans les champs de l'interface.

        Args:
            profile: Profil moteur à appliquer.
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
        """Lit et valide la configuration courante de l'interface.

        Returns:
            dict[str, object]: Configuration prête à être utilisée pour l'ouverture du
            port série et l'envoi de la commande ``CFG``.

        Raises:
            ValueError: Si une valeur obligatoire est absente ou incohérente.
        """
        port = self.get_selected_port_device()
        baud = int(self.baud_var.get().strip())
        target_rpm = float(self.speed_rpm_var.get().replace(",", "."))
        speed_hz = float(self.speed_hz_var.get().replace(",", "."))
        pole_pairs = int(self.pole_pairs_var.get().strip())
        iq_limit = float(self.iq_limit_var.get().replace(",", "."))
        hard_limit = float(self.hard_limit_var.get().replace(",", "."))
        accel = float(self.accel_var.get().replace(",", "."))
        datalog_ms = int(self.datalog_ms_var.get().strip())
        ds18b20_ms = int(self.ds18b20_ms_var.get().strip())
        csv_path = self.csv_path_var.get().strip()

        if target_rpm <= 0:
            raise ValueError("La vitesse doit être > 0.")
        if iq_limit <= 0:
            raise ValueError("Iq limite doit être > 0.")
        if hard_limit <= 0:
            raise ValueError("Hard stop doit être > 0.")
        if accel <= 0:
            raise ValueError("L'accélération doit être > 0.")
        if datalog_ms < 1:
            raise ValueError("La période DATA doit être >= 1 ms.")
        if ds18b20_ms < 1:
            raise ValueError("La période DS18B20 doit être >= 1 ms.")
        if not port:
            raise ValueError("Aucun port COM sélectionné.")
        if not csv_path:
            raise ValueError("Aucun fichier CSV sélectionné.")

        return {
            "port": port,
            "baud": baud,
            "target_rpm": target_rpm,
            "iq_limit": iq_limit,
            "hard_limit": hard_limit,
            "accel": accel,
            "datalog_ms": datalog_ms,
            "ds18b20_ms": ds18b20_ms,
            "csv_path": csv_path,
        }

    def set_field_invalid(self, key, invalid=True):
        """Applique le style invalide à un champ de saisie.

        Args:
            key: Identifiant interne du widget dans ``entry_widgets``.
            invalid: ``True`` pour afficher le champ en erreur, ``False`` pour revenir
                au style normal.
        """
        widget = self.entry_widgets.get(key)
        if widget is not None:
            try:
                widget.configure(style="Invalid.TEntry" if invalid else "TEntry")
            except Exception:
                pass

    def set_combo_invalid(self, key, invalid=True):
        """Applique le style invalide à une combobox.

        Args:
            key: Identifiant interne du widget dans ``combo_widgets``.
            invalid: ``True`` pour afficher la combobox en erreur, ``False`` pour
                revenir au style normal.
        """
        widget = self.combo_widgets.get(key)
        if widget is not None:
            try:
                widget.configure(style="Invalid.TCombobox" if invalid else "TCombobox")
            except Exception:
                pass

    def reset_field_styles(self):
        """Réinitialise les styles visuels de tous les champs validables."""
        for key in list(self.entry_widgets.keys()):
            self.set_field_invalid(key, False)
        for key in list(self.combo_widgets.keys()):
            self.set_combo_invalid(key, False)

    def validate_form(self):
        """Valide tous les champs de configuration utilisateur.

        La méthode met à jour les styles visuels des champs, le message de warning, le
        bouton de lancement et le statut global.

        Returns:
            bool: ``True`` si la configuration est exploitable, sinon ``False``.
        """
        self.reset_field_styles()
        warnings = []
        errors = []

        def parse_float(key, var, label, min_value=None, allow_zero=False):
            """Convertit et valide un champ flottant.

            Args:
                key: Identifiant interne du widget à marquer en cas d'erreur.
                var: Variable Tkinter contenant la valeur texte.
                label: Nom lisible utilisé dans les messages d'erreur.
                min_value: Valeur minimale optionnelle.
                allow_zero: Autorise une valeur égale à ``min_value`` si ``True``.

            Returns:
                float | None: Valeur convertie, ou ``None`` si elle est invalide.
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
            """Convertit et valide un champ entier.

            Args:
                key: Identifiant interne du widget à marquer en cas d'erreur.
                var: Variable Tkinter contenant la valeur texte.
                label: Nom lisible utilisé dans les messages d'erreur.
                min_value: Valeur minimale optionnelle.
                allow_zero: Autorise une valeur égale à ``min_value`` si ``True``.

            Returns:
                int | None: Valeur convertie, ou ``None`` si elle est invalide.
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
        target_rpm = parse_float("speed_rpm", self.speed_rpm_var, "Vitesse rpm", 0)
        speed_hz = parse_float("speed_hz", self.speed_hz_var, "Vitesse Hz", 0)
        pole_pairs = parse_int("pole_pairs", self.pole_pairs_var, "Paires de pôles", 0)
        iq_limit = parse_float("iq_limit", self.iq_limit_var, "Iq limite", 0)
        hard_limit = parse_float("hard_limit", self.hard_limit_var, "Hard stop", 0)
        accel = parse_float("accel", self.accel_var, "Accélération", 0)
        datalog_ms = parse_int("datalog_ms", self.datalog_ms_var, "Période DATA", 0)
        ds18b20_ms = parse_int("ds18b20_ms", self.ds18b20_ms_var, "Période DS18B20", 0)

        csv_path = self.csv_path_var.get().strip()
        if not csv_path:
            self.set_field_invalid("csv_path", True)
            errors.append("Aucun fichier CSV sélectionné.")

        if pole_pairs is not None and target_rpm is not None and speed_hz is not None:
            expected_hz = (target_rpm * pole_pairs) / 60.0
            if abs(expected_hz - speed_hz) > max(0.05, abs(expected_hz) * 0.01):
                self.set_field_invalid("speed_hz", True)
                errors.append("La vitesse Hz ne correspond pas au rpm/paires de pôles.")
                warnings.append("La vitesse Hz ne correspond pas exactement au rpm. Elle sera recalculée automatiquement à la prochaine édition.")

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
                self.set_status("Configuration valide", "Prêt à lancer.")
            else:
                self.start_button.configure(state=tk.DISABLED, bg=COLORS["panel_3"], fg=COLORS["muted"])
                self.set_status("Erreur configuration", errors[0])

        self.update_iq_warning_color()
        return is_valid

    def start_run(self):
        """Démarre une session moteur et datalogging.

        La méthode valide la configuration, prépare le fichier CSV, ouvre le port série,
        lance le thread de lecture puis délègue la séquence ``SYNC/CFG/START`` à un
        thread de lancement.
        """
        if self.is_running or self.is_launching:
            return
        if not self.validate_form():
            messagebox.showerror("Configuration invalide", self.status_detail_var.get())
            return

        cfg = self.parse_config()
        csv_path = Path(cfg["csv_path"])
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

        self.start_button.configure(state=tk.DISABLED, bg="#14532D", fg="white")
        self.stop_button.configure(state=tk.NORMAL)
        self.set_status("Lancement", "Synchronisation avec la carte...")
        self.log("Port série ouvert.")

        self.launch_thread = threading.Thread(target=self.launch_sequence_thread, args=(cfg,), daemon=True)
        self.launch_thread.start()

    def launch_sequence_thread(self, cfg):
        """Exécute la séquence UART de démarrage dans un thread secondaire.

        Args:
            cfg: Configuration validée retournée par ``parse_config``.
        """
        try:
            cfg_cmd = (
                f"CFG,{cfg['target_rpm']:.3f},{cfg['iq_limit']:.3f},{cfg['hard_limit']:.3f},"
                f"{cfg['accel']:.3f},{cfg['datalog_ms']},{cfg['ds18b20_ms']}\n"
            )

            self.clear_ack_queue()
            self.send_command("SYNC\n", char_delay=0.002)
            try:
                sync_ok = self.wait_for_ack(
                    "SYNC",
                    timeout=2.0,
                    ignored_errors={"ERR,UNKNOWN_CMD", "ERR,BAD_CFG", "ERR,NO_VALID_CFG"},
                )
                if not sync_ok:
                    self.gui_queue.put(("log", "SYNC non supporté/ignoré par la carte, poursuite avec CFG."))
            except TimeoutError:
                self.gui_queue.put(("log", "Pas de réponse SYNC, poursuite avec CFG."))

            time.sleep(0.1)

            self.clear_ack_queue()
            self.send_command(cfg_cmd, char_delay=0.003)
            self.wait_for_ack("CFG", timeout=5.0)

            time.sleep(0.1)

            self.clear_ack_queue()
            self.send_command("START\n", char_delay=0.002)
            self.wait_for_ack("START", timeout=5.0)

            self.gui_queue.put(("launch_success", None))
        except Exception as exc:
            self.gui_queue.put(("launch_failed", str(exc)))

    def stop_run(self):
        """Demande l'arrêt moteur et lance la séquence ``STOP``."""
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
        """Envoie ``STOP`` et ferme les ressources après réception ou timeout ACK."""
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
        """Ferme proprement le CSV, le port série et remet l'état GUI au repos."""
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
        self.validate_form()
        self.set_status("Arrêté", "Session terminée.")

        if self.close_requested:
            self.destroy()

    def send_command(self, command, char_delay=0.0):
        """Envoie une commande ASCII au firmware STM32.

        Args:
            command: Commande complète à envoyer, généralement terminée par ``
        ``.
            char_delay: Délai optionnel entre deux caractères pour les firmwares qui
                tolèrent mal les rafales UART.

        Raises:
            RuntimeError: Si aucun port série ouvert n'est disponible.
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
        """Vide toutes les réponses ACK/ERR encore en attente."""
        try:
            while True:
                self.ack_queue.get_nowait()
        except queue.Empty:
            pass

    def wait_for_ack(self, name, timeout=5.0, ignored_errors=None):
        """Attend une réponse ``ACK,<name>`` ou une erreur firmware.

        Args:
            name: Nom de commande attendu après ``ACK,``.
            timeout: Durée maximale d'attente en secondes.
            ignored_errors: Ensemble optionnel d'erreurs ``ERR,...`` à considérer comme
                non bloquantes.

        Returns:
            bool: ``True`` si l'ACK attendu est reçu, ``False`` si une erreur ignorée
            est reçue.

        Raises:
            TimeoutError: Si aucune réponse exploitable n'arrive avant le timeout.
            RuntimeError: Si le firmware renvoie une erreur non ignorée.
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
        """Lit le port série en continu depuis un thread secondaire.

        Les lignes complètes sont envoyées au thread GUI via ``gui_queue``. Les réponses
        ``ACK`` et ``ERR`` sont également copiées dans ``ack_queue`` pour les threads de
        séquence.
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
        """Traite les événements produits par les threads secondaires.

        Cette méthode est appelée périodiquement par ``after`` afin que toutes les mises
        à jour Tkinter restent exécutées dans le thread principal.
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
        """Nettoie les espaces autour d'une liste de champs CSV/UART.

        Args:
            fields: Champs texte à nettoyer.

        Returns:
            list[str]: Champs sans espaces de début/fin.
        """
        return [x.strip() for x in fields]

    @staticmethod
    def safe_float(value):
        """Convertit une valeur en flottant fini ou ``NaN``.

        Args:
            value: Valeur texte ou numérique à convertir.

        Returns:
            float: Valeur convertie si elle est finie, sinon ``math.nan``.
        """
        try:
            v = float(str(value).strip().replace(",", "."))
            if math.isfinite(v):
                return v
        except Exception:
            pass
        return math.nan

    def open_csv_with_header(self, columns):
        """Ouvre le fichier CSV après réception de l'en-tête firmware.

        Args:
            columns: Colonnes annoncées par la ligne ``#CSV_HEADER``.
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
        """Force ou planifie l'écriture disque du CSV.

        Args:
            force: ``True`` pour forcer immédiatement le ``flush`` même si le seuil de
                lignes ou de temps n'est pas atteint.
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
        """Analyse une ligne reçue depuis l'UART.

        La méthode route les headers CSV, les lignes ``DATA``, les ACK/ERR et les
        messages de debug. Les données valides sont enregistrées en CSV puis propagées
        vers les cartes live et le graphique.

        Args:
            line: Ligne UART décodée et nettoyée.
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
        """Met à jour les cartes live avec les valeurs d'une ligne DATA.

        La vitesse électrique est volontairement affichée mais pas dataloggée. Elle est
        donc recalculée côté interface à partir de la vitesse mécanique et du nombre de
        paires de pôles configuré.

        Args:
            row: Dictionnaire ``colonne -> valeur`` construit depuis la ligne CSV.
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
        """Retourne la limite Iq configurée, ou ``NaN`` si elle est invalide.

        Returns:
            float: Limite Iq positive, sinon ``math.nan``.
        """
        try:
            value = float(self.iq_limit_var.get().strip().replace(",", "."))
            if math.isfinite(value) and value > 0.0:
                return value
        except Exception:
            pass
        return math.nan

    def update_iq_warning_color(self):
        """Colore la valeur Iq selon son rapprochement avec la limite configurée."""
        label = self.live_value_labels.get("motor_iq_a")
        if label is None:
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
        """Ajoute une ligne de données aux buffers du graphique.

        Args:
            row: Dictionnaire ``colonne -> valeur`` construit depuis la ligne CSV.
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
        """Réinitialise tous les buffers du graphique dynamique."""
        self.plot_x.clear()
        for data in self.plot_data.values():
            data.clear()
        self.plot_t0_s = None
        self.plot_dirty = True
        if MATPLOTLIB_AVAILABLE and self.ax is not None:
            self.redraw_plot(force=True)

    def mark_plot_dirty(self):
        """Marque le graphique comme devant être redessiné."""
        self.plot_dirty = True

    def style_axis(self, ax=None, title=False):
        """Applique le style sombre du tableau de bord à un axe matplotlib."""
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
        """Callback périodique de rafraîchissement du graphique."""
        if self.plot_dirty:
            self.redraw_plot()
        self.after(PLOT_REFRESH_MS, self.redraw_plot_periodic)

    def redraw_plot(self, force=False):
        """Redessine le graphique dynamique si nécessaire.

        Args:
            force: ``True`` pour redessiner même si aucune donnée n'est marquée comme
                modifiée.
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
        """Gère la fermeture de la fenêtre principale.

        Si un essai est en cours, la méthode demande confirmation puis passe par la
        séquence d'arrêt moteur avant de détruire la fenêtre.
        """
        if self.is_running or self.is_launching:
            if not messagebox.askyesno("Quitter", "Un essai est en cours. Arrêter le moteur et quitter ?"):
                return
            self.close_requested = True
            self.stop_run()
        else:
            self.destroy()


if __name__ == "__main__":
    app = MotorDatalogGui()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
