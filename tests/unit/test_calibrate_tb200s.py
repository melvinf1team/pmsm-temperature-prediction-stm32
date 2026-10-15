from __future__ import annotations

import math
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable, Optional

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

from calibrate_tb200s import (
    ACQUISITION_SOURCE,
    VALIDATION_SOURCE,
    AMMETER,
    CURRENT_UNITS,
    DISPLAY,
    VOLTAGE_UNITS,
    AbortSession,
    Calibration,
    Operator,
    Reading,
    RepeatStep,
    Session,
    SkipStep,
    Step,
    analyse,
    build_plan,
    build_report,
    code_to_volts,
    define_lines,
    find_saturation,
    load_points,
    parse_args,
    parse_reading,
    read_calibration,
    run_prechecks,
    run_session,
    save_report,
    settings_of,
)
from check_wiring import CSV_COLUMNS, BoardLink
from test_check_wiring import diag_transcript

CAL = Calibration(0.4352, 0.05, 1.2210, 0.25)


def inverse_of(calibration: Calibration) -> Callable[[float], float]:
    """TB-200S transfer that the calibration compensates exactly."""
    slope = (calibration.high_a - calibration.low_a) / (calibration.high_v - calibration.low_v)
    return lambda volts: calibration.low_a + (volts - calibration.low_v) * slope


def synthetic_session(
    current_of_volts: Callable[[float], float],
    cycles: int = 2,
    down_offset_a: float = 0.0,
    ammeter: bool = False,
) -> Session:
    session = Session("TEST", [], CAL, "test")
    for planned in build_plan(load_points(0.025), cycles):
        code = CAL.dac_code(planned.command_a)
        volts = code_to_volts(code)
        current = max(0.0, current_of_volts(volts)) + (down_offset_a if planned.direction == "down" else 0.0)
        step = Step(
            len(session.steps) + 1,
            planned.cycle,
            planned.direction,
            planned.command_a,
            code,
            volts,
            display=Reading(round(current, 2)),
            echo_a=planned.command_a,
            iq_a=0.5 + 4.0 * planned.command_a,
        )
        if ammeter:
            step.ammeter = Reading(round(current, 3))
        session.steps.append(step)
    session.completed = True
    return session


class FakeBoard:
    """Serial stand-in emulating the firmware_acquisition protocol."""

    def __init__(self, diag_lines: Optional[list[str]] = None, fault_at_load: Optional[float] = None) -> None:
        self.diag_lines = diag_lines or []
        self.fault_at_load = fault_at_load
        self.running = False
        self.app_state = "IDLE"
        self.fault_reason = "NONE"
        self.requested = 0.05
        self.load = 0.05
        self.pending = b""
        self.partial = b""
        self.written: list[str] = []
        self.next_row = 0.0
        self.ms = 0

    def reply(self, text: str) -> None:
        self.pending += text.encode("ascii")

    def stop(self, state: str, reason: str = "NONE") -> None:
        self.running = False
        self.app_state = state
        self.fault_reason = reason
        self.load = 0.05

    def write(self, data: bytes) -> None:
        self.partial += data
        while b"\n" in self.partial:
            raw, self.partial = self.partial.split(b"\n", 1)
            self.handle(raw.decode("ascii").strip())

    def handle(self, command: str) -> None:
        self.written.append(command)
        if command in ("SYNC", "STOP"):
            self.stop("IDLE")
            self.reply(f"ACK,{command}\r\n")
        elif command.startswith("CFG,"):
            self.reply("ACK,CFG\r\n#CFG_OK\r\n")
        elif command.startswith("LOAD,"):
            self.requested = float(command[5:])
            self.load = self.requested if self.running else 0.05
            if self.running and self.fault_at_load is not None and math.isclose(self.load, self.fault_at_load):
                self.fault_at_load = None
                self.stop("FAULT", "HARD_OVERCURRENT")
            self.reply("ACK,LOAD\r\n")
        elif command == "START":
            self.running = True
            self.app_state = "RUNNING"
            self.fault_reason = "NONE"
            self.load = self.requested
            self.reply("#LOG_START\r\n#CSV_HEADER," + ",".join(CSV_COLUMNS) + "\r\nACK,START\r\n")
        elif command == "STATUS":
            self.reply(
                f"STATUS,app_state={self.app_state},fault_reason={self.fault_reason},mc_state=6,"
                f"faults_now=0x0000,faults_occurred=0x0000,vbus_mv=24000,speed_rpm=1500,"
                f"load_ma={round(self.load * 1000)}\r\nACK,STATUS\r\n"
            )
        elif command == "DIAG":
            self.reply("\r\n".join(self.diag_lines) + "\r\nACK,DIAG\r\n")

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.pending = b""

    def read(self, size: int) -> bytes:
        now = time.monotonic()
        if self.running and now >= self.next_row:
            self.next_row = now + 0.01
            self.ms += 10
            iq = 0.5 + 4.0 * self.load
            self.reply(f"DATA,{self.ms},22.0,23.0,0.500,4.000,1500.000,0.000,{iq:.3f},{self.load:.3f}\r\n")
        if not self.pending:
            time.sleep(0.001)
            return b""
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk


def scripted(board: FakeBoard, overrides: Optional[dict[str, object]] = None) -> Callable[[str], str]:
    replies: dict[str, object] = {
        "Press Enter": "",
        "DC ammeter": "n",
        "multimeter during": "n",
        "Bench notes": "",
        "TB-200S display": lambda: f"{board.load:.2f}",
        "Restart the motor": "y",
        "Continue anyway": "n",
    }
    replies.update(overrides or {})

    def ask(prompt: str) -> str:
        for key, value in replies.items():
            if key in prompt:
                return value() if callable(value) else str(value)
        raise AssertionError(f"Unexpected prompt: {prompt!r}")

    return ask


def quiet_operator(ask: Callable[[str], str]) -> Operator:
    return Operator(ask, lambda _text: None)


FAST_ARGS = ["--cycles", "1", "--step", "0.1", "--settle", "0.02", "--window", "0.08", "--warmup", "0", "--yes"]


class CalibrationTests(unittest.TestCase):
    def test_dac_codes_follow_the_firmware_rounding(self) -> None:
        self.assertEqual(CAL.dac_code(0.05), 540)
        self.assertEqual(CAL.dac_code(0.25), 1515)
        self.assertEqual(Calibration(0.0, 0.0, 10.0, 0.01).dac_code(0.25), 4095)

    def test_both_firmwares_share_the_calibration(self) -> None:
        self.assertEqual(read_calibration(ACQUISITION_SOURCE), read_calibration(VALIDATION_SOURCE))

    def test_define_lines_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "app_motor_control.c"
            source.write_text("\n".join(define_lines(CAL)) + "\n", encoding="utf-8")
            self.assertEqual(read_calibration(source), CAL)


class ParsingTests(unittest.TestCase):
    def test_currents(self) -> None:
        self.assertEqual(parse_reading("0.15", CURRENT_UNITS, "a"), Reading(0.15, 0.0))
        self.assertEqual(parse_reading("0,15", CURRENT_UNITS, "a"), Reading(0.15, 0.0))
        self.assertAlmostEqual(parse_reading("152", CURRENT_UNITS, "ma").value, 0.152)
        self.assertAlmostEqual(parse_reading("0.152 A", CURRENT_UNITS, "ma").value, 0.152)

    def test_alternating_display(self) -> None:
        reading = parse_reading("0.15/0.16", CURRENT_UNITS, "a")
        self.assertAlmostEqual(reading.value, 0.155)
        self.assertAlmostEqual(reading.spread, 0.01)
        self.assertAlmostEqual(parse_reading("0.15 - 0.16", CURRENT_UNITS, "a").value, 0.155)

    def test_rejected_inputs(self) -> None:
        self.assertIsNone(parse_reading("abc", CURRENT_UNITS, "a"))
        self.assertIsNone(parse_reading("12 mV", CURRENT_UNITS, "a"))
        self.assertAlmostEqual(parse_reading("830 mV", VOLTAGE_UNITS, "v").value, 0.83)

    def test_operator_retries_and_actions(self) -> None:
        answers = iter(["15", "0.15"])
        operator = quiet_operator(lambda _prompt: next(answers))
        self.assertEqual(operator.reading("display", DISPLAY).value, 0.15)
        answers = iter(["0.152", "152"])
        operator = quiet_operator(lambda _prompt: next(answers))
        self.assertAlmostEqual(operator.reading("ammeter", AMMETER).value, 0.152)
        for text, error in (("r", RepeatStep), ("s", SkipStep), ("q", AbortSession)):
            with self.assertRaises(error):
                quiet_operator(lambda _prompt, text=text: text).reading("display", DISPLAY, step_actions=True)


class PlanTests(unittest.TestCase):
    def test_points_and_sweeps(self) -> None:
        points = load_points(0.025)
        self.assertEqual(len(points), 9)
        plan = build_plan(points, 2)
        self.assertEqual(len(plan), 33)
        self.assertEqual((plan[0].direction, plan[0].command_a), ("up", 0.05))
        self.assertEqual((plan[9].direction, plan[9].command_a), ("down", 0.225))
        self.assertEqual((plan[17].cycle, plan[17].direction, plan[17].command_a), (1, "up", 0.075))

    def test_uneven_step_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_points(0.03)


class AnalysisTests(unittest.TestCase):
    def test_matching_transfer_needs_no_change(self) -> None:
        analysis = analyse(synthetic_session(inverse_of(CAL)))
        self.assertEqual(analysis.reference, "display")
        self.assertEqual(analysis.saturated, [])
        self.assertEqual(analysis.verdicts[0][0], "OK")
        self.assertTrue(any("No firmware change" in text for _status, text in analysis.verdicts))

    def test_saturation_is_excluded(self) -> None:
        analysis = analyse(synthetic_session(lambda volts: min(inverse_of(CAL)(volts), 0.23)))
        self.assertEqual([command for command, _level in analysis.saturated], [0.25])
        self.assertEqual(analysis.verdicts[0][0], "OK")
        self.assertTrue(any(status == "WARN" and "saturates" in text for status, text in analysis.verdicts))

    def test_display_rounding_is_not_saturation(self) -> None:
        points = [(command, CAL.pa5_voltage(command), round(command, 2)) for command in load_points(0.01)]
        self.assertEqual(find_saturation(points, 0.01), [])

    def test_offset_transfer_is_recovered_from_the_ammeter(self) -> None:
        analysis = analyse(synthetic_session(lambda volts: 0.30 * (volts - 0.30), ammeter=True))
        self.assertEqual(analysis.reference, "ammeter")
        self.assertAlmostEqual(analysis.fits["ammeter"].slope, 0.30, delta=0.003)
        self.assertAlmostEqual(analysis.fits["ammeter"].threshold_v, 0.30, delta=0.005)
        self.assertAlmostEqual(analysis.proposed.low_v, 0.30 + 0.05 / 0.30, delta=0.003)
        self.assertAlmostEqual(analysis.proposed.high_v, 0.30 + 0.25 / 0.30, delta=0.003)
        self.assertIn("FIX", [status for status, _text in analysis.verdicts])

    def test_hysteresis_is_reported(self) -> None:
        analysis = analyse(synthetic_session(inverse_of(CAL), down_offset_a=0.02))
        self.assertTrue(any(status == "WARN" and text.startswith("Hysteresis") for status, text in analysis.verdicts))
        self.assertTrue(all(abs(difference - 0.02) < 1e-9 for _c, _a, difference in analysis.hysteresis))

    def test_dead_zone_readings_are_excluded(self) -> None:
        analysis = analyse(synthetic_session(lambda volts: 0.30 * (volts - 0.50)))
        self.assertTrue(analysis.dead_zone_steps)
        self.assertTrue(any(status == "INFO" and "dead zone" in text for status, text in analysis.verdicts))
        self.assertAlmostEqual(analysis.fits["display"].threshold_v, 0.50, delta=0.03)

    def test_report_and_csv(self) -> None:
        session = synthetic_session(lambda volts: 0.30 * (volts - 0.30))
        report = build_report(session, analyse(session))
        self.assertIn("Readings", report)
        self.assertIn("#define APP_TB200S_CAL_LOW_V", report)
        with tempfile.TemporaryDirectory() as folder:
            text_path, csv_path = save_report(session, report, Path(folder))
            self.assertTrue(text_path.exists())
            self.assertEqual(len(csv_path.read_text(encoding="utf-8").splitlines()), len(session.steps) + 1)


class SessionTests(unittest.TestCase):
    def run_fake_session(self, board: FakeBoard, extra_args: list[str]) -> Session:
        args = parse_args(FAST_ARGS + extra_args)
        session = Session("FAKE", settings_of(args), CAL, "test")
        run_session(BoardLink(board, char_delay_s=0.0), args, quiet_operator(scripted(board)), session)
        return session

    def test_complete_session(self) -> None:
        board = FakeBoard()
        session = self.run_fake_session(board, ["--skip-diag"])
        self.assertTrue(session.completed)
        self.assertEqual([step.command_a for step in session.steps], [0.05, 0.15, 0.25, 0.15, 0.05])
        self.assertTrue(all(step.valid for step in session.steps))
        self.assertTrue(all(abs(step.echo_a - step.command_a) < 1e-6 for step in session.steps))
        self.assertIn("LOAD,0.250", board.written)
        self.assertEqual(analyse(session).verdicts[0][0], "OK")

    def test_motor_fault_is_restarted_and_the_step_redone(self) -> None:
        board = FakeBoard(fault_at_load=0.25)
        session = self.run_fake_session(board, ["--skip-diag"])
        self.assertTrue(session.completed)
        invalid = [step for step in session.steps if not step.valid]
        self.assertEqual([step.command_a for step in invalid], [0.25])
        self.assertEqual(len(session.steps), 6)
        self.assertTrue(any("HARD_OVERCURRENT" in event for event in session.events))
        self.assertEqual(board.written.count("START"), 2)

    def test_prechecks_accept_the_flashed_calibration(self) -> None:
        board = FakeBoard(diag_transcript(dac={"drive_code": "1515", "v_drive_mv": "1221"}))
        args = parse_args(FAST_ARGS)
        session = Session("FAKE", settings_of(args), CAL, "test")
        self.assertTrue(run_prechecks(BoardLink(board, char_delay_s=0.0), args, quiet_operator(scripted(board)), session))
        self.assertFalse(session.flashed_mismatch)
        self.assertAlmostEqual(session.diag.vbus_v, 24.0)

    def test_prechecks_stop_on_another_flashed_calibration(self) -> None:
        board = FakeBoard(diag_transcript())
        args = parse_args(FAST_ARGS)
        session = Session("FAKE", settings_of(args), CAL, "test")
        self.assertFalse(run_prechecks(BoardLink(board, char_delay_s=0.0), args, quiet_operator(scripted(board)), session))
        self.assertTrue(session.flashed_mismatch)


if __name__ == "__main__":
    unittest.main()
