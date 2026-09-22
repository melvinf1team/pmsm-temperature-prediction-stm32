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
EXPECTED_B2_MIN_PERIOD_MS = 2000.0
EXPECTED_B2_MAX_PERIOD_MS = 5000.0
EXPECTED_B2_IQ_LIMIT_A = 25.0
EXPECTED_B2_TOTAL_CURRENT_A = 28.0
EXPECTED_B2_ACCEL_ELEC_HZ_S = 500.0
EXPECTED_CFG_MAX_ACCEL_ELEC_HZ_S = 50.0
EXPECTED_TB200S_MIN_LOAD_A = 0.05
EXPECTED_TB200S_MAX_LOAD_A = 0.25
EXPECTED_TB200S_MIN_PERIOD_MS = 2000.0
EXPECTED_TB200S_MAX_PERIOD_MS = 5000.0
EXPECTED_PROFILE_FIXED_SPEED_RPM = 2500.0
EXPECTED_PROFILE_FIXED_LOAD_A = 0.10


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
    assert numeric_define(app_header, "APP_TB200S_MIN_LOAD_A") == EXPECTED_TB200S_MIN_LOAD_A
    assert numeric_define(app_header, "APP_TB200S_MAX_LOAD_A") == EXPECTED_TB200S_MAX_LOAD_A

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
        "M1_IQMAX": "30",
        "M1_MAX_APPLICATION_SPEED": "4500",
        "M1_MOTOR_MAX_SPEED_RPM": "4500",
        "M1_NOMINAL_CURRENT": "30",
        "M1_POLPULSES_PULSE_CURRENT_GOAL": "14.0",
        "WB_UI_MAX_CURRENT": "30",
    }
    for ioc_name in ("tets_motor_dewalt.ioc", "tets_motor_dewalt.ioc.wb"):
        values = ioc_values(root / ioc_name)
        assert all(values.get(key) == value for key, value in expected_ioc.items())

    values = wbdef_values(root / "tets_motor_dewalt.wbdef")
    assert all(values.get(key) == value for key, value in expected_ioc.items())

    expected_b2_defines = {
        "APP_BUTTON_MIN_TARGET_SPEED_RPM": EXPECTED_B2_MIN_SPEED_RPM,
        "APP_BUTTON_MAX_TARGET_SPEED_RPM": EXPECTED_B2_MAX_SPEED_RPM,
        "APP_BUTTON_MIN_CHANGE_PERIOD_MS": EXPECTED_B2_MIN_PERIOD_MS,
        "APP_BUTTON_MAX_CHANGE_PERIOD_MS": EXPECTED_B2_MAX_PERIOD_MS,
        "APP_BUTTON_IQ_LIMIT_A": EXPECTED_B2_IQ_LIMIT_A,
        "APP_BUTTON_HARD_STOP_CURRENT_A": EXPECTED_B2_TOTAL_CURRENT_A,
        "APP_BUTTON_ACCEL_ELEC_HZ_S": EXPECTED_B2_ACCEL_ELEC_HZ_S,
    }
    for name, expected in expected_b2_defines.items():
        assert numeric_define(app_source, name) == expected

    assert (
        numeric_define(app_source, "APP_TB200S_MIN_CHANGE_PERIOD_MS")
        == EXPECTED_TB200S_MIN_PERIOD_MS
    )
    assert (
        numeric_define(app_source, "APP_TB200S_MAX_CHANGE_PERIOD_MS")
        == EXPECTED_TB200S_MAX_PERIOD_MS
    )
    assert numeric_define(app_source, "APP_TB200S_DAC_VREF_V") == 3.3
    assert numeric_define(app_source, "APP_TB200S_FULL_SCALE_V") == 10.0
    assert numeric_define(app_source, "APP_TB200S_FULL_SCALE_A") == 3.0

    source = app_source.read_text(encoding="utf-8")
    assert (
        numeric_define(app_source, "APP_CFG_MAX_ACCEL_ELEC_HZ_S")
        == EXPECTED_CFG_MAX_ACCEL_ELEC_HZ_S
    )
    assert "APP_BUTTON_MIN_SPEED_STEP_RPM" not in source
    assert "APP_BUTTON_MAX_SPEED_STEP_RPM" not in source
    if root == ACQUISITION_ROOT:
        assert "initial_speed_rpm = AppMotorControl_SelectNextButtonSpeed();" in source
    else:
        assert (
            numeric_define(app_source, "APP_PROFILE_FIXED_SPEED_RPM")
            == EXPECTED_PROFILE_FIXED_SPEED_RPM
        )
        assert (
            numeric_define(app_source, "APP_PROFILE_FIXED_LOAD_A")
            == EXPECTED_PROFILE_FIXED_LOAD_A
        )
        assert "? AppMotorControl_SelectNextButtonSpeed()" in source
        assert ": APP_PROFILE_FIXED_SPEED_RPM;" in source
    assert "(uint32_t)APP_BUTTON_MIN_TARGET_SPEED_RPM" in source
    assert "(uint32_t)APP_BUTTON_MAX_TARGET_SPEED_RPM" in source
    assert "} while ((float)next_speed_rpm == app_target_speed_rpm);" in source
    assert "? APP_BUTTON_ACCEL_ELEC_HZ_S" in source
    assert "speed_ref_ramped_pu" in source
    assert "MCI_SetMaxCurrent(pMCI[M1], FIXP16(total_limit_a));" in source
    assert "APP_BUTTON_HARD_STOP_CURRENT_A - APP_BUTTON_IQ_LIMIT_A" in source
    runtime_config = source.split("void AppMotorControl_SetRuntimeConfig", maxsplit=1)[1]
    assert "AppMotorControl_StopButtonProfile();" in runtime_config.split(
        "void AppMotorControl_Init", maxsplit=1
    )[0]
    for function_name in (
        "AppMotorControl_ApplySpeedReference",
        "AppMotorControl_GetOverspeedReferenceRpm",
        "AppMotorControl_SelectNextButtonSpeed",
        "AppMotorControl_ScheduleNextButtonSpeedChange",
        "AppMotorControl_ServiceButtonProfile",
        "AppMotorControl_PrepareLoadForMotorStart",
        "AppMotorControl_OnMotorRunning",
        "AppMotorControl_ServiceVariableLoad",
        "AppMotorControl_SetLoadFixed",
        "AppMotorControl_SetLoadVariable",
        "AppMotorControl_GetLoadSetpointA",
    ):
        assert function_name in source

    start_body = source.split("bool AppMotorControl_Start(void)", maxsplit=1)[1].split(
        "void AppMotorControl_Stop(void)", maxsplit=1
    )[0]
    assert start_body.index("AppMotorControl_PrepareLoadForMotorStart()") < start_body.index(
        "MC_StartWithPolarizationMotor1()"
    )
    assert "AppMotorControl_ApplyLoadSetpoint(APP_TB200S_MIN_LOAD_A)" in source
    assert "AppMotorControl_ScheduleNextLoadChange(now);" in source
    assert "(app_mc_state == APP_MC_RUNNING) ? load_a : APP_TB200S_MIN_LOAD_A" in source

    ioc_text = (root / "tets_motor_dewalt.ioc").read_text(encoding="utf-8")
    assert "PA5.Signal=DAC1_OUT2" in ioc_text
    assert "PA5.GPIO_Label=TB200S_ADJ" in ioc_text
    assert "DAC1.DAC_OutputBuffer-DAC_OUT2=DAC_OUTPUTBUFFER_ENABLE" in ioc_text

    main_source = (root / "Src" / "main.c").read_text(encoding="utf-8")
    msp_source = (root / "Src" / "stm32g4xx_hal_msp.c").read_text(encoding="utf-8")
    assert "MX_DAC1_Init();" in main_source
    assert "HAL_DAC_ConfigChannel(&hdac1, &sConfig, DAC_CHANNEL_2)" in main_source
    assert "PA5     ------> DAC1_OUT2" in msp_source

    assert source.count("AppMotorControl_ApplySpeedReference();") >= 3
    assert "if (speed_limit_rpm > APP_MOTOR_MAX_TARGET_SPEED_RPM)" in source
    assert "AppMotorControl_IsFiniteFloat" in source
    assert "!AppMotorControl_IsFiniteFloat(target_rpm)" in source
    assert "!AppMotorControl_IsFiniteFloat(iq_limit_a)" in source
    assert "!AppMotorControl_IsFiniteFloat(hard_limit_a)" in source
    assert "!AppMotorControl_IsFiniteFloat(accel_elec_hz_s)" in source
    assert "!AppMotorControl_IsFiniteFloat(idq.D)" in source
    assert "!AppMotorControl_IsFiniteFloat(idq.Q)" in source
    assert "!AppMotorControl_IsFiniteFloat(i2)" in source
    assert "!AppMotorControl_IsFiniteFloat(speed_elec_hz)" in source
    assert "!AppMotorControl_IsFiniteFloat(speed_rpm)" in source

    if root == ACQUISITION_ROOT:
        assert "AppMotorControl_ServiceStartRequest" in source
        assert "if (state != IDLE)" in source
        assert "iq_limit_a=25,hard_limit_a=28,accel_elec_hz_s=500" in source
        serial_source = (
            root / "STM32CubeIDE" / "Application" / "User" / "app_serial_control.c"
        ).read_text(encoding="utf-8")
        datalog_source = (
            root / "STM32CubeIDE" / "Application" / "User" / "app_datalog.c"
        ).read_text(encoding="utf-8")
        assert 'strcmp(line, "LOAD,VARIABLE") == 0' in serial_source
        assert 'AppSerial_SendAck("LOAD")' in serial_source
        assert '"load_setpoint_a\\r\\n"' in datalog_source
    else:
        datalog_source = (
            root / "STM32CubeIDE" / "Application" / "User" / "app_datalog.c"
        ).read_text(encoding="utf-8")
        assert 'strcmp(line, "LOAD,VARIABLE") == 0' in datalog_source
        assert '"ACK,LOAD\\r\\n"' in datalog_source
        assert "AppMotorControl_GetLoadSetpointA()" in datalog_source
        assert "AppMotorControl_StartProfile" in source
        assert "AppMotorControl_ConfigureProfile" in source
        assert "app_button_speed_variable = variable_speed;" in source
        assert "app_tb200s_requested_load_a = variable_load" in source
        assert "? APP_TB200S_MIN_LOAD_A" in source
        assert ": APP_PROFILE_FIXED_LOAD_A;" in source
        assert (
            "AppMotorControl_StartProfile(APP_MOTOR_PROFILE_VARIABLE_ALL)"
            in source
        )
        assert "if (!app_tb200s_load_command_received)" not in source
        assert "app_motor_start_requested = true;" in source

        for token in (
            "STABLE",
            "VARIABLE_LOAD",
            "VARIABLE_SPEED",
            "VARIABLE_ALL",
        ):
            assert f'PROFILE,{token}' in datalog_source
            assert f'ACK,PROFILE,{token}\\r\\n' in datalog_source
        assert 'strcmp(line, "STOP") == 0' in datalog_source
        assert '"ACK,STOP\\r\\n"' in datalog_source
        assert '"ERR,BAD_PROFILE\\r\\n"' in datalog_source
        assert '"ERR,UNKNOWN_COMMAND\\r\\n"' in datalog_source

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
    assert dashboard.MIN_LOAD_SETPOINT_A == EXPECTED_TB200S_MIN_LOAD_A
    assert dashboard.MAX_LOAD_SETPOINT_A == EXPECTED_TB200S_MAX_LOAD_A
    assert dashboard.LOAD_SETPOINT_COLUMN in dashboard.CSV_OUTPUT_COLUMNS

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

    print(
        "Motor limits validation passed: global 4500 rpm/30 A; "
        "B2 2000-4000 rpm and TB-200S 0.05-0.25 A every 2-5 s; "
        "Iq 25 A, total 28 A, 500 Hz_e/s."
    )


if __name__ == "__main__":
    main()
