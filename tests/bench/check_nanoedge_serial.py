"""Check both serial contracts of the validation firmware."""

from __future__ import annotations

import argparse
import math
import time

import serial
from serial.tools import list_ports


EXPECTED_COUNTS = {
    "emulator": 55,
    "model": 3,
}

MIN_LOAD_SETPOINT_A = 0.05
MAX_LOAD_SETPOINT_A = 0.25


def available_ports() -> str:
    ports = [f"{port.device} ({port.description})" for port in list_ports.comports()]
    return ", ".join(ports) if ports else "no port detected"


def parse_line(raw_line: bytes, expected_count: int = 55) -> list[float]:
    try:
        line = raw_line.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("non-ASCII bytes received") from exc

    fields = line.split(";")
    if len(fields) != expected_count:
        preview = line[:120]
        raise ValueError(
            f"{len(fields)} values received instead of {expected_count}: {preview!r}"
        )

    try:
        values = [float(field) for field in fields]
    except ValueError as exc:
        raise ValueError(f"nonnumeric field in: {line[:120]!r}") from exc

    if not all(math.isfinite(value) for value in values):
        raise ValueError("NaN or inf received in the NanoEdge vector")

    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the UART contract of the validation firmware."
    )
    parser.add_argument("--port", help="Board serial port, for example COM7.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--lines", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--mode",
        choices=sorted(EXPECTED_COUNTS),
        default="model",
        help=(
            "model: D6T/prediction/TB-200S load command; "
            "emulator: vector of 55 features."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.port:
        raise SystemExit(f"Use --port. Available ports: {available_ports()}")
    if args.lines <= 0:
        raise SystemExit("--lines must be strictly positive")

    deadline = time.monotonic() + args.timeout
    checked = 0
    expected_count = EXPECTED_COUNTS[args.mode]

    with serial.Serial(args.port, args.baud, timeout=0.5) as connection:
        connection.reset_input_buffer()

        while checked < args.lines:
            raw_line = connection.readline()
            if not raw_line:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "no complete line received; check the COM port, 115200 baud, "
                        "and that firmware_validation is flashed"
                    )
                continue

            values = parse_line(raw_line, expected_count)
            if args.mode == "model" and not (
                MIN_LOAD_SETPOINT_A <= values[2] <= MAX_LOAD_SETPOINT_A
            ):
                raise ValueError(
                    "TB-200S setpoint out of range: "
                    f"{values[2]:.6f} A"
                )
            checked += 1
            deadline = time.monotonic() + args.timeout

            if checked <= 3 and args.mode == "model":
                print(
                    f"line {checked}: D6T={values[0]:.6f} C, "
                    f"prediction={values[1]:.6f} C, "
                    f"error={values[1] - values[0]:+.6f} C, "
                    f"load={values[2]:.6f} A"
                )
            elif checked <= 3:
                print(
                    f"line {checked}: 55 values, "
                    f"first={values[0]:.6f}, last={values[-1]:.6f}"
                )

    print(f"{args.mode} contract valid on {checked} lines.")


if __name__ == "__main__":
    main()
