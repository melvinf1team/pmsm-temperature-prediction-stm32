from __future__ import annotations

import unittest
import queue
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

DATALOGGING_DIR = Path(__file__).resolve().parents[2] / "datalogging"
if str(DATALOGGING_DIR) not in sys.path:
    sys.path.insert(0, str(DATALOGGING_DIR))

from motor_datalog_gui_dashboard import (
    LOAD_MODE_FIXED,
    LOAD_MODE_VARIABLE,
    LOAD_SETPOINT_COLUMN,
    CSV_OUTPUT_COLUMNS,
    ACQUISITION_MODE_IDLE,
    ACQUISITION_MODE_MOTOR,
    MIN_LOAD_SETPOINT_A,
    PRESET_FIXED_LOAD_A,
    PRESET_IQ_LIMIT_A,
    PRESET_PROFILE_TOKENS,
    QUICK_PROFILE_ALL_VARIABLE,
    QUICK_PROFILE_COLLECT_ONLY,
    QUICK_PROFILE_CUSTOM,
    QUICK_PROFILE_OPTIONS,
    QUICK_PROFILE_STABLE,
    QUICK_PROFILE_VARIABLE_LOAD,
    QUICK_PROFILE_VARIABLE_SPEED,
    QUICK_PROFILES,
    MotorDatalogGui,
    build_load_command,
    build_profile_command,
    describe_motor_fault,
    normalize_quick_profile,
    preset_load_settings,
)


class FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


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

    def test_load_fallback_when_header_lacks_load_column(self) -> None:
        app = object.__new__(MotorDatalogGui)
        app.active_acquisition_mode = ACQUISITION_MODE_MOTOR
        app.active_load_mode = LOAD_MODE_FIXED
        app.active_load_setpoint_a = 0.15
        self.assertEqual(app.load_setpoint_fallback(), "0.150000")

        app.active_load_mode = LOAD_MODE_VARIABLE
        self.assertEqual(app.load_setpoint_fallback(), "NaN")


class QuickProfileTests(unittest.TestCase):
    def test_profiles_mirror_the_validation_gui_plus_custom(self) -> None:
        self.assertEqual(
            QUICK_PROFILE_OPTIONS,
            ("Collect only", "Stable", "Variable load", "Variable speed", "All variable", "Custom"),
        )
        self.assertEqual(
            PRESET_PROFILE_TOKENS,
            {
                QUICK_PROFILE_STABLE: "STABLE",
                QUICK_PROFILE_VARIABLE_LOAD: "VARIABLE_LOAD",
                QUICK_PROFILE_VARIABLE_SPEED: "VARIABLE_SPEED",
                QUICK_PROFILE_ALL_VARIABLE: "VARIABLE_ALL",
            },
        )
        for name, profile in QUICK_PROFILES.items():
            self.assertEqual(profile.token, PRESET_PROFILE_TOKENS.get(name))

    def test_variation_flags_match_each_preset(self) -> None:
        flags = {name: (p.speed_variable, p.load_variable) for name, p in QUICK_PROFILES.items()}
        self.assertEqual(flags[QUICK_PROFILE_STABLE], (False, False))
        self.assertEqual(flags[QUICK_PROFILE_VARIABLE_LOAD], (False, True))
        self.assertEqual(flags[QUICK_PROFILE_VARIABLE_SPEED], (True, False))
        self.assertEqual(flags[QUICK_PROFILE_ALL_VARIABLE], (True, True))

    def test_preset_command_carries_logging_periods(self) -> None:
        self.assertEqual(build_profile_command(QUICK_PROFILE_STABLE, 100, 1000), "PROFILE,STABLE,100,1000\n")
        self.assertEqual(build_profile_command("all variable", 50, 750), "PROFILE,VARIABLE_ALL,50,750\n")

    def test_collect_only_and_custom_send_no_profile_command(self) -> None:
        self.assertIsNone(build_profile_command(QUICK_PROFILE_COLLECT_ONLY, 100, 1000))
        self.assertIsNone(build_profile_command(QUICK_PROFILE_CUSTOM, 100, 1000))

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_quick_profile("Turbo")

    def test_preset_load_settings(self) -> None:
        fixed = (LOAD_MODE_FIXED, PRESET_FIXED_LOAD_A)
        variable = (LOAD_MODE_VARIABLE, MIN_LOAD_SETPOINT_A)
        self.assertEqual(preset_load_settings(QUICK_PROFILE_STABLE), fixed)
        self.assertEqual(preset_load_settings(QUICK_PROFILE_VARIABLE_SPEED), fixed)
        self.assertEqual(preset_load_settings(QUICK_PROFILE_VARIABLE_LOAD), variable)
        self.assertEqual(preset_load_settings(QUICK_PROFILE_ALL_VARIABLE), variable)
        with self.assertRaises(ValueError):
            preset_load_settings(QUICK_PROFILE_CUSTOM)


class ParseConfigTests(unittest.TestCase):
    def make_app(self, quick_profile: str, csv_path: Path):
        app = object.__new__(MotorDatalogGui)
        app.quick_profile_var = FakeVar(quick_profile)
        app.port_var = FakeVar("COM7")
        app.port_display_to_device = {}
        app.baud_var = FakeVar("115200")
        app.datalog_ms_var = FakeVar("100")
        app.ds18b20_ms_var = FakeVar("1000")
        app.csv_path_var = FakeVar(str(csv_path))
        app.iq_limit_var = FakeVar("2")
        return app

    def test_preset_config_carries_firmware_token_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(QUICK_PROFILE_VARIABLE_LOAD, Path(directory) / "run.csv")
            cfg = app.parse_config()
            self.assertEqual(app.get_iq_limit_safe(), PRESET_IQ_LIMIT_A)

        self.assertEqual(cfg["acquisition_mode"], ACQUISITION_MODE_MOTOR)
        self.assertEqual(cfg["profile_token"], "VARIABLE_LOAD")
        self.assertEqual(cfg["load_mode"], LOAD_MODE_VARIABLE)
        self.assertEqual(cfg["iq_limit"], PRESET_IQ_LIMIT_A)
        self.assertNotIn("target_rpm", cfg)

    def test_collect_only_config_has_no_motor_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(QUICK_PROFILE_COLLECT_ONLY, Path(directory) / "run.csv")
            cfg = app.parse_config()

        self.assertEqual(cfg["acquisition_mode"], ACQUISITION_MODE_IDLE)
        self.assertNotIn("profile_token", cfg)
        self.assertNotIn("load_mode", cfg)

    def test_custom_iq_ceiling_is_28_while_hard_limit_stays_30(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(QUICK_PROFILE_CUSTOM, Path(directory) / "run.csv")
            for name, value in {
                "speed_rpm_var": "4500", "speed_hz_var": "150",
                "pole_pairs_var": "2", "hard_limit_var": "30", "accel_var": "50",
                "load_mode_var": LOAD_MODE_FIXED, "load_setpoint_var": "0.25",
                "iq_limit_var": "28",
            }.items():
                setattr(app, name, FakeVar(value))
            cfg = app.parse_config()
            self.assertEqual(cfg["iq_limit"], 28.0)
            self.assertEqual(cfg["hard_limit"], 30.0)
            for rejected in ("28.1", "30"):
                app.iq_limit_var = FakeVar(rejected)
                with self.subTest(iq=rejected), self.assertRaisesRegex(ValueError, "at most 28 A"):
                    app.parse_config()


class StartupSequenceTests(unittest.TestCase):
    CONFIG = {
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
    PRESET_CONFIG = {
        "acquisition_mode": ACQUISITION_MODE_MOTOR,
        "quick_profile": QUICK_PROFILE_VARIABLE_SPEED,
        "profile_token": "VARIABLE_SPEED",
        "iq_limit": PRESET_IQ_LIMIT_A,
        "datalog_ms": 100,
        "ds18b20_ms": 1000,
        "load_mode": LOAD_MODE_FIXED,
        "load_setpoint_a": PRESET_FIXED_LOAD_A,
    }

    def make_app(self):
        app = object.__new__(MotorDatalogGui)
        app.ack_queue = queue.Queue()
        app.gui_queue = queue.Queue()
        app.launch_cancel_event = threading.Event()
        return app

    def test_load_is_applied_between_cfg_and_start(self) -> None:
        app = self.make_app()
        commands: list[str] = []
        acknowledgements: list[str] = []
        app.send_command = lambda command, char_delay=0.0: commands.append(command)
        app.wait_for_ack = lambda name, **_kwargs: acknowledgements.append(name) or True

        with mock.patch("motor_datalog_gui_dashboard.time.sleep", return_value=None):
            app.launch_sequence_thread(self.CONFIG)

        self.assertEqual(
            [command.split(",", 1)[0].strip() for command in commands],
            ["SYNC", "CFG", "LOAD", "START"],
        )
        self.assertEqual(acknowledgements, ["SYNC", "CFG", "LOAD", "START"])
        self.assertEqual(app.gui_queue.get_nowait(), ("launch_success", ACQUISITION_MODE_MOTOR))

    def test_cancelled_startup_sends_no_further_command(self) -> None:
        app = self.make_app()
        commands: list[str] = []
        app.send_command = lambda command, char_delay=0.0: commands.append(command)

        def wait_for_ack(name, **_kwargs):
            if name == "CFG":
                app.launch_cancel_event.set()
            return True

        app.wait_for_ack = wait_for_ack

        with mock.patch("motor_datalog_gui_dashboard.time.sleep", return_value=None):
            app.launch_sequence_thread(self.CONFIG)

        self.assertEqual(
            [command.split(",", 1)[0].strip() for command in commands],
            ["SYNC", "CFG"],
        )
        self.assertTrue(app.gui_queue.empty())

    def test_preset_is_started_by_a_single_profile_command(self) -> None:
        app = self.make_app()
        commands: list[str] = []
        acknowledgements: list[str] = []
        app.send_command = lambda command, char_delay=0.0: commands.append(command)
        app.wait_for_ack = lambda name, **_kwargs: acknowledgements.append(name) or True

        with mock.patch("motor_datalog_gui_dashboard.time.sleep", return_value=None):
            app.launch_sequence_thread(self.PRESET_CONFIG)

        self.assertEqual(commands, ["SYNC\n", "PROFILE,VARIABLE_SPEED,100,1000\n"])
        self.assertEqual(acknowledgements, ["SYNC", "PROFILE,VARIABLE_SPEED"])
        self.assertEqual(app.gui_queue.get_nowait(), ("launch_success", ACQUISITION_MODE_MOTOR))

    def test_collect_only_uses_acq_start(self) -> None:
        app = self.make_app()
        commands: list[str] = []
        app.send_command = lambda command, char_delay=0.0: commands.append(command)
        app.wait_for_ack = lambda name, **_kwargs: True
        config = {
            "acquisition_mode": ACQUISITION_MODE_IDLE,
            "quick_profile": QUICK_PROFILE_COLLECT_ONLY,
            "datalog_ms": 100,
            "ds18b20_ms": 1000,
        }

        with mock.patch("motor_datalog_gui_dashboard.time.sleep", return_value=None):
            app.launch_sequence_thread(config)

        self.assertEqual(commands, ["SYNC\n", "ACQ_START,100,1000\n"])
        self.assertEqual(app.gui_queue.get_nowait(), ("launch_success", ACQUISITION_MODE_IDLE))

    def test_firmware_without_profile_support_gets_an_actionable_error(self) -> None:
        app = self.make_app()
        app.send_command = lambda command, char_delay=0.0: None

        def wait_for_ack(name, **_kwargs):
            if name.startswith("PROFILE,"):
                raise TimeoutError(f"Timed out waiting for ACK,{name}")
            return True

        app.wait_for_ack = wait_for_ack

        with mock.patch("motor_datalog_gui_dashboard.time.sleep", return_value=None):
            app.launch_sequence_thread(self.PRESET_CONFIG)

        kind, message = app.gui_queue.get_nowait()
        self.assertEqual(kind, "launch_failed")
        self.assertIn("PROFILE support", message)
        self.assertIn("Custom", message)


class FirmwareEventTests(unittest.TestCase):
    def make_app(self):
        app = object.__new__(MotorDatalogGui)
        app.is_running = True
        app.is_launching = False
        app.active_acquisition_mode = ACQUISITION_MODE_MOTOR
        app.active_quick_profile = QUICK_PROFILE_STABLE
        app.motor_start_confirmed = False
        app.motor_fault_message = ""
        app.statuses = []
        app.log = mock.Mock()
        app.set_status = lambda state, detail="": app.statuses.append(state)
        return app

    def test_profile_start_line_confirms_the_running_motor(self) -> None:
        app = self.make_app()
        app.handle_firmware_event("#PROFILE,START,STABLE,rpm=2500,load_a=0.10")
        self.assertTrue(app.motor_start_confirmed)
        self.assertEqual(app.statuses, ["Motor running"])

    def test_preset_waits_for_confirmation_before_reporting_running(self) -> None:
        app = self.make_app()
        app.show_running_status()
        self.assertEqual(app.statuses, ["Motor starting"])

    def test_b2_stop_is_reported(self) -> None:
        app = self.make_app()
        app.handle_firmware_event("#B2,STOP,motor_off")
        self.assertEqual(app.statuses, ["Motor stopped (B2)"])

    def test_events_are_ignored_outside_a_session(self) -> None:
        app = self.make_app()
        app.is_running = False
        app.handle_firmware_event("#B2,STOP,motor_off")
        self.assertEqual(app.statuses, [])

    def test_fault_is_visible_and_not_overwritten_by_running_status(self) -> None:
        app = self.make_app()
        app.handle_firmware_event(
            "#FAULT,OVERSPEED,t_ms=15000,speed_mrpm=4510000,"
            "speed_limit_rpm=4500,iq_ma=4800,id_ma=0,hard_ma=30000,"
            "load_ma=250,mc_now=0x0000,mc_occurred=0x0000"
        )
        self.assertEqual(app.statuses, ["Motor error"])
        self.assertIn("OVERSPEED", app.motor_fault_message)
        self.assertIn("4510.000 rpm", app.motor_fault_message)
        self.assertIn("brake command 0.250 A", app.motor_fault_message)
        app.log.assert_called_once()
        self.assertIn("ERROR: Motor stopped", app.log.call_args.args[0])
        app.show_running_status()  # Late ACK/header must not mask the error.
        app.handle_firmware_event("#CUSTOM,READY")
        self.assertEqual(app.statuses, ["Motor error", "Motor error"])
        self.assertTrue(app.is_running)  # Logging session stays open.

    def test_new_explicit_start_clears_fault_display(self) -> None:
        app = self.make_app()
        app.handle_firmware_event("#FAULT,D6T_OVERTEMP")
        app.handle_firmware_event("#CUSTOM,START")
        self.assertEqual(app.motor_fault_message, "")
        self.assertEqual(app.statuses[-1], "Motor starting")
        app.handle_firmware_event("#CUSTOM,READY")
        self.assertEqual(app.statuses[-1], "Motor running")

    def test_late_header_during_startup_does_not_hide_fault(self) -> None:
        app = self.make_app()
        app.is_running = False
        app.is_launching = True
        app.csv_writer = None
        app.open_csv_with_header = mock.Mock()
        app.handle_firmware_event("#FAULT,MCSDK_FAULT,mc_now=0x0008")
        app.handle_serial_line("#CSV_HEADER,stm32_time_ms,d6t_temp_c")
        self.assertEqual(app.statuses, ["Motor error", "Motor error"])

    def test_mcsdk_mask_and_legacy_faults_are_explained(self) -> None:
        reason, detail = describe_motor_fault(
            "#FAULT,MCSDK_FAULT,mc_now=0x0008,mc_occurred=0x800C"
        )
        self.assertEqual(reason, "MCSDK_FAULT")
        self.assertIn("power-board overtemperature", detail)
        self.assertIn("undervoltage", detail)
        self.assertIn("unknown bits 0x8000", detail)
        reason, detail = describe_motor_fault("#FAULT,D6T_OVERTEMP")
        self.assertEqual(reason, "D6T_OVERTEMP")
        self.assertIn("70 C", detail)

    def test_unknown_or_malformed_diagnostics_keep_the_reason(self) -> None:
        reason, detail = describe_motor_fault(
            "#FAULT,NEW_REASON,speed_mrpm=oops,iq_ma=NaN,mc_now=invalid"
        )
        self.assertEqual(reason, "NEW_REASON")
        self.assertEqual(detail, "The firmware stopped the motor.")


class SessionPathTests(unittest.TestCase):
    def test_stale_data_threshold_follows_the_data_period(self) -> None:
        app = object.__new__(MotorDatalogGui)
        app.active_datalog_ms = 100
        self.assertEqual(app.stale_data_threshold_s(), 2.0)
        app.active_datalog_ms = 5000
        self.assertEqual(app.stale_data_threshold_s(), 12.5)

    def test_next_csv_is_suggested_after_an_auto_named_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "daq_log_20261015_093000.csv"
            written.write_text("header\n", encoding="utf-8")
            app = object.__new__(MotorDatalogGui)
            app.paths = SimpleNamespace(log_dir=Path(directory))
            app.csv_path = str(written)
            app.csv_path_var = mock.Mock()
            app.csv_path_var.get.return_value = str(written)
            app.log = lambda _message: None

            app.suggest_next_csv_path()

            app.csv_path_var.set.assert_called_once()
            suggested = Path(app.csv_path_var.set.call_args.args[0])
            self.assertEqual(suggested.parent, Path(directory))
            self.assertRegex(suggested.name, r"^daq_log_\d{8}_\d{6}\.csv$")

    def test_user_named_csv_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            written = Path(directory) / "endurance_test.csv"
            written.write_text("header\n", encoding="utf-8")
            app = object.__new__(MotorDatalogGui)
            app.csv_path = str(written)
            app.csv_path_var = mock.Mock()
            app.csv_path_var.get.return_value = str(written)

            app.suggest_next_csv_path()

            app.csv_path_var.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
