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
    diagnose_fault_source,
    FAULT_VERDICT_BOARD,
    FAULT_VERDICT_FOC_LATE,
    FAULT_VERDICT_COMPARATOR,
    FAULT_VERDICT_GLITCH,
    FAULT_VERDICT_NO_BREAK,
    FAULT_VERDICT_PROBABLE_OCP,
    normalize_quick_profile,
    parse_fault_trace,
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


class IndependentControlTests(unittest.TestCase):
    CUSTOM = dict(acquisition_mode=ACQUISITION_MODE_MOTOR, quick_profile=QUICK_PROFILE_CUSTOM,
                  target_rpm=4000., iq_limit=28., hard_limit=30., accel=50.,
                  datalog_ms=100, ds18b20_ms=1000, load_mode=LOAD_MODE_FIXED, load_setpoint_a=.2)
    PRESET = dict(acquisition_mode=ACQUISITION_MODE_MOTOR, quick_profile=QUICK_PROFILE_STABLE,
                  profile_token="STABLE", iq_limit=28., datalog_ms=100, ds18b20_ms=1000,
                  load_mode=LOAD_MODE_FIXED, load_setpoint_a=.2)

    def make_app(self, connected=True):
        app = object.__new__(MotorDatalogGui)
        app.ack_queue, app.gui_queue = queue.Queue(), queue.Queue()
        app.launch_cancel_event = threading.Event()
        app.board_hello_needed = not connected
        app.command_lock = threading.Lock()
        app.board_motor_running = False
        app.is_stopping = False
        app.commands = []
        app.send_command = lambda command, **kwargs: app.commands.append(command)
        app.wait_for_ack = lambda name, **kwargs: True
        return app

    def test_custom_motor_start_never_touches_logging(self):
        app = self.make_app(connected=False)
        app.board_action_thread("motor_start", self.CUSTOM)
        self.assertEqual([c.split(',')[0].strip() for c in app.commands], ["HELLO", "MOTOR_CFG", "LOAD", "MOTOR_START"])
        self.assertEqual(app.gui_queue.get_nowait(), ("action_success", ("motor_start", self.CUSTOM)))

    def test_apply_profile_does_not_start_either_subsystem(self):
        app = self.make_app()
        app.board_action_thread("apply", self.PRESET)
        self.assertEqual(app.commands, ["PROFILE_SET,STABLE\n"])

    def test_motor_restart_during_logging_sends_no_sync_or_log_command(self):
        app = self.make_app()
        app.csv_file = object()
        original_csv = app.csv_file
        app.board_action_thread("motor_start", self.PRESET)
        self.assertEqual(app.commands, ["PROFILE_SET,STABLE\n", "MOTOR_START\n"])
        self.assertIs(app.csv_file, original_csv)

    def test_logging_start_and_stop_send_no_motor_commands(self):
        for action, command in (("log_start", "LOG_START,100,1000\n"), ("log_stop", "LOG_STOP\n")):
            with self.subTest(action=action):
                app = self.make_app()
                app.board_action_thread(action, self.PRESET)
                self.assertEqual(app.commands, [command])

    def test_first_logging_connection_preserves_motor_started_by_b2(self):
        app = self.make_app(connected=False)
        app.board_motor_running = True
        app.board_action_thread("log_start", self.PRESET)
        self.assertEqual(app.commands, ["HELLO\n", "LOG_START,100,1000\n"])
        self.assertTrue(app.board_motor_running)

    def test_apply_rejects_an_already_active_board_motor_without_stopping_it(self):
        app = self.make_app()
        app.board_motor_running = True
        app.board_action_thread("apply", self.PRESET)
        self.assertEqual(app.commands, [])
        self.assertEqual(app.gui_queue.get_nowait()[0], "action_failed")

    def test_cancelled_motor_start_sends_no_start_command(self):
        app = self.make_app()
        def ack(name, **kwargs):
            if name == "MOTOR_CFG": app.launch_cancel_event.set()
            return True
        app.wait_for_ack = ack
        app.board_action_thread("motor_start", self.CUSTOM)
        self.assertEqual([c.split(',')[0].strip() for c in app.commands], ["MOTOR_CFG"])
        self.assertTrue(app.gui_queue.empty())

    def test_start_failure_rolls_back_only_its_subsystem(self):
        for action, start, stop in (("motor_start", "MOTOR_START", "MOTOR_STOP"), ("log_start", "LOG_START", "LOG_STOP")):
            with self.subTest(action=action):
                app = self.make_app()
                def ack(name, **kwargs):
                    if name == start: raise TimeoutError("Missing ACK")
                    return True
                app.wait_for_ack = ack
                app.board_action_thread(action, self.PRESET)
                self.assertEqual(app.commands[-1], stop + "\n")
                forbidden = "LOG_STOP" if action == "motor_start" else "MOTOR_STOP"
                self.assertFalse(any(c.startswith(forbidden) for c in app.commands))

    def test_motor_stop_preserves_active_csv_and_connection(self):
        app = self.make_app()
        app.is_running = app.motor_running = app.motor_start_confirmed = True
        app.motor_fault_message = ""
        app.serial_obj, app.csv_file = object(), object()
        original_serial, original_csv = app.serial_obj, app.csv_file
        app.update_controls_state = mock.Mock()
        app.show_running_status = mock.Mock()
        app.complete_board_action("motor_stop", {})
        self.assertFalse(app.motor_running)
        self.assertTrue(app.is_running)
        self.assertIs(app.csv_file, original_csv)
        self.assertIs(app.serial_obj, original_serial)

    def test_logging_stop_preserves_running_motor_and_connection(self):
        app = self.make_app()
        app.is_running = app.motor_running = True
        app.serial_obj, app.csv_file = object(), mock.Mock()
        original_serial, original_csv = app.serial_obj, app.csv_file
        app.csv_path = "test.csv"
        app.session_started_s, app.session_ended_s = 1., None
        app.flush_csv = mock.Mock()
        app.log = mock.Mock()
        app.refresh_session_stats = mock.Mock()
        app.suggest_next_csv_path = mock.Mock()
        app.update_controls_state = mock.Mock()
        app.show_running_status = mock.Mock()
        app.complete_board_action("log_stop", {})
        self.assertTrue(app.motor_running)
        self.assertFalse(app.is_running)
        original_csv.close.assert_called_once()
        self.assertIs(app.serial_obj, original_serial)

    def test_logging_stop_finishes_while_motor_stop_is_pending(self):
        app = self.make_app()
        app.is_stopping = True
        app.finish_logging = mock.Mock()
        app.update_controls_state = mock.Mock()
        app.show_running_status = mock.Mock()
        app.complete_board_action("log_stop", {})
        app.finish_logging.assert_called_once()
        self.assertTrue(app.is_stopping)


class ContinuousCsvTests(unittest.TestCase):
    def test_motor_stop_restart_keeps_one_header_and_all_data_rows(self):
        import csv
        import time
        with tempfile.TemporaryDirectory() as directory:
            app = object.__new__(MotorDatalogGui)
            app.csv_path = str(Path(directory) / "continuous.csv")
            app.csv_file = app.csv_writer = None
            app.csv_columns, app.csv_output_columns = [], []
            app.csv_pending_rows = 0
            app.csv_last_flush_s = time.monotonic()
            app.logging_requested = app.is_running = app.motor_running = True
            app.is_stopping = app.is_launching = False
            app.motor_fault_message = ""
            app.serial_obj = object()
            app.show_data_frames_var = FakeVar(False)
            app.live_vars = {}
            app.session_rows, app.data_times = 0, []
            app.session_started_s, app.session_ended_s = time.monotonic(), None
            for name in ("log", "update_controls_state", "show_running_status", "register_csv_fields_for_live",
                         "register_csv_fields_for_plot", "update_live_values", "append_plot_row",
                         "refresh_session_stats", "suggest_next_csv_path"):
                setattr(app, name, mock.Mock())
            app.handle_serial_line("#CSV_HEADER," + ",".join(CSV_OUTPUT_COLUMNS))
            writer = app.csv_writer
            def data_row(index):
                values = [str(index * 100) if column == "stm32_time_ms" else "0" for column in CSV_OUTPUT_COLUMNS]
                app.handle_serial_line("DATA," + ",".join(values))
            data_row(1)
            app.complete_board_action("motor_stop", {})
            data_row(2)
            app.complete_board_action("motor_start", IndependentControlTests.PRESET)
            data_row(3)
            self.assertIs(app.csv_writer, writer)
            self.assertEqual(app.session_rows, 3)
            app.finish_logging()
            with open(app.csv_path, newline="", encoding="utf-8") as stream:
                rows = list(csv.reader(stream, delimiter=";"))
            self.assertEqual(rows[0], list(CSV_OUTPUT_COLUMNS))
            self.assertEqual(len(rows), 4)
            self.assertTrue(app.motor_running)


class ConfigurationIndependenceTests(unittest.TestCase):
    def make_app(self, profile):
        app = object.__new__(MotorDatalogGui)
        app.quick_profile_var = FakeVar(profile)
        app.get_selected_port_device = lambda: "COM_TEST"
        app.baud_var = FakeVar("115200")
        app.datalog_ms_var = FakeVar("100")
        app.ds18b20_ms_var = FakeVar("1000")
        app.csv_path_var = FakeVar(str(Path(tempfile.gettempdir()) / "independent.csv"))
        return app

    def test_logging_does_not_read_custom_motor_fields(self):
        app = self.make_app(QUICK_PROFILE_CUSTOM)
        cfg = app.parse_config(include_motor=False)
        self.assertEqual(cfg["datalog_ms"], 100)
        self.assertNotIn("iq_limit", cfg)

    def test_motor_configuration_does_not_require_valid_csv_or_logging_fields(self):
        app = self.make_app(QUICK_PROFILE_STABLE)
        app.csv_path_var = FakeVar("")
        app.datalog_ms_var = FakeVar("invalid")
        app.ds18b20_ms_var = FakeVar("invalid")
        cfg = app.parse_config(include_logging=False)
        self.assertEqual(cfg["profile_token"], "STABLE")
        self.assertIsNone(cfg["csv_path"])



class FirmwareEventTests(unittest.TestCase):
    def make_app(self):
        app = object.__new__(MotorDatalogGui)
        app.is_running = True
        app.motor_running = True
        app.serial_obj = object()
        app.logging_requested = True
        app.update_controls_state = mock.Mock()
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

    def test_events_are_ignored_without_connection(self) -> None:
        app = self.make_app()
        app.is_running = False
        app.serial_obj = None
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


def fault_diag(**overrides) -> str:
    fields = {
        "break": "1", "t_break_ms": "406684", "t_now_ms": "406690", "irq_count": "1",
        "tim_sr": "0x0080", "bkin_low": "0", "comp": "0x0", "bkin_now_low": "0",
        "comp_now": "0x0", "peak_run_da": "160", "peak_recent_da": "120",
        "ocp_da": "1100", "vbus_dv": "180", "trace_n": "8", "trace_period_us": "62",
    }
    fields.update(overrides)
    return "#FAULT_DIAG," + ",".join(f"{key}={value}" for key, value in fields.items())


def flat_trace(peak_a: float, vbus_v: float = 18.0, count: int = 8):
    return [(peak_a, -peak_a / 2, -peak_a / 2, vbus_v)] * count


class FaultSourceTests(unittest.TestCase):
    def test_trace_line_is_parsed_in_amps_and_volts(self) -> None:
        first, samples = parse_fault_trace("#FAULT_TRACE,-8,123:-45:-78:180,0:1:-1:179")
        self.assertEqual(first, -8)
        self.assertEqual(samples, [(12.3, -4.5, -7.8, 18.0), (0.0, 0.1, -0.1, 17.9)])
        with self.assertRaises(ValueError):
            parse_fault_trace("#FAULT_TRACE,-8,123:-45")

    def test_low_current_without_source_is_a_glitch(self) -> None:
        verdict, details = diagnose_fault_source(fault_diag(), flat_trace(12.0))
        self.assertEqual(verdict, FAULT_VERDICT_GLITCH)
        self.assertTrue(any("never reached 110 A" in line for line in details))

    def test_board_fault_line_is_identified(self) -> None:
        verdict, details = diagnose_fault_source(
            fault_diag(bkin_low="1", bkin_now_low="1"), flat_trace(12.0))
        self.assertEqual(verdict, FAULT_VERDICT_BOARD)
        self.assertTrue(any("still LOW" in line for line in details))

    def test_comparator_phase_is_identified(self) -> None:
        verdict, details = diagnose_fault_source(fault_diag(comp="0x2"), flat_trace(100.0))
        self.assertEqual(verdict, FAULT_VERDICT_COMPARATOR)
        self.assertTrue(any("V (COMP6)" in line for line in details))

    def test_released_source_near_threshold_is_a_probable_overcurrent(self) -> None:
        verdict, _ = diagnose_fault_source(fault_diag(), flat_trace(95.0))
        self.assertEqual(verdict, FAULT_VERDICT_PROBABLE_OCP)

    def test_bus_excursions_are_reported(self) -> None:
        _, details = diagnose_fault_source(fault_diag(), flat_trace(12.0, vbus_v=21.0))
        self.assertTrue(any("regenerative" in line for line in details))
        _, details = diagnose_fault_source(fault_diag(), flat_trace(12.0, vbus_v=8.0))
        self.assertTrue(any("UVLO" in line for line in details))

    def test_software_fault_has_no_break(self) -> None:
        verdict, _ = diagnose_fault_source(fault_diag(**{"break": "0", "trace_n": "0"}))
        self.assertEqual(verdict, FAULT_VERDICT_NO_BREAK)

    def test_dashboard_reports_once_the_trace_is_complete(self) -> None:
        app = object.__new__(MotorDatalogGui)
        app.fault_diag_line = ""
        app.fault_trace_lines = []
        app.fault_trace_samples = []
        app.fault_trace_expected = 0
        app.fault_report_after_id = None
        app.last_fault_line = ""
        app.motor_fault_message = "MCSDK_FAULT: protection fault"
        app.after = mock.Mock(return_value="after#1")
        app.after_cancel = mock.Mock()
        app.log = mock.Mock()
        app.save_fault_report = mock.Mock()
        app.show_running_status = mock.Mock()

        app.handle_firmware_event(fault_diag())
        app.save_fault_report.assert_not_called()
        app.handle_firmware_event("#FAULT_TRACE,-8," + ",".join(["120:-60:-60:180"] * 8))

        app.save_fault_report.assert_called_once()
        self.assertEqual(app.save_fault_report.call_args.args[0], FAULT_VERDICT_GLITCH)
        self.assertIn(FAULT_VERDICT_GLITCH, app.log.call_args_list[0].args[0])
        self.assertTrue(app.motor_fault_message.startswith(
            f"MCSDK_FAULT — source: {FAULT_VERDICT_GLITCH}: "))
        self.assertEqual(app.fault_diag_line, "")

    def test_dashboard_names_an_foc_overrun_from_the_fault_masks(self) -> None:
        app = object.__new__(MotorDatalogGui)
        app.fault_diag_line = ""
        app.fault_trace_lines = []
        app.fault_trace_samples = []
        app.fault_trace_expected = 0
        app.fault_report_after_id = None
        app.last_fault_line = "#FAULT,MCSDK_FAULT,mc_now=0x0000,mc_occurred=0x0001"
        app.motor_fault_message = "MCSDK_FAULT: protection fault"
        app.after = mock.Mock()
        app.after_cancel = mock.Mock()
        app.log = mock.Mock()
        app.save_fault_report = mock.Mock()
        app.show_running_status = mock.Mock()

        app.handle_firmware_event(fault_diag(**{
            "break": "0", "trace_n": "0", "foc_period_ns": "62494", "foc_max_ns": "31000",
            "foc_late_max_ns": "13200", "foc_overruns": "1", "foc_overrun_ns": "30500",
            "foc_overrun_late_ns": "13200", "quiet_timeouts": "0"}))

        app.save_fault_report.assert_called_once()
        self.assertEqual(app.save_fault_report.call_args.args[0], FAULT_VERDICT_FOC_LATE)
        self.assertTrue(app.motor_fault_message.startswith(
            f"MCSDK_FAULT — source: {FAULT_VERDICT_FOC_LATE}: "))


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
