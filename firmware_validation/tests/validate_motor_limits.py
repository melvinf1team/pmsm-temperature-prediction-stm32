"""Validate motor limits and the B2 profile across firmware and host files."""

from __future__ import annotations

import importlib.util
import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACQUISITION_ROOT = PROJECT_ROOT / "firmware_acquisition" / "tets_motor_dewalt"
VALIDATION_ROOT = PROJECT_ROOT / "firmware_validation"

EXPECTED_MAX_SPEED_RPM = 4500.0
EXPECTED_MAX_CURRENT_A = 30.0
EXPECTED_POLPULSE_CURRENT_A = 14.0
EXPECTED_B2_MIN_SPEED_RPM = 2000.0
EXPECTED_B2_MAX_SPEED_RPM = 4000.0
EXPECTED_B2_MIN_STEP_RPM = 200.0
EXPECTED_B2_MAX_STEP_RPM = 500.0
EXPECTED_B2_MIN_PERIOD_MS = 10000.0
EXPECTED_B2_MAX_PERIOD_MS = 30000.0
EXPECTED_B2_ACCEL_ELEC_HZ_S = 10.0


def numeric_define(path: Path, name: str) -> float:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^#define\s+{re.escape(name)}\s+(.+?)\s*(?:/\*|$)", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"Missing {name} in {path.relative_to(PROJECT_ROOT)}")

    value = match.group(1).replace("(float_t)", "").strip().strip("()")
    value = value.rstrip("fFuUlL")
    return float(value)


def ioc_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MotorControl.") and "=" in line:
            key, value = line.split("=", 1)
            values[key.removeprefix("MotorControl.")] = value
    return values


def wbdef_values(path: Path) -> dict[str, str]:
    root = ElementTree.parse(path).getroot()
    return {
        element.attrib["key"]: element.attrib["value"]
        for element in root.iter("define")
    }


def load_dashboard_module():
    path = PROJECT_ROOT / "datalogging" / "motor_datalog_gui_dashboard.py"
    spec = importlib.util.spec_from_file_location("motor_datalog_gui_dashboard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_firmware_root(root: Path) -> None:
    app_header = root / "Inc" / "app_motor_control.h"
    drive_header = root / "Inc" / "drive_parameters.h"
    motor_header = root / "Inc" / "pmsm_motor_parameters.h"
    power_header = root / "Inc" / "power_stage_parameters.h"
    app_source = root / "STM32CubeIDE" / "Application" / "User" / "app_motor_control.c"

    assert numeric_define(app_header, "APP_MOTOR_MAX_TARGET_SPEED_RPM") == EXPECTED_MAX_SPEED_RPM
    assert numeric_define(app_header, "APP_MOTOR_MAX_IQ_LIMIT_A") == EXPECTED_MAX_CURRENT_A
    assert numeric_define(app_header, "APP_MOTOR_MAX_TOTAL_CURRENT_A") == EXPECTED_MAX_CURRENT_A

    assert numeric_define(drive_header, "MAX_APPLICATION_SPEED_RPM") == EXPECTED_MAX_SPEED_RPM
    assert numeric_define(drive_header, "BOARD_MAX_CURRENT") == EXPECTED_MAX_CURRENT_A
    assert numeric_define(drive_header, "BOARD_SOFT_OVERCURRENT_TRIP") == EXPECTED_MAX_CURRENT_A
    assert numeric_define(drive_header, "IQMAX_A") == EXPECTED_MAX_CURRENT_A
    assert numeric_define(drive_header, "PULSE_CURRENT_GOAL") == EXPECTED_POLPULSE_CURRENT_A
    assert numeric_define(motor_header, "MOTOR_MAX_SPEED_RPM") == EXPECTED_MAX_SPEED_RPM
    assert numeric_define(motor_header, "NOMINAL_CURRENT_A") == EXPECTED_MAX_CURRENT_A

    readable_current_a = 3.3 / (
        2.0
        * numeric_define(power_header, "RSHUNT")
        * numeric_define(power_header, "AMPLIFICATION_GAIN")
    )
    assert readable_current_a >= EXPECTED_MAX_CURRENT_A

    expected_ioc = {
        "M1_BOARD_MAX_CURRENT": "30",
        "M1_BOARD_SOFT_OVERCURRENT_TRIP": "30",
        "M1_MAX_APPLICATION_SPEED": "4500",
        "M1_MOTOR_MAX_SPEED_RPM": "4500",
        "M1_NOMINAL_CURRENT": "30",
        "M1_POLPULSES_PULSE_CURRENT_GOAL": "14.0",
    }
    for ioc_name in ("tets_motor_dewalt.ioc", "tets_motor_dewalt.ioc.wb"):
        values = ioc_values(root / ioc_name)
        assert all(values.get(key) == value for key, value in expected_ioc.items())

    values = wbdef_values(root / "tets_motor_dewalt.wbdef")
    assert all(values.get(key) == value for key, value in expected_ioc.items())

    expected_b2_defines = {
        "APP_BUTTON_MIN_TARGET_SPEED_RPM": EXPECTED_B2_MIN_SPEED_RPM,
        "APP_BUTTON_MAX_TARGET_SPEED_RPM": EXPECTED_B2_MAX_SPEED_RPM,
        "APP_BUTTON_MIN_SPEED_STEP_RPM": EXPECTED_B2_MIN_STEP_RPM,
        "APP_BUTTON_MAX_SPEED_STEP_RPM": EXPECTED_B2_MAX_STEP_RPM,
        "APP_BUTTON_MIN_CHANGE_PERIOD_MS": EXPECTED_B2_MIN_PERIOD_MS,
        "APP_BUTTON_MAX_CHANGE_PERIOD_MS": EXPECTED_B2_MAX_PERIOD_MS,
        "APP_BUTTON_IQ_LIMIT_A": EXPECTED_MAX_CURRENT_A,
        "APP_BUTTON_HARD_STOP_CURRENT_A": EXPECTED_MAX_CURRENT_A,
        "APP_BUTTON_ACCEL_ELEC_HZ_S": EXPECTED_B2_ACCEL_ELEC_HZ_S,
    }
    for name, expected in expected_b2_defines.items():
        assert numeric_define(app_source, name) == expected

    source = app_source.read_text(encoding="utf-8")
    for function_name in (
        "AppMotorControl_ApplySpeedReference",
        "AppMotorControl_SelectNextButtonSpeed",
        "AppMotorControl_ScheduleNextButtonSpeedChange",
        "AppMotorControl_ServiceButtonProfile",
    ):
        assert function_name in source

    assert source.count("AppMotorControl_ApplySpeedReference();") >= 3
    assert "if (speed_limit_rpm > APP_MOTOR_MAX_TARGET_SPEED_RPM)" in source
    assert "!isfinite(target_rpm)" in source
    assert "!isfinite(iq_limit_a)" in source
    assert "!isfinite(hard_limit_a)" in source
    assert "!isfinite(accel_elec_hz_s)" in source
    assert "!isfinite(idq.D)" in source
    assert "!isfinite(idq.Q)" in source
    assert "!isfinite(i2)" in source
    assert "!isfinite(speed_elec_hz)" in source
    assert "!isfinite(speed_rpm)" in source

    if root == ACQUISITION_ROOT:
        assert "AppMotorControl_ServiceStartRequest" in source
        assert "if (state != IDLE)" in source

    parameters_source = (root / "Src" / "mc_parameters.c").read_text(encoding="utf-8")
    polpulse_source = (root / "Src" / "mc_polpulse.c").read_text(encoding="utf-8")
    config_source = (root / "Src" / "mc_config.c").read_text(encoding="utf-8")
    assert ".softOverCurrentTrip = BOARD_SOFT_OVERCURRENT_TRIP" in parameters_source
    assert "FIXP30(BOARD_SOFT_OVERCURRENT_TRIP / CURRENT_SCALE)" in polpulse_source
    assert ".dcac_DCmax_current_A = BOARD_MAX_CURRENT" in config_source


def main() -> None:
    validate_firmware_root(ACQUISITION_ROOT)
    validate_firmware_root(VALIDATION_ROOT)

    dashboard = load_dashboard_module()
    assert dashboard.MIN_TARGET_SPEED_RPM == 100.0
    assert dashboard.MAX_TARGET_SPEED_RPM == EXPECTED_MAX_SPEED_RPM
    assert dashboard.MAX_IQ_LIMIT_A == EXPECTED_MAX_CURRENT_A
    assert dashboard.MAX_HARD_LIMIT_A == EXPECTED_MAX_CURRENT_A
    assert dashboard.MAX_ACCEL_ELEC_HZ_S == 50.0

    profiles = json.loads(
        (PROJECT_ROOT / "datalogging" / "motor_profiles.json").read_text(encoding="utf-8")
    )
    assert all(profile["accel_elec_hz_s"] <= dashboard.MAX_ACCEL_ELEC_HZ_S for profile in profiles)

    workbench = json.loads(
        (PROJECT_ROOT / "firmware_acquisition" / "tets_motor_dewalt.stwb6").read_text(
            encoding="utf-8"
        )
    )
    motor = workbench["hardwares"]["motor"][0]
    assert motor["nominalCurrent"] == EXPECTED_MAX_CURRENT_A
    assert motor["maxRatedSpeed"] == EXPECTED_MAX_SPEED_RPM

    print("Motor limits validation passed: 4500 rpm, 30 A, randomized smooth B2 profile.")


if __name__ == "__main__":
    main()