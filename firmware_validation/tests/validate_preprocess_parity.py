"""Compare le pretraitement float32 embarque au pipeline pandas de reference."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_SCRIPT = PROJECT_ROOT / "pretraitement" / "preprocess_logs_ewma.py"
FEATURE_ORDER_FILE = PROJECT_ROOT / "firmware_validation" / "AI_Model" / "feature_order.txt"
DEFAULT_LOGS = PROJECT_ROOT / "datalogging" / "logs"
MAX_SCALED_RELATIVE_ERROR = 5.0e-4


def load_reference_module():
    spec = importlib.util.spec_from_file_location("preprocess_logs_ewma", REFERENCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Impossible de charger {REFERENCE_SCRIPT}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def firmware_feature_names() -> list[str]:
    return [
        line.strip()
        for line in FEATURE_ORDER_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def float32_signals(frame: pd.DataFrame, reference) -> list[np.ndarray]:
    base = {
        column: pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float32)
        for column in reference.FEATURE_INPUT_COLUMNS
    }

    with np.errstate(over="ignore", invalid="ignore"):
        u_s = np.sqrt(
            np.float32(base["motor_ud_v"] * base["motor_ud_v"])
            + np.float32(base["motor_uq_v"] * base["motor_uq_v"])
        )
        i_s = np.sqrt(
            np.float32(base["motor_id_a"] * base["motor_id_a"])
            + np.float32(base["motor_iq_a"] * base["motor_iq_a"])
        )
        apparent_power = np.float32(np.float32(1.5) * u_s * i_s)
        speed_current = np.float32(base["motor_speed_mech_rpm"] * i_s)
        speed_power = np.float32(base["motor_speed_mech_rpm"] * apparent_power)

    return [
        *(base[column] for column in reference.FEATURE_INPUT_COLUMNS),
        u_s,
        i_s,
        apparent_power,
        speed_current,
        speed_power,
    ]


def simulate_embedded(frame: pd.DataFrame, spans: list[int], reference) -> np.ndarray:
    output_columns: list[np.ndarray] = []

    for values in float32_signals(frame, reference):
        output_columns.append(
            np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
        )

        for span in spans:
            alpha = np.float32(2.0 / (span + 1.0))
            old_weight_factor = np.float32(1.0 - alpha)
            mean = np.float32(0.0)
            old_weight = np.float32(0.0)
            initialized = False
            result = np.empty(values.size, dtype=np.float32)

            for index, value in enumerate(values):
                value_is_valid = bool(np.isfinite(value))

                if initialized:
                    old_weight = np.float32(old_weight * old_weight_factor)

                    if value_is_valid:
                        if mean != value:
                            numerator = np.float32(old_weight * mean) + np.float32(alpha * value)
                            mean = np.float32(numerator / np.float32(old_weight + alpha))
                        old_weight = np.float32(1.0)
                elif value_is_valid:
                    mean = value
                    old_weight = np.float32(1.0)
                    initialized = True

                result[index] = mean if initialized else np.float32(0.0)

            output_columns.append(result)

    return np.column_stack(output_columns).astype(np.float64)


def pandas_reference(frame: pd.DataFrame, spans: list[int], reference) -> tuple[list[str], np.ndarray]:
    work = frame.copy()
    for column in reference.FEATURE_INPUT_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")

    reference.add_physical_features(work)
    ewma = reference.build_ewma_features(work, spans)
    work = pd.concat([work, ewma], axis=1)
    names = reference.output_columns(spans, include_time_ms=False)[1:]
    values = (
        work[names]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .to_numpy(dtype=np.float64)
    )
    return names, values


def validate_file(csv_file: Path, reference) -> dict[str, float | int | str]:
    frame = pd.read_csv(csv_file, sep=";", keep_default_na=False)
    frequency_hz = reference.detect_acquisition_frequency_hz(frame)
    spans = reference.scaled_spans(frequency_hz)

    if spans != [6600, 16800, 31800, 47400]:
        raise AssertionError(f"{csv_file.name}: spans inattendus {spans}")

    names, expected = pandas_reference(frame, spans, reference)
    if firmware_feature_names() != names:
        raise AssertionError("L'ordre des features firmware differe du script Python")

    actual = simulate_embedded(frame, spans, reference)
    absolute_error = np.abs(actual - expected)
    scaled_relative_error = absolute_error / np.maximum(np.abs(expected), 1.0)
    maximum_error = float(np.max(scaled_relative_error))

    if maximum_error > MAX_SCALED_RELATIVE_ERROR:
        row, column = np.unravel_index(np.argmax(scaled_relative_error), expected.shape)
        raise AssertionError(
            f"{csv_file.name}: erreur {maximum_error:.6g} sur "
            f"{names[column]} a la ligne {row}"
        )

    return {
        "file": csv_file.name,
        "rows": len(frame),
        "frequency_hz": frequency_hz,
        "max_absolute_error": float(np.max(absolute_error)),
        "max_scaled_relative_error": maximum_error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--logs",
        type=Path,
        default=DEFAULT_LOGS,
        help="Dossier contenant les daq_log_*.csv de reference.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reference = load_reference_module()
    csv_files = sorted(args.logs.resolve().glob("daq_log_*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"Aucun log trouve dans {args.logs}")

    for csv_file in csv_files:
        result = validate_file(csv_file, reference)
        print(
            "{file}: rows={rows}, frequency={frequency_hz:.6f} Hz, "
            "max_abs={max_absolute_error:.6g}, "
            "max_scaled_rel={max_scaled_relative_error:.6g}".format(**result)
        )

    print("Parity validation passed.")


if __name__ == "__main__":
    main()