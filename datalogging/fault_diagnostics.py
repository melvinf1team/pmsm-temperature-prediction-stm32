"""Decode firmware motor-stop reports and identify their source.

Shared by the acquisition dashboard (``motor_datalog_gui_dashboard.py``) and
the validation interface (``validation/test/temperature_validation_gui.py``).
Both firmware projects send the same lines after an unexpected motor stop::

    #FAULT,<reason>,t_ms=...,speed_mrpm=...,...,mc_now=0x...,mc_occurred=0x...
    #FAULT_DIAG,break=<0|1>,...,trace_n=...,foc_period_ns=...,foc_max_ns=...,...
    #FAULT_TRACE,<first index>,<u>:<v>:<w>:<vbus>,...   (0.1 A / 0.1 V)

This module only uses the standard library, so it can be imported without
Tkinter, serial or matplotlib.
"""

from __future__ import annotations

import math
from datetime import datetime

MOTOR_FAULT_DESCRIPTIONS = {
    "OVERSPEED": "Measured speed exceeded the shutdown threshold.",
    "HARD_OVERCURRENT": "Measured Id/Iq current exceeded the hard-stop limit.",
    "MCSDK_FAULT": "The motor-control SDK reported a protection fault.",
    "MCSDK_STOPPED": "MCSDK unexpectedly left the running state.",
    "D6T_OVERTEMP": "D6T motor temperature exceeded 70 C.",
    "D6T_UNAVAILABLE": "D6T motor temperature is missing, invalid or stale.",
    "CUSTOM_SPEED_TIMEOUT": "Speed failed to follow the reference for 5 seconds.",
    "CUSTOM_MOTOR_STOPPED": "MCSDK left RUN during the Custom startup sequence.",
    "STARTUP_TIMEOUT": "MCSDK did not reach RUN within the startup timeout.",
    "START_REJECTED": "MCSDK rejected the start command.",
    "MCSDK_NOT_IDLE": "MCSDK was not ready to accept a start command.",
    "LOAD_DAC": "The TB-200S load command could not be written to the DAC.",
    "NONFINITE_SPEED": "The measured motor speed is invalid.",
    "NONFINITE_CURRENT": "The measured motor current is invalid.",
}
MC_DURATION = 0x0001
MC_OVER_CURR = 0x0040
MCSDK_FAULT_BITS = {
    MC_DURATION: "FOC duration", 0x0002: "overvoltage", 0x0004: "undervoltage",
    0x0008: "power-board overtemperature", 0x0010: "startup failure",
    0x0020: "speed feedback", MC_OVER_CURR: "TIM8 break: comparator OCP or power-board fault line",
    0x0080: "software error", 0x0100: "current-sampling fault",
    0x0200: "software overcurrent", 0x0400: "driver protection",
}

# Hardware-break diagnostics (app_fault_diag.c). Board values come from the
# Workbench project: OCP threshold, LS in pmsm_motor_parameters.h, regen limit.
OCP_THRESHOLD_FALLBACK_A = 110.0
MOTOR_LS_H = 17e-6
VBUS_REGEN_HIGH_V = 19.44
VBUS_SAG_V = 10.0
BREAK_PROBABLE_OCP_RATIO = 0.80
COMPARATOR_PHASES = ((0x1, "U (COMP4)"), (0x2, "V (COMP6)"), (0x4, "W (COMP7)"))
TIM_SR_BREAK_FLAGS = ((0x0080, "BIF"), (0x0100, "B2IF"), (0x2000, "SBIF"))
# FOC timing (app_foc_timing.c): a start delayed by more than this is not
# ISR-entry jitter but masked interrupts or another IRQ in front of the FOC.
FOC_LATE_SIGNIFICANT_NS = 2000
FAULT_REPORT_TIMEOUT_MS = 1500

FAULT_VERDICT_BOARD = "Power-board fault line (PB7)"
FAULT_VERDICT_COMPARATOR = "Real phase overcurrent (comparator)"
FAULT_VERDICT_PROBABLE_OCP = "Probable real overcurrent"
FAULT_VERDICT_GLITCH = "Probable glitch (noise on the break inputs)"
FAULT_VERDICT_UNDETERMINED = "Undetermined break source"
FAULT_VERDICT_NO_BREAK = "No hardware break"
FAULT_VERDICT_FOC_LATE = "FOC overrun: interrupt started late (CPU timing, not electrical)"
FAULT_VERDICT_FOC_SLOW = "FOC overrun: computation too long (CPU timing, not electrical)"
FAULT_VERDICT_FOC_OVERRUN = "FOC overrun (CPU timing, not electrical)"
FAULT_VERDICT_NO_DIAG = "No firmware diagnostic"


def parse_fields(line):
    """Return the ``key=value`` fields of a comma-separated firmware line."""
    return dict(part.split("=", 1) for part in line.strip().split(",")[1:] if "=" in part)


def fault_masks(fault_line):
    """Return the MCSDK ``(mc_now, mc_occurred)`` masks of a ``#FAULT`` line, 0 if absent."""
    fields = parse_fields(fault_line or "")
    masks = []
    for key in ("mc_now", "mc_occurred"):
        try:
            masks.append(int(fields[key], 0))
        except (KeyError, ValueError):
            masks.append(0)
    return masks[0], masks[1]


def describe_motor_fault(line):
    """Decode both legacy #FAULT lines and detailed firmware stop snapshots."""
    parts = line.split(",")
    reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "UNKNOWN"
    fields = dict(part.split("=", 1) for part in parts[2:] if "=" in part)
    detail = [MOTOR_FAULT_DESCRIPTIONS.get(reason, "The firmware stopped the motor.")]
    for key, label, scale, unit in (
        ("speed_mrpm", "speed", 1000.0, "rpm"),
        ("speed_limit_rpm", "shutdown speed", 1.0, "rpm"),
        ("iq_ma", "Iq", 1000.0, "A"), ("id_ma", "Id", 1000.0, "A"),
        ("hard_ma", "hard current limit", 1000.0, "A"),
        ("load_ma", "brake command", 1000.0, "A"),
    ):
        try:
            value = float(fields[key]) / scale
            if math.isfinite(value):
                detail.append(f"{label} {value:.3f} {unit}")
        except (KeyError, ValueError):
            pass
    for key in ("mc_now", "mc_occurred"):
        try:
            mask = int(fields[key], 0)
        except (KeyError, ValueError):
            continue
        if mask:
            names = [name for bit, name in MCSDK_FAULT_BITS.items() if mask & bit]
            unknown = mask & ~sum(MCSDK_FAULT_BITS)
            if unknown:
                names.append(f"unknown bits 0x{unknown:04X}")
            detail.append(f"{key}=0x{mask:04X} ({', '.join(names)})")
    return reason, " | ".join(detail)


def parse_fault_trace(line):
    """Parse ``#FAULT_TRACE,<first>,u:v:w:vbus,...`` into amps and volts.

    Returns:
        tuple[int, list[tuple[float, float, float, float]]]: Index of the first
        sample relative to the break (-1 is the last one) and the samples.
    """
    parts = line.strip().split(",")
    first = int(parts[1])
    samples = []
    for item in parts[2:]:
        values = item.split(":")
        if len(values) != 4:
            raise ValueError(f"Invalid trace sample: {item}")
        samples.append(tuple(int(value) / 10.0 for value in values))
    return first, samples


def _mask_names(mask, names):
    return ", ".join(name for bit, name in names if mask & bit) or "none"


def _diagnose_foc_overrun(number, details):
    """Explain an MC_DURATION stop from the FOC timing fields of ``#FAULT_DIAG``."""
    if number("foc_period_ns", -1.0) < 0:
        details.append(
            "MC_DURATION: the duty cycles were written after the next TIM8 update. This firmware does "
            "not report FOC timing; flash the current firmware to tell a delayed start from a slow "
            "computation."
        )
        return FAULT_VERDICT_FOC_OVERRUN
    period_us = number("foc_period_ns") / 1000.0
    if int(number("foc_overruns")) <= 0:
        details.append("MC_DURATION was reported but no overrun period was captured since START: "
                       "compare the maximum start delay and duration above with the period.")
        return FAULT_VERDICT_FOC_OVERRUN
    late_us = number("foc_overrun_late_ns") / 1000.0
    duration_us = number("foc_overrun_ns") / 1000.0
    details.append(f"Overrun period: started {late_us:.1f} us late, HF task ran {duration_us:.1f} us "
                   f"of the {period_us:.1f} us period.")
    if number("foc_overrun_late_ns") >= FOC_LATE_SIGNIFICANT_NS:
        details.append(
            "The FOC interrupt started late: code masked interrupts (__disable_irq) or another IRQ of the "
            "same or higher priority ran just before it. Look for critical sections in the main loop "
            "(1-Wire, bit-banged buses, flash writes)."
        )
        return FAULT_VERDICT_FOC_LATE
    details.append(
        "The FOC started on time but the computation did not finish before the TIM8 update: reduce the "
        "work done in TSK_HighFrequencyTask."
    )
    return FAULT_VERDICT_FOC_SLOW


def diagnose_fault_source(diag_line, trace=(), fault_line=""):
    """Identify which protection stopped the motor from ``#FAULT_DIAG`` and its trace.

    The MCSDK reports both break sources as MC_OVER_CURR. The firmware reads
    them at the start of the break IRQ: the external BKIN pin PB7 (power-board
    fault line, active low) and the COMP4/6/7 outputs (phase overcurrent). If
    both are already released, the phase currents sampled every FOC period
    decide: with ``LS`` and Vbus, the current cannot rise by more than
    ``Vbus / LS * period`` between two samples, so a last sample far below the
    threshold rules out a real overcurrent.

    Without a break, an MC_DURATION bit in the ``#FAULT`` masks means the FOC
    wrote its duty cycles too late; the FOC timing fields tell a delayed
    interrupt (masked IRQs) from a computation that is too long.

    Args:
        diag_line: Raw ``#FAULT_DIAG`` line.
        trace: ``(u, v, w, vbus)`` samples, oldest first, in A and V.
        fault_line: Raw ``#FAULT`` line, for its MCSDK fault masks.

    Returns:
        tuple[str, list[str]]: Verdict label and the evidence lines.
    """
    fields = parse_fields(diag_line)

    def number(key, default=0.0, scale=1.0):
        try:
            return int(fields[key], 0) / scale
        except (KeyError, ValueError):
            return default

    ocp_a = number("ocp_da", OCP_THRESHOLD_FALLBACK_A * 10.0, 10.0)
    peak_run_a = number("peak_run_da", math.nan, 10.0)
    peak_recent_a = number("peak_recent_da", math.nan, 10.0)
    period_us = number("trace_period_us", 62.0)
    details = [
        f"Phase-current peak since START {peak_run_a:.1f} A, last 20 ms {peak_recent_a:.1f} A "
        f"(OCP threshold {ocp_a:.0f} A)."
    ]
    if number("foc_period_ns", -1.0) >= 0:
        details.append(
            f"FOC timing since START: HF task up to {number('foc_max_ns') / 1000.0:.1f} us, start delayed "
            f"up to {number('foc_late_max_ns') / 1000.0:.1f} us (period "
            f"{number('foc_period_ns') / 1000.0:.1f} us)."
        )
    quiet_timeouts = int(number("quiet_timeouts"))
    if quiet_timeouts:
        details.append(f"{quiet_timeouts} DS18B20 1-Wire slot(s) were dropped because no FOC-free window "
                       "was found: the FOC leaves little idle time per period.")

    if not int(number("break")):
        _now, occurred = fault_masks(fault_line)
        if occurred & MC_DURATION:
            return _diagnose_foc_overrun(number, details), details
        details.insert(0, "No TIM8 break captured: another protection stopped the motor.")
        return FAULT_VERDICT_NO_BREAK, details

    bkin_low = bool(number("bkin_low"))
    comp = int(number("comp"))
    irq_count = int(number("irq_count"))
    tim_sr = int(number("tim_sr"))
    details.append(
        f"At the break IRQ: PB7 {'LOW (board fault asserted)' if bkin_low else 'high'}, "
        f"comparators above threshold: {_mask_names(comp, COMPARATOR_PHASES)}, "
        f"TIM8 flags: {_mask_names(tim_sr, TIM_SR_BREAK_FLAGS)}."
    )
    if number("bkin_now_low"):
        details.append("PB7 is still LOW when the report is sent: the board keeps its fault asserted "
                       "(driver supply/UVLO, board protection or wiring).")
    if irq_count > 1:
        details.append(f"The break IRQ fired {irq_count} times: the source chatters.")

    last_peak_a = math.nan
    max_rise_a = math.nan
    if trace:
        peaks = [max(abs(u), abs(v), abs(w)) for u, v, w, _ in trace]
        vbus = [sample[3] for sample in trace]
        last_peak_a = peaks[-1]
        max_rise_a = max(vbus) / MOTOR_LS_H * period_us * 1e-6
        u, v, w, _ = trace[-1]
        details.append(
            f"Last FOC sample before the break: U {u:.1f} A, V {v:.1f} A, W {w:.1f} A; "
            f"max |I| over the last {len(trace) * period_us / 1000.0:.1f} ms {max(peaks):.1f} A."
        )
        details.append(f"Vbus over the trace: {min(vbus):.1f}-{max(vbus):.1f} V.")
        if max(vbus) > VBUS_REGEN_HIGH_V:
            details.append(f"Vbus exceeded {VBUS_REGEN_HIGH_V:.2f} V: regenerative braking pumped the bus.")
        if min(vbus) < VBUS_SAG_V:
            details.append(f"Vbus fell below {VBUS_SAG_V:.0f} V: supply sag, the driver may have hit UVLO.")

    if comp:
        verdict = FAULT_VERDICT_COMPARATOR
        details.append(f"Phase {_mask_names(comp, COMPARATOR_PHASES)} crossed ~{ocp_a:.0f} A: "
                       "check angle estimation, current-loop stability and speed/load steps.")
    elif bkin_low:
        verdict = FAULT_VERDICT_BOARD
        details.append("The STDES-LVHP01 / L6491 fault line cut the PWM, not the MCU comparators: "
                       "check the driver supply, board protections and the PB7 wiring.")
    elif trace and last_peak_a + max_rise_a < ocp_a:
        verdict = FAULT_VERDICT_GLITCH
        details.append(
            f"Both sources had already released and the last sample ({last_peak_a:.1f} A) could rise by at "
            f"most ~{max_rise_a:.0f} A in one {period_us:.0f} us period: the current never reached "
            f"{ocp_a:.0f} A. Suspect EMI on PB7/BKIN or the comparator inputs; raise the break filter "
            "or improve wiring/grounding."
        )
    elif max(last_peak_a if trace else 0.0, peak_recent_a if math.isfinite(peak_recent_a) else 0.0) \
            >= BREAK_PROBABLE_OCP_RATIO * ocp_a:
        verdict = FAULT_VERDICT_PROBABLE_OCP
        details.append("The source released before the IRQ read, but the sampled current was already "
                       f"near {ocp_a:.0f} A: treat it as a real overcurrent.")
    else:
        verdict = FAULT_VERDICT_UNDETERMINED
        details.append("The source released before the IRQ read and no trace allows a conclusion.")
    return verdict, details


def format_fault_report(verdict, details, raw_lines, now=None):
    """Return the text of a saved fault report: verdict, evidence, raw firmware lines."""
    now = now or datetime.now()
    content = [
        f"Fault report {now.isoformat(timespec='seconds')}",
        f"Verdict: {verdict}",
        *(f"- {line}" for line in details),
        "",
        "Raw firmware lines:",
        *(line for line in raw_lines if line),
    ]
    return "\n".join(content) + "\n"


class FaultReportCollector:
    """Assemble ``#FAULT``, ``#FAULT_DIAG`` and its ``#FAULT_TRACE`` lines.

    ``feed`` returns True once the report is complete: the diagnostic line
    arrived with every announced trace sample. A caller that waits longer
    than ``FAULT_REPORT_TIMEOUT_MS`` calls ``diagnose`` with what it has.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.fault_line = ""
        self.diag_line = ""
        self.trace_lines = []
        self.samples = []
        self.expected = 0

    @property
    def active(self):
        """Whether a report is being collected."""
        return bool(self.fault_line or self.diag_line)

    @property
    def complete(self):
        return bool(self.diag_line) and len(self.samples) >= self.expected

    def feed(self, line):
        """Add one firmware line; return True when the report is complete.

        Raises:
            ValueError: For a malformed ``#FAULT_TRACE`` line, which is ignored.
        """
        line = line.strip()
        if line.startswith("#FAULT,"):
            self.reset()
            self.fault_line = line
            return False
        if line.startswith("#FAULT_DIAG,"):
            self.diag_line = line
            self.trace_lines = []
            self.samples = []
            try:
                self.expected = max(0, int(parse_fields(line).get("trace_n", "0")))
            except ValueError:
                self.expected = 0
            return self.complete
        if line.startswith("#FAULT_TRACE,") and self.diag_line:
            try:
                _first, samples = parse_fault_trace(line)
            except IndexError as exc:
                raise ValueError(f"Invalid fault trace: {line}") from exc
            self.trace_lines.append(line)
            self.samples.extend(samples)
            return self.complete
        return False

    def diagnose(self):
        """Return ``(verdict, details)`` for the lines received so far."""
        if not self.diag_line:
            return FAULT_VERDICT_NO_DIAG, [
                "The firmware sent no #FAULT_DIAG line after #FAULT: flash the current firmware to get "
                "the source of the stop."
            ]
        verdict, details = diagnose_fault_source(self.diag_line, self.samples, self.fault_line)
        if len(self.samples) < self.expected:
            details.append(f"Trace incomplete: {len(self.samples)}/{self.expected} samples received.")
        return verdict, details

    def raw_lines(self):
        return [self.fault_line, self.diag_line, *self.trace_lines]
