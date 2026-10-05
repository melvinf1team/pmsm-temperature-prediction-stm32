"""Tests for the EWMA preprocessing output."""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pretraitement import preprocess_logs_ewma as preprocess


BASE_ROWS = {
    "stm32_time_ms": [0, 500, 1000],
    "d6t_temp_c": [24.0, 24.1, 24.2],
    "ds18b20_temp_c": [25.0, 25.1, 25.2],
    "motor_ud_v": [1.0, 1.1, 1.2],
    "motor_uq_v": [2.0, 2.1, 2.2],
    "motor_speed_mech_rpm": [600.0, 650.0, 700.0],
    "motor_id_a": [0.1, 0.2, 0.3],
    "motor_iq_a": [0.4, 0.5, 0.6],
}


class LoadSetpointPreprocessingTests(unittest.TestCase):
    def process(
        self,
        frame: pd.DataFrame,
        *,
        include_load_setpoint: bool = False,
    ) -> pd.DataFrame:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            input_file = workdir / "daq_log_test.csv"
            output_dir = workdir / "processed"
            output_dir.mkdir()
            frame.to_csv(input_file, sep=";", index=False)

            preprocess.process_file(
                csv_file=input_file,
                output_dir=output_dir,
                write_header=True,
                include_time_ms=False,
                forced_frequency_hz=None,
                include_load_setpoint=include_load_setpoint,
            )

            return pd.read_csv(output_dir / input_file.name, sep=";")

    def test_default_output_is_target_plus_55_features(self):
        frame = pd.DataFrame(
            {
                **BASE_ROWS,
                preprocess.LOAD_SETPOINT_COLUMN: [0.05, 0.25, 0.12],
            }
        )

        result = self.process(frame)
        result_without_load = self.process(pd.DataFrame(BASE_ROWS))

        self.assertEqual(
            list(result.columns),
            [preprocess.TARGET_COLUMN]
            + preprocess.model_feature_columns(preprocess.REFERENCE_SPANS),
        )
        self.assertNotIn(preprocess.LOAD_SETPOINT_COLUMN, result.columns)
        pd.testing.assert_frame_equal(result, result_without_load)

    def test_opt_in_preserves_raw_load_without_ewma(self):
        frame = pd.DataFrame(
            {
                **BASE_ROWS,
                preprocess.LOAD_SETPOINT_COLUMN: [0.05, 0.25, 0.12],
            }
        )

        result = self.process(frame, include_load_setpoint=True)

        self.assertEqual(
            result.columns[-1],
            preprocess.LOAD_SETPOINT_COLUMN,
        )
        np.testing.assert_allclose(
            result[preprocess.LOAD_SETPOINT_COLUMN],
            [0.05, 0.25, 0.12],
        )
        self.assertFalse(
            any(
                column.startswith(f"{preprocess.LOAD_SETPOINT_COLUMN}_ewma_")
                for column in result.columns
            )
        )
        self.assertEqual(
            list(result.columns[1:-1]),
            preprocess.model_feature_columns(preprocess.REFERENCE_SPANS),
        )

    def test_missing_load_column_gives_unknown_load_with_same_schema(self):
        result_without_load = self.process(
            pd.DataFrame(BASE_ROWS),
            include_load_setpoint=True,
        )
        result_with_load = self.process(
            pd.DataFrame(
                {
                    **BASE_ROWS,
                    preprocess.LOAD_SETPOINT_COLUMN: [0.05, 0.10, 0.25],
                }
            ),
            include_load_setpoint=True,
        )

        self.assertEqual(list(result_without_load.columns), list(result_with_load.columns))
        self.assertTrue(result_without_load[preprocess.LOAD_SETPOINT_COLUMN].isna().all())

    def test_invalid_load_is_unknown_instead_of_zero(self):
        frame = pd.DataFrame(
            {
                **BASE_ROWS,
                preprocess.LOAD_SETPOINT_COLUMN: [0.05, "invalid", np.inf],
            }
        )

        result = self.process(frame, include_load_setpoint=True)

        self.assertAlmostEqual(result.loc[0, preprocess.LOAD_SETPOINT_COLUMN], 0.05)
        self.assertTrue(result.loc[1:, preprocess.LOAD_SETPOINT_COLUMN].isna().all())

    def test_rows_with_invalid_target_are_dropped_after_ewma(self):
        frame = pd.DataFrame(
            {
                **BASE_ROWS,
                preprocess.TARGET_COLUMN: [24.0, "NaN", ""],
            }
        )

        result = self.process(frame)
        reference = self.process(pd.DataFrame(BASE_ROWS))

        self.assertEqual(len(result), 1)
        pd.testing.assert_frame_equal(result, reference.iloc[[0]])

    def test_load_setpoint_option_is_a_toggle(self):
        self.assertTrue(preprocess.parse_args(["--include-load-setpoint"]).include_load_setpoint)
        self.assertFalse(preprocess.parse_args(["--no-include-load-setpoint"]).include_load_setpoint)

        with unittest.mock.patch.object(preprocess, "INCLUDE_LOAD_SETPOINT", True):
            self.assertTrue(preprocess.parse_args([]).include_load_setpoint)
        with unittest.mock.patch.object(preprocess, "INCLUDE_LOAD_SETPOINT", False):
            self.assertFalse(preprocess.parse_args([]).include_load_setpoint)


if __name__ == "__main__":
    unittest.main()
