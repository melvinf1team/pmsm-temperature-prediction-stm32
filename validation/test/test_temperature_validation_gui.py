"""Tests unitaires du dashboard de validation, sans ouvrir de fenetre Tk."""

from __future__ import annotations

import math
import unittest

from temperature_validation_gui import (
    COLORS,
    SessionAccumulator,
    classify_error,
    format_one_decimal,
    is_current_connection_event,
    parse_validation_line,
)


class ParseValidationLineTests(unittest.TestCase):
    def test_parses_firmware_frame(self) -> None:
        self.assertEqual(parse_validation_line(b"31.4;30.8\r\n"), (31.4, 30.8))

    def test_rejects_wrong_field_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "Deux valeurs"):
            parse_validation_line("31.4;30.8;0.6")

    def test_rejects_non_finite_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "NaN"):
            parse_validation_line("31.4;nan")


class ErrorMetricTests(unittest.TestCase):
    def test_color_thresholds(self) -> None:
        self.assertEqual(classify_error(math.nan).color, COLORS["neutral"])
        self.assertEqual(classify_error(0.49).color, COLORS["green"])
        self.assertEqual(classify_error(0.50).color, COLORS["blue"])
        self.assertEqual(classify_error(1.00).color, COLORS["orange"])
        self.assertEqual(classify_error(1.50).color, COLORS["orange"])
        self.assertEqual(classify_error(1.51).color, COLORS["red"])

    def test_cumulative_error_is_session_mae(self) -> None:
        accumulator = SessionAccumulator()
        first = accumulator.add(10.0, 30.0, 30.4)
        second = accumulator.add(10.1, 30.0, 31.2)

        self.assertAlmostEqual(first.absolute_error_c, 0.4)
        self.assertAlmostEqual(second.absolute_error_c, 1.2)
        self.assertAlmostEqual(second.cumulative_mae_c, 0.8)
        self.assertAlmostEqual(second.elapsed_s, 0.1)

    def test_display_has_one_decimal(self) -> None:
        self.assertEqual(format_one_decimal(31.46), "31,5")
        self.assertEqual(format_one_decimal(-0.04), "0,0")
        self.assertEqual(format_one_decimal(math.nan), "--,-")

    def test_stale_connection_events_are_rejected(self) -> None:
        self.assertTrue(is_current_connection_event(4, 4))
        self.assertFalse(is_current_connection_event(3, 4))


if __name__ == "__main__":
    unittest.main()