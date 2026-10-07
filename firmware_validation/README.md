# NanoEdge AI Validation Firmware

This standalone STM32CubeIDE project reuses motor and sensor acquisition,
computes the same EWMA preprocessing as
`pretraitement/preprocess_logs_ewma.py` at 10 Hz, then outputs either the
55 features or the NanoEdge AI temperature prediction.

Select the mode at compile time in `Inc/app_config.h`:

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U`: run the embedded model and output
  `D6T temperature;prediction;TB-200S load setpoint`;
- `0U`: skip the model and output 55 features for the Serial Emulator.

The stream starts automatically at boot. It contains no header, `DATA`
prefix, or diagnostic text. USART1 uses 115200 baud, 8N1.

## Organization

Validation-specific components are mainly in:

| Path | Purpose |
|---|---|
| `STM32CubeIDE/Application/User/preprocess_ewma.c` | Computation of the 55 features |
| `STM32CubeIDE/Application/User/app_ai_model.c` | NanoEdge AI checks and calls |
| `STM32CubeIDE/Application/User/app_datalog.c` | Acquisition, timing, and USART1 output |
| `STM32CubeIDE/Application/User/app_motor_control.c` | Motor state machine and TB-200S DAC/load scheduler |
| `Inc/app_config.h` | Model/Emulator mode selection |
| `Inc/preprocess_ewma.h` | Preprocessing period and dimensions |
| `AI_Model/feature_order.txt` | Required order of 55 axes |
| `AI_Model/` | Model library, header, metadata, and artifacts |

Off-target checks are in `../tests/consistency`; the serial contract test and
the D6T pixel calibration tool are in `../tests/bench` (see `../tests/README.md`).

`Drivers`, `MCSDK_v6.4.2-Full`, `Src`, and parts of `Inc` come from STM32
tools. Review any CubeMX/Workbench regeneration before integrating it.

## Motor limits and B2 profile

Limits shared by the dashboard and both firmware projects are 4500 rpm,
30 A for `Iq`, and 30 A for total current. The current sensor has a calculated
full scale of about 110 A; this ensures numeric representation, not the test
bench's thermal capacity. Startup polarization is capped at 14 A; its
software threshold and the DC profiler cannot exceed 30 A.

The first press of B2 immediately draws a pseudorandom first target between
2000 and 4000 rpm. A new target, necessarily different from the previous
one, is then drawn directly from this entire range every 2 to 5 seconds.
Startup and transitions use a 500 electrical Hz/s MCSDK ramp, or
15,000 rpm/s with two pole pairs. The PI `Iq` output is capped at 25 A;
the `Id/Iq` command magnitude and application shutdown threshold on measured
magnitude are capped at 28 A. A second press stops and disables the profile.

B2 always selects the fully variable profile. It forces the TB-200S command
to 0.05 A before starting the motor, then varies both speed and brake load;
it overrides an earlier serial load or profile selection. The same profile, and
three controlled alternatives, are available over USART1:

| Command | Speed | Brake command |
|---|---|---|
| `PROFILE,STABLE` | 2500 rpm | Fixed 0.10 A |
| `PROFILE,VARIABLE_LOAD` | 2500 rpm | Pseudorandom 0.05--0.25 A every 2--5 s |
| `PROFILE,VARIABLE_SPEED` | Pseudorandom 2000--4000 rpm every 2--5 s | Fixed 0.10 A |
| `PROFILE,VARIABLE_ALL` | Pseudorandom 2000--4000 rpm every 2--5 s | Pseudorandom 0.05--0.25 A every 2--5 s |

All four commands first arm the DAC at 0.05 A and then start the motor. For
the fixed-load profiles, 0.10 A is applied only after MCSDK reports `RUN`.
They use the same 500 electrical Hz/s ramp and 25 A/28 A profile limits as B2.
An accepted request returns `ACK,PROFILE,<TOKEN>`. `STOP` is idempotent,
stops the motor, restores 0.05 A, and returns `ACK,STOP`. A missing or unknown
profile token returns `ERR,BAD_PROFILE`; a DAC initialization failure returns
`ERR,LOAD_DAC_FAILED`; an internal profile rejection returns
`ERR,PROFILE_REJECTED`; other unknown commands return `ERR,UNKNOWN_COMMAND`.
If a different profile is already active, the firmware requests its stop,
waits asynchronously for MCSDK to return to `IDLE`, and then starts the
requested profile. The acknowledgment confirms acceptance, not that `RUN` has
been reached.

The firmware also accepts `LOAD,<amps>` (0.05--0.25 A) and `LOAD,VARIABLE`,
answered by `ACK,LOAD`. The validation GUI does not use them; a
`PROFILE,<TOKEN>` command or B2 replaces the load mode they set.

Both firmware projects drive `PA5 / DAC1_OUT2` on Morpho `CN7-32`. Connect it
through 1 kΩ to TB-200S `ADJ`, connect board and controller grounds, and fit
4.7 µF from ADJ to GND (`+` on ADJ for a polarized capacitor). Use the
controller's 0--10 V external-input mode, never connect its `+10V` terminal
to the STM32, and see `docs/source/cablage.rst` for the DAC pin table and
calibration warning. Only 0.1667--0.8333 V is requested; no 0--10 V amplifier
is used.
Because the buffered STM32G473 DAC is guaranteed only from 0.2 V, measure and
qualify the nominal 0.1667 V / 0.05 A launch point on the actual hardware.

A nonfinite (`NaN` or infinite) MCSDK current or speed reading immediately
stops the motor, disables B2, and puts motor control into a fault state.

> These high limits require electrical, thermal, and mechanical qualification
> on the actual test bench before use. Preventive DC bus protection is disabled
> (`M1_BUS_PROTECTION=false`): monitor bus voltage during rapid deceleration
> and validate energy absorption or braking.

## Embedded model

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

These figures come from the NanoEdge export; they are not an independent
measurement on separate validation data.

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

Each valid UART line contains the two temperatures in degrees Celsius and the
instantaneous commanded TB-200S current in amperes:

```text
<d6t_temp_c>;<predicted_temp_c>;<load_setpoint_a>
```

Example:

```text
31.400000;30.872314;0.125000
```

No line is emitted until the D6T provides a valid reading, or if NanoEdge
initialization or inference fails. B2 speed changes add no text to the data
stream.

The companion `validation/test/temperature_validation_gui.py` separates the
serial connection from motor control. Connecting never sends a motor command.
Its visual `Collecte seule` card records the stream without controlling the
motor, allowing B2 to start `VARIABLE_ALL` physically. The four controlled
cards map to the four `PROFILE,<TOKEN>` commands and are started explicitly
with **Démarrer ce profil**; **Arrêter le moteur** sends `STOP` only after the
GUI has launched a profile. A pure collection session neither stops a B2 run
nor sends a command when closing. When it owns the active profile, the GUI
attempts a safety `STOP` before disconnecting or closing.

The dashboard presents separate serial, data, and motor indicators, five KPI
cards, a read-only TB-200S load gauge, and synchronized temperature, error, and
load plots. It records `load_setpoint_a` in its validation CSV.

Check the contract with a connected board:

```powershell
.\.venv\Scripts\python.exe .\tests\bench\check_nanoedge_serial.py --port COM5 --mode model
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
.\.venv\Scripts\python.exe .\tests\bench\check_nanoedge_serial.py --port COM5 --mode emulator
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
`pandas.Series.ewm(span=..., adjust=False)`. Signals are computed in float32;
the EWMA states are kept in double precision, because with spans up to 47400
the float32 increment `alpha * (x - mean)` falls below the resolution of large
signals such as `speed_power`. Outputs are converted to float32. Nonfinite
feature values are replaced by zero in both implementations.

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

A replacement export must satisfy:

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
.\.venv\Scripts\python.exe .\tests\consistency\validate_neai_export.py
```

It checks the library ID, dimensions, ABI, symbols, feature order, and Ridge
artifacts.

Check limits in the dashboard, both firmware projects, and Workbench/CubeMX
files:

```powershell
.\.venv\Scripts\python.exe .\tests\consistency\validate_motor_limits.py
```

This check covers the global 4500 rpm and 30 A ceilings, 14 A polarization,
B2's 25 A `Iq`, 28 A total threshold, 2000–4000 rpm range, direct draws,
2 to 5 second delays, and 500 electrical Hz/s ramp. It also checks the
0.05--0.25 A TB-200S range, four validation profiles, 2--5 second schedules,
PA5/DAC1_OUT2 setup, profile command handlers, raw telemetry field, and analog
current range.

Compare the simulated embedded preprocessing with pandas:

```powershell
.\.venv\Scripts\python.exe .\tests\consistency\validate_preprocess_parity.py
```

The check passes on all eight logs of `datalogging/logs` with a maximum scaled
relative error of about `3.3e-7`, against a `1e-6` limit.

Debug and Release configurations build with STM32CubeIDE 2.1.1. The serial
check, TB-200S voltage/current calibration, and qualification at 4500 rpm/30 A
require a board on a secured test bench.
