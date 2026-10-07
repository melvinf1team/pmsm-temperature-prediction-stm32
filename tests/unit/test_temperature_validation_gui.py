from __future__ import annotations

import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

VALIDATION_GUI_DIR = Path(__file__).resolve().parents[2] / "validation" / "test"
if str(VALIDATION_GUI_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATION_GUI_DIR))

from temperature_validation_gui import (
    CsvSessionRecorder,
    PROFILE_ALL_VARIABLE,
    PROFILE_COLLECT_ONLY,
    PROFILE_STABLE,
    PROFILE_VARIABLE_LOAD,
    PROFILE_VARIABLE_SPEED,
    SessionAccumulator,
    TemperatureValidationApp,
    build_profile_command,
    is_transient_serial_error,
    normalize_profile,
    parse_control_response,
    parse_validation_line,
)


class FakeSerialConnection:
    def __init__(self, stop_event: threading.Event, responses: list[bytes | Exception]) -> None:
        self.stop_event = stop_event
        self.responses = responses
        self.writes: list[bytes] = []
        self.closed = False

    def read(self, _size: int) -> bytes:
        if not self.responses:
            self.stop_event.set()
            return b""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class DummyWidget:
    def configure(self, **_kwargs: object) -> None:
        return None


class SerialRecoveryTests(unittest.TestCase):
    def test_clear_comm_error_is_transient(self) -> None:
        error = Exception(
            "ClearCommError failed "
            "(PermissionError(13, 'The device does not recognize the command.', None, 22))"
        )

        self.assertTrue(is_transient_serial_error(error))
        self.assertFalse(is_transient_serial_error(Exception("Port is already open")))

    def test_reader_recovers_after_clear_comm_error(self) -> None:
        stop_event = threading.Event()
        connection = FakeSerialConnection(
            stop_event,
            [Exception("ClearCommError failed"), b"31.5;32.25\n"],
        )
        app = SimpleNamespace(events=queue.Queue())

        TemperatureValidationApp.serial_reader_loop(app, connection, stop_event, 4)

        warning_event = app.events.get_nowait()
        sample_event = app.events.get_nowait()
        self.assertEqual(warning_event[0], "serial_warning")
        self.assertEqual(sample_event[0], "sample")
        self.assertEqual(sample_event[1][0], 4)
        self.assertEqual(sample_event[1][2:], (31.5, 32.25, None))

    def test_reader_accepts_load_aware_frame_and_separates_profile_ack(self) -> None:
        stop_event = threading.Event()
        connection = FakeSerialConnection(
            stop_event,
            [b"ACK,PROFILE,STABLE\n31.5;32.25;0.125\n"],
        )
        app = SimpleNamespace(events=queue.Queue())

        TemperatureValidationApp.serial_reader_loop(app, connection, stop_event, 7)

        control_event = app.events.get_nowait()
        sample_event = app.events.get_nowait()
        self.assertEqual(control_event, ("control", (7, "ACK,PROFILE,STABLE")))
        self.assertEqual(sample_event[0], "sample")
        self.assertEqual(sample_event[1][0], 7)
        self.assertEqual(sample_event[1][2:], (31.5, 32.25, 0.125))


class ProfileProtocolTests(unittest.TestCase):
    def test_builds_the_four_profile_commands(self) -> None:
        expected_commands = {
            PROFILE_STABLE: b"PROFILE,STABLE\n",
            PROFILE_VARIABLE_LOAD: b"PROFILE,VARIABLE_LOAD\n",
            PROFILE_VARIABLE_SPEED: b"PROFILE,VARIABLE_SPEED\n",
            PROFILE_ALL_VARIABLE: b"PROFILE,VARIABLE_ALL\n",
        }
        for profile, expected in expected_commands.items():
            with self.subTest(profile=profile):
                self.assertEqual(build_profile_command(profile), expected)

    def test_collect_only_builds_no_command(self) -> None:
        self.assertIsNone(build_profile_command(PROFILE_COLLECT_ONLY))

    def test_profile_normalization_and_validation(self) -> None:
        self.assertEqual(normalize_profile(" variable LOAD "), PROFILE_VARIABLE_LOAD)
        with self.assertRaises(ValueError):
            build_profile_command("unsupported")

    def test_parses_profile_stop_and_error_responses(self) -> None:
        self.assertEqual(
            parse_control_response("ACK,PROFILE,VARIABLE_SPEED"),
            ("profile_ack", "VARIABLE_SPEED"),
        )
        self.assertEqual(parse_control_response(b"ACK,STOP\n"), ("stop_ack", None))
        self.assertEqual(parse_control_response("ERR,PROFILE"), ("error", None))

    def test_parser_accepts_two_field_lines(self) -> None:
        self.assertEqual(parse_validation_line("30;31"), (30.0, 31.0, None))
        self.assertEqual(parse_validation_line("30;31;0.2"), (30.0, 31.0, 0.2))


class CommandSafetyTests(unittest.TestCase):
    @staticmethod
    def make_disconnection_app(
        connection: FakeSerialConnection,
        *,
        gui_started_motor: bool,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            serial_connection=connection,
            active_profile_token=None,
            pending_control_command=None,
            control_command_error=None,
            gui_started_motor=gui_started_motor,
            connection_generation=1,
            reader_stop_event=connection.stop_event,
            connect_button=DummyWidget(),
            port_combo=DummyWidget(),
            refresh_button=DummyWidget(),
            stop_automatic_export=lambda: None,
            update_profile_controls_state=lambda: None,
            set_status=lambda _text, _color: None,
        )

    def test_collect_only_launch_sends_no_command(self) -> None:
        statuses: list[str] = []
        app = SimpleNamespace(
            selected_profile=lambda: PROFILE_COLLECT_ONLY,
            gui_started_motor=False,
            set_status=lambda text, _color: statuses.append(text),
        )

        TemperatureValidationApp.launch_selected_profile(app)

        self.assertFalse(app.gui_started_motor)
        self.assertEqual(statuses, ["Collecte seule · aucune commande moteur envoyée"])

    def test_disconnect_does_not_stop_a_motor_started_outside_the_gui(self) -> None:
        connection = FakeSerialConnection(threading.Event(), [])
        app = self.make_disconnection_app(connection, gui_started_motor=False)

        TemperatureValidationApp.disconnect_serial(app)

        self.assertEqual(connection.writes, [])
        self.assertTrue(connection.closed)

    def test_disconnect_stops_a_profile_started_by_the_gui(self) -> None:
        connection = FakeSerialConnection(threading.Event(), [])
        app = self.make_disconnection_app(connection, gui_started_motor=True)

        TemperatureValidationApp.disconnect_serial(app)

        self.assertEqual(connection.writes, [b"STOP\n"])
        self.assertTrue(connection.closed)

    def test_serial_error_disconnect_does_not_attempt_a_new_command(self) -> None:
        connection = FakeSerialConnection(threading.Event(), [])
        app = self.make_disconnection_app(connection, gui_started_motor=True)

        TemperatureValidationApp.disconnect_serial(app, stop_owned_motor=False)

        self.assertEqual(connection.writes, [])


class CsvSessionRecorderTests(unittest.TestCase):
    def test_recorder_writes_and_flushes_sample(self) -> None:
        sample = SessionAccumulator().add(10.0, 30.0, 31.25, 0.125)

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "session.csv"
            recorder = CsvSessionRecorder(output_path)
            recorder.append(sample)

            lines_before_close = output_path.read_text(encoding="utf-8").splitlines()
            recorder.close()

        self.assertEqual(
            lines_before_close,
            [
                "elapsed_s;d6t_temp_c;predicted_temp_c;signed_error_c;"
                "absolute_error_c;cumulative_mae_c;load_setpoint_a",
                "0.000;30.000000;31.250000;1.250000;1.250000;1.250000;0.125000",
            ],
        )


if __name__ == "__main__":
    unittest.main()
