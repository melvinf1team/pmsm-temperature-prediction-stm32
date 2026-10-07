# PMSM Data Logging and Thermal Prediction on STM32

This repository contains the complete acquisition, data preparation, and
on-device validation pipeline for a NanoEdge AI model that estimates a PMSM
motor's internal temperature. The training target is `d6t_temp_c`; explanatory
variables come from the DS18B20 sensor and motor control.

The test bench uses a **B-G473E-ZEST1S** board, an **STDES-LVHP01** power
board, an **Omron D6T** infrared sensor, a **DS18B20** sensor, and a
**TB-200S** controller for variable powder-brake load. PC communication uses
USART1 at 115200 baud.

## Repository layout

| Path | Purpose |
|---|---|
| `datalogging/` | Tkinter dashboard, motor profiles, and raw CSV logs |
| `pretraitement/` | Physical quantities and EWMA feature generation for NanoEdge AI |
| `firmware_acquisition/tets_motor_dewalt/` | STM32CubeIDE/MCSDK firmware controlled by the dashboard |
| `firmware_validation/` | Standalone firmware for 55-feature computation and NanoEdge AI inference |
| `validation/` | PC interface comparing measured and predicted temperatures |
| `tests/` | Checks and tools: `bench/` (board needed: wiring check, D6T calibration, NanoEdge serial check), `consistency/`, `unit/`, and `run_all.py` |
| `inventories/` | Dataset inventory scripts |
| `docs/` | Detailed Sphinx documentation |
| `dashboard_config.yaml` | Default dashboard paths |
| `preprocess_ewma.yaml` | Default preprocessing input and output paths |

The processing pipeline is:

```text
Sensors + MCSDK
       |
       v
Acquisition firmware --USART1--> Python dashboard --> Raw CSV files
                                                         |
                                                         v
                                               Python preprocessing
                                                         |
                                                         v
                                                Target + 55 features
                                                         |
                                                         v
                                                 NanoEdge AI Studio
                                                         |
                                                         v
                                               Validation firmware
                                                         |
                                  +----------------------+-------------------+
                                  |                                          |
                           Serial Emulator                        Validation interface
```

## Prerequisites

- Windows with Python 3.10 or newer and Tkinter;
- STM32CubeIDE with a GNU Arm toolchain compatible with Cortex-M4 hard-float;
- STM32CubeProgrammer/ST-LINK to program the board;
- NanoEdge AI Studio to train or replace the embedded library;
- access to the B-G473E-ZEST1S serial port.

Create the Python environment from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## TB-200S wiring and DAC range

Both firmware projects reserve `PA5 / DAC1_OUT2`, available on
**Morpho CN7 pin 32**, for the TB-200S. Use **CN7 pin 20 or 22** for signal
ground:

```text
CN7-32 / PA5 / DAC1_OUT2 ---- 1 kΩ ----+---- ADJ (TB-200S)
                                        |
                                      4.7 µF
                                        |
CN7-20 or CN7-22 / GND ----------------+---- GND (TB-200S)
```

For a polarized 4.7 µF capacitor, connect `+` to ADJ and `-` to GND. The TB-200S
`GND` used here is its signal ground, not protective earth: with power removed,
verify the exact controller revision and check that no hazardous potential
exists before joining it to the board ground. Remove the TB-200S factory link
used by its local adjustment before driving ADJ, select the **0–10 V external
input**, and never connect its `+10V` terminal to the STM32. The firmware
assumes the nominal 0–10 V → 0–3 A transfer:

```text
V_ADJ = load_setpoint_a × 10 / 3
0.05 A -> 0.1667 V
0.25 A -> 0.8333 V
```

No 0–10 V amplifier is used. DAC3/DAC4 are reserved for the motor
overcurrent comparators. The STM32G473 buffered DAC is guaranteed linear only
from 0.2 V, so the nominal 0.05 A point must be measured and qualified on the
bench. See [`docs/source/cablage.rst`](docs/source/cablage.rst) for the DAC
pin table and safety checklist, the
[B-G473E-ZEST1S user manual](https://www.st.com/resource/en/user_manual/um3118-motor-control-discovery-kit-with-stm32g473qe-mcu-stmicroelectronics.pdf),
a [TB-200S external-control reference (third-party mirror)](https://manuals.plus/ae/1005008626943763),
and the manual supplied with the TB-200S controller.

## Wiring check

With `firmware_acquisition` flashed and the dashboard closed, run:

```powershell
python .\tests\bench\check_wiring.py --port COM5
python .\tests\bench\check_wiring.py --port COM5 --motor
```

The first command keeps the motor stopped. It sends `DIAG`, then records
3 seconds of `ACQ_START`, and reports `OK`, `WARN`, or `FAIL` for each module
with its probable causes:

- **D6T** (CN10-27/CN10-24): missing pull-up or 3V3 rail, SCL/SDA swapped or
  shorted, wire on a neighbouring Morpho pin, unpowered sensor (lines clamped
  low), rejected frames;
- **DS18B20** (CN7-1): no presence pulse, sensor on another pin, VDD not
  supplied (parasite power), missing external pull-up, corrupted data;
- **TB-200S command** (CN7-32): nothing connected (wire, SB91, R1), R1/C1
  network found on CN7-31 or CN7-34, C1 missing, ADJ shorted to GND, external
  voltage on ADJ;
- **power stage**: bus voltage and latched MCSDK faults.

`--motor` asks for confirmation (or `--yes`), runs the motor at 1500 rpm with
a 6 A `Iq` limit, and checks the U/V/W phases: MCSDK faults and firmware stop
reasons are decoded when the motor does not start, the d/q telemetry is
checked, the rotation direction is asked to detect swapped phases, and the
brake response to a 0.05 → 0.25 → 0.05 A step is measured. The exit code is
`0` without failure, `1` with at least one failure, and `2` when the port
cannot be opened.

> During `DIAG`, the PA5 test briefly lets ADJ rise to about 3 V (about 1 A
> brake command for less than one second) while the motor is stopped.
> Misplaced wires are only searched on the free Morpho pins adjacent to the
> expected ones. Swapped motor phases are detected only through the direction
> answer.

## D6T pixel calibration

The D6T-44L-06 measures 16 pixels; both firmwares log one of them as
`d6t_temp_c`. With `firmware_acquisition` flashed and the motor warmer than
its surroundings, open the live map:

```powershell
python .\tests\bench\d6t_calibration.py --port COM5
python .\tests\bench\d6t_calibration.py --demo
```

Click the pixel that sees the motor (or **Select hottest**), then
**Write pixel N to both firmwares**: the tool sets
`D6TIR_SELECTED_PIXEL_INDEX` in the `d6t_ir.c` file of both projects. Rebuild
and flash both firmwares afterwards. A new pixel means a new target: record new
logs and retrain the model. The procedure and the list of D6T settings in the
firmwares are in [`tests/README.md`](tests/README.md).

## Acquisition and data logging

Start the dashboard:

```powershell
python .\datalogging\motor_datalog_gui_dashboard.py
```

Two modes are available:

- **Motor + logging**: `SYNC`, `CFG`, `LOAD`, then `START`;
- **Logging only (motor stopped)**: `SYNC`, then `ACQ_START`, useful for
  recording a cooling phase.

In both cases, `STOP` ends the session cleanly. Motor profiles are loaded from
`datalogging/motor_profiles.json`. Each motor profile selects either a fixed
0.05–0.25 A brake command or `Variable` (pseudorandom command every
2–5 seconds).

The raw CSV uses semicolons and contains nine columns:

```text
stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a;load_setpoint_a
```

`load_setpoint_a` is the instantaneous command reported by the firmware, not
an independently measured brake current. The logs stored in
`datalogging/logs` contain only the first eight columns.

Configure paths in `dashboard_config.yaml`, with `--config`, or with these
environment variables:

| Option | Environment variable | Default |
|---|---|---|
| `--log-dir` | `PMSM_DATALOG_LOG_DIR` | `datalogging/logs` |
| `--profile-store` | `PMSM_DATALOG_PROFILE_STORE` | `datalogging/motor_profiles.json` |
| `--csv-path` | `PMSM_DATALOG_CSV_PATH` | Timestamped name in the log directory |

Example:

```powershell
python .\datalogging\motor_datalog_gui_dashboard.py `
    --config .\dashboard_config.yaml `
    --log-dir .\datalogging\logs
```

## NanoEdge AI preprocessing

Run with the default configuration:

```powershell
python .\pretraitement\preprocess_logs_ewma.py
```

The script reads `datalogging/logs/daq_log_*.csv` and writes results to
`pretraitement/logs_processed_ewma`. It:

1. keeps `d6t_temp_c` as the first column and extrapolation target;
2. uses six raw measurements as explanatory variables;
3. computes `u_s`, `i_s`, `S_el`, `speed_current`, and `speed_power`;
4. adds four EWMAs to each of the eleven explanatory variables;
5. produces **55 features** in addition to the target.

The TB-200S `load_setpoint_a` command is not a model input and is omitted by
default. Like the timestamp, it can be kept in the output: it is then written
unfiltered after the 55 features, and left empty for logs that do not contain
it. Do not feed this extra column to the 55-input embedded model.

Reference spans `[1320, 3360, 6360, 9480]` correspond to 2 Hz. The actual
rate is derived from the median of positive `stm32_time_ms` differences, then
the spans are rescaled. At 10 Hz, they become
`[6600, 16800, 31800, 47400]`.

By default, the output has no header, no timestamp, and no load command. Set
`WRITE_HEADER`, `INCLUDE_TIME_MS`, or `INCLUDE_LOAD_SETPOINT` to `True` at the
top of the script to change these defaults, or use the command-line options:

```powershell
python .\pretraitement\preprocess_logs_ewma.py --header
python .\pretraitement\preprocess_logs_ewma.py --include-time
python .\pretraitement\preprocess_logs_ewma.py --include-load-setpoint
python .\pretraitement\preprocess_logs_ewma.py --frequency-hz 10
python .\pretraitement\preprocess_logs_ewma.py --config .\preprocess_ewma.yaml
```

`--no-header`, `--no-include-time`, and `--no-include-load-setpoint` force the
opposite choice. `PMSM_PREPROCESS_INPUT_DIR`, `PMSM_PREPROCESS_OUTPUT_DIR`,
and `PMSM_PREPROCESS_PATTERN` override the input directory, output directory,
and filename pattern respectively.

> **Data quality:** preprocessing converts explanatory variables to numbers
> and replaces their invalid or infinite values with `0.0`. Rows whose
> `d6t_temp_c` target is not a finite number are dropped from the output after
> the EWMAs are computed; their count is printed for each file.

## Acquisition firmware

Import the project at
`firmware_acquisition/tets_motor_dewalt/STM32CubeIDE` into STM32CubeIDE.

The main application modules are:

- `app_serial_control.c`: UART protocol, receive queue, and validation;
- `app_motor_control.c`: MCSDK control, ramps, protections, DAC conversion,
  and fixed/variable TB-200S scheduling;
- `app_datalog.c`: sensor scheduling and nonblocking CSV transmission;
- `app_wiring_diag.c`: wiring diagnostics (`DIAG`) and motor status
  (`STATUS`);
- `d6t_ir.c`: I2C reads from the D6T sensor (logged pixel
  `D6TIR_SELECTED_PIXEL_INDEX`);
- `ds18b20.c`: 1-Wire reads from the DS18B20 sensor.

The protocol accepts:

```text
SYNC
CFG,<target_rpm>,<iq_limit_a>,<hard_limit_a>,<accel_elec_hz_s>,<datalog_ms>,<ds18b20_ms>
LOAD,<load_setpoint_a>
LOAD,VARIABLE
START
ACQ_START,<datalog_ms>,<ds18b20_ms>
STOP
DIAG
STATUS
D6T_FRAME
```

`DIAG` stops the motor and logging, probes the sensor and TB-200S lines, then
answers `DIAG,...` lines followed by `ACK,DIAG`. `STATUS` answers
`STATUS,app_state=...,fault_reason=...,faults_occurred=0x....,vbus_mv=...`.
`D6T_FRAME` answers the 16 D6T pixels and PTAT in tenths of a degree
(`D6T_FRAME,ok=1,selected=10,ptat=253,px=241:...`) without touching the motor.

Application limits are 4500 rpm, 30 A for `Iq`, and 30 A for total current.
Minimum speed is 100 rpm, and acceleration is capped at 50 electrical Hz/s
in both dashboard and firmware.

B2 starts a standalone profile in which both speed and TB-200S load vary,
regardless of any earlier `LOAD` command:

- speed: pseudorandom target drawn from 2000–4000 rpm every 2–5 seconds,
  never twice the same value in a row;
- ramp: 500 electrical Hz/s (15,000 rpm/s with two pole pairs) at startup
  and on every change;
- current: PI `Iq` output capped at 25 A; `Id/Iq` command magnitude and
  shutdown threshold on measured magnitude capped at 28 A;
- load: 0.05 A at launch, then pseudorandom 0.05–0.25 A every 2–5 seconds.

A second press stops the motor. A `CFG` received over UART disables the B2
profile and takes control; dashboard-configured starts stay capped at
50 electrical Hz/s.

UART starts also launch with a 0.05 A load command: a higher fixed setpoint is
applied once MCSDK reaches `RUN`, and variable mode makes its first draw
2–5 seconds later. The speed setpoint is reapplied on `RUN`, a restart
requested during shutdown waits for `IDLE`, and overspeed protection never
exceeds the absolute 4500 rpm ceiling.

> **Qualification required:** these are software ceilings, not test bench
> certification. Before running at 4500 rpm or 30 A, verify the motor,
> STDES-LVHP01, supply, wiring, cooling, and protections. Startup polarization
> is limited to 14 A. Preventive DC bus protection is disabled
> (`M1_BUS_PROTECTION=false`): rapid B2 deceleration can regenerate energy
> into the bus. Monitor bus voltage and validate absorption or braking
> capacity before the test.

## NanoEdge AI validation firmware

The `firmware_validation/STM32CubeIDE` project recomputes the same 55 features
as the Python script on the board every 100 ms. Select the mode in
`firmware_validation/Inc/app_config.h`:

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U`: run the model and output
  `d6t_temp_c;predicted_temp_c;load_setpoint_a` at 10 Hz;
- `0U`: output the 55 values for the NanoEdge AI Studio Serial Emulator.

While either stream is running, the validation firmware accepts four complete
profiles through `PROFILE,<TOKEN>`:

| Token | Speed | TB-200S command |
|---|---|---|
| `STABLE` | 2500 rpm | Fixed 0.10 A |
| `VARIABLE_LOAD` | 2500 rpm | Pseudorandom 0.05–0.25 A every 2–5 s |
| `VARIABLE_SPEED` | Pseudorandom 2000–4000 rpm every 2–5 s | Fixed 0.10 A |
| `VARIABLE_ALL` | Pseudorandom 2000–4000 rpm every 2–5 s | Pseudorandom 0.05–0.25 A every 2–5 s |

Every profile forces 0.05 A before motor launch; fixed 0.10 A is applied only
after MCSDK reaches `RUN`. The four profiles use the B2 ramp and current
limits, and the physical B2 button always launches `VARIABLE_ALL`. `STOP`
stops the motor and returns the load to 0.05 A. Accepted commands answer
`ACK,PROFILE,<TOKEN>` or `ACK,STOP`.

The included export is a `1 x 55` Ridge regression for Cortex-M4 hard-float.
Its ID is `6a99400cd097fef61cf265dc`. Export metadata reports a score of
`0.9827`, a main KPI of `0.9944`, 464 estimated bytes of RAM, and 892
estimated bytes of Flash.

To replace the model, replace `libneai.a`, `NanoEdgeAI.h`,
`metadata.json`, and `artifacts/` together in `firmware_validation/AI_Model`.
Keep 55 inputs and the extrapolation API, then run **Clean Project** followed
by **Build Project** in STM32CubeIDE.

## Validation

Every check that needs no board, in one command (see
[`tests/README.md`](tests/README.md)):

```powershell
python .\tests\run_all.py
```

It runs the five unit-test files of `tests/unit` and the three consistency
checks of `tests/consistency` (NanoEdge export, motor limits, embedded/pandas
parity) in about 10 s and prints one `PASS`/`FAIL` line per script. All eight
pass.

Check the serial contract with a connected board:

```powershell
python .\tests\bench\check_nanoedge_serial.py --port COM5 --mode model
python .\tests\bench\check_nanoedge_serial.py --port COM5 --mode emulator
```

Temperature validation interface:

```powershell
python .\validation\test\temperature_validation_gui.py --port COM5
python .\validation\test\temperature_validation_gui.py --demo
```

The interface presents five profile cards: `Collecte seule`,
`Profil stable`, `Charge variable`, `Vitesse variable`, and `Tout variable`.
Connecting never starts the motor. In `Collecte seule`, the GUI sends no motor
command, so B2 can launch the standalone all-variable profile. For another
selection, **Démarrer ce profil** sends `PROFILE,<TOKEN>`; **Arrêter le moteur**
is enabled only for a profile launched by the GUI and sends `STOP`. A
collection-only disconnect sends no command; when the GUI owns the active
profile, disconnecting or closing attempts a safety `STOP`.

The header keeps serial, data, and motor states distinct. Five KPI cards show
the D6T temperature, AI prediction, instantaneous error, running MAE, and the
reported TB-200S setpoint with a read-only gauge. Three synchronized plots
show temperature, absolute error, and load over the latest 90 seconds. The
session CSV includes `load_setpoint_a`.

## Documentation

Build the HTML documentation:

```powershell
python -m sphinx -b html .\docs\source .\docs\build\html
```

The generated entry point is `docs/build/html/index.html`. The documentation
covers architecture, wiring, protocol, processing, both firmware projects,
AI validation, and the Python API. Open points are listed in
`docs/source/etat_projet.rst`.

## Known limitations

- No continuous integration pipeline is provided.
- Firmware builds rely on STM32CubeIDE; there is no versioned command-line
  build.
- The serial test requires a programmed board and an available COM port.
- The TB-200S command chain and the 4500 rpm / 30 A limits are not qualified
  on the physical test bench.
- No versioned script computes model metrics on independent validation data.
