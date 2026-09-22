from __future__ import annotations

import unittest
import queue
import sys
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from motor_datalog_gui_dashboard import (
    LOAD_MODE_FIXED,
    LOAD_MODE_VARIABLE,
    LOAD_SETPOINT_COLUMN,
    CSV_OUTPUT_COLUMNS,
    ACQUISITION_MODE_MOTOR,
    MotorDatalogGui,
    build_load_command,
)


class LoadProtocolTests(unittest.TestCase):
    def test_fixed_load_boundaries_are_serialized(self) -> None:
        self.assertEqual(build_load_command(LOAD_MODE_FIXED, 0.05), "LOAD,0.050\n")
        self.assertEqual(build_load_command(LOAD_MODE_FIXED, 0.25), "LOAD,0.250\n")

    def test_variable_load_command_has_no_numeric_argument(self) -> None:
        self.assertEqual(build_load_command(LOAD_MODE_VARIABLE, 0.2), "LOAD,VARIABLE\n")

    def test_invalid_fixed_load_is_rejected(self) -> None:
        for value in (0.0, 0.049, 0.251, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_load_command(LOAD_MODE_FIXED, value)


class RawCsvSchemaTests(unittest.TestCase):
    def test_load_setpoint_is_part_of_raw_csv_schema(self) -> None:
        self.assertIn(LOAD_SETPOINT_COLUMN, CSV_OUTPUT_COLUMNS)

    def test_missing_load_value_is_explicit_nan(self) -> None:
        self.assertEqual(
            MotorDatalogGui.csv_value_for_column({}, LOAD_SETPOINT_COLUMN),
            "NaN",
        )

    def test_legacy_firmware_fallback_is_safe(self) -> None:
        app = object.__new__(MotorDatalogGui)
        app.active_acquisition_mode = ACQUISITION_MODE_MOTOR
        app.active_load_mode = LOAD_MODE_FIXED
        app.active_load_setpoint_a = 0.15
        self.assertEqual(app.load_setpoint_fallback(), "0.150000")

        app.active_load_mode = LOAD_MODE_VARIABLE
        self.assertEqual(app.load_setpoint_fallback(), "NaN")


class StartupSequenceTests(unittest.TestCase):
    def test_load_is_applied_between_cfg_and_start(self) -> None:
        app = object.__new__(MotorDatalogGui)
        app.ack_queue = queue.Queue()
        app.gui_queue = queue.Queue()
        commands: list[str] = []
        acknowledgements: list[str] = []
        app.send_command = lambda command, char_delay=0.0: commands.append(command)
        app.wait_for_ack = lambda name, **_kwargs: acknowledgements.append(name) or True

        config = {
            "acquisition_mode": ACQUISITION_MODE_MOTOR,
            "target_rpm": 1200.0,
            "iq_limit": 2.0,
            "hard_limit": 6.0,
            "accel": 5.0,
            "datalog_ms": 100,
            "ds18b20_ms": 1000,
            "load_mode": LOAD_MODE_FIXED,
            "load_setpoint_a": 0.15,
        }

        with mock.patch("motor_datalog_gui_dashboard.time.sleep", return_value=None):
            app.launch_sequence_thread(config)

        self.assertEqual(
            [command.split(",", 1)[0].strip() for command in commands],
            ["SYNC", "CFG", "LOAD", "START"],
        )
        self.assertEqual(acknowledgements, ["SYNC", "CFG", "LOAD", "START"])
        self.assertEqual(app.gui_queue.get_nowait(), ("launch_success", ACQUISITION_MODE_MOTOR))


if __name__ == "__main__":
    unittest.main()
