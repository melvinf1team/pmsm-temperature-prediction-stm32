# NanoEdge AI Validation Firmware

This standalone STM32CubeIDE project reuses motor and sensor acquisition,
computes the same EWMA preprocessing as
`pretraitement/preprocess_logs_ewma.py` at 10 Hz, then outputs either the
55 features or the NanoEdge AI temperature prediction.

Select the mode at compile time in `Inc/app_config.h`:

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U`: run the embedded model and output `D6T temperature;prediction`;
- `0U`: skip the model and output 55 features for the Serial Emulator.

The stream starts automatically at boot. It contains no header, `DATA`
prefix, or diagnostic text. USART1 uses 115200 baud, 8N1.

## Organization

Validation-specific components are mainly in:

| Path | Purpose |
|---|---|
| `STM32CubeIDE/Application/User/preprocess_ewma.c` | Float32 computation of 55 features |
| `STM32CubeIDE/Application/User/app_ai_model.c` | NanoEdge AI checks and calls |
| `STM32CubeIDE/Application/User/app_datalog.c` | Acquisition, timing, and USART1 output |
| `Inc/app_config.h` | Model/Emulator mode selection |
| `Inc/preprocess_ewma.h` | Preprocessing period and dimensions |
| `AI_Model/feature_order.txt` | Required order of 55 axes |
| `AI_Model/` | Model library, header, metadata, and artifacts |
| `tests/` | Off-target checks and serial contract test |

`Drivers`, `MCSDK_v6.4.2-Full`, `Src`, and parts of `Inc` come from STM32
tools. Review any CubeMX/Workbench regeneration before integrating it.

## Motor limits and B2 profile

Limits shared by the dashboard and both firmware projects are 4500 rpm,
30 A for `Iq`, and 30 A for total current. The current sensor has a calculated
full scale of about 110 A; this ensures numeric representation, not the test
bench's thermal capacity. Startup polarization remains capped at 14 A; its
software threshold and the DC profiler cannot exceed 30 A.

The first press of B2 immediately draws a pseudorandom first target between
2000 and 4000 rpm. A new target, necessarily different from the previous
one, is then drawn directly from this entire range every 2 to 5 seconds.
Startup and transitions use a 500 electrical Hz/s MCSDK ramp, or
15,000 rpm/s with two pole pairs. The PI `Iq` output is capped at 25 A;
the `Id/Iq` command magnitude and application shutdown threshold on measured
magnitude are capped at 28 A. This internal profile does not raise the
50 electrical Hz/s limit for the acquisition firmware's UART configurations.
A second press stops and disables the profile.

A nonfinite (`NaN` or infinite) MCSDK current or speed reading immediately
stops the motor, disables B2, and puts motor control into a fault state.

> These high limits require electrical, thermal, and mechanical qualification
> on the actual test bench before use. Preventive DC bus protection is disabled
> (`M1_BUS_PROTECTION=false`): monitor bus voltage during rapid deceleration
> and validate energy absorption or braking.

## Current embedded model

`AI_Model/metadata.json` and `NanoEdgeAI.h` describe this export:

| Property | Value |
|---|---|
| Algorithm | Ridge regression |
| NanoEdge AI Studio | 5.2.0 |
| Library ID | `6a99400cd097fef61cf265dc` |
| Target | STM32G4, Cortex-M4, hard-float |
| Input | 1 sample with 55 axes |
| NanoEdge score | `0.9827` |
| Main metadata KPI | `0.9944` |
| Estimated RAM | 464 bytes |
| Estimated Flash | 892 bytes |
| Export build date | September 3, 2026 |

These figures come from the NanoEdge export. Alone, they are not an independent
measurement on separate validation data. Previous R² `0.8069` and SMAPE
`1.55 %` values cited in this README had no versioned calculation script, so
they are no longer presented as acceptance criteria.

The model directory contains:

```text
AI_Model/
|-- libneai.a
|-- NanoEdgeAI.h
|-- metadata.json
|-- feature_order.txt
`-- artifacts/
    |-- ridge_model_params.json
    |-- ridge_preprocessing_config.json
    `-- ridge_preprocessing_params.json
```

## Model-enabled mode

With `APP_NEAI_MODEL_ENABLED == 1U`, `app_ai_model.c` checks library identity
and dimensions, initializes extrapolation, copies the 55-`float` vector,
then calls `neai_extrapolation()`.

Each valid UART line contains two temperatures in degrees Celsius:

```text
<d6t_temp_c>;<predicted_temp_c>
```

Example:

```text
31.400000;30.872314
```

No line is emitted until the D6T provides a valid reading, or if NanoEdge
initialization or inference fails. B2 speed changes add no text to the data
stream.

Check the contract with a connected board:

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
```

The corresponding graphical interface is described in
`../validation/README.md`.

## Serial Emulator mode

Set:

```c
#define APP_NEAI_MODEL_ENABLED  0U
```

Then perform a clean build, rebuild, and reflash. The library is not called,
and every line contains exactly 55 finite numbers separated by `;`, with no
D6T target, timestamp, header, or prefix.

```text
27.180000;27.180000;...;31385.884088
```

In NanoEdge AI Studio, open **Validation > Serial Emulator**, select the COM
port and 115200 baud. No `START` command is needed.

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
```

## Contract for the 55 features

The period is fixed at 100 ms. Spans adjusted for 10 Hz are:

```text
6600, 16800, 31800, 47400
```

The eleven signals are ordered as follows:

1. `ds18b20_temp_c`
2. `motor_ud_v`
3. `motor_uq_v`
4. `motor_speed_mech_rpm`
5. `motor_id_a`
6. `motor_iq_a`
7. `u_s = sqrt(ud^2 + uq^2)`
8. `i_s = sqrt(id^2 + iq^2)`
9. `S_el = 1.5 * u_s * i_s`
10. `speed_current = motor_speed_mech_rpm * i_s`
11. `speed_power = motor_speed_mech_rpm * S_el`

For each signal, the vector contains the instantaneous value and its four
EWMAs, or `11 x 5 = 55` values. The recurrence reproduces
`pandas.Series.ewm(span=..., adjust=False)`. Nonfinite feature values are
replaced by zero in both implementations.

The 44 EWMA states are saved after each sample in two alternating snapshots
in SRAM section `.noinit`. A signature, version, sequence number, and CRC32
allow restoration of the latest complete state after a CPU/NRST reset while
the board remains powered. Power loss or an invalid snapshot resets the
state without writing to Flash.

## Replacing the NanoEdge model

Export an extrapolation library from NanoEdge AI Studio, then replace these
items together in `AI_Model`:

1. `libneai.a`;
2. `NanoEdgeAI.h`;
3. `metadata.json`;
4. the contents of `artifacts/`.

Empty `artifacts/` first to avoid mixing parameters from two models. Keep
`feature_order.txt`, which defines the order imposed by the firmware.

The new export must satisfy:

- an STM32G4 Cortex-M4 target compatible with the board;
- hard-float ABI and VFPv4-D16;
- `NEAI_INPUT_SIGNAL_LENGTH == 1`;
- `NEAI_INPUT_AXIS_NUMBER == 55`;
- `neai_extrapolation_init` and `neai_extrapolation` symbols without a
  multi-library suffix.

Assertions in `app_ai_model.c` fail the build if dimensions change. An export
with suffixed symbols requires an explicit change to this module.

## Build

Import `firmware_validation/STM32CubeIDE` as an existing project in
STM32CubeIDE. Both Debug and Release configurations reference:

- the header in `../../AI_Model`;
- the library in `../../AI_Model`;
- `:libneai.a` on the linker line.

After changing the mode or model:

1. run **Project > Clean**;
2. rebuild the desired configuration;
3. confirm that `libneai.a` appears on the linker line in model mode;
4. program `STM32CubeIDE/Debug/firmware_validation.elf` or
   `STM32CubeIDE/Release/firmware_validation.elf`, according to the configuration;
5. check the corresponding UART contract.

## Automated checks

Check the export structure:

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py
```

This check passes with the versioned export: ID, dimensions, ABI, symbols,
feature order, and Ridge artifacts are consistent.

Check limits in the dashboard, both firmware projects, and Workbench/CubeMX
files:

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_motor_limits.py
```

This check covers the global 4500 rpm and 30 A ceilings, 14 A polarization,
and B2's 25 A `Iq`, 28 A total threshold, 2000–4000 rpm range, direct draws,
2 to 5 second delays, and 500 electrical Hz/s ramp. It also checks the
analog current range.

Compare simulated float32 preprocessing with pandas:

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_preprocess_parity.py
```

State rechecked on September 15, 2026: the first seven logs pass, but
`daq_log_20260827_080523.csv` reaches a scaled relative error of `0.000512959`
on `speed_power_ewma_6600` at row 60913, against a `0.0005` limit. The full
test currently fails. Assess this small float32 drift before changing the
tolerance or implementation.

The latest versioned Debug and Release builds produce their ELF files under
STM32CubeIDE 2.1.1. The current revision of both controllers passes ARM GCC
14.3 syntax compilation with the project options; a full relink has not been
run. The serial check and qualification at 4500 rpm/30 A require a board and
a secured test bench. No continuous integration pipeline is provided in the
repository.
