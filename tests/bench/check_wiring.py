"""Check the test-bench wiring through the acquisition firmware.

The script talks to ``firmware_acquisition`` over USART1. With the motor
stopped, the ``DIAG`` command probes the D6T and DS18B20 lines, the TB-200S
command network on PA5, and the power-stage supply. Each failure is turned into
probable causes: missing wire, wire on a neighbouring Morpho pin, swapped or
shorted lines, missing pull-up, missing supply, and so on.

``--motor`` additionally runs the motor at low speed to check the U/V/W phases,
the d/q telemetry, and the brake response to a 0.05 A -> 0.25 A step.

Usage::

    python tests/bench/check_wiring.py --port COM5
    python tests/bench/check_wiring.py --port COM5 --motor
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

import serial
from serial.tools import list_ports


BAUD_RATE = 115200
SERIAL_READ_TIMEOUT_S = 0.05
COMMAND_CHAR_DELAY_S = 0.002
COMMAND_TIMEOUT_S = 3.0
DIAG_TIMEOUT_S = 20.0
STREAM_PERIOD_MS = 100
STREAM_SECONDS = 3.0

SCL_PIN = "PB6"
SDA_PIN = "PB9"
DQ_PIN = "PG6"
DAC_PIN = "PA5"

# Morpho position of every MCU pin probed by the firmware.
PIN_HEADERS = {
    "PB6": "CN10-27",
    "PB9": "CN10-24",
    "PG6": "CN7-1",
    "PA5": "CN7-32",
    "PD0": "CN7-2",
    "PD3": "CN7-17",
    "PA4": "CN7-31",
    "PE10": "CN7-34",
    "PD15": "CN10-22",
    "PE9": "CN10-23",
    "PD7": "CN10-25",
    "PA12": "CN10-28",
    "PA6": "CN10-29",
    "PA11": "CN10-30",
}

MIN_VBUS_V = 8.0
DAC_VREF_MV = 3300.0
DAC_FULL_SCALE = 4095.0
RC_MIN_RISE_US = 5000
EXTERNAL_VOLTAGE_ZERO_MV = 300
EXTERNAL_VOLTAGE_PULLDOWN_MV = 500
RESISTIVE_LOAD_MV = 1000
NO_ADJ_LOAD_MV = 3150
D6T_RANGE_C = (-20.0, 100.0)
DS18B20_RANGE_C = (-20.0, 85.0)
DS18B20_POWER_ON_CENTI = 8500

MIN_LOAD_A = 0.05
MAX_LOAD_A = 0.25
SPEED_REACHED_RATIO = 0.9
SPEED_TRACKING_TOLERANCE = 0.15
BRAKE_MIN_DELTA_IQ_A = 0.15
BRAKE_MIN_SPEED_DROP = 0.05

CSV_COLUMNS = (
    "stm32_time_ms",
    "d6t_temp_c",
    "ds18b20_temp_c",
    "motor_ud_v",
    "motor_uq_v",
    "motor_speed_mech_rpm",
    "motor_id_a",
    "motor_iq_a",
    "load_setpoint_a",
)

VALIDATION_FIRMWARE_LINE = re.compile(r"^-?\d+(\.\d+)?(;-?\d+(\.\d+)?){1,54}$")

MCSDK_FAULTS = {
    0x0001: "FOC loop overrun (firmware issue, not wiring)",
    0x0002: "bus overvoltage: supply too high, or braking energy fed back without absorption",
    0x0004: "bus undervoltage: power supply off, not connected to Vbat/GND of the STDES-LVHP01, or current-limited",
    0x0008: "power-stage overtemperature, or invalid power-board temperature reading",
    0x0010: "startup failure: a U/V/W phase disconnected, rotor blocked, or load too high at startup",
    0x0020: "speed feedback lost: a U/V/W phase disconnected or intermittent, or rotor stalled",
    0x0040: "hardware overcurrent: phases shorted together or to the supply, rotor blocked, or damaged power stage",
    0x0080: "firmware software error",
    0x0100: "not enough phase-current samples (PWM / current-sensing configuration)",
    0x0200: "software overcurrent: short between phases or blocked rotor",
    0x0400: "gate-driver protection: power-board supply problem or short circuit",
}

APP_FAULT_REASONS = {
    "STARTUP_TIMEOUT": "the motor never reached RUN within 5 s: a U/V/W phase is probably disconnected, or the rotor is blocked",
    "HARD_OVERCURRENT": "total current above the configured limit: phase short, blocked rotor, or brake load too high",
    "OVERSPEED": "estimated speed far above the target: unstable observer, typically an intermittent phase connection",
    "NONFINITE_CURRENT": "the current measurement became invalid: current sensing on the power board",
    "NONFINITE_SPEED": "the speed estimate became invalid: observer lost, typically a phase connection problem",
    "LOAD_DAC": "the DAC refused the TB-200S setpoint",
    "START_REJECTED": "MCSDK refused the start command: power stage not ready",
    "MCSDK_NOT_IDLE": "MCSDK did not return to IDLE within 1 s after a stop or a fault acknowledgement",
}


@dataclass(frozen=True)
class PinLevels:
    """Levels read on a pin with the internal pull-down, then the internal pull-up."""

    name: str
    role: str
    pd: bool
    pu: bool

    @property
    def state(self) -> str:
        if self.pd and self.pu:
            return "pulled_up"
        if self.pu:
            return "floating"
        if not self.pd:
            return "pulled_low"
        return "unstable"


@dataclass
class DiagReport:
    """Parsed ``DIAG`` answer of the firmware."""

    power: dict[str, str] = field(default_factory=dict)
    pins: dict[str, PinLevels] = field(default_factory=dict)
    shorts: dict[frozenset, bool] = field(default_factory=dict)
    i2c_acks: dict[tuple[str, str], bool] = field(default_factory=dict)
    i2c_devices: list[int] = field(default_factory=list)
    onewire: dict[str, bool] = field(default_factory=dict)
    ds18b20: Optional[dict[str, str]] = None
    dac: Optional[dict[str, str]] = None
    rc_rise_us: dict[str, Optional[int]] = field(default_factory=dict)
    d6t: Optional[dict[str, str]] = None
    complete: bool = False


@dataclass
class CheckResult:
    module: str
    status: str
    summary: str
    causes: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Sample:
    speed_rpm: float
    id_a: float
    iq_a: float
    ud_v: float
    uq_v: float
    load_a: float


@dataclass
class MotorTestResult:
    target_rpm: float
    started: bool = False
    reached_speed: bool = False
    error: Optional[str] = None
    status: Optional[dict[str, str]] = None
    fault_phase: Optional[str] = None
    windows: dict[str, list[Sample]] = field(default_factory=dict)
    direction_ok: Optional[bool] = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_fields(parts: Iterable[str]) -> dict[str, str]:
    """Parse ``key=value`` fields."""
    fields = {}
    for part in parts:
        key, separator, value = part.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def field_int(fields: Optional[dict[str, str]], key: str) -> Optional[int]:
    """Return an integer field, accepting the ``0x`` hexadecimal prefix."""
    if not fields or key not in fields:
        return None
    value = fields[key].strip().lower()
    try:
        if value.startswith(("0x", "-0x")):
            return int(value, 16)
        return int(value, 10)
    except ValueError:
        return None


def parse_float(value: Optional[str]) -> float:
    try:
        return float(value) if value is not None else math.nan
    except ValueError:
        return math.nan


def parse_diag_lines(lines: Iterable[str]) -> DiagReport:
    """Build a :class:`DiagReport` from the ``DIAG,...`` lines of the firmware."""
    report = DiagReport()
    for raw_line in lines:
        line = raw_line.strip()
        if not line.startswith("DIAG,"):
            continue
        parts = line.split(",")
        kind = parts[1] if len(parts) > 1 else ""
        fields = parse_fields(parts[2:])

        if kind == "POWER":
            report.power = fields
        elif kind == "PIN" and "name" in fields:
            report.pins[fields["name"]] = PinLevels(
                fields["name"],
                fields.get("role", ""),
                fields.get("pd") == "1",
                fields.get("pu") == "1",
            )
        elif kind == "SHORT" and {"a", "b"} <= fields.keys():
            report.shorts[frozenset((fields["a"], fields["b"]))] = fields.get("shorted") == "1"
        elif kind == "I2C" and {"scl", "sda"} <= fields.keys():
            report.i2c_acks[(fields["scl"], fields["sda"])] = fields.get("ack") == "1"
        elif kind == "I2C_SCAN":
            report.i2c_devices = [
                int(address, 16) for address in fields.get("devices", "").split(":") if address
            ]
        elif kind == "OW" and "pin" in fields:
            report.onewire[fields["pin"]] = fields.get("presence") == "1"
        elif kind == "DS18B20":
            report.ds18b20 = fields
        elif kind == "DAC":
            report.dac = fields
        elif kind == "RC" and "pin" in fields:
            rise_us = field_int(fields, "rise_us")
            report.rc_rise_us[fields["pin"]] = None if rise_us is None or rise_us < 0 else rise_us
        elif kind == "D6T":
            report.d6t = fields
        elif kind == "END":
            report.complete = True
    return report


def header(pin: str) -> str:
    return f"{PIN_HEADERS.get(pin, '?')} ({pin})"


def decode_mcsdk_faults(mask: Optional[int]) -> list[str]:
    if not mask:
        return []
    return [f"MCSDK fault 0x{bit:04X}: {text}" for bit, text in MCSDK_FAULTS.items() if mask & bit]


# ---------------------------------------------------------------------------
# Diagnosis rules (motor stopped)
# ---------------------------------------------------------------------------


def neighbor_hints(report: DiagReport) -> list[str]:
    """Explain signals found on free pins next to the expected ones."""
    hints = []
    for name, levels in report.pins.items():
        if levels.role != "NEIGHBOR":
            continue
        if levels.state == "pulled_up":
            hints.append(
                f"A line pulled up to 3.3 V was found on {header(name)}: "
                "a sensor signal wire is probably plugged there instead of its own pin."
            )
        elif levels.state == "pulled_low":
            hints.append(
                f"A load to ground was found on {header(name)}: a GND, supply, or "
                "TB-200S wire is probably plugged there."
            )
    return hints


def sensor_rail_missing(report: DiagReport) -> bool:
    """All three sensor lines floating means their common 3.3 V pull-up rail is absent."""
    lines = [report.pins.get(pin) for pin in (SCL_PIN, SDA_PIN, DQ_PIN)]
    return all(levels is not None and levels.state == "floating" for levels in lines)


RAIL_CAUSE = (
    "The 3.3 V supply of the sensor plaque is missing: 3V3 wire not on CN7-12, "
    "or the plaque harness is unplugged."
)


def diagnose_power(report: DiagReport, motor_requested: bool) -> CheckResult:
    name = "Power stage supply"
    vbus_mv = field_int(report.power, "vbus_mv")
    if vbus_mv is None:
        return CheckResult(name, "SKIP", "No bus-voltage reading.")

    vbus_v = vbus_mv / 1000.0
    occurred = decode_mcsdk_faults(field_int(report.power, "faults_occurred"))
    if vbus_v < MIN_VBUS_V:
        return CheckResult(
            name,
            "FAIL" if motor_requested else "WARN",
            f"Bus voltage {vbus_v:.1f} V: the STDES-LVHP01 power stage is not supplied.",
            [
                "Motor power supply off, current-limited, or not connected to Vbat/GND of the STDES-LVHP01.",
                "Supply polarity reversed, or blown fuse on the supply line.",
            ],
            ["Measure the voltage between Vbat and GND on the STDES-LVHP01 input."],
        )
    if occurred:
        return CheckResult(
            name,
            "WARN",
            f"Bus voltage {vbus_v:.1f} V; MCSDK faults latched since the last start.",
            occurred,
        )
    return CheckResult(name, "OK", f"Bus voltage {vbus_v:.1f} V.")


def diagnose_d6t(report: DiagReport) -> CheckResult:
    name = "D6T infrared sensor"
    scl = report.pins.get(SCL_PIN)
    sda = report.pins.get(SDA_PIN)
    if scl is None or sda is None:
        return CheckResult(name, "SKIP", "No DIAG data for PB6/PB9.")

    read_ok = report.d6t is not None and report.d6t.get("read") == "1"
    normal_ack = report.i2c_acks.get((SCL_PIN, SDA_PIN), False)

    if normal_ack and read_ok:
        temp_c = parse_float(report.d6t.get("temp_c"))
        summary = (
            f"Answers at 0x0A with SCL on {header(SCL_PIN)} and SDA on "
            f"{header(SDA_PIN)}; T = {temp_c:.1f} degC."
        )
        if not D6T_RANGE_C[0] <= temp_c <= D6T_RANGE_C[1]:
            return CheckResult(
                name,
                "WARN",
                summary,
                ["Implausible temperature: check that the selected pixel faces the motor."],
            )
        return CheckResult(name, "OK", summary)

    if normal_ack:
        causes = []
        if "floating" in (scl.state, sda.state):
            causes.append("A 4.7 kOhm pull-up to 3V3 is missing on SCL or SDA.")
        causes += [
            "D6T supply below 4.5 V: check 5 V between D6T pins 2 and 1 (CN7-18).",
            "Harness too long or routed along the motor phases.",
        ]
        return CheckResult(
            name,
            "FAIL",
            "The D6T acknowledges its address but its frames are rejected (PEC or range error).",
            causes,
        )

    found = [pair for pair, ack in report.i2c_acks.items() if ack]
    if found:
        scl_found, sda_found = found[0]
        if (scl_found, sda_found) == (SDA_PIN, SCL_PIN):
            return CheckResult(
                name,
                "FAIL",
                "SDA and SCL are swapped.",
                ["The SCL wire is on CN10-24 and the SDA wire on CN10-27, or D6T pins 3 and 4 are crossed."],
                ["Connect D6T SCL (pin 4) to CN10-27 (PB6) and SDA (pin 3) to CN10-24 (PB9)."],
            )
        return CheckResult(
            name,
            "FAIL",
            f"The D6T answers with SCL on {header(scl_found)} and SDA on {header(sda_found)}.",
            ["At least one D6T wire is plugged into the wrong Morpho pin."],
            ["Connect D6T SCL to CN10-27 (PB6) and SDA to CN10-24 (PB9)."],
        )

    shorted = report.shorts.get(frozenset((SCL_PIN, SDA_PIN)), False)
    if shorted and scl.state == "pulled_up" and sda.state == "pulled_up":
        return CheckResult(
            name,
            "FAIL",
            "SCL and SDA are shorted together.",
            ["Solder bridge between the SCL and SDA pads, or both wires on the same pin."],
            ["With the power off, check that SCL and SDA are not continuous."],
        )

    low_lines = [label for label, levels in (("SCL", scl), ("SDA", sda)) if levels.state == "pulled_low"]
    if low_lines:
        return CheckResult(
            name,
            "FAIL",
            f"{' and '.join(low_lines)} held at 0 V.",
            [
                "D6T without 5 V supply: an unpowered D6T clamps SDA/SCL to ground through its protection diodes.",
                "Line shorted to GND, or wire plugged into a GND pin.",
                "Pull-up resistor connected to GND instead of 3V3.",
            ],
            ["Measure 5 V between D6T pin 2 (VCC) and pin 1 (GND); check CN7-18 and CN7-20."],
        )

    floating = [label for label, levels in (("SCL", scl), ("SDA", sda)) if levels.state == "floating"]
    if floating:
        expected = {"SCL": "CN10-27", "SDA": "CN10-24"}
        causes = [RAIL_CAUSE] if sensor_rail_missing(report) else []
        causes += [
            f"{label} wire not connected to {expected[label]}, or plugged into another pin." for label in floating
        ]
        causes.append("4.7 kOhm pull-up resistor to 3V3 not soldered.")
        causes += neighbor_hints(report)
        return CheckResult(
            name,
            "FAIL",
            f"No pull-up on {' and '.join(floating)}.",
            causes,
            ["Check the continuity from the D6T pins to CN10-27 (SCL) and CN10-24 (SDA)."],
        )

    causes = [
        "D6T supply missing: 5 V (CN7-18) or GND (CN7-20) not connected.",
        "D6T connector unplugged or pin order wrong (1 GND, 2 VCC, 3 SDA, 4 SCL).",
        "Damaged sensor.",
    ]
    others = [address for address in report.i2c_devices if address != 0x0A]
    if others:
        causes.insert(0, "Another I2C device answers at " + ", ".join(f"0x{a:02X}" for a in others) + ".")
    return CheckResult(name, "FAIL", "SCL and SDA are correct but the D6T does not answer.", causes)


def diagnose_ds18b20(report: DiagReport) -> CheckResult:
    name = "DS18B20 ambient sensor"
    dq = report.pins.get(DQ_PIN)
    if dq is None:
        return CheckResult(name, "SKIP", "No DIAG data for PG6.")

    if report.onewire.get(DQ_PIN, False):
        data = report.ds18b20 or {}
        temp_centi = field_int(data, "temp_centi")
        if data.get("power") == "parasite":
            return CheckResult(
                name,
                "FAIL",
                "The DS18B20 answers but its VDD pin is not supplied (parasite-power mode).",
                ["VDD (TO-92 pin 3) not connected to 3V3, or the 3V3 wire of the plaque is missing."],
                ["Connect DS18B20 VDD to 3V3."],
            )
        if data.get("crc") != "1" or temp_centi is None:
            causes = []
            if dq.state != "pulled_up":
                causes.append("No external 4.7 kOhm pull-up between DQ and 3V3: the MCU internal pull-up is too weak.")
            causes.append("Long or unshielded cable routed along the motor phases.")
            return CheckResult(name, "FAIL", "The DS18B20 answers but its data is corrupted.", causes)
        temp_c = temp_centi / 100.0
        if temp_centi == DS18B20_POWER_ON_CENTI:
            return CheckResult(
                name,
                "WARN",
                "Power-on value 85.00 degC: the temperature conversion did not complete.",
                ["Weak or intermittent VDD supply."],
            )
        if dq.state != "pulled_up":
            return CheckResult(
                name,
                "WARN",
                f"Answers on {header(DQ_PIN)} (T = {temp_c:.2f} degC) but the external pull-up is missing.",
                ["4.7 kOhm resistor between DQ and 3V3 not soldered."],
            )
        if not DS18B20_RANGE_C[0] <= temp_c <= DS18B20_RANGE_C[1]:
            return CheckResult(name, "WARN", f"Implausible temperature {temp_c:.2f} degC.")
        return CheckResult(name, "OK", f"Answers on {header(DQ_PIN)}; T = {temp_c:.2f} degC.")

    elsewhere = [pin for pin, present in report.onewire.items() if present and pin != DQ_PIN]
    if elsewhere:
        return CheckResult(
            name,
            "FAIL",
            f"The DS18B20 answers on {header(elsewhere[0])} instead of {header(DQ_PIN)}.",
            ["The DQ wire is plugged into the wrong Morpho pin, or swapped with a D6T wire."],
            ["Connect DS18B20 DQ to CN7-1 (PG6)."],
        )

    if dq.state == "pulled_low":
        return CheckResult(
            name,
            "FAIL",
            "DQ is held at 0 V.",
            [
                "DQ shorted to GND, or the DQ wire plugged into a GND pin.",
                "Sensor pins inverted (TO-92: 1 GND, 2 DQ, 3 VDD).",
                "Damaged sensor.",
            ],
        )
    if dq.state == "floating":
        causes = [RAIL_CAUSE] if sensor_rail_missing(report) else []
        causes += [
            "DQ wire not connected to CN7-1, or plugged into another pin.",
            "4.7 kOhm pull-up resistor between DQ and 3V3 not soldered.",
        ]
        causes += neighbor_hints(report)
        return CheckResult(name, "FAIL", "DQ has no pull-up.", causes)
    return CheckResult(
        name,
        "FAIL",
        "The DQ line is correct but no DS18B20 answers.",
        [
            "DS18B20 GND not connected (CN7-20).",
            "Pin order wrong (TO-92: 1 GND, 2 DQ, 3 VDD); for a cabled probe, check its colour code.",
            "VDD and GND swapped (the sensor heats up), or damaged sensor.",
        ],
    )


def diagnose_tb200s_wiring(report: DiagReport) -> CheckResult:
    name = "TB-200S command (PA5 -> 1 kOhm -> ADJ, 4.7 uF)"
    dac = report.dac
    if dac is None:
        return CheckResult(name, "SKIP", "No DIAG data for PA5.")

    adc_ok = dac.get("adc") == "1"
    rise_us = field_int(dac, "rise_us")
    never_rose = rise_us is None or rise_us < 0
    v_drive = field_int(dac, "v_drive_mv") or 0
    v_hold = field_int(dac, "v_hold_mv") or 0
    v_zero = field_int(dac, "v_zero_mv") or 0
    v_pu_end = field_int(dac, "v_pu_end_mv") or 0
    v_pd_end = field_int(dac, "v_pd_end_mv") or 0
    expected_mv = (field_int(dac, "drive_code") or 0) * DAC_VREF_MV / DAC_FULL_SCALE

    if adc_ok and (v_zero > EXTERNAL_VOLTAGE_ZERO_MV or v_pd_end > EXTERNAL_VOLTAGE_PULLDOWN_MV):
        return CheckResult(
            name,
            "FAIL",
            f"External voltage on ADJ ({max(v_zero, v_pd_end)} mV while the DAC is at 0 V).",
            [
                "TB-200S local adjustment link still fitted: the internal potentiometer drives ADJ.",
                "ADJ connected to the TB-200S +10V terminal, or the ADJ wire is on a supply pin.",
            ],
            [
                "Disconnect the ADJ wire now: PA5 does not tolerate more than 3.3 V.",
                "Remove the TB-200S local control link and leave its +10V terminal unconnected.",
            ],
        )

    if adc_ok and expected_mv > 0 and v_drive < 0.5 * expected_mv:
        return CheckResult(
            name,
            "FAIL",
            f"The DAC cannot drive PA5 ({v_drive} mV instead of about {expected_mv:.0f} mV).",
            [
                "PA5 shorted to GND: wire from CN7-32 on a GND net, or C1 placed directly on PA5 without R1.",
            ],
        )

    if not never_rose and rise_us < RC_MIN_RISE_US:
        # Read right after releasing the pull-up: an open pin stays high, a resistor pulls it down at once.
        if adc_ok and v_pu_end < RESISTIVE_LOAD_MV:
            return CheckResult(
                name,
                "FAIL",
                "Resistive load on PA5 but no 4.7 uF capacitor.",
                ["C1 (4.7 uF between ADJ and GND) missing, not soldered, or open."],
            )
        causes = []
        for pin, rise in report.rc_rise_us.items():
            if rise is None or rise >= RC_MIN_RISE_US:
                causes.append(f"The R1/C1 network was found on {header(pin)}: the wire is on the wrong pin.")
        causes += [
            "Wire not connected to CN7-32.",
            "Solder bridge SB91 open on the B-G473E-ZEST1S: CN7-32 is then not connected to PA5.",
            "R1 (1 kOhm) not soldered.",
        ]
        return CheckResult(
            name,
            "FAIL",
            f"Nothing is connected to {header(DAC_PIN)}.",
            causes,
            ["Check the continuity between PA5 and CN7-32, then between CN7-32 and R1."],
        )

    # C1 keeps its charge when the DAC output is released; a purely resistive path does not.
    if never_rose and adc_ok and v_drive > 0 and v_hold < 0.3 * v_drive:
        return CheckResult(
            name,
            "FAIL",
            "ADJ is held at 0 V by a resistive path behind the 1 kOhm resistor.",
            [
                "ADJ shorted to GND: C1 reversed or bridged by solder, or ADJ wire on a GND terminal.",
                "C1 missing while the TB-200S ADJ input has a low impedance.",
            ],
        )

    summary = (
        "R1/C1 network detected on PA5 (pin held low by the ADJ load)."
        if never_rose
        else f"R1/C1 network detected on PA5 (rise time {rise_us / 1000:.0f} ms)."
    )
    causes = []
    if adc_ok and v_pu_end >= NO_ADJ_LOAD_MV:
        causes.append("No load seen at ADJ: the TB-200S input may not be connected; --motor checks the brake response.")
    if not adc_ok:
        causes.append("ADC readback unavailable: only the timing test was used.")
    return CheckResult(name, "OK", summary, causes)


# ---------------------------------------------------------------------------
# Diagnosis rules (data stream and running motor)
# ---------------------------------------------------------------------------


def row_float(row: dict[str, str], column: str) -> float:
    return parse_float(row.get(column))


def diagnose_stream(header_columns: list[str], rows: Optional[list[dict[str, str]]], seconds: float) -> CheckResult:
    name = "Logging stream (ACQ_START)"
    if rows is None:
        return CheckResult(name, "FAIL", "ACQ_START was not acknowledged.")
    missing = [column for column in CSV_COLUMNS if column not in header_columns]
    if missing:
        return CheckResult(name, "WARN", "The firmware header lacks " + ", ".join(missing) + ".")
    expected_rows = seconds * 1000.0 / STREAM_PERIOD_MS
    if len(rows) < 0.6 * expected_rows:
        return CheckResult(
            name,
            "FAIL",
            f"{len(rows)} DATA rows in {seconds:.0f} s instead of about {expected_rows:.0f}.",
            ["Unstable USB link, or another program reading the same COM port."],
        )
    return CheckResult(name, "OK", f"{len(rows)} DATA rows in {seconds:.0f} s with the 9 expected columns.")


def diagnose_sensors_from_stream(rows: Optional[list[dict[str, str]]]) -> list[CheckResult]:
    """Coarse sensor checks for firmware without the DIAG command."""
    if not rows:
        return []
    d6t = [row_float(row, "d6t_temp_c") for row in rows]
    ds = [row_float(row, "ds18b20_temp_c") for row in rows]
    results = []
    if all(math.isnan(value) for value in d6t):
        results.append(
            CheckResult(
                "D6T infrared sensor",
                "FAIL",
                "d6t_temp_c stays NaN.",
                [
                    "SCL/SDA not on CN10-27/CN10-24, swapped, or without 4.7 kOhm pull-ups to 3V3.",
                    "D6T 5 V (CN7-18) or GND (CN7-20) missing.",
                ],
            )
        )
    else:
        results.append(CheckResult("D6T infrared sensor", "OK", f"d6t_temp_c = {d6t[-1]:.1f} degC."))
    if all(math.isnan(value) for value in ds):
        results.append(
            CheckResult(
                "DS18B20 ambient sensor",
                "FAIL",
                "ds18b20_temp_c stays NaN.",
                [
                    "DQ not on CN7-1, or no 4.7 kOhm pull-up to 3V3.",
                    "DS18B20 VDD or GND missing, or pin order wrong.",
                ],
            )
        )
    else:
        results.append(CheckResult("DS18B20 ambient sensor", "OK", f"ds18b20_temp_c = {ds[-1]:.2f} degC."))
    return results


def describe_fault(status: Optional[dict[str, str]]) -> list[str]:
    if not status:
        return []
    causes = []
    reason = status.get("fault_reason", "NONE")
    if reason in APP_FAULT_REASONS:
        causes.append(f"Firmware stop reason {reason}: {APP_FAULT_REASONS[reason]}.")
    causes += decode_mcsdk_faults(field_int(status, "faults_occurred"))
    return causes


def mean(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.fmean(finite) if finite else math.nan


def stdev(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.pstdev(finite) if len(finite) > 1 else 0.0


def diagnose_motor(result: MotorTestResult) -> CheckResult:
    name = "Motor phases U/V/W and d/q telemetry"
    if not result.started:
        causes = describe_fault(result.status) or [
            "Power stage not supplied, or a protection is latched (see the power-stage line)."
        ]
        return CheckResult(name, "FAIL", f"START refused ({result.error or 'no answer'}).", causes)

    if not result.reached_speed:
        causes = describe_fault(result.status) or [
            "A U/V/W phase is disconnected or intermittent.",
            "Rotor blocked mechanically, or brake already engaged.",
        ]
        return CheckResult(
            name,
            "FAIL",
            f"The motor did not reach {result.target_rpm:.0f} rpm.",
            causes,
            ["With the power off, check that U, V, W are tightly connected to VoutU/V/W of the STDES-LVHP01."],
        )

    base = result.windows.get("base", [])
    if not base:
        return CheckResult(name, "FAIL", "No telemetry received while the motor was running.")

    speed = [sample.speed_rpm for sample in base]
    iq = [sample.iq_a for sample in base]
    id_values = [sample.id_a for sample in base]
    us = [math.hypot(sample.ud_v, sample.uq_v) for sample in base]
    speed_mean, iq_mean, iq_std = mean(speed), mean(iq), stdev(iq)
    summary = (
        f"{speed_mean:.0f} rpm (target {result.target_rpm:.0f}), Iq = {iq_mean:.2f} +/- {iq_std:.2f} A, "
        f"Id = {mean(id_values):.2f} A, |Vs| = {mean(us):.1f} V."
    )

    if all(abs(value) < 1e-6 for value in speed + iq + us):
        return CheckResult(name, "FAIL", "RUN reached but the d/q telemetry stays at zero.", ["Firmware telemetry issue."])
    if result.direction_ok is False:
        return CheckResult(
            name,
            "FAIL",
            "The motor turns in the wrong direction.",
            ["Two of the U/V/W phases are swapped."],
            ["Swap two phase wires on VoutU/V/W."],
        )

    causes = []
    if abs(speed_mean - result.target_rpm) > SPEED_TRACKING_TOLERANCE * result.target_rpm:
        causes.append("Speed does not follow the target: intermittent phase connection or excessive load.")
    if iq_std > max(0.5, 0.6 * abs(iq_mean)):
        causes.append("Iq fluctuates strongly: loose U/V/W connection or mechanical binding.")
    if causes:
        return CheckResult(name, "WARN", summary, causes)
    return CheckResult(name, "OK", summary)


def diagnose_brake(result: MotorTestResult, wiring: CheckResult) -> CheckResult:
    name = "TB-200S brake response"
    if not result.reached_speed:
        return CheckResult(name, "SKIP", "The motor test did not reach steady speed.")

    reason = (result.status or {}).get("fault_reason", "NONE")
    if result.fault_phase == "brake" and reason == "HARD_OVERCURRENT":
        return CheckResult(
            name,
            "WARN",
            "The brake reacts but 0.25 A stops the motor on overcurrent.",
            ["Brake torque at 0.25 A exceeds the configured --iq-limit/--hard-limit."],
        )

    base = result.windows.get("base", []) + result.windows.get("release", [])
    braked = result.windows.get("brake", [])
    if not base or not braked:
        return CheckResult(name, "SKIP", "Incomplete measurement windows.", describe_fault(result.status))

    iq_base = mean([sample.iq_a for sample in base])
    iq_brake = mean([sample.iq_a for sample in braked])
    speed_base = mean([sample.speed_rpm for sample in base])
    speed_brake = mean([sample.speed_rpm for sample in braked])
    load_brake = mean([sample.load_a for sample in braked])
    delta_iq = iq_brake - iq_base
    speed_drop = (speed_base - speed_brake) / speed_base if speed_base > 0 else 0.0
    summary = (
        f"Iq {iq_base:.2f} A -> {iq_brake:.2f} A (delta {delta_iq:+.2f} A), "
        f"speed drop {speed_drop * 100:.1f} % with load_setpoint_a = {load_brake:.3f} A."
    )

    if math.isfinite(load_brake) and abs(load_brake - MAX_LOAD_A) > 0.01:
        return CheckResult(name, "FAIL", "The firmware did not apply the 0.25 A setpoint.", [summary])
    if delta_iq >= max(BRAKE_MIN_DELTA_IQ_A, 0.1 * abs(iq_base)) or speed_drop >= BRAKE_MIN_SPEED_DROP:
        return CheckResult(name, "OK", summary)

    if wiring.status == "FAIL":
        causes = [f"The command network failed the electrical test: {wiring.summary}"]
    else:
        causes = [
            "TB-200S not in external 0-10 V mode, or its local adjustment link is still fitted.",
            "ADJ and GND wires not on the TB-200S ADJ/GND terminals, or swapped.",
            "TB-200S signal GND not common with the board GND (CN7-20).",
            "TB-200S not powered, or OUT+/OUT- not connected to the brake coil.",
            "Brake not mechanically coupled to the motor shaft.",
        ]
    return CheckResult(name, "FAIL", "No measurable brake torque between 0.05 A and 0.25 A. " + summary, causes)


# ---------------------------------------------------------------------------
# Serial link
# ---------------------------------------------------------------------------


class BoardLink:
    """Line-oriented access to the acquisition firmware."""

    def __init__(self, connection, char_delay_s: float = COMMAND_CHAR_DELAY_S) -> None:
        self.connection = connection
        self.char_delay_s = char_delay_s
        self.buffer = b""
        self.header: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.status: Optional[dict[str, str]] = None

    def send(self, command: str) -> None:
        # Same pacing as the dashboard: USART1 has no RX FIFO enabled.
        for byte in (command + "\n").encode("ascii"):
            self.connection.write(bytes((byte,)))
            self.connection.flush()
            if self.char_delay_s > 0:
                time.sleep(self.char_delay_s)

    def next_line(self, timeout_s: float) -> Optional[str]:
        deadline = time.monotonic() + timeout_s
        while True:
            if b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                line = raw.decode("ascii", errors="replace").strip()
                if line:
                    self.dispatch(line)
                    return line
                continue
            if time.monotonic() >= deadline:
                return None
            chunk = self.connection.read(256)
            if chunk:
                self.buffer += chunk

    def dispatch(self, line: str) -> None:
        if line.startswith("#CSV_HEADER,"):
            self.header = [column.strip() for column in line.split(",")[1:]]
        elif line.startswith("DATA,") and self.header:
            values = [value.strip() for value in line.split(",")[1:]]
            if len(values) == len(self.header):
                self.rows.append(dict(zip(self.header, values)))
        elif line.startswith("STATUS,"):
            self.status = parse_fields(line.split(",")[1:])

    def read_for(self, duration_s: float) -> list[str]:
        lines = []
        deadline = time.monotonic() + duration_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return lines
            line = self.next_line(remaining)
            if line is not None:
                lines.append(line)

    def wait_for(self, predicate: Callable[[str], bool], timeout_s: float) -> tuple[Optional[str], list[str]]:
        lines = []
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, lines
            line = self.next_line(remaining)
            if line is None:
                continue
            lines.append(line)
            if predicate(line):
                return line, lines

    def command(self, command: str, name: str, timeout_s: float = COMMAND_TIMEOUT_S) -> tuple[bool, Optional[str], list[str]]:
        """Send a command and wait for ``ACK,<name>`` or ``ERR,...``."""
        self.send(command)
        expected = f"ACK,{name}"
        match, lines = self.wait_for(lambda line: line == expected or line.startswith("ERR,"), timeout_s)
        if match == expected:
            return True, None, lines
        return False, match, lines

    def query_status(self, timeout_s: float = 2.0) -> Optional[dict[str, str]]:
        self.status = None
        self.send("STATUS")
        self.wait_for(lambda line: line.startswith("STATUS,"), timeout_s)
        return self.status


def identify_firmware(link: BoardLink) -> tuple[str, list[str]]:
    """Return ``acquisition``, ``validation``, ``silent``, or ``unknown``."""
    ok, _error, lines = link.command("SYNC", "SYNC", timeout_s=3.0)
    if ok:
        return "acquisition", lines
    if not lines:
        lines = link.read_for(1.0)
    if any(VALIDATION_FIRMWARE_LINE.match(line) for line in lines):
        return "validation", lines
    if not lines:
        return "silent", lines
    return "unknown", lines


def firmware_problem(kind: str, port: str, lines: list[str]) -> CheckResult:
    name = "Board and firmware"
    if kind == "validation":
        return CheckResult(
            name,
            "FAIL",
            "firmware_validation is flashed on the board.",
            ["This check needs firmware_acquisition."],
            ["Flash firmware_acquisition/tets_motor_dewalt/STM32CubeIDE from STM32CubeIDE."],
        )
    if kind == "silent":
        return CheckResult(
            name,
            "FAIL",
            f"No data received on {port}.",
            [
                "Wrong COM port: use the ST-LINK virtual COM port of the B-G473E-ZEST1S.",
                "Board not powered, USB cable without data lines, or firmware not flashed.",
            ],
        )
    return CheckResult(
        name,
        "FAIL",
        "Unexpected answer to SYNC.",
        ["Another firmware is flashed, or the baud rate is not 115200."] + [f"Received: {line}" for line in lines[:3]],
    )


def run_diag(link: BoardLink) -> DiagReport:
    link.send("DIAG")
    first, lines = link.wait_for(lambda line: line.startswith(("DIAG,", "ERR,")), COMMAND_TIMEOUT_S)
    if first is None or first.startswith("ERR,"):
        return parse_diag_lines(lines)
    _match, more = link.wait_for(lambda line: line == "ACK,DIAG", DIAG_TIMEOUT_S)
    return parse_diag_lines(lines + more)


def run_stream_check(link: BoardLink, seconds: float) -> Optional[list[dict[str, str]]]:
    ok, _error, _lines = link.command(f"ACQ_START,{STREAM_PERIOD_MS},1000", "ACQ_START")
    if not ok:
        return None
    start = len(link.rows)
    link.read_for(seconds)
    rows = link.rows[start:]
    link.command("STOP", "STOP")
    return rows


def rows_to_samples(rows: list[dict[str, str]]) -> list[Sample]:
    return [
        Sample(
            row_float(row, "motor_speed_mech_rpm"),
            row_float(row, "motor_id_a"),
            row_float(row, "motor_iq_a"),
            row_float(row, "motor_ud_v"),
            row_float(row, "motor_uq_v"),
            row_float(row, "load_setpoint_a"),
        )
        for row in rows
    ]


def motor_faulted(link: BoardLink) -> bool:
    status = link.query_status()
    return status is not None and status.get("app_state") == "FAULT"


def wait_for_speed(link: BoardLink, target_rpm: float, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        start = len(link.rows)
        link.read_for(1.0)
        recent = [sample.speed_rpm for sample in rows_to_samples(link.rows[start:])[-3:]]
        if len(recent) == 3 and all(speed >= SPEED_REACHED_RATIO * target_rpm for speed in recent):
            return True
        if motor_faulted(link):
            return False
    return False


def collect_window(link: BoardLink, settle_s: float, window_s: float) -> tuple[list[Sample], bool]:
    link.read_for(settle_s)
    start = len(link.rows)
    link.read_for(window_s)
    samples = rows_to_samples(link.rows[start:])
    return samples, motor_faulted(link)


def run_motor_test(link: BoardLink, args: argparse.Namespace) -> MotorTestResult:
    result = MotorTestResult(target_rpm=args.speed)
    try:
        link.command("SYNC", "SYNC")
        cfg = f"CFG,{args.speed:.3f},{args.iq_limit:.3f},{args.hard_limit:.3f},{args.accel:.3f},100,1000"
        for command, name in ((cfg, "CFG"), (f"LOAD,{MIN_LOAD_A:.3f}", "LOAD")):
            ok, error, _lines = link.command(command, name)
            if not ok:
                result.error = error or f"no ACK,{name}"
                return result

        ok, error, _lines = link.command("START", "START", timeout_s=5.0)
        result.started = ok
        if not ok:
            result.error = error or "no ACK,START"
            result.status = link.query_status()
            return result

        ramp_s = args.speed * 2.0 / 60.0 / args.accel
        result.reached_speed = wait_for_speed(link, args.speed, ramp_s + 8.0)
        if not result.reached_speed:
            result.fault_phase = "startup"
            result.status = link.query_status()
            return result

        steps = (
            ("base", f"LOAD,{MIN_LOAD_A:.3f}"),
            ("brake", f"LOAD,{MAX_LOAD_A:.3f}"),
            ("release", f"LOAD,{MIN_LOAD_A:.3f}"),
        )
        for phase, command in steps:
            link.command(command, "LOAD")
            samples, faulted = collect_window(link, args.settle, args.window)
            result.windows[phase] = samples
            if faulted:
                result.fault_phase = phase
                break
        result.status = link.status
    finally:
        link.command("STOP", "STOP")
    return result


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def available_ports() -> list[str]:
    return [f"{port.device} ({port.description})" for port in list_ports.comports()]


def default_port() -> Optional[str]:
    candidates = [port.device for port in list_ports.comports() if "stlink" in port.description.lower().replace("-", "")]
    return candidates[0] if len(candidates) == 1 else None


def ask_direction() -> Optional[bool]:
    answer = input("Did the motor turn in its normal working direction? [y/n/Enter to skip] ").strip().lower()
    if answer.startswith("y") or answer.startswith("o"):
        return True
    if answer.startswith("n"):
        return False
    return None


def confirm_motor_start(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    print(
        f"\nThe motor will run at {args.speed:.0f} rpm (Iq limit {args.iq_limit:.1f} A, "
        f"hard stop {args.hard_limit:.1f} A) with the brake at {MIN_LOAD_A} A then {MAX_LOAD_A} A."
    )
    print("Keep clear of the rotating parts and have the supply switch at hand.")
    return input("Start the motor? [yes/no] ").strip().lower() in {"y", "yes", "o", "oui"}


def print_report(port: str, results: list[CheckResult]) -> None:
    print(f"\nWiring check on {port} - {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    for result in results:
        print(f"[{result.status:^4}] {result.module}: {result.summary}")
        if result.causes:
            label = "Probable causes" if result.status == "FAIL" else "Notes"
            print(f"       {label}:")
            for cause in result.causes:
                print(f"         - {cause}")
        if result.actions:
            print("       Fix:")
            for action in result.actions:
                print(f"         - {action}")
    failures = sum(result.status == "FAIL" for result in results)
    warnings = sum(result.status == "WARN" for result in results)
    print(f"\nResult: {failures} failure(s), {warnings} warning(s).")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the bench wiring through firmware_acquisition.")
    parser.add_argument("--port", help="ST-LINK virtual COM port, for example COM5.")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--motor", action="store_true", help="Also run the motor to check U/V/W and the brake.")
    parser.add_argument("--yes", action="store_true", help="Start the motor without confirmation.")
    parser.add_argument("--no-direction-prompt", action="store_true", help="Do not ask for the rotation direction.")
    parser.add_argument("--speed", type=float, default=1500.0, help="Test speed in rpm (100-4500).")
    parser.add_argument("--iq-limit", type=float, default=6.0, help="Iq limit in A during the motor test.")
    parser.add_argument("--hard-limit", type=float, default=10.0, help="Total-current stop threshold in A.")
    parser.add_argument("--accel", type=float, default=50.0, help="Acceleration in electrical Hz/s (max 50).")
    parser.add_argument("--settle", type=float, default=1.5, help="Settling time after each load step in s.")
    parser.add_argument("--window", type=float, default=3.0, help="Measurement window per load step in s.")
    args = parser.parse_args(argv)

    if not 100.0 <= args.speed <= 4500.0:
        parser.error("--speed must be between 100 and 4500 rpm")
    if not 0.0 < args.iq_limit <= 30.0 or not 0.0 < args.hard_limit <= 30.0:
        parser.error("--iq-limit and --hard-limit must be in ]0, 30] A")
    if not 0.0 < args.accel <= 50.0:
        parser.error("--accel must be in ]0, 50] electrical Hz/s")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    port = args.port or default_port()
    if port is None:
        print("Use --port. Available ports: " + (", ".join(available_ports()) or "none"))
        return 2

    try:
        connection = serial.Serial(port, args.baud, timeout=SERIAL_READ_TIMEOUT_S)
    except serial.SerialException as exc:
        print(f"Cannot open {port}: {exc}")
        print("Close the dashboard, Motor Pilot, or any terminal using the port, and check the USB cable.")
        print("Available ports: " + (", ".join(available_ports()) or "none"))
        return 2

    results: list[CheckResult] = []
    with connection:
        connection.reset_input_buffer()
        link = BoardLink(connection)

        kind, lines = identify_firmware(link)
        if kind != "acquisition":
            print_report(port, [firmware_problem(kind, port, lines)])
            return 1

        print("Running DIAG (motor stopped, about 5 s)...")
        report = run_diag(link)
        wiring = CheckResult("TB-200S command", "SKIP", "Not tested.")
        if report.complete:
            wiring = diagnose_tb200s_wiring(report)
            results += [
                diagnose_power(report, args.motor),
                diagnose_d6t(report),
                diagnose_ds18b20(report),
                wiring,
            ]
        else:
            results.append(
                CheckResult(
                    "Board and firmware",
                    "WARN",
                    "The firmware does not answer DIAG: flash the current firmware_acquisition for detailed causes.",
                )
            )

        rows = run_stream_check(link, STREAM_SECONDS)
        results.append(diagnose_stream(link.header, rows, STREAM_SECONDS))
        if not report.complete:
            results += diagnose_sensors_from_stream(rows)

        if args.motor and confirm_motor_start(args):
            print("Motor test running...")
            motor = run_motor_test(link, args)
            if motor.reached_speed and not args.no_direction_prompt:
                motor.direction_ok = ask_direction()
            results += [diagnose_motor(motor), diagnose_brake(motor, wiring)]
        else:
            skipped = "Cancelled by the operator." if args.motor else "Run with --motor (the motor turns)."
            results += [
                CheckResult("Motor phases U/V/W and d/q telemetry", "SKIP", skipped),
                CheckResult("TB-200S brake response", "SKIP", skipped),
            ]

    print_report(port, results)
    return 1 if any(result.status == "FAIL" for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
