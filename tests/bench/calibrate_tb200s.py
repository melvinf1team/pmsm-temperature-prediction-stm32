"""Calibrate the TB-200S load command through firmware_acquisition.

The script drives the unchanged acquisition firmware over USART1 (SYNC, DIAG,
CFG, LOAD, START, STATUS, STOP). With the motor running, it steps the TB-200S
command over 0.05-0.25 A, up then down, and at every step asks the operator for
the current shown by the TB-200S and, when available, the brake current read on
an ammeter and the PA5/ADJ voltages. It then fits the real TB-200S transfer,
checks linearity, hysteresis, repeatability, the DAC, the ADJ input and the
motor noise, and proposes new ``APP_TB200S_CAL_*`` constants. A text report and
a CSV of every reading are written to ``tests/bench/calibration_reports``.

Usage::

    python tests/bench/calibrate_tb200s.py --port COM5
    python tests/bench/calibrate_tb200s.py --port COM5 --cycles 1 --warmup 0
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import serial

from check_wiring import (
    BAUD_RATE,
    COMMAND_TIMEOUT_S,
    MAX_LOAD_A,
    MIN_LOAD_A,
    SERIAL_READ_TIMEOUT_S,
    BoardLink,
    CheckResult,
    Sample,
    available_ports,
    default_port,
    describe_fault,
    diagnose_power,
    diagnose_tb200s_wiring,
    field_int,
    firmware_problem,
    identify_firmware,
    mean,
    rows_to_samples,
    run_diag,
    stdev,
    wait_for_speed,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = Path("STM32CubeIDE") / "Application" / "User" / "app_motor_control.c"
ACQUISITION_SOURCE = PROJECT_ROOT / "firmware_acquisition" / "tets_motor_dewalt" / APP_SOURCE
VALIDATION_SOURCE = PROJECT_ROOT / "firmware_validation" / APP_SOURCE
REPORT_DIR = Path(__file__).resolve().parent / "calibration_reports"

CAL_NAMES = ("LOW_V", "LOW_A", "HIGH_V", "HIGH_A")
DAC_VREF_V = 3.3
DAC_MAX_CODE = 4095
DAC_LINEAR_RANGE_V = (0.2, 3.1)
DIAG_CODE_TOLERANCE = 2
SERIES_RESISTOR_OHM = 1000.0

DATALOG_MS = 100
DS18B20_MS = 1000
START_TIMEOUT_S = 5.0
POLE_PAIRS = 2
SPEED_MARGIN_S = 8.0
HOLD_REPORT_S = 15.0

DISPLAY_TOLERANCE_A = 0.01
AMMETER_TOLERANCE_A = 0.005
DEAD_ZONE_A = 0.005
ECHO_TOLERANCE_A = 0.001
DAC_GAIN_TOLERANCE = 0.02
ADJ_LOADED_RATIO = 0.97
MIN_IQ_RISE_A = 0.1
# A quadratic fit must cut the line residual RMS by this ratio to call the transfer curved.
CURVATURE_RMS_RATIO = 0.7

CHECKLIST = (
    "TB-200S on its external 0-10 V input, local adjustment link removed (docs/source/cablage.rst).",
    "PA5 (CN7-32) -> 1 kOhm -> TB-200S ADJ, 4.7 uF from ADJ to GND, common GND (CN7-20 or CN7-22).",
    "Brake coupled to the motor, rotating parts guarded, power-supply switch within reach.",
    "Recommended: DC ammeter on its mA range in series with the brake coil (TB-200S OUT+ -> ammeter -> coil).\n"
    "     Wire it with the TB-200S switched off and never open this circuit while the coil is energised.",
    "Optional: multimeter on DC volts to read PA5 (CN7-32) and ADJ against GND during the first sweep.",
    "Dashboard, Motor Pilot and serial terminals closed (this script owns the COM port).",
)

ANSWER_HELP = (
    "How to answer at each step:",
    "  - type the current shown by the TB-200S in A, for example 0.15 (a comma also works: 0,15);",
    "  - if the display alternates between two values, type both: 0.15/0.16;",
    "  - ammeter values are in mA (for example 152) unless you add the unit (0.152 A);",
    "  - r = wait and read again, s = skip this step, q = stop the motor and write the report.",
)

CSV_FIELDS = (
    "step",
    "cycle",
    "direction",
    "command_a",
    "dac_code",
    "pa5_computed_v",
    "echo_a",
    "display_a",
    "display_spread_a",
    "ammeter_a",
    "ammeter_spread_a",
    "pa5_measured_v",
    "adj_measured_v",
    "speed_rpm",
    "iq_a",
    "iq_std_a",
    "valid",
    "note",
)


# ---------------------------------------------------------------------------
# Firmware calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calibration:
    """Firmware line from the TB-200S load command to the PA5 voltage."""

    low_v: float
    low_a: float
    high_v: float
    high_a: float

    def pa5_voltage(self, load_a: float) -> float:
        return self.low_v + (load_a - self.low_a) * (self.high_v - self.low_v) / (self.high_a - self.low_a)

    def dac_code(self, load_a: float) -> int:
        """Same rounding and clamping as ``AppMotorControl_LoadToDacCode``."""
        code = self.pa5_voltage(load_a) / DAC_VREF_V * DAC_MAX_CODE
        if code <= 0.0:
            return 0
        if code >= DAC_MAX_CODE:
            return DAC_MAX_CODE
        return int(code + 0.5)


def code_to_volts(code: int) -> float:
    return code * DAC_VREF_V / DAC_MAX_CODE


def read_calibration(source: Path) -> Calibration:
    text = source.read_text(encoding="utf-8")
    values = []
    for name in CAL_NAMES:
        match = re.search(rf"^#define\s+APP_TB200S_CAL_{name}\s+([-+]?\d*\.?\d+)[fF]?\s", text, re.MULTILINE)
        if match is None:
            raise ValueError(f"APP_TB200S_CAL_{name} not found in {source}")
        values.append(float(match.group(1)))
    return Calibration(*values)


def define_lines(calibration: Calibration) -> list[str]:
    values = (
        f"{calibration.low_v:.4f}",
        f"{calibration.low_a:.2f}",
        f"{calibration.high_v:.4f}",
        f"{calibration.high_a:.2f}",
    )
    return [f"#define {'APP_TB200S_CAL_' + name:<36}{value}f" for name, value in zip(CAL_NAMES, values)]


# ---------------------------------------------------------------------------
# Operator input
# ---------------------------------------------------------------------------


class RepeatStep(Exception):
    """The operator wants to wait and read the step again."""


class SkipStep(Exception):
    """The operator skips the current step."""


class AbortSession(Exception):
    """The operator stopped the session; the report is still written."""


class SessionError(Exception):
    """The board refused a command or the motor could not run."""


@dataclass(frozen=True)
class Reading:
    value: float
    spread: float = 0.0


@dataclass(frozen=True)
class Quantity:
    units: dict[str, float]
    default_unit: str
    maximum: float
    hint: str
    minimum_nonzero: float = 0.0


CURRENT_UNITS = {"a": 1.0, "ma": 1e-3}
VOLTAGE_UNITS = {"v": 1.0, "mv": 1e-3}
DISPLAY = Quantity(CURRENT_UNITS, "a", 1.0, "Type the TB-200S value in A, for example 0.15, or 0.15/0.16 if it alternates.")
AMMETER = Quantity(
    CURRENT_UNITS, "ma", 1.0, "Type the ammeter value in mA, for example 152, or add the unit: 0.152 A.", 0.001
)
VOLTAGE = Quantity(VOLTAGE_UNITS, "v", 3.6, "Type the voltage in V, for example 0.83, or add the unit: 830 mV.")

NUMBER = r"(\d+(?:[.,]\d*)?|[.,]\d+)"
READING_PATTERN = re.compile(rf"^{NUMBER}\s*(?:(?:/|-|to)\s*{NUMBER})?\s*([a-z]+)?$", re.IGNORECASE)


def parse_reading(text: str, units: dict[str, float], default_unit: str) -> Optional[Reading]:
    """Parse ``0.15``, ``0,15``, ``152 mA`` or an alternating ``0.15/0.16`` (mean and spread)."""
    match = READING_PATTERN.match(text.strip())
    if match is None:
        return None
    unit = (match.group(3) or default_unit).lower()
    if unit not in units:
        return None
    values = [float(group.replace(",", ".")) * units[unit] for group in match.group(1, 2) if group]
    return Reading(statistics.fmean(values), max(values) - min(values))


class Operator:
    """Console prompts; ``ask`` and ``say`` are replaceable for tests."""

    def __init__(self, ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> None:
        self.ask = ask
        self.say = say

    def answer(self, prompt: str) -> str:
        text = self.ask(prompt).strip()
        if text.lower() in {"q", "quit"}:
            raise AbortSession()
        return text

    def yes_no(self, question: str) -> bool:
        while True:
            text = self.answer(f"{question} [y/n] ").lower()
            if text in {"y", "yes"}:
                return True
            if text in {"n", "no"}:
                return False
            self.say("  Answer y or n (q to quit).")

    def reading(
        self, label: str, quantity: Quantity, optional: bool = False, step_actions: bool = False
    ) -> Optional[Reading]:
        while True:
            text = self.answer(f"  {label}: ")
            if step_actions and text.lower() == "r":
                raise RepeatStep()
            if step_actions and text.lower() == "s":
                raise SkipStep()
            if not text and optional:
                return None
            reading = parse_reading(text, quantity.units, quantity.default_unit)
            if (
                reading is None
                or not 0.0 <= reading.value <= quantity.maximum
                or 0.0 < reading.value < quantity.minimum_nonzero
            ):
                self.say(f"  {quantity.hint}")
                continue
            return reading


# ---------------------------------------------------------------------------
# Session data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedStep:
    cycle: int
    direction: str
    command_a: float


@dataclass
class Step:
    index: int
    cycle: int
    direction: str
    command_a: float
    dac_code: int
    dac_v: float
    display: Optional[Reading] = None
    ammeter: Optional[Reading] = None
    pa5_v: Optional[float] = None
    adj_v: Optional[float] = None
    echo_a: float = math.nan
    speed_rpm: float = math.nan
    iq_a: float = math.nan
    iq_std_a: float = math.nan
    valid: bool = True
    note: str = ""


@dataclass
class DiagSummary:
    vbus_v: float = math.nan
    drive_code: Optional[int] = None
    expected_code: Optional[int] = None
    v_drive_mv: Optional[int] = None
    wiring: Optional[CheckResult] = None


@dataclass
class Session:
    port: str
    settings: list[str]
    calibration: Calibration
    calibration_source: str
    started_at: datetime = field(default_factory=datetime.now)
    ammeter: bool = False
    voltmeter: bool = False
    notes: str = ""
    diag: Optional[DiagSummary] = None
    stopped_display: Optional[Reading] = None
    stopped_ammeter: Optional[Reading] = None
    steps: list[Step] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    flashed_mismatch: bool = False
    completed: bool = False


def load_points(step_a: float) -> list[float]:
    count = round((MAX_LOAD_A - MIN_LOAD_A) / step_a)
    if count < 2 or abs(MIN_LOAD_A + count * step_a - MAX_LOAD_A) > 1e-9:
        raise ValueError(f"--step must split {MIN_LOAD_A}-{MAX_LOAD_A} A into at least 2 equal steps")
    return [round(MIN_LOAD_A + index * step_a, 4) for index in range(count + 1)]


def build_plan(points: list[float], cycles: int) -> list[PlannedStep]:
    """Up then down sweeps; every point is reached from the side given by its direction."""
    plan = []
    for cycle in range(cycles):
        up = points if cycle == 0 else points[1:]
        plan += [PlannedStep(cycle, "up", value) for value in up]
        plan += [PlannedStep(cycle, "down", value) for value in reversed(points[:-1])]
    return plan


# ---------------------------------------------------------------------------
# Board control
# ---------------------------------------------------------------------------


def discard_input(link: BoardLink) -> None:
    """Drop the telemetry buffered while the operator was typing."""
    link.connection.reset_input_buffer()
    link.buffer = b""


def motor_state(link: BoardLink) -> tuple[bool, Optional[dict[str, str]]]:
    status = link.query_status()
    return status is not None and status.get("app_state") == "RUNNING", status


def stop_reason(status: Optional[dict[str, str]]) -> str:
    if not status:
        return "no STATUS answer"
    reason = f"app_state={status.get('app_state', '?')}, fault_reason={status.get('fault_reason', '?')}"
    if status.get("app_state") == "IDLE" and status.get("fault_reason") == "NONE":
        reason += " (stopped without fault: B2 button pressed, or STOP sent by another program)"
    causes = describe_fault(status)
    return reason + (". " + " ".join(causes) if causes else "")


def set_load(link: BoardLink, load_a: float) -> None:
    ok, error, _lines = link.command(f"LOAD,{load_a:.3f}", "LOAD")
    if not ok:
        raise SessionError(f"LOAD,{load_a:.3f} refused: {error or 'no ACK,LOAD'}.")


def start_motor(link: BoardLink, args: argparse.Namespace, operator: Operator, session: Session) -> bool:
    operator.say(f"\nStarting the motor at {args.speed:.0f} rpm...")
    cfg = (
        f"CFG,{args.speed:.3f},{args.iq_limit:.3f},{args.hard_limit:.3f},{args.accel:.3f},"
        f"{DATALOG_MS},{DS18B20_MS}"
    )
    for command, name, timeout_s in (
        (cfg, "CFG", COMMAND_TIMEOUT_S),
        (f"LOAD,{MIN_LOAD_A:.3f}", "LOAD", COMMAND_TIMEOUT_S),
        ("START", "START", START_TIMEOUT_S),
    ):
        ok, error, _lines = link.command(command, name, timeout_s=timeout_s)
        if not ok:
            session.events.append(f"{name} refused ({error or 'no answer'}): {stop_reason(link.query_status())}")
            return False

    ramp_s = args.speed * POLE_PAIRS / 60.0 / args.accel
    if wait_for_speed(link, args.speed, ramp_s + SPEED_MARGIN_S):
        operator.say("  Motor at speed.")
        return True
    session.events.append(f"The motor did not reach {args.speed:.0f} rpm: {stop_reason(link.query_status())}")
    return False


def hold_load(link: BoardLink, seconds: float, operator: Operator, label: str) -> Optional[dict[str, str]]:
    """Keep the current load for ``seconds``; return the status if the motor stopped."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return None
        start = len(link.rows)
        link.read_for(min(HOLD_REPORT_S, remaining))
        samples = rows_to_samples(link.rows[start:])
        running, status = motor_state(link)
        if not running:
            return status or {}
        if seconds > HOLD_REPORT_S:
            left = max(0.0, end - time.monotonic())
            operator.say(
                f"  {label}: {left:.0f} s left, {mean([s.speed_rpm for s in samples]):.0f} rpm, "
                f"Iq {mean([s.iq_a for s in samples]):.2f} A"
            )


def measure(link: BoardLink, settle_s: float, window_s: float) -> list[Sample]:
    link.read_for(settle_s)
    start = len(link.rows)
    link.read_for(window_s)
    return rows_to_samples(link.rows[start:])


def fill_telemetry(step: Step, samples: list[Sample]) -> None:
    step.echo_a = mean([sample.load_a for sample in samples])
    step.speed_rpm = mean([sample.speed_rpm for sample in samples])
    step.iq_a = mean([sample.iq_a for sample in samples])
    step.iq_std_a = stdev([sample.iq_a for sample in samples])


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def show_step(operator: Operator, step: Step, position: int, total: int) -> None:
    operator.say("")
    operator.say(
        f"Step {position}/{total} - cycle {step.cycle + 1}, sweep {step.direction}, command {step.command_a:.3f} A"
    )
    operator.say(f"  Firmware: load_setpoint_a {fmt(step.echo_a)} A, DAC code {step.dac_code} -> PA5 {step.dac_v:.3f} V")
    operator.say(f"  Motor: {fmt(step.speed_rpm, 0)} rpm, Iq {fmt(step.iq_a, 2)} +/- {fmt(step.iq_std_a, 2)} A")
    if math.isfinite(step.echo_a) and abs(step.echo_a - step.command_a) > ECHO_TOLERANCE_A:
        operator.say("  WARNING: the firmware reports another load setpoint than the command.")


def run_step(
    link: BoardLink,
    args: argparse.Namespace,
    operator: Operator,
    session: Session,
    planned: PlannedStep,
    position: int,
    total: int,
) -> tuple[Step, Optional[dict[str, str]]]:
    """Apply one command and collect the readings; the status is returned if the motor stopped."""
    code = session.calibration.dac_code(planned.command_a)
    step = Step(len(session.steps) + 1, planned.cycle, planned.direction, planned.command_a, code, code_to_volts(code))
    set_load(link, planned.command_a)
    ask_voltages = session.voltmeter and planned.cycle == 0 and planned.direction == "up"

    while True:
        fill_telemetry(step, measure(link, args.settle, args.window))
        running, status = motor_state(link)
        if not running:
            step.valid = False
            step.note = "motor stopped"
            return step, status or {}
        show_step(operator, step, position, total)
        try:
            step.display = operator.reading("TB-200S display [A]", DISPLAY, step_actions=True)
            if session.ammeter:
                step.ammeter = operator.reading("Ammeter [mA]", AMMETER, step_actions=True)
            if ask_voltages:
                pa5 = operator.reading("PA5 voltage, CN7-32 to GND [V] (Enter to skip)", VOLTAGE, True, True)
                adj = operator.reading("ADJ voltage, TB-200S ADJ to GND [V] (Enter to skip)", VOLTAGE, True, True)
                step.pa5_v = pa5.value if pa5 is not None else None
                step.adj_v = adj.value if adj is not None else None
        except RepeatStep:
            step.display = step.ammeter = None
            step.pa5_v = step.adj_v = None
            discard_input(link)
            operator.say("  Waiting again before the next reading...")
            continue
        except SkipStep:
            step.valid = False
            step.note = "skipped by the operator"
        break

    discard_input(link)
    running, status = motor_state(link)
    if not running:
        step.valid = False
        step.note = "motor stopped during the reading"
        return step, status or {}
    return step, None


def run_prechecks(link: BoardLink, args: argparse.Namespace, operator: Operator, session: Session) -> bool:
    operator.say("\nRunning DIAG with the motor stopped (about 5 s; ADJ briefly rises to about 3 V)...")
    report = run_diag(link)
    if not report.complete:
        session.events.append("The firmware did not answer DIAG: wiring and flashed calibration not checked.")
        return True

    diag = DiagSummary(
        vbus_v=(field_int(report.power, "vbus_mv") or 0) / 1000.0,
        drive_code=field_int(report.dac, "drive_code"),
        expected_code=session.calibration.dac_code(MAX_LOAD_A),
        v_drive_mv=field_int(report.dac, "v_drive_mv"),
        wiring=diagnose_tb200s_wiring(report),
    )
    session.diag = diag
    problems = [result for result in (diagnose_power(report, motor_requested=True), diag.wiring) if result.status == "FAIL"]
    for result in problems:
        session.events.append(f"{result.module}: {result.summary} " + " ".join(result.causes + result.actions))
        operator.say(f"  FAIL {result.module}: {result.summary}")
        for line in result.causes + result.actions:
            operator.say(f"    - {line}")
    if problems:
        operator.say("  Fix the problem above (python tests/bench/check_wiring.py gives details), then run again.")
        return False

    if diag.drive_code is not None and abs(diag.drive_code - diag.expected_code) > DIAG_CODE_TOLERANCE:
        session.flashed_mismatch = True
        message = (
            f"The flashed firmware gives DAC code {diag.drive_code} at {MAX_LOAD_A} A but {session.calibration_source} "
            f"gives {diag.expected_code}: rebuild and flash firmware_acquisition, or pass --cal with the flashed values."
        )
        session.events.append(message)
        operator.say(f"  WARNING: {message}")
        if not operator.yes_no("Continue anyway? The PA5 voltages in the report will be wrong."):
            return False
    else:
        operator.say(f"  DIAG OK: bus {diag.vbus_v:.1f} V, flashed calibration matches {session.calibration_source}.")
    return True


def read_stopped(link: BoardLink, args: argparse.Namespace, operator: Operator, session: Session) -> None:
    operator.say(
        f"\nMotor stopped, TB-200S command {MIN_LOAD_A:.3f} A "
        f"(PA5 {code_to_volts(session.calibration.dac_code(MIN_LOAD_A)):.3f} V). Read the TB-200S now."
    )
    link.read_for(args.settle)
    discard_input(link)
    session.stopped_display = operator.reading("TB-200S display [A]", DISPLAY)
    if session.ammeter:
        session.stopped_ammeter = operator.reading("Ammeter [mA]", AMMETER)


def run_session(link: BoardLink, args: argparse.Namespace, operator: Operator, session: Session) -> None:
    plan = build_plan(args.points, args.cycles)

    operator.say("TB-200S calibration with firmware_acquisition\n\nBefore starting, check:")
    for number, item in enumerate(CHECKLIST, start=1):
        operator.say(f"  {number}. {item}")
    operator.answer("Check the list above. Press Enter to continue (q to quit). ")
    session.ammeter = operator.yes_no("Is a DC ammeter in series with the brake coil?")
    session.voltmeter = operator.yes_no("Will you measure PA5 and ADJ with a multimeter during the first sweep?")
    session.notes = operator.answer("Bench notes for the report (TB-200S unit, ammeter model...; Enter to skip): ")

    if args.skip_diag:
        session.events.append("DIAG skipped (--skip-diag): wiring and flashed calibration not checked.")
    elif not run_prechecks(link, args, operator, session):
        return

    read_stopped(link, args, operator, session)

    if not args.yes and not operator.yes_no(
        f"\nThe motor will run at {args.speed:.0f} rpm with the brake between {MIN_LOAD_A} and {MAX_LOAD_A} A "
        f"({len(plan)} steps). Keep clear of the rotating parts. Start the motor?"
    ):
        session.events.append("Motor start cancelled by the operator.")
        return
    if not start_motor(link, args, operator, session):
        return

    if args.warmup > 0:
        warm_load = round((MIN_LOAD_A + MAX_LOAD_A) / 2.0, 3)
        set_load(link, warm_load)
        operator.say(f"\nWarm-up at {warm_load:.3f} A for {args.warmup:.0f} s...")
        status = hold_load(link, args.warmup, operator, "Warm-up")
        if status is not None:
            raise SessionError(f"The motor stopped during the warm-up: {stop_reason(status)}")
    set_load(link, MIN_LOAD_A)
    status = hold_load(link, args.settle, operator, "Pre-conditioning")
    if status is not None:
        raise SessionError(f"The motor stopped at {MIN_LOAD_A} A: {stop_reason(status)}")

    operator.say("")
    for line in ANSWER_HELP:
        operator.say(line)

    index = 0
    while index < len(plan):
        step, stopped_status = run_step(link, args, operator, session, plan[index], index + 1, len(plan))
        session.steps.append(step)
        if stopped_status is None:
            index += 1
            continue

        reason = stop_reason(stopped_status)
        session.events.append(f"Motor stopped at step {step.index} ({step.command_a:.3f} A): {reason}")
        operator.say(f"\n  The motor stopped: {reason}")
        if not operator.yes_no("Restart the motor and redo this step?"):
            raise AbortSession()
        if not start_motor(link, args, operator, session):
            raise SessionError("The motor could not be restarted.")
        if index > 0:
            # Reach the step again from the same side as planned.
            set_load(link, plan[index - 1].command_a)
            link.read_for(args.settle)

    session.completed = True
    operator.say("\nAll steps done.")


def shutdown(link: BoardLink) -> None:
    try:
        link.command("STOP", "STOP")
    except (serial.SerialException, OSError):
        pass


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorStats:
    count: int
    bias: float
    rms: float
    max_abs: float


@dataclass(frozen=True)
class LineFit:
    slope: float
    intercept: float
    r2: float
    residuals: ErrorStats
    quadratic_rms: float

    @property
    def threshold_v(self) -> float:
        return -self.intercept / self.slope

    def voltage(self, current_a: float) -> float:
        return (current_a - self.intercept) / self.slope


@dataclass
class Analysis:
    reference: str
    tolerance_a: float
    command_errors: dict[str, ErrorStats] = field(default_factory=dict)
    fits: dict[str, LineFit] = field(default_factory=dict)
    proposed: Optional[Calibration] = None
    dead_zone_steps: list[int] = field(default_factory=list)
    saturated: list[tuple[float, float]] = field(default_factory=list)
    hysteresis: list[tuple[int, float, float]] = field(default_factory=list)
    repeatability: list[tuple[str, float, float]] = field(default_factory=list)
    display_minus_ammeter: Optional[ErrorStats] = None
    pa5_ratio: Optional[float] = None
    adj_ratio: Optional[float] = None
    stopped_vs_running_a: Optional[float] = None
    iq_by_command: list[tuple[float, float]] = field(default_factory=list)
    echo_mismatch_steps: list[int] = field(default_factory=list)
    verdicts: list[tuple[str, str]] = field(default_factory=list)


def error_stats(errors: list[float]) -> Optional[ErrorStats]:
    if not errors:
        return None
    return ErrorStats(
        len(errors),
        statistics.fmean(errors),
        math.sqrt(statistics.fmean([error * error for error in errors])),
        max(abs(error) for error in errors),
    )


def fit_transfer(points: list[tuple[float, float]]) -> Optional[LineFit]:
    """Least-squares line of the TB-200S current against the PA5 voltage."""
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    if len(points) < 3 or len(set(xs)) < 2:
        return None
    slope, intercept = statistics.linear_regression(xs, ys)
    residuals = [y - (slope * x + intercept) for x, y in points]
    total = sum((y - statistics.fmean(ys)) ** 2 for y in ys)
    r2 = 1.0 - sum(r * r for r in residuals) / total if total > 0 else math.nan
    quadratic_rms = math.nan
    if len(set(xs)) >= 4:
        coefficients = np.polyfit(xs, ys, 2)
        quadratic = np.asarray(ys) - np.polyval(coefficients, xs)
        quadratic_rms = float(np.sqrt(np.mean(quadratic**2)))
    return LineFit(slope, intercept, r2, error_stats(residuals), quadratic_rms)


def proposed_calibration(fit: Optional[LineFit]) -> Optional[Calibration]:
    if fit is None or fit.slope <= 0:
        return None
    return Calibration(round(fit.voltage(MIN_LOAD_A), 4), MIN_LOAD_A, round(fit.voltage(MAX_LOAD_A), 4), MAX_LOAD_A)


def find_saturation(points: list[tuple[float, float, float]], tolerance_a: float) -> list[float]:
    """Top commands whose readings fall below the line of the lower commands.

    ``points`` holds ``(command_a, pa5_v, reading_a)`` tuples.
    """
    commands = sorted({command for command, _volts, _reading in points})
    saturated = []
    while len(commands) > 3:
        top = commands[-1]
        fit = fit_transfer(
            [(volts, reading) for command, volts, reading in points if command < top and reading > DEAD_ZONE_A]
        )
        if fit is None:
            break
        deficit = statistics.fmean(
            fit.slope * volts + fit.intercept - reading for command, volts, reading in points if command == top
        )
        if deficit <= tolerance_a:
            break
        saturated.append(commands.pop())
    return sorted(saturated)


def reading_value(step: Step, source: str) -> Optional[float]:
    reading = step.display if source == "display" else step.ammeter
    return reading.value if reading is not None else None


def analyse(session: Session) -> Analysis:
    steps = [step for step in session.steps if step.valid]
    reference = "ammeter" if any(step.ammeter is not None for step in steps) else "display"
    analysis = Analysis(reference, AMMETER_TOLERANCE_A if reference == "ammeter" else DISPLAY_TOLERANCE_A)

    ref_points = [
        (step.command_a, step.dac_v, value) for step in steps if (value := reading_value(step, reference)) is not None
    ]
    saturated = find_saturation(ref_points, analysis.tolerance_a)
    analysis.saturated = [
        (command, statistics.fmean([value for c, _v, value in ref_points if c == command])) for command in saturated
    ]
    linear_steps = [step for step in steps if step.command_a not in saturated]

    for source in ("display", "ammeter") if reference == "ammeter" else ("display",):
        pairs = [(step, value) for step in linear_steps if (value := reading_value(step, source)) is not None]
        stats = error_stats([value - step.command_a for step, value in pairs])
        if stats is not None:
            analysis.command_errors[source] = stats
        fit = fit_transfer([(step.dac_v, value) for step, value in pairs if value > DEAD_ZONE_A])
        if fit is not None:
            analysis.fits[source] = fit

    ref_pairs = [(step, value) for step in steps if (value := reading_value(step, reference)) is not None]
    analysis.dead_zone_steps = [step.index for step, value in ref_pairs if value <= DEAD_ZONE_A]
    analysis.proposed = proposed_calibration(analysis.fits.get(reference))

    directions: dict[tuple[int, float], dict[str, float]] = {}
    groups: dict[tuple[str, float], list[float]] = {}
    for step, value in ref_pairs:
        directions.setdefault((step.cycle, step.command_a), {})[step.direction] = value
        groups.setdefault((step.direction, step.command_a), []).append(value)
    analysis.hysteresis = [
        (cycle, command, both["down"] - both["up"])
        for (cycle, command), both in sorted(directions.items())
        if len(both) == 2
    ]
    analysis.repeatability = [
        (direction, command, max(values) - min(values))
        for (direction, command), values in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0]))
        if len(values) > 1
    ]

    analysis.display_minus_ammeter = error_stats(
        [step.display.value - step.ammeter.value for step in steps if step.display and step.ammeter]
    )
    pa5 = [(step.pa5_v, step.dac_v) for step in steps if step.pa5_v is not None and step.dac_v > 0]
    if pa5:
        analysis.pa5_ratio = sum(m for m, _ in pa5) / sum(c for _, c in pa5)
    adj = [
        (step.adj_v, step.pa5_v if step.pa5_v is not None else step.dac_v)
        for step in steps
        if step.adj_v is not None and step.dac_v > 0
    ]
    if adj:
        analysis.adj_ratio = sum(a for a, _ in adj) / sum(p for _, p in adj)

    stopped = session.stopped_ammeter if reference == "ammeter" else session.stopped_display
    running = next(
        (value for step, value in ref_pairs if math.isclose(step.command_a, MIN_LOAD_A) and step.direction == "up"),
        None,
    )
    if stopped is not None and running is not None:
        analysis.stopped_vs_running_a = running - stopped.value

    iq_groups: dict[float, list[float]] = {}
    for step in steps:
        if math.isfinite(step.iq_a):
            iq_groups.setdefault(step.command_a, []).append(step.iq_a)
    analysis.iq_by_command = [(command, statistics.fmean(values)) for command, values in sorted(iq_groups.items())]
    analysis.echo_mismatch_steps = [
        step.index
        for step in steps
        if math.isfinite(step.echo_a) and abs(step.echo_a - step.command_a) > ECHO_TOLERANCE_A
    ]
    analysis.verdicts = build_verdicts(session, analysis)
    return analysis


def build_verdicts(session: Session, analysis: Analysis) -> list[tuple[str, str]]:
    verdicts: list[tuple[str, str]] = []
    tolerance = analysis.tolerance_a
    if session.flashed_mismatch:
        verdicts.append(
            ("FAIL", "The flashed firmware does not use the source calibration: the PA5 voltages are wrong. "
                     "Flash firmware_acquisition and run again.")
        )
    if analysis.echo_mismatch_steps:
        verdicts.append(("FAIL", f"The firmware did not apply the command at step(s) {analysis.echo_mismatch_steps}."))

    errors = analysis.command_errors.get(analysis.reference)
    if errors is None:
        verdicts.append(("FAIL", "No valid reading: nothing to analyse."))
        return verdicts
    if errors.max_abs <= tolerance:
        verdicts.append(
            ("OK", f"Current calibration: the {analysis.reference} stays within {tolerance:.3f} A of the command "
                   f"(max {errors.max_abs:.3f} A, bias {errors.bias:+.3f} A).")
        )
    else:
        verdicts.append(
            ("FIX", f"Current calibration: the {analysis.reference} is up to {errors.max_abs:.3f} A away from the "
                    f"command (bias {errors.bias:+.3f} A, tolerance {tolerance:.3f} A).")
        )
    if analysis.saturated:
        levels = ", ".join(f"{command:.3f} A reads {level:.3f} A" for command, level in analysis.saturated)
        verdicts.append(
            ("WARN", f"The TB-200S output saturates ({levels}): these readings are excluded from the errors and "
                     "the fit. Check the brake coil resistance (24 V output / R) and the TB-200S current limit, "
                     "or lower APP_TB200S_MAX_LOAD_A.")
        )

    fit = analysis.fits.get(analysis.reference)
    proposed = analysis.proposed
    if fit is None:
        verdicts.append(("WARN", "Not enough readings above the dead zone to fit the TB-200S transfer."))
    elif proposed is None:
        verdicts.append(
            ("FAIL", "The TB-200S current does not increase with the command: check ADJ and the TB-200S input mode.")
        )
    else:
        low, high = DAC_LINEAR_RANGE_V
        if not (low <= proposed.low_v and proposed.high_v <= high):
            verdicts.append(("FAIL", f"The proposed constants leave the DAC linear range ({low}-{high} V)."))
        elif errors.max_abs > tolerance:
            verdicts.append(
                ("FIX", f"Apply the proposed constants below (expected error after update: max "
                        f"{fit.residuals.max_abs:.3f} A, RMS {fit.residuals.rms:.3f} A).")
            )
        else:
            verdicts.append(("OK", "No firmware change needed; the proposed constants are given for reference."))

        curved = math.isfinite(fit.quadratic_rms) and fit.quadratic_rms < CURVATURE_RMS_RATIO * fit.residuals.rms
        if fit.residuals.max_abs > tolerance and curved:
            verdicts.append(
                ("WARN", f"The transfer is curved: a straight line leaves up to {fit.residuals.max_abs:.3f} A. "
                         "Send this report to switch the firmware to a piecewise table.")
            )
        elif fit.residuals.max_abs > tolerance:
            verdicts.append(
                ("WARN", f"Readings scatter up to {fit.residuals.max_abs:.3f} A around the best line "
                         "(hysteresis or reading noise): a calibration cannot remove this scatter.")
            )
        else:
            verdicts.append(("OK", f"The transfer is linear (residuals within {fit.residuals.max_abs:.3f} A)."))

    if analysis.dead_zone_steps:
        verdicts.append(
            ("INFO", f"Reading(s) at or below {DEAD_ZONE_A} A excluded from the fit (TB-200S dead zone): "
                     f"step(s) {analysis.dead_zone_steps}.")
        )
    if analysis.hysteresis:
        worst = max(abs(difference) for _, _, difference in analysis.hysteresis)
        verdicts.append(
            ("WARN" if worst > tolerance else "OK",
             f"Hysteresis (down sweep - up sweep): up to {worst:.3f} A.")
        )
    if analysis.repeatability:
        worst = max(spread for _, _, spread in analysis.repeatability)
        verdicts.append(
            ("WARN" if worst > tolerance else "OK", f"Repeatability between cycles: up to {worst:.3f} A.")
        )
    if analysis.display_minus_ammeter is not None:
        difference = analysis.display_minus_ammeter
        verdicts.append(
            ("WARN" if difference.max_abs > DISPLAY_TOLERANCE_A else "OK",
             f"TB-200S display - ammeter: bias {difference.bias:+.3f} A, max {difference.max_abs:.3f} A "
             "(the fit uses the ammeter).")
        )
    if analysis.pa5_ratio is not None:
        deviation = analysis.pa5_ratio - 1.0
        if abs(deviation) > DAC_GAIN_TOLERANCE:
            verdicts.append(
                ("WARN", f"PA5 reads {deviation * 100:+.1f} % from the computed DAC voltage "
                         f"(VDDA about {DAC_VREF_V * analysis.pa5_ratio:.2f} V, or DAC output loaded).")
            )
        else:
            verdicts.append(("OK", f"PA5 matches the computed DAC voltage ({deviation * 100:+.1f} %)."))
    if analysis.adj_ratio is not None:
        ratio = analysis.adj_ratio
        if ratio < ADJ_LOADED_RATIO:
            input_ohm = SERIES_RESISTOR_OHM * ratio / (1.0 - ratio)
            verdicts.append(
                ("INFO", f"ADJ receives {ratio * 100:.0f} % of PA5: TB-200S ADJ input about {input_ohm / 1000:.1f} "
                         "kOhm behind the 1 kOhm resistor. The calibration compensates it.")
            )
        else:
            verdicts.append(("OK", f"ADJ follows PA5 ({ratio * 100:.0f} %): the ADJ input does not load the 1 kOhm."))
    if analysis.stopped_vs_running_a is not None:
        difference = analysis.stopped_vs_running_a
        if abs(difference) > tolerance:
            verdicts.append(
                ("WARN", f"At {MIN_LOAD_A} A the reading changes by {difference:+.3f} A between motor stopped and "
                         "running: PWM noise reaches ADJ. Twist ADJ with its GND wire, away from the motor phases.")
            )
        else:
            verdicts.append(("OK", f"Same reading at {MIN_LOAD_A} A with the motor stopped and running."))
    if len(analysis.iq_by_command) >= 2:
        (low_command, low_iq), (high_command, high_iq) = analysis.iq_by_command[0], analysis.iq_by_command[-1]
        rise = high_iq - low_iq
        verdicts.append(
            ("WARN" if rise < MIN_IQ_RISE_A else "OK",
             f"Motor Iq rises by {rise:+.2f} A from {low_command:.3f} to {high_command:.3f} A"
             + (": the brake hardly loads the motor (not coupled or coil not driven)." if rise < MIN_IQ_RISE_A else "."))
        )
    return verdicts


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value:.{digits}f}"


def fmt_reading(reading: Optional[Reading]) -> str:
    if reading is None:
        return "-"
    return f"{reading.value:.3f}" + ("~" if reading.spread > 0 else "")


def describe_errors(stats: ErrorStats) -> str:
    return f"n={stats.count}, bias {stats.bias:+.3f} A, RMS {stats.rms:.3f} A, max {stats.max_abs:.3f} A"


def describe_fit(fit: LineFit) -> str:
    text = (
        f"I = {fit.slope:.4f} A/V x (V_PA5 - {fit.threshold_v:.4f} V), R2 {fit.r2:.4f}, "
        f"residuals RMS {fit.residuals.rms:.3f} A / max {fit.residuals.max_abs:.3f} A ({fit.residuals.count} readings)"
    )
    if math.isfinite(fit.quadratic_rms):
        text += f", quadratic RMS {fit.quadratic_rms:.3f} A"
    return text


def build_report(session: Session, analysis: Analysis) -> str:
    lines: list[str] = []
    add = lines.append
    valid = sum(step.valid for step in session.steps)
    calibration = session.calibration

    add("TB-200S calibration report")
    add("==========================")
    add(f"Date: {session.started_at:%Y-%m-%d %H:%M:%S}")
    add(f"Port: {session.port}")
    add(f"Status: {'complete' if session.completed else 'INCOMPLETE'} ({valid}/{len(session.steps)} valid readings)")
    add(
        f"Calibration under test ({session.calibration_source}): {calibration.low_v:.4f} V -> {calibration.low_a:.3f} A, "
        f"{calibration.high_v:.4f} V -> {calibration.high_a:.3f} A"
    )
    add("Settings: " + ", ".join(session.settings))
    instruments = ["TB-200S display"]
    if session.ammeter:
        instruments.append("ammeter in series with the brake coil")
    if session.voltmeter:
        instruments.append("multimeter on PA5/ADJ")
    add("Instruments: " + ", ".join(instruments))
    if session.notes:
        add(f"Notes: {session.notes}")

    add("")
    add("Pre-checks")
    add("----------")
    diag = session.diag
    if diag is None:
        add("  DIAG not run.")
    else:
        add(f"  Bus voltage: {fmt(diag.vbus_v, 1)} V")
        if diag.wiring is not None:
            add(f"  TB-200S wiring (DIAG): {diag.wiring.status} - {diag.wiring.summary}")
        if diag.drive_code is not None:
            match = "match" if not session.flashed_mismatch else "MISMATCH"
            add(f"  Flashed calibration: DAC code {diag.drive_code} at {MAX_LOAD_A} A, source {diag.expected_code} ({match})")
            if diag.v_drive_mv is not None:
                add(
                    f"  PA5 ADC readback at that code: {diag.v_drive_mv} mV "
                    f"(computed {code_to_volts(diag.drive_code) * 1000:.0f} mV)"
                )
    add(
        f"  Motor stopped, command {MIN_LOAD_A:.3f} A: display {fmt_reading(session.stopped_display)} A"
        + (f", ammeter {fmt_reading(session.stopped_ammeter)} A" if session.ammeter else "")
    )

    add("")
    add("Readings")
    add("--------")
    add(
        f"{'#':>3} {'cyc':>3} {'dir':<4} {'cmd A':>6} {'code':>5} {'PA5 V':>6} {'echo A':>6} {'disp A':>7} "
        f"{'amm A':>7} {'PA5m V':>6} {'ADJm V':>6} {'rpm':>5} {'Iq A':>5}  note"
    )
    for step in session.steps:
        add(
            f"{step.index:>3} {step.cycle + 1:>3} {step.direction:<4} {step.command_a:>6.3f} {step.dac_code:>5} "
            f"{step.dac_v:>6.3f} {fmt(step.echo_a):>6} {fmt_reading(step.display):>7} {fmt_reading(step.ammeter):>7} "
            f"{fmt(step.pa5_v):>6} {fmt(step.adj_v):>6} {fmt(step.speed_rpm, 0):>5} {fmt(step.iq_a, 2):>5}  {step.note}"
        )
    add("PA5 V: computed from the flashed DAC code. PA5m/ADJm: measured. ~: the value alternated (mean shown).")

    add("")
    add("Analysis")
    add("--------")
    add(f"Reference for the fit: {analysis.reference} (tolerance {analysis.tolerance_a:.3f} A)")
    if analysis.saturated:
        add("Saturated commands, excluded from errors and fit: " + ", ".join(
            f"{command:.3f} A reads {level:.3f} A" for command, level in analysis.saturated
        ))
    add("Error of the calibration under test (reading - command):")
    for source, stats in analysis.command_errors.items():
        add(f"  {source:<8}: {describe_errors(stats)}")
    add(f"Measured TB-200S transfer (readings above {DEAD_ZONE_A} A against the computed PA5 voltage):")
    for source, fit in analysis.fits.items():
        add(f"  {source:<8}: {describe_fit(fit)}")
    if analysis.hysteresis:
        add("Hysteresis, down - up: " + ", ".join(
            f"c{cycle + 1} {command:.3f} A {difference:+.3f}" for cycle, command, difference in analysis.hysteresis
        ))
    if analysis.repeatability:
        add("Repeatability, max - min between cycles: " + ", ".join(
            f"{command:.3f} A {direction} {spread:.3f}" for direction, command, spread in analysis.repeatability
        ))
    if analysis.display_minus_ammeter is not None:
        add(f"Display - ammeter: {describe_errors(analysis.display_minus_ammeter)}")
    if analysis.pa5_ratio is not None:
        add(f"PA5 measured / computed: {analysis.pa5_ratio:.4f}")
    if analysis.adj_ratio is not None:
        add(f"ADJ / PA5: {analysis.adj_ratio:.4f}")
    if analysis.stopped_vs_running_a is not None:
        add(f"Reading at {MIN_LOAD_A} A, running - stopped: {analysis.stopped_vs_running_a:+.3f} A")
    if analysis.iq_by_command:
        add("Motor Iq by command: " + ", ".join(f"{command:.3f} A {iq:.2f}" for command, iq in analysis.iq_by_command))

    add("")
    add("Verdict")
    add("-------")
    for status, text in analysis.verdicts:
        add(f"[{status:^4}] {text}")

    proposed = analysis.proposed
    fit = analysis.fits.get(analysis.reference)
    if proposed is not None and fit is not None:
        add("")
        add(f"Proposed firmware constants (fitted on the {analysis.reference})")
        add("-------------------------------------------------------------")
        add("Same values in both files, then rebuild and flash both projects, and run this script again to verify:")
        add(f"  {ACQUISITION_SOURCE.relative_to(PROJECT_ROOT).as_posix()}")
        add(f"  {VALIDATION_SOURCE.relative_to(PROJECT_ROOT).as_posix()}")
        add("")
        lines.extend(define_lines(proposed))
        add("")
        add(
            f"PA5 at {MIN_LOAD_A} A: {calibration.pa5_voltage(MIN_LOAD_A):.4f} V -> {proposed.low_v:.4f} V; "
            f"at {MAX_LOAD_A} A: {calibration.pa5_voltage(MAX_LOAD_A):.4f} V -> {proposed.high_v:.4f} V"
        )
        add(f"Expected error after the update: RMS {fit.residuals.rms:.3f} A, max {fit.residuals.max_abs:.3f} A")

    add("")
    add("Events")
    add("------")
    for event in session.events or ["None."]:
        add(f"- {event}")
    return "\n".join(lines)


def csv_value(value: Optional[float]) -> str:
    return "" if value is None or not math.isfinite(value) else f"{value:.4f}"


def save_report(session: Session, report: str, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = session.started_at.strftime("%Y%m%d_%H%M%S")
    text_path = output_dir / f"tb200s_calibration_{stamp}.txt"
    csv_path = output_dir / f"tb200s_calibration_{stamp}.csv"
    text_path.write_text(report + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(CSV_FIELDS)
        for step in session.steps:
            writer.writerow(
                (
                    step.index,
                    step.cycle + 1,
                    step.direction,
                    f"{step.command_a:.4f}",
                    step.dac_code,
                    f"{step.dac_v:.4f}",
                    csv_value(step.echo_a),
                    csv_value(step.display.value if step.display else None),
                    csv_value(step.display.spread if step.display else None),
                    csv_value(step.ammeter.value if step.ammeter else None),
                    csv_value(step.ammeter.spread if step.ammeter else None),
                    csv_value(step.pa5_v),
                    csv_value(step.adj_v),
                    csv_value(step.speed_rpm),
                    csv_value(step.iq_a),
                    csv_value(step.iq_std_a),
                    int(step.valid),
                    step.note,
                )
            )
    return text_path, csv_path


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate the TB-200S load command through firmware_acquisition.")
    parser.add_argument("--port", help="ST-LINK virtual COM port, for example COM5.")
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    parser.add_argument("--speed", type=float, default=1500.0, help="Motor speed in rpm (100-4500).")
    parser.add_argument("--iq-limit", type=float, default=6.0, help="Iq limit in A.")
    parser.add_argument("--hard-limit", type=float, default=10.0, help="Total-current stop threshold in A.")
    parser.add_argument("--accel", type=float, default=50.0, help="Acceleration in electrical Hz/s (max 50).")
    parser.add_argument("--step", type=float, default=0.025, help="Load step in A (must split 0.05-0.25 A evenly).")
    parser.add_argument("--cycles", type=int, default=2, help="Number of up/down sweeps.")
    parser.add_argument("--settle", type=float, default=8.0, help="Wait after each load change before reading, in s.")
    parser.add_argument("--window", type=float, default=2.0, help="Telemetry averaging window per step, in s.")
    parser.add_argument("--warmup", type=float, default=120.0, help="Warm-up at mid load, in s (0 to skip).")
    parser.add_argument("--skip-diag", action="store_true", help="Do not run DIAG before starting the motor.")
    parser.add_argument(
        "--cal",
        type=float,
        nargs=4,
        metavar=("LOW_V", "LOW_A", "HIGH_V", "HIGH_A"),
        help="Calibration flashed in the board, when it differs from the firmware_acquisition source.",
    )
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR, help="Folder of the report and CSV.")
    parser.add_argument("--yes", action="store_true", help="Start the motor without confirmation.")
    args = parser.parse_args(argv)

    if not 100.0 <= args.speed <= 4500.0:
        parser.error("--speed must be between 100 and 4500 rpm")
    if not 0.0 < args.iq_limit <= 30.0 or not 0.0 < args.hard_limit <= 30.0:
        parser.error("--iq-limit and --hard-limit must be in ]0, 30] A")
    if not 0.0 < args.accel <= 50.0:
        parser.error("--accel must be in ]0, 50] electrical Hz/s")
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.settle < 0.0 or args.window <= 0.0 or args.warmup < 0.0:
        parser.error("--settle and --warmup must be >= 0 and --window > 0")
    if args.cal is not None and not (args.cal[2] > args.cal[0] and args.cal[3] > args.cal[1]):
        parser.error("--cal needs HIGH_V > LOW_V and HIGH_A > LOW_A")
    try:
        args.points = load_points(args.step)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def settings_of(args: argparse.Namespace) -> list[str]:
    return [
        f"speed {args.speed:.0f} rpm",
        f"Iq limit {args.iq_limit:.1f} A",
        f"hard limit {args.hard_limit:.1f} A",
        f"accel {args.accel:.0f} Hz_e/s",
        f"step {args.step:.3f} A",
        f"cycles {args.cycles}",
        f"settle {args.settle:.1f} s",
        f"window {args.window:.1f} s",
        f"warm-up {args.warmup:.0f} s",
    ]


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.cal is not None:
            calibration, source = Calibration(*args.cal), "--cal"
        else:
            calibration = read_calibration(ACQUISITION_SOURCE)
            source = ACQUISITION_SOURCE.relative_to(PROJECT_ROOT).as_posix()
        validation_calibration = read_calibration(VALIDATION_SOURCE)
    except (OSError, ValueError) as exc:
        print(f"Cannot read the firmware calibration: {exc}")
        return 2

    port = args.port or default_port()
    if port is None:
        print("Use --port. Available ports: " + (", ".join(available_ports()) or "none"))
        return 2
    try:
        connection = serial.Serial(port, args.baud, timeout=SERIAL_READ_TIMEOUT_S)
    except serial.SerialException as exc:
        print(f"Cannot open {port}: {exc}")
        print("Close the dashboard, Motor Pilot, or any terminal using the port, and check the USB cable.")
        return 2

    session = Session(port, settings_of(args), calibration, source)
    if validation_calibration != calibration:
        session.events.append("firmware_validation uses other APP_TB200S_CAL_* values than the calibration under test.")

    with connection:
        connection.reset_input_buffer()
        link = BoardLink(connection)
        kind, lines = identify_firmware(link)
        if kind != "acquisition":
            problem = firmware_problem(kind, port, lines)
            print(problem.summary)
            for line in problem.causes + problem.actions:
                print(f"  - {line}")
            return 1
        try:
            run_session(link, args, Operator(), session)
        except AbortSession:
            session.events.append("Session stopped by the operator.")
        except KeyboardInterrupt:
            session.events.append("Session interrupted with Ctrl+C.")
        except SessionError as exc:
            session.events.append(str(exc))
        finally:
            print("\nStopping the motor...")
            shutdown(link)

    report = build_report(session, analyse(session))
    print("\n" + report)
    text_path, csv_path = save_report(session, report, args.output_dir)
    print(f"\nReport:   {text_path}\nReadings: {csv_path}")
    print("Send the report file to update the firmware calibration.")
    return 0 if session.completed else 1


if __name__ == "__main__":
    sys.exit(main())
