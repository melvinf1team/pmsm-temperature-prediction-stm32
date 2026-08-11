"""Verifie les deux contrats serie du firmware de validation."""

from __future__ import annotations

import argparse
import math
import time

import serial
from serial.tools import list_ports


EXPECTED_COUNTS = {
    "emulator": 55,
    "model": 2,
}


def available_ports() -> str:
    ports = [f"{port.device} ({port.description})" for port in list_ports.comports()]
    return ", ".join(ports) if ports else "aucun port detecte"


def parse_line(raw_line: bytes, expected_count: int = 55) -> list[float]:
    try:
        line = raw_line.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("octets non ASCII recus") from exc

    fields = line.split(";")
    if len(fields) != expected_count:
        preview = line[:120]
        raise ValueError(
            f"{len(fields)} valeurs recues au lieu de {expected_count}: {preview!r}"
        )

    try:
        values = [float(field) for field in fields]
    except ValueError as exc:
        raise ValueError(f"champ non numerique dans: {line[:120]!r}") from exc

    if not all(math.isfinite(value) for value in values):
        raise ValueError("NaN ou inf recu dans le vecteur NanoEdge")

    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controle le contrat UART du Serial Emulator NanoEdge."
    )
    parser.add_argument("--port", help="Port serie de la carte, par exemple COM7.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--lines", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--mode",
        choices=sorted(EXPECTED_COUNTS),
        default="model",
        help="model: D6T/prediction; emulator: vecteur de 55 features.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.port:
        raise SystemExit(f"Utilisez --port. Ports disponibles: {available_ports()}")
    if args.lines <= 0:
        raise SystemExit("--lines doit etre strictement positif")

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
                        "aucune ligne complete recue; verifiez le COM, 115200 bauds "
                        "et le flash du firmware_validation"
                    )
                continue

            values = parse_line(raw_line, expected_count)
            checked += 1
            deadline = time.monotonic() + args.timeout

            if checked <= 3 and args.mode == "model":
                print(
                    f"ligne {checked}: D6T={values[0]:.6f} C, "
                    f"prediction={values[1]:.6f} C, "
                    f"erreur={values[1] - values[0]:+.6f} C"
                )
            elif checked <= 3:
                print(
                    f"ligne {checked}: 55 valeurs, "
                    f"premiere={values[0]:.6f}, derniere={values[-1]:.6f}"
                )

    print(f"Contrat {args.mode} valide sur {checked} lignes.")


if __name__ == "__main__":
    main()