from __future__ import annotations

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from temperature_validation_gui import (
    CsvSessionRecorder,
    SessionAccumulator,
    TemperatureValidationApp,
    is_transient_serial_error,
)


class FakeSerialConnection:
    def __init__(self, stop_event: threading.Event, responses: list[bytes | Exception]) -> None:
        self.stop_event = stop_event
        self.responses = responses

    def read(self, _size: int) -> bytes:
        if not self.responses:
            self.stop_event.set()
            return b""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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
        self.assertEqual(sample_event[1][2:], (31.5, 32.25))


class CsvSessionRecorderTests(unittest.TestCase):
    def test_recorder_writes_and_flushes_sample(self) -> None:
        sample = SessionAccumulator().add(10.0, 30.0, 31.25)

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
                "absolute_error_c;cumulative_mae_c",
                "0.000;30.000000;31.250000;1.250000;1.250000;1.250000",
            ],
        )


if __name__ == "__main__":
    unittest.main()