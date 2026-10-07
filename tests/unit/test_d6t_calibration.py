from __future__ import annotations

import math
import queue
import shutil
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import d6t_calibration as calib
from d6t_calibration import (
    FIRMWARE_TARGETS,
    PIXEL_COUNT,
    FirmwareTarget,
    FrameReply,
    PixelHistory,
    contrast_text,
    heat_color,
    ordinal,
    parse_frame_reply,
    pixel_at,
    read_firmware_pixel,
    view_cell,
    write_firmware_pixel,
)

FRAME_LINE = "D6T_FRAME,ok=1,selected=10,ptat=253," + "px=" + ":".join(str(240 + i) for i in range(PIXEL_COUNT))


class ParsingTests(unittest.TestCase):
    def test_valid_frame(self) -> None:
        reply = parse_frame_reply(FRAME_LINE)
        self.assertTrue(reply.ok)
        self.assertEqual(reply.selected, 10)
        self.assertAlmostEqual(reply.ptat_c, 25.3)
        self.assertEqual(len(reply.pixels_c), PIXEL_COUNT)
        self.assertAlmostEqual(reply.pixels_c[15], 25.5)

    def test_sensor_missing(self) -> None:
        reply = parse_frame_reply("D6T_FRAME,ok=0,selected=10")
        self.assertEqual((reply.ok, reply.selected, reply.pixels_c), (False, 10, ()))

    def test_other_lines_and_malformed_frames(self) -> None:
        self.assertIsNone(parse_frame_reply("ACK,D6T_FRAME"))
        self.assertIsNone(parse_frame_reply("D6T_FRAME,ok=1,selected=10,ptat=253,px=1:2:3"))
        self.assertIsNone(parse_frame_reply("D6T_FRAME,ok=1,selected=10,ptat=x,px=" + ":".join("1" * PIXEL_COUNT)))


class ColourTests(unittest.TestCase):
    def test_scale_ends_and_missing_value(self) -> None:
        self.assertEqual(heat_color(10.0, 20.0, 40.0), "#140B34")
        self.assertEqual(heat_color(50.0, 20.0, 40.0), "#FCFDBF")
        self.assertEqual(heat_color(math.nan, 20.0, 40.0), calib.COLORS["empty"])

    def test_text_contrast(self) -> None:
        self.assertEqual(contrast_text("#FCFDBF"), calib.COLORS["ink_dark"])
        self.assertEqual(contrast_text("#140B34"), calib.COLORS["text"])

    def test_ordinal(self) -> None:
        self.assertEqual([ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 16)], ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "16th"])


class OrientationTests(unittest.TestCase):
    def test_every_view_is_a_permutation(self) -> None:
        for rotation in range(4):
            for mirrored in (False, True):
                cells = {view_cell(index, rotation, mirrored) for index in range(PIXEL_COUNT)}
                self.assertEqual(len(cells), PIXEL_COUNT)
                for index in range(PIXEL_COUNT):
                    self.assertEqual(pixel_at(*view_cell(index, rotation, mirrored), rotation, mirrored), index)

    def test_clockwise_rotation_and_mirror(self) -> None:
        self.assertEqual(view_cell(0, 1, False), (0, 3))
        self.assertEqual(view_cell(0, 0, True), (0, 3))
        self.assertEqual(view_cell(5, 0, False), (1, 1))


class HistoryTests(unittest.TestCase):
    def test_statistics_and_expiry(self) -> None:
        history = PixelHistory(seconds=5.0)
        for second in range(8):
            history.add(float(second), tuple(float(second + index) for index in range(PIXEL_COUNT)))
        self.assertEqual(len(history.samples), 6)
        self.assertAlmostEqual(history.mean(0), 4.5)
        self.assertEqual(history.span(0), (2.0, 7.0))
        self.assertEqual(history.overall_range(), (2.0, 22.0))
        self.assertAlmostEqual(history.rate_hz(), 1.0)

    def test_empty_history(self) -> None:
        history = PixelHistory()
        self.assertTrue(math.isnan(history.mean(3)))
        self.assertEqual(history.rate_hz(), 0.0)


class FirmwareSourceTests(unittest.TestCase):
    def test_both_firmwares_log_the_same_pixel(self) -> None:
        values = [read_firmware_pixel(target.path) for target in FIRMWARE_TARGETS]
        self.assertNotIn(None, values)
        self.assertEqual(len(set(values)), 1)

    def test_write_updates_define_and_comment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for target in FIRMWARE_TARGETS:
                copy = Path(directory) / f"{target.name}.c"
                shutil.copyfile(target.path, copy)
                self.assertTrue(write_firmware_pixel(copy, 5))
                self.assertEqual(read_firmware_pixel(copy), 5)
                text = copy.read_text(encoding="utf-8")
                self.assertIn("#define D6TIR_SELECTED_PIXEL_INDEX  5U", text)
                self.assertIn("d6t_temp_c : 5 = ligne 2, colonne 2", text)
                self.assertFalse(write_firmware_pixel(copy, 5))

    def test_write_keeps_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "d6t_ir.c"
            path.write_bytes(b"/* d6t_temp_c : 10 = ligne 3, colonne 3 */\r\n#define D6TIR_SELECTED_PIXEL_INDEX  10U\r\n")
            write_firmware_pixel(path, 15)
            self.assertEqual(
                path.read_bytes(),
                b"/* d6t_temp_c : 15 = ligne 4, colonne 4 */\r\n#define D6TIR_SELECTED_PIXEL_INDEX  15U\r\n",
            )

    def test_invalid_index_and_missing_define(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "d6t_ir.c"
            path.write_text("int x;\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                write_firmware_pixel(path, 16)
            with self.assertRaises(ValueError):
                write_firmware_pixel(path, 3)
            self.assertIsNone(read_firmware_pixel(path))


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        targets = []
        for target in FIRMWARE_TARGETS:
            copy = Path(self.directory.name) / f"{target.name}.c"
            shutil.copyfile(target.path, copy)
            targets.append(FirmwareTarget(target.name, copy))
        patcher = mock.patch.object(calib, "FIRMWARE_TARGETS", tuple(targets))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.targets = targets
        try:
            self.app = calib.D6tCalibrationApp()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.app.withdraw()

    def tearDown(self) -> None:
        if hasattr(self, "app"):
            self.app.destroy()
        self.directory.cleanup()

    def push_frame(self, hot_index: int) -> None:
        pixels = tuple(35.0 if index == hot_index else 24.0 for index in range(PIXEL_COUNT))
        self.app.events.put((self.app.source_id, "frame", FrameReply(True, 10, 25.0, pixels)))
        self.app.drain_events()

    def test_frame_updates_selection_panel(self) -> None:
        self.push_frame(hot_index=9)
        self.app.select_hottest()
        self.assertEqual(self.app.selected_index, 9)
        self.assertEqual(self.app.pixel_live_var.get(), "35.0 \u00b0C")
        self.assertEqual(self.app.pixel_rank_var.get(), "Hottest")
        self.assertEqual(self.app.pixel_delta_var.get(), "+10.0 \u00b0C")
        self.assertEqual(self.app.board_pixel_var.get(), "Board: logs pixel 10.")

    def test_events_from_stopped_sources_are_ignored(self) -> None:
        self.app.events.put((self.app.source_id - 1, "frame", FrameReply(True, 10, 25.0, (30.0,) * PIXEL_COUNT)))
        self.app.drain_events()
        self.assertIsNone(self.app.latest)

    def test_write_to_firmwares(self) -> None:
        self.app.select_pixel(6)
        with mock.patch.object(calib.messagebox, "askyesno", return_value=True):
            self.app.write_to_firmwares()
        self.assertEqual([read_firmware_pixel(target.path) for target in self.targets], [6, 6])
        self.assertEqual(self.app.firmware_hint_var.get(), "Pixel 6 written: rebuild and flash both projects.")
        self.assertEqual(str(self.app.write_button["state"]), tk.DISABLED)

    def test_write_cancelled(self) -> None:
        self.app.select_pixel(6)
        with mock.patch.object(calib.messagebox, "askyesno", return_value=False):
            self.app.write_to_firmwares()
        self.assertEqual([read_firmware_pixel(target.path) for target in self.targets], [10, 10])

    def test_keyboard_moves_follow_the_rotated_view(self) -> None:
        self.app.select_pixel(0)
        self.app.rotate_view()
        self.app.on_key_move(None, 0, -1)
        self.assertEqual(self.app.selected_index, 4)


class SerialSourceTests(unittest.TestCase):
    def test_reply_is_found_among_other_lines(self) -> None:
        events: queue.Queue = queue.Queue()
        source = calib.SerialFrameSource(1, events, 0.25, "COM_TEST", 115200)
        connection = mock.Mock()
        connection.read.side_effect = [b"DATA,1,2\r\nACK,D6T", b"_FRAME\r\n" + FRAME_LINE.encode() + b"\r\n"]
        reply = source.wait_reply(connection)
        self.assertEqual(reply.selected, 10)

    def test_validation_firmware_is_recognised(self) -> None:
        source = calib.SerialFrameSource(1, queue.Queue(), 0.25, "COM_TEST", 115200)
        connection = mock.Mock()
        chunks = [b"24.125;25.031;0.250\r\n"]
        connection.read.side_effect = lambda _size: chunks.pop(0) if chunks else b""
        with mock.patch.object(calib, "REPLY_TIMEOUT_S", 0.05):
            self.assertIsNone(source.wait_reply(connection))
        self.assertIn("firmware_validation", source.silence_message())


if __name__ == "__main__":
    unittest.main()
