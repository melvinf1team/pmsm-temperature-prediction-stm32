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

- Windows with Python 3 and Tkinter;
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

No 0–10 V amplifier is used. `PA4/DAC1_OUT1` and `PA6/DAC2_OUT1` were also
free in the original `.ioc` files, but PA5 is the selected accessible output;
DAC3/DAC4 remain reserved for motor protection comparators. Note that the
STM32G473 guarantees its buffered DAC linear output only from 0.2 V, so the
nominal 0.05 A point must be measured and qualified on the real bench. See
[`docs/source/cablage.rst`](docs/source/cablage.rst) for the complete pin
audit and safety checklist, the
[B-G473E-ZEST1S user manual](https://www.st.com/resource/en/user_manual/um3118-motor-control-discovery-kit-with-stm32g473qe-mcu-stmicroelectronics.pdf),
 this [third-party mirror of a TB-200S external-control reference](https://manuals.plus/ae/1005008626943763),
 and the controller manual supplied with the exact TB-200S revision.

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
`datalogging/motor_profiles.json`. Each motor profile now selects either a
fixed 0.05–0.25 A brake command or `Variable` for a new pseudorandom command
every 2–5 seconds.

The raw CSV uses semicolons and contains nine columns:

```text
stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a;load_setpoint_a
```

`load_setpoint_a` is the instantaneous command reported by the firmware, not
an independently measured brake current.

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

The new raw `load_setpoint_a` column is accepted without changing this model
contract. It is omitted by default. Add `--include-load-setpoint` to append
the unfiltered command after the 55 axes for traceability; do not use that
extra column with the current 55-input embedded model.

Reference spans `[1320, 3360, 6360, 9480]` correspond to 2 Hz. The actual
rate is derived from the median of positive `stm32_time_ms` differences, then
the spans are rescaled. At 10 Hz, they become
`[6600, 16800, 31800, 47400]`.

By default, the output has neither a header nor a timestamp. Main options:

```powershell
python .\pretraitement\preprocess_logs_ewma.py --header
python .\pretraitement\preprocess_logs_ewma.py --include-time
python .\pretraitement\preprocess_logs_ewma.py --include-load-setpoint
python .\pretraitement\preprocess_logs_ewma.py --frequency-hz 10
python .\pretraitement\preprocess_logs_ewma.py --config .\preprocess_ewma.yaml
```

`PMSM_PREPROCESS_INPUT_DIR`, `PMSM_PREPROCESS_OUTPUT_DIR`, and
`PMSM_PREPROCESS_PATTERN` override the input directory, output directory,
and filename pattern respectively.

> **Data quality:** the current implementation converts explanatory variables
> to numbers and replaces their invalid or infinite values with `0.0`. The
> `d6t_temp_c` target is not converted: a `NaN` string or empty cell therefore
> remains in the output. Check and filter these rows before importing into
> NanoEdge AI Studio.

## Acquisition firmware

Import the project at
`firmware_acquisition/tets_motor_dewalt/STM32CubeIDE` into STM32CubeIDE.

The main application modules are:

- `app_serial_control.c`: UART protocol, receive queue, and validation;
- `app_motor_control.c`: MCSDK control, ramps, protections, DAC conversion,
  and fixed/variable TB-200S scheduling;
- `app_datalog.c`: sensor scheduling and nonblocking CSV transmission;
- `d6t_ir.c`: I2C reads from the D6T sensor;
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
```

Application limits are 4500 rpm, 30 A for `Iq`, and 30 A for total current.
Minimum speed is 100 rpm, and acceleration is capped at 50 electrical Hz/s
in both dashboard and firmware.

B2 starts a standalone variable-speed profile separate from UART
configuration. The first target and each subsequent one are drawn directly
and pseudorandomly from the entire 2000–4000 rpm range. The target changes
every 2 to 5 seconds, and consecutive draws cannot be identical. Startup
and every transition use the fast MCSDK ramp of 500 electrical Hz/s, or
15,000 rpm/s with the configured two pole pairs. The PI `Iq` output is capped
at 25 A; the `Id/Iq` command magnitude and application shutdown threshold
on measured magnitude are capped at 28 A. A second press stops the profile.
The TB-200S command is set to 0.05 A before launch, then changes randomly
between 0.05 and 0.25 A every 2–5 seconds. A fixed UART request above 0.05 A
is likewise deferred until the motor reaches RUN; variable mode waits 2–5
seconds before its first new draw.
Pressing B2 always selects the fully variable profile: it overrides any
earlier serial load selection, varies both speed and TB-200S load, and can be
stopped with a second press.
The 50 electrical Hz/s ceiling still applies to motor starts configured by
the acquisition dashboard. The four validation-firmware `PROFILE` commands
use the same 500 electrical Hz/s ramp as B2. A new acquisition UART
configuration disables the standalone profile and takes control immediately.

The setpoint is reapplied when MCSDK actually reaches `RUN`. A restart
requested during shutdown waits for `IDLE`, and overspeed protection never
exceeds the absolute 4500 rpm ceiling.

> **Qualification required:** these are software ceilings, not test bench
> certification. Before running at 4500 rpm or 30 A, verify the motor,
> STDES-LVHP01, supply, wiring, cooling, and protections. Startup polarization
> deliberately remains limited to 14 A. Preventive DC bus protection is
> disabled in the current configuration (`M1_BUS_PROTECTION=false`): rapid
> B2 deceleration can regenerate energy into the bus. Monitor bus voltage
> and validate absorption or braking capacity before the test.

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
after MCSDK reaches `RUN`. The physical B2 button always launches
`VARIABLE_ALL`. `STOP` stops the motor and returns the load to 0.05 A.
Accepted commands answer `ACK,PROFILE,<TOKEN>` or `ACK,STOP`.

The included export is a `1 x 55` Ridge regression for Cortex-M4 hard-float.
Its ID is `6a99400cd097fef61cf265dc`. Export metadata reports a score of
`0.9827`, a main KPI of `0.9944`, 464 estimated bytes of RAM, and 892
estimated bytes of Flash.

To replace the model, replace `libneai.a`, `NanoEdgeAI.h`,
`metadata.json`, and `artifacts/` together in `firmware_validation/AI_Model`.
Keep 55 inputs and the extrapolation API, then run **Clean Project** followed
by **Build Project** in STM32CubeIDE.

## Validation

Checks without hardware:

```powershell
python .\firmware_validation\tests\validate_neai_export.py
python .\firmware_validation\tests\validate_motor_limits.py
python .\datalogging\test_motor_datalog_gui_dashboard.py
python .\pretraitement\test\test_preprocess_logs_ewma.py
python .\validation\test\test_temperature_validation_gui.py
python .\firmware_validation\tests\validate_preprocess_parity.py
```

The first five checks pass in the current state. The full parity test fails
on `daq_log_20260827_080523.csv`: `0.000512959` on
`speed_power_ewma_6600` against a tolerance of `0.0005`. See the validation
section of `firmware_validation/README.md` before changing the threshold.

Check the serial contract with a connected board:

```powershell
python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
```

Temperature validation interface:

```powershell
python .\validation\test\temperature_validation_gui.py --port COM5
python .\validation\test\temperature_validation_gui.py --demo
```

The redesigned interface presents five visual profile cards: `Collecte seule`,
`Profil stable`, `Charge variable`, `Vitesse variable`, and `Tout variable`.
Connecting never starts the motor. In `Collecte seule`, the GUI sends no motor
command, so B2 can launch the standalone all-variable profile. For another
selection, **Démarrer ce profil** sends `PROFILE,<TOKEN>`; **Arrêter le moteur**
is enabled only for a profile launched by the GUI and sends `STOP`. A
collection-only disconnect sends no command; when the GUI owns the active
profile, disconnecting or closing attempts a safety `STOP`.

The header keeps serial, data, and motor states distinct. Five KPI cards show
the D6T temperature, AI prediction, signed instantaneous error, running MAE,
and the reported TB-200S setpoint with a read-only gauge. Three synchronized
plots show temperature, absolute error, and load over the latest 90 seconds.
CSV export includes `load_setpoint_a`; legacy two-field telemetry is still
accepted and shown as an unavailable load value.

## Documentation

Build the HTML documentation:

```powershell
python -m sphinx -b html .\docs\source .\docs\build\html
```

The generated entry point is `docs/build/html/index.html`. The documentation
covers architecture, wiring, protocol, processing, both firmware projects,
AI validation, and the Python API. The technical audit, risks, and action
plan are in `docs/source/etat_projet.rst`.

## Current limitations

- No continuous integration pipeline is provided.
- Firmware builds use the STM32CubeIDE-generated Makefiles; there is no
  versioned toolchain bootstrap command.
- The serial test requires a programmed board and an available COM port.
- All four current Debug/Release targets produce ELF files and the changed C
  sources pass ARM GCC 14.3 syntax checks. Physical test-bench validation
  still needs to be run for this TB-200S revision.
- Float32/pandas parity slightly exceeds tolerance on the latest log.
- Independent validation metrics need their source CSV files for
  reproducibility.
