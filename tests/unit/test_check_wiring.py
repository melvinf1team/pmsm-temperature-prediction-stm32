from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import check_wiring
from check_wiring import (
    RAIL_CAUSE,
    VALIDATION_FIRMWARE_LINE,
    BoardLink,
    CheckResult,
    MotorTestResult,
    Sample,
    diagnose_brake,
    diagnose_d6t,
    diagnose_ds18b20,
    diagnose_motor,
    diagnose_power,
    diagnose_sensors_from_stream,
    diagnose_tb200s_wiring,
    identify_firmware,
    parse_diag_lines,
    run_diag,
)

NEIGHBORS = ("PD0", "PD3", "PA4", "PE10", "PD15", "PE9", "PD7", "PA12", "PA6", "PA11")
LEVELS = {"pulled_up": (1, 1), "floating": (0, 1), "pulled_low": (0, 0)}


def diag_transcript(
    pins: Optional[dict[str, str]] = None,
    acks: Optional[dict[tuple[str, str], bool]] = None,
    shorts: Optional[dict[tuple[str, str], bool]] = None,
    onewire: Optional[dict[str, bool]] = None,
    ds18b20: Optional[dict[str, str]] = None,
    dac: Optional[dict[str, str]] = None,
    rc: Optional[dict[str, int]] = None,
    d6t: Optional[dict[str, str]] = None,
    power: Optional[dict[str, str]] = None,
) -> list[str]:
    """Firmware ``DIAG`` answer of a correctly wired bench, with overrides."""
    states = {"PB6": "pulled_up", "PB9": "pulled_up", "PG6": "pulled_up"}
    states.update({name: "floating" for name in NEIGHBORS})
    states.update(pins or {})
    i2c = {("PB6", "PB9"): True}
    if acks is not None:
        i2c = acks
    presence = {"PG6": True} if onewire is None else onewire
    power_fields = {
        "app_state": "IDLE",
        "fault_reason": "NONE",
        "mc_state": "0",
        "faults_now": "0x0000",
        "faults_occurred": "0x0000",
        "vbus_mv": "24000",
        "speed_rpm": "0",
        "load_ma": "50",
    }
    power_fields.update(power or {})
    dac_fields = {
        "adc": "1",
        "drive_code": "1034",
        "v_drive_mv": "833",
        "v_hold_mv": "820",
        "v_zero_mv": "5",
        "rise_us": "230000",
        "v_pu_end_mv": "2400",
        "v_pd_end_mv": "15",
    }
    dac_fields.update(dac or {})
    rc_fields = {"PA4": 40, "PE10": 40}
    rc_fields.update(rc or {})
    d6t_fields = {"read": "1", "temp_c": "24.50"}
    d6t_fields.update(d6t or {})

    def join(kind: str, fields: dict[str, object]) -> str:
        return f"DIAG,{kind}," + ",".join(f"{key}={value}" for key, value in fields.items())

    lines = ["DIAG,BEGIN,version=1", join("POWER", power_fields)]
    roles = {"PB6": "SCL", "PB9": "SDA", "PG6": "DQ"}
    candidates = ["PB6", "PB9", "PG6"]
    for name in ("PB6", "PB9", "PG6") + NEIGHBORS:
        pd, pu = LEVELS[states[name]]
        lines.append(join("PIN", {"name": name, "role": roles.get(name, "NEIGHBOR"), "pd": pd, "pu": pu}))
        if name in NEIGHBORS and states[name] == "pulled_up":
            candidates.append(name)
    for a, b in (("PB6", "PB9"), ("PB6", "PG6"), ("PB9", "PG6")):
        shorted = (shorts or {}).get((a, b), False)
        lines.append(join("SHORT", {"a": a, "b": b, "shorted": int(shorted)}))
    for scl in candidates:
        for sda in candidates:
            if scl != sda:
                lines.append(join("I2C", {"scl": scl, "sda": sda, "ack": int(i2c.get((scl, sda), False))}))
    lines.append(join("I2C_SCAN", {"devices": "0A" if any(i2c.values()) else ""}))
    for pin in candidates:
        lines.append(join("OW", {"pin": pin, "presence": int(presence.get(pin, False))}))
    if presence.get("PG6", False):
        ds_fields = {"power": "external", "conv_ms": "600", "crc": "1", "temp_centi": "2350"}
        ds_fields.update(ds18b20 or {})
        lines.append(join("DS18B20", ds_fields))
    lines.append(join("DAC", dac_fields))
    for pin, rise in rc_fields.items():
        lines.append(join("RC", {"pin": pin, "rise_us": rise}))
    lines.append(join("D6T", d6t_fields))
    lines.append("DIAG,END")
    return lines


def report_of(**overrides):
    return parse_diag_lines(diag_transcript(**overrides))


def samples(count: int, speed: float, iq: float, load: float) -> list[Sample]:
    return [Sample(speed, 0.0, iq, 0.5, 4.0, load) for _ in range(count)]


class FakeConnection:
    """Serial stand-in answering each command with a prepared reply."""

    def __init__(self, replies: Optional[dict[str, str]] = None, unsolicited: str = "") -> None:
        self.replies = replies or {}
        self.pending = unsolicited.encode("ascii")
        self.partial = b""
        self.written: list[str] = []

    def write(self, data: bytes) -> None:
        self.partial += data
        while b"\n" in self.partial:
            raw, self.partial = self.partial.split(b"\n", 1)
            command = raw.decode("ascii").strip()
            self.written.append(command)
            self.pending += self.replies.get(command, "").encode("ascii")

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        if not self.pending:
            time.sleep(0.001)
            return b""
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk


def fake_link(replies: Optional[dict[str, str]] = None, unsolicited: str = "") -> BoardLink:
    return BoardLink(FakeConnection(replies, unsolicited), char_delay_s=0.0)


class ParsingTests(unittest.TestCase):
    def test_transcript_is_parsed(self) -> None:
        report = report_of()
        self.assertTrue(report.complete)
        self.assertEqual(report.pins["PB6"].state, "pulled_up")
        self.assertEqual(report.pins["PD0"].state, "floating")
        self.assertTrue(report.i2c_acks[("PB6", "PB9")])
        self.assertEqual(report.i2c_devices, [0x0A])
        self.assertTrue(report.onewire["PG6"])
        self.assertEqual(report.rc_rise_us["PA4"], 40)

    def test_rc_timeout_is_stored_as_none(self) -> None:
        self.assertIsNone(report_of(rc={"PA4": -1}).rc_rise_us["PA4"])

    def test_validation_firmware_line_is_recognized(self) -> None:
        self.assertIsNotNone(VALIDATION_FIRMWARE_LINE.match("24.125;25.031;0.250"))
        self.assertIsNone(VALIDATION_FIRMWARE_LINE.match("ACK,SYNC"))


class StoppedBenchTests(unittest.TestCase):
    def test_correct_wiring_passes(self) -> None:
        report = report_of()
        for result in (
            diagnose_power(report, motor_requested=True),
            diagnose_d6t(report),
            diagnose_ds18b20(report),
            diagnose_tb200s_wiring(report),
        ):
            self.assertEqual(result.status, "OK", result)

    def test_low_bus_voltage(self) -> None:
        report = report_of(power={"vbus_mv": "300"})
        self.assertEqual(diagnose_power(report, motor_requested=True).status, "FAIL")
        self.assertEqual(diagnose_power(report, motor_requested=False).status, "WARN")

    def test_latched_fault_is_decoded(self) -> None:
        result = diagnose_power(report_of(power={"faults_occurred": "0x0004"}), motor_requested=False)
        self.assertEqual(result.status, "WARN")
        self.assertIn("0x0004", result.causes[0])

    def test_swapped_i2c_lines(self) -> None:
        result = diagnose_d6t(report_of(acks={("PB9", "PB6"): True}, d6t={"read": "0", "temp_c": "nan"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("swapped", result.summary)

    def test_d6t_on_neighbor_pin(self) -> None:
        report = report_of(
            pins={"PB9": "floating", "PD15": "pulled_up"},
            acks={("PB6", "PD15"): True},
            d6t={"read": "0", "temp_c": "nan"},
        )
        result = diagnose_d6t(report)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("CN10-22 (PD15)", result.summary)

    def test_unpowered_d6t_clamps_lines(self) -> None:
        report = report_of(
            pins={"PB6": "pulled_low", "PB9": "pulled_low"},
            acks={},
            d6t={"read": "0", "temp_c": "nan"},
        )
        result = diagnose_d6t(report)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("SCL and SDA held at 0 V", result.summary)
        self.assertIn("without 5 V", result.causes[0])

    def test_shorted_i2c_lines(self) -> None:
        report = report_of(acks={}, shorts={("PB6", "PB9"): True}, d6t={"read": "0", "temp_c": "nan"})
        self.assertIn("shorted", diagnose_d6t(report).summary)

    def test_d6t_rejected_frames(self) -> None:
        result = diagnose_d6t(report_of(d6t={"read": "0", "temp_c": "nan"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("acknowledges", result.summary)

    def test_missing_sensor_rail(self) -> None:
        report = report_of(
            pins={"PB6": "floating", "PB9": "floating", "PG6": "floating"},
            acks={},
            onewire={},
            d6t={"read": "0", "temp_c": "nan"},
        )
        d6t = diagnose_d6t(report)
        ds18b20 = diagnose_ds18b20(report)
        self.assertEqual((d6t.status, ds18b20.status), ("FAIL", "FAIL"))
        self.assertEqual(d6t.causes[0], RAIL_CAUSE)
        self.assertEqual(ds18b20.causes[0], RAIL_CAUSE)

    def test_floating_line_reports_neighbor_signal(self) -> None:
        report = report_of(pins={"PB6": "floating", "PE9": "pulled_up"}, acks={}, d6t={"read": "0", "temp_c": "nan"})
        result = diagnose_d6t(report)
        self.assertEqual(result.summary, "No pull-up on SCL.")
        self.assertTrue(any("CN10-23 (PE9)" in cause for cause in result.causes))

    def test_ds18b20_parasite_power(self) -> None:
        result = diagnose_ds18b20(report_of(ds18b20={"power": "parasite"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("VDD", result.summary)

    def test_ds18b20_on_wrong_pin(self) -> None:
        report = report_of(pins={"PG6": "floating", "PD0": "pulled_up"}, onewire={"PD0": True})
        result = diagnose_ds18b20(report)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("CN7-2 (PD0)", result.summary)

    def test_ds18b20_without_external_pullup(self) -> None:
        result = diagnose_ds18b20(report_of(pins={"PG6": "floating"}))
        self.assertEqual(result.status, "WARN")

    def test_ds18b20_conversion_not_done(self) -> None:
        result = diagnose_ds18b20(report_of(ds18b20={"temp_centi": "8500"}))
        self.assertEqual(result.status, "WARN")
        self.assertIn("85.00", result.summary)

    def test_ds18b20_line_correct_but_silent(self) -> None:
        result = diagnose_ds18b20(report_of(onewire={}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("no DS18B20 answers", result.summary)

    def test_dac_open_with_network_on_neighbor(self) -> None:
        report = report_of(dac={"rise_us": "30", "v_pu_end_mv": "3290"}, rc={"PA4": -1})
        result = diagnose_tb200s_wiring(report)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("Nothing is connected", result.summary)
        self.assertIn("CN7-31 (PA4)", result.causes[0])

    def test_missing_capacitor(self) -> None:
        result = diagnose_tb200s_wiring(report_of(dac={"rise_us": "30", "v_pu_end_mv": "200"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("no 4.7 uF", result.summary)

    def test_external_voltage_on_adj(self) -> None:
        result = diagnose_tb200s_wiring(report_of(dac={"v_zero_mv": "2500"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("Disconnect the ADJ wire", result.actions[0])

    def test_dac_output_shorted(self) -> None:
        result = diagnose_tb200s_wiring(report_of(dac={"v_drive_mv": "20", "v_hold_mv": "0"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("cannot drive", result.summary)

    def test_adj_shorted_behind_resistor(self) -> None:
        result = diagnose_tb200s_wiring(report_of(dac={"rise_us": "-1", "v_hold_mv": "10", "v_pu_end_mv": "20"}))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("resistive path", result.summary)

    def test_network_held_low_by_adj_input(self) -> None:
        result = diagnose_tb200s_wiring(report_of(dac={"rise_us": "-1", "v_pu_end_mv": "600"}))
        self.assertEqual(result.status, "OK")


class StreamTests(unittest.TestCase):
    def test_sensor_fallback_without_diag(self) -> None:
        rows = [{"d6t_temp_c": "nan", "ds18b20_temp_c": "23.50"} for _ in range(5)]
        d6t, ds18b20 = diagnose_sensors_from_stream(rows)
        self.assertEqual((d6t.status, ds18b20.status), ("FAIL", "OK"))


class MotorTests(unittest.TestCase):
    def running_result(self, brake_iq: float, direction_ok: Optional[bool] = True) -> MotorTestResult:
        result = MotorTestResult(target_rpm=1500.0, started=True, reached_speed=True, direction_ok=direction_ok)
        result.windows = {
            "base": samples(30, 1500.0, 1.0, 0.05),
            "brake": samples(30, 1490.0, brake_iq, 0.25),
            "release": samples(30, 1500.0, 1.0, 0.05),
        }
        return result

    def test_start_refused_decodes_faults(self) -> None:
        motor = MotorTestResult(
            target_rpm=1500.0,
            error="ERR,START",
            status={"fault_reason": "START_REJECTED", "faults_occurred": "0x0004"},
        )
        result = diagnose_motor(motor)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("START_REJECTED", result.causes[0])
        self.assertIn("0x0004", result.causes[1])

    def test_startup_timeout(self) -> None:
        motor = MotorTestResult(target_rpm=1500.0, started=True, status={"fault_reason": "STARTUP_TIMEOUT"})
        result = diagnose_motor(motor)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("phase", result.causes[0])

    def test_healthy_motor_and_brake(self) -> None:
        result = self.running_result(brake_iq=2.0)
        self.assertEqual(diagnose_motor(result).status, "OK")
        self.assertEqual(diagnose_brake(result, CheckResult("TB-200S", "OK", "")).status, "OK")

    def test_reversed_direction(self) -> None:
        result = diagnose_motor(self.running_result(brake_iq=2.0, direction_ok=False))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("swapped", result.causes[0])

    def test_brake_without_reaction(self) -> None:
        result = diagnose_brake(self.running_result(brake_iq=1.02), CheckResult("TB-200S", "OK", ""))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("external 0-10 V mode", result.causes[0])

    def test_brake_without_reaction_reuses_wiring_failure(self) -> None:
        wiring = CheckResult("TB-200S", "FAIL", "Nothing is connected to CN7-32 (PA5).")
        result = diagnose_brake(self.running_result(brake_iq=1.02), wiring)
        self.assertIn("CN7-32", result.causes[0])


class BoardLinkTests(unittest.TestCase):
    def test_command_and_dispatch(self) -> None:
        link = fake_link(
            {
                "SYNC": "ACK,SYNC\r\n",
                "ACQ_START,100,1000": "ACK,ACQ_START\r\n#LOG_START\r\n#CSV_HEADER,a,b\r\nDATA,1,2\r\n",
                "STATUS": "STATUS,app_state=IDLE,fault_reason=NONE\r\nACK,STATUS\r\n",
            }
        )
        self.assertEqual(identify_firmware(link)[0], "acquisition")
        self.assertTrue(link.command("ACQ_START,100,1000", "ACQ_START")[0])
        link.read_for(0.05)
        self.assertEqual(link.rows, [{"a": "1", "b": "2"}])
        self.assertEqual(link.query_status()["app_state"], "IDLE")

    def test_error_reply(self) -> None:
        link = fake_link({"START": "ERR,NO_CFG\r\n"})
        self.assertEqual(link.command("START", "START", timeout_s=0.5)[:2], (False, "ERR,NO_CFG"))

    def test_diag_round_trip(self) -> None:
        reply = "\r\n".join(diag_transcript() + ["ACK,DIAG"]) + "\r\n"
        report = run_diag(fake_link({"DIAG": reply}))
        self.assertTrue(report.complete)
        self.assertEqual(diagnose_d6t(report).status, "OK")

    def test_diag_unsupported_returns_quickly(self) -> None:
        with mock.patch.object(check_wiring, "COMMAND_TIMEOUT_S", 0.2):
            started = time.monotonic()
            report = run_diag(fake_link())
        self.assertFalse(report.complete)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_validation_firmware_is_identified(self) -> None:
        with mock.patch.object(BoardLink, "command", return_value=(False, None, [])):
            kind, _lines = identify_firmware(fake_link(unsolicited="24.125;25.031;0.250\r\n"))
        self.assertEqual(kind, "validation")


class ConfirmationTests(unittest.TestCase):
    def test_french_and_english_answers(self) -> None:
        args = check_wiring.parse_args([])
        for answer, expected in (("oui", True), ("y", True), ("YES", True), ("non", False), ("", False)):
            with mock.patch("builtins.input", return_value=answer), mock.patch("builtins.print"):
                self.assertEqual(check_wiring.confirm_motor_start(args), expected, answer)


if __name__ == "__main__":
    unittest.main()
