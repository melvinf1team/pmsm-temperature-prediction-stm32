"""Preprocess PMSM logs for NanoEdge AI extrapolation.

The script reads CSV files produced by
``datalogging/motor_datalog_gui_dashboard.py``, keeps ``d6t_temp_c`` as the
unchanged first-column target, then builds explanatory variables and their
EWMAs. Rows whose target is not a finite number are dropped after the EWMAs
are computed. The default output is the target followed by the 55 model
features; ``stm32_time_ms`` and the TB-200S ``load_setpoint_a`` command can be
added with ``INCLUDE_TIME_MS`` and ``INCLUDE_LOAD_SETPOINT`` or the matching
command-line options. A log without ``load_setpoint_a`` gets an empty value.
Reference EWMA spans were set for 2 Hz and are rescaled using the input file's
actual acquisition rate. Paths can be supplied through the command line, a
YAML file, or environment variables via ConfigArgParse.
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

# Optional YAML files read by ConfigArgParse when they exist.
DEFAULT_CONFIG_FILES = [
    PROJECT_ROOT / "preprocess_ewma.yaml",
    SCRIPT_DIR / "preprocess_ewma.yaml",
]

# Write column names in the output CSV files.
WRITE_HEADER = False

# Keep stm32_time_ms in the output CSV files, right after the target.
INCLUDE_TIME_MS = False

# Keep the TB-200S setpoint load_setpoint_a in the output CSV files, after the
# 55 features. Leave False for files imported into NanoEdge AI.
INCLUDE_LOAD_SETPOINT = False

# Reference spans for 2 Hz data logging.
REFERENCE_FREQUENCY_HZ = 2.0
REFERENCE_SPANS = [1320, 3360, 6360, 9480]

TIME_COLUMN = "stm32_time_ms"
TARGET_COLUMN = "d6t_temp_c"
LOAD_SETPOINT_COLUMN = "load_setpoint_a"

# Explanatory columns; the d6t_temp_c target is never smoothed.
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
    parser.add_argument(
        "--include-load-setpoint",
        dest="include_load_setpoint",
        action="store_true",
        default=INCLUDE_LOAD_SETPOINT,
        help=(
            "Keep the TB-200S load_setpoint_a column, after the 55 features, "
            "in the output CSV file."
        ),
    )
    parser.add_argument(
        "--no-include-load-setpoint",
        dest="include_load_setpoint",
        action="store_false",
        help="Omit the load_setpoint_a column from the output CSV file.",
    )
    args = parser.parse_args(argv)
    args.input_dir = path_from_arg(args.input_dir)
    args.output_dir = path_from_arg(args.output_dir)
    return args


def require_input_directory(input_dir):
    """Validate that the input path exists and is a directory."""
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")
    return input_dir


def prepare_output_directory(output_dir):
    """Create the output directory unless the path already exists as a file."""
    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def detect_acquisition_frequency_hz(df, forced_frequency_hz=None):
    if forced_frequency_hz is not None:
        if forced_frequency_hz <= 0.0:
            raise ValueError("The forced frequency must be strictly positive.")
        return forced_frequency_hz

    if TIME_COLUMN not in df.columns:
        raise ValueError(
            f"Cannot derive the frequency: column {TIME_COLUMN!r} is missing. "
            "Use --frequency-hz to set it."
        )

    time_ms = pd.to_numeric(df[TIME_COLUMN], errors="coerce")
    deltas_ms = time_ms.diff()
    valid_deltas_ms = deltas_ms[np.isfinite(deltas_ms) & (deltas_ms > 0.0)]

    if valid_deltas_ms.empty:
        raise ValueError(
            f"Cannot derive the frequency from {TIME_COLUMN!r}. "
            "Use --frequency-hz to set it."
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
        raise ValueError(f"{csv_file.name}: missing columns: {missing}")


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


def model_feature_columns(spans):
    """Return the ordered 55-feature contract used by the embedded model.

    ``load_setpoint_a`` is not part of this list: it is a piecewise-constant
    command, not a measured signal to smooth, and the embedded model takes
    55 inputs.
    """
    columns = []

    for column in EWM_COLUMNS:
        columns.append(column)

        for span in spans:
            columns.append(f"{column}_ewma_{span}")

    return columns


def output_columns(spans, include_time_ms, include_load_setpoint=False):
    columns_to_keep = [TARGET_COLUMN]

    if include_time_ms:
        columns_to_keep.append(TIME_COLUMN)

    columns_to_keep.extend(model_feature_columns(spans))

    if include_load_setpoint:
        # After the model features so their positions do not change.
        columns_to_keep.append(LOAD_SETPOINT_COLUMN)

    return columns_to_keep


def process_file(
    csv_file,
    output_dir,
    write_header,
    include_time_ms,
    forced_frequency_hz,
    include_load_setpoint=False,
):
    print(f"Processing {csv_file.name}")

    df = pd.read_csv(csv_file, sep=";", keep_default_na=False)
    require_columns(df, [TIME_COLUMN] + INPUT_COLUMNS, csv_file)

    if include_load_setpoint and LOAD_SETPOINT_COLUMN not in df.columns:
        # An absent command is unknown, not a 0 A setpoint.
        df[LOAD_SETPOINT_COLUMN] = np.nan
        print(f"  {LOAD_SETPOINT_COLUMN} missing: column written empty")

    numeric_input_columns = [TIME_COLUMN] + FEATURE_INPUT_COLUMNS
    if LOAD_SETPOINT_COLUMN in df.columns:
        numeric_input_columns.append(LOAD_SETPOINT_COLUMN)

    for column in numeric_input_columns:
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

    columns_to_sanitize = model_feature_columns(spans)
    if include_time_ms:
        columns_to_sanitize = [TIME_COLUMN] + columns_to_sanitize

    # Invalid model features become zero; the load command keeps NaN so an
    # unknown value is not confused with a real 0 A setpoint.
    df[columns_to_sanitize] = (
        df[columns_to_sanitize]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    if include_load_setpoint:
        df[LOAD_SETPOINT_COLUMN] = df[LOAD_SETPOINT_COLUMN].replace(
            [np.inf, -np.inf],
            np.nan,
        )

    df_out = df[
        output_columns(
            spans,
            include_time_ms,
            include_load_setpoint=include_load_setpoint,
        )
    ]
    assert not df_out[columns_to_sanitize].isna().any().any()

    # EWMAs cover every sample, as on the board; only the output rows need a valid target.
    valid_target = np.isfinite(pd.to_numeric(df_out[TARGET_COLUMN], errors="coerce"))
    dropped_rows = int((~valid_target).sum())
    if dropped_rows:
        print(f"  Rows dropped (invalid {TARGET_COLUMN}): {dropped_rows}")
        df_out = df_out[valid_target]

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
        raise ValueError("The CSV file pattern cannot be empty.")

    csv_files = sorted(input_dir.glob(args.pattern))
    if not csv_files:
        raise FileNotFoundError(f"No file found in {input_dir} matching {args.pattern!r}")

    for csv_file in csv_files:
        process_file(
            csv_file=csv_file,
            output_dir=output_dir,
            write_header=args.write_header,
            include_time_ms=args.include_time_ms,
            forced_frequency_hz=args.frequency_hz,
            include_load_setpoint=args.include_load_setpoint,
        )

    print("Done.")


if __name__ == "__main__":
    main()
