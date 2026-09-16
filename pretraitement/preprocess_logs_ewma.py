"""Preprocess PMSM logs for NanoEdge AI extrapolation.

The script reads CSV files produced by
``datalogging/motor_datalog_gui_dashboard.py``, keeps ``d6t_temp_c`` as the
unchanged first-column target, then builds explanatory variables and their
EWMAs. Reference EWMA spans were set for 2 Hz and are rescaled using the
input file's actual acquisition rate. Paths can be supplied through the
command line, a YAML file, or environment variables via ConfigArgParse.
"""

import os
from pathlib import Path

import configargparse
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATALOGGING_DIR = PROJECT_ROOT / "datalogging"

INPUT_DIR = DATALOGGING_DIR / "logs"
OUTPUT_DIR = SCRIPT_DIR / "logs_processed_ewma"
INPUT_PATTERN = "daq_log_*.csv"

# Fichiers YAML optionnels lus automatiquement par ConfigArgParse s'ils existent.
DEFAULT_CONFIG_FILES = [
    PROJECT_ROOT / "preprocess_ewma.yaml",
    SCRIPT_DIR / "preprocess_ewma.yaml",
]

# Mettre a True pour ecrire les noms de colonnes dans les CSV de sortie.
WRITE_HEADER = False

# Mettre a True si vous voulez garder stm32_time_ms dans les CSV de sortie.
INCLUDE_TIME_MS = False

# Spans optimises pour un datalogging a 2 Hz.
REFERENCE_FREQUENCY_HZ = 2.0
REFERENCE_SPANS = [1320, 3360, 6360, 9480]

TIME_COLUMN = "stm32_time_ms"
TARGET_COLUMN = "d6t_temp_c"

# Colonnes explicatives : la target d6t_temp_c n'est jamais lissée.
FEATURE_INPUT_COLUMNS = [
    "ds18b20_temp_c",
    "motor_ud_v",
    "motor_uq_v",
    "motor_speed_mech_rpm",
    "motor_id_a",
    "motor_iq_a",
]

INPUT_COLUMNS = [TARGET_COLUMN] + FEATURE_INPUT_COLUMNS

DERIVED_COLUMNS = [
    "u_s",
    "i_s",
    "S_el",
    "speed_current",
    "speed_power",
]

EWM_COLUMNS = FEATURE_INPUT_COLUMNS + DERIVED_COLUMNS


def path_from_arg(value):
    """Normalize a path supplied by CLI, configuration, or environment variable."""
    path = Path(os.path.expandvars(str(value))).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def parse_args(argv=None):
    parser = configargparse.ArgParser(
        description="Preprocess PMSM dashboard logs with EWMAs scaled to the acquisition rate.",
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
        "--input-dir",
        type=path_from_arg,
        default=INPUT_DIR,
        env_var="PMSM_PREPROCESS_INPUT_DIR",
        help="Directory containing raw dashboard CSV files.",
    )
    parser.add_argument(
        "--output-dir",
        type=path_from_arg,
        default=OUTPUT_DIR,
        env_var="PMSM_PREPROCESS_OUTPUT_DIR",
        help="Output directory for preprocessed CSV files.",
    )
    parser.add_argument(
        "--pattern",
        default=INPUT_PATTERN,
        env_var="PMSM_PREPROCESS_PATTERN",
        help="Glob pattern for CSV files to process in input-dir.",
    )
    parser.add_argument(
        "--frequency-hz",
        type=float,
        default=None,
        help="Set the acquisition rate instead of deriving it from stm32_time_ms.",
    )
    parser.add_argument(
        "--header",
        dest="write_header",
        action="store_true",
        default=WRITE_HEADER,
        help="Write column names in the output CSV file.",
    )
    parser.add_argument(
        "--no-header",
        dest="write_header",
        action="store_false",
        help="Omit column names from the output CSV file.",
    )
    parser.add_argument(
        "--include-time",
        dest="include_time_ms",
        action="store_true",
        default=INCLUDE_TIME_MS,
        help="Keep the stm32_time_ms column in the output CSV file.",
    )
    parser.add_argument(
        "--no-include-time",
        dest="include_time_ms",
        action="store_false",
        help="Omit the stm32_time_ms column from the output CSV file.",
    )
    args = parser.parse_args(argv)
    args.input_dir = path_from_arg(args.input_dir)
    args.output_dir = path_from_arg(args.output_dir)
    return args


def require_input_directory(input_dir):
    """Validate that the input path exists and is a directory."""
    if not input_dir.exists():
        raise FileNotFoundError(f"Dossier d'entree introuvable: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Le chemin d'entree n'est pas un dossier: {input_dir}")
    return input_dir


def prepare_output_directory(output_dir):
    """Create the output directory unless the path already exists as a file."""
    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(f"Le chemin de sortie n'est pas un dossier: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def detect_acquisition_frequency_hz(df, forced_frequency_hz=None):
    if forced_frequency_hz is not None:
        if forced_frequency_hz <= 0.0:
            raise ValueError("La frequence forcee doit etre strictement positive.")
        return forced_frequency_hz

    if TIME_COLUMN not in df.columns:
        raise ValueError(
            f"Impossible de deduire la frequence: colonne {TIME_COLUMN!r} absente. "
            "Utilisez --frequency-hz pour la forcer."
        )

    time_ms = pd.to_numeric(df[TIME_COLUMN], errors="coerce")
    deltas_ms = time_ms.diff()
    valid_deltas_ms = deltas_ms[np.isfinite(deltas_ms) & (deltas_ms > 0.0)]

    if valid_deltas_ms.empty:
        raise ValueError(
            f"Impossible de deduire la frequence depuis {TIME_COLUMN!r}. "
            "Utilisez --frequency-hz pour la forcer."
        )

    median_period_ms = float(valid_deltas_ms.median())
    return 1000.0 / median_period_ms


def scaled_spans(acquisition_frequency_hz):
    spans = []
    for span in REFERENCE_SPANS:
        scaled_span = int(round(span * acquisition_frequency_hz / REFERENCE_FREQUENCY_HZ))
        spans.append(max(1, scaled_span))
    return spans


def require_columns(df, columns, csv_file):
    missing_columns = [column for column in columns if column not in df.columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"{csv_file.name}: colonnes manquantes: {missing}")


def add_physical_features(df):
    df["u_s"] = np.sqrt(
        df["motor_ud_v"] ** 2
        + df["motor_uq_v"] ** 2
    )

    df["i_s"] = np.sqrt(
        df["motor_id_a"] ** 2
        + df["motor_iq_a"] ** 2
    )

    df["S_el"] = (
        1.5
        * df["u_s"]
        * df["i_s"]
    )

    df["speed_current"] = (
        df["motor_speed_mech_rpm"]
        * df["i_s"]
    )

    df["speed_power"] = (
        df["motor_speed_mech_rpm"]
        * df["S_el"]
    )


def build_ewma_features(df, spans):
    features = {}

    for column in EWM_COLUMNS:
        series = df[column]

        for span in spans:
            ewm = series.ewm(
                span=span,
                adjust=False,
            )

            features[f"{column}_ewma_{span}"] = ewm.mean()

    return pd.DataFrame(features, index=df.index)


def output_columns(spans, include_time_ms):
    columns_to_keep = [TARGET_COLUMN]

    if include_time_ms:
        columns_to_keep.append(TIME_COLUMN)

    for column in EWM_COLUMNS:
        columns_to_keep.append(column)

        for span in spans:
            columns_to_keep.append(f"{column}_ewma_{span}")

    return columns_to_keep


def process_file(csv_file, output_dir, write_header, include_time_ms, forced_frequency_hz):
    print(f"Processing {csv_file.name}")

    df = pd.read_csv(csv_file, sep=";", keep_default_na=False)
    require_columns(df, [TIME_COLUMN] + INPUT_COLUMNS, csv_file)

    for column in [TIME_COLUMN] + FEATURE_INPUT_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    acquisition_frequency_hz = detect_acquisition_frequency_hz(df, forced_frequency_hz)
    spans = scaled_spans(acquisition_frequency_hz)

    print(
        f"  Frequency: {acquisition_frequency_hz:.3f} Hz | "
        f"EWMA spans: {', '.join(str(span) for span in spans)}"
    )

    add_physical_features(df)
    ewma_features = build_ewma_features(df, spans)

    df = pd.concat(
        [
            df,
            ewma_features,
        ],
        axis=1,
    )

    df.replace(
        [np.inf, -np.inf],
        np.nan,
        inplace=True,
    )

    df.fillna(
        0.0,
        inplace=True,
    )

    df_out = df[output_columns(spans, include_time_ms)]
    assert not df_out.drop(columns=[TARGET_COLUMN]).isna().any().any()

    output_file = output_dir / csv_file.name
    df_out.to_csv(
        output_file,
        sep=";",
        index=False,
        header=write_header,
        float_format="%.6f",
    )


def main():
    args = parse_args()
    input_dir = require_input_directory(args.input_dir)
    output_dir = prepare_output_directory(args.output_dir)

    if not args.pattern.strip():
        raise ValueError("Le motif de fichiers CSV ne peut pas etre vide.")

    csv_files = sorted(input_dir.glob(args.pattern))
    if not csv_files:
        raise FileNotFoundError(f"Aucun fichier trouve dans {input_dir} avec le motif {args.pattern!r}")

    for csv_file in csv_files:
        process_file(
            csv_file=csv_file,
            output_dir=output_dir,
            write_header=args.write_header,
            include_time_ms=args.include_time_ms,
            forced_frequency_hz=args.frequency_hz,
        )

    print("Done.")


if __name__ == "__main__":
    main()
