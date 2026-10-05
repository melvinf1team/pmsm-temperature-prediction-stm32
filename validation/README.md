# Temperature Validation Interface

The `test/temperature_validation_gui.py` application compares the temperature
measured by the D6T with the temperature estimated by NanoEdge AI on the STM32
board in real time. It is intended for `firmware_validation`, not for the
firmware controlled by the data logging dashboard.

## Prepare the firmware

Enable the model in `firmware_validation/Inc/app_config.h`:

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

Then run **Clean Project**, rebuild, and reflash the board. The USART1 stream
starts automatically at boot at 115200 baud, 8N1. A load-aware frame contains
three finite numbers separated by semicolons:

```text
<d6t_temp_c>;<predicted_temp_c>;<load_setpoint_a>
```

Example:

```text
31.400000;30.872314;0.137000
```

The third field is the commanded TB-200S current, not a measured current and
not one of the 55 model inputs. Two-field frames (`D6T;prediction`) are also
accepted; their load is recorded as `NaN`.

In this firmware, B2 starts a motor profile with `Iq` limited to 25 A; the
`Id/Iq` command magnitude and shutdown threshold on measured magnitude are
limited to 28 A. The first target, then a new target every 2 to 5 seconds,
are drawn directly and pseudorandomly between 2000 and 4000 rpm. Startup
and transition ramps are 500 electrical Hz/s, or 15,000 rpm/s with two
pole pairs. Use them only after electrical, thermal, and mechanical
qualification of the test bench.
Preventive DC bus protection is disabled (`M1_BUS_PROTECTION=false`):
monitor regenerative overvoltage during rapid deceleration and validate
absorption or braking capacity.

The TB-200S is always commanded to 0.05 A before the motor starts. The
validation firmware then provides four complete, predefined motor/load
profiles:

| GUI profile | Serial token | Speed | Brake command |
|---|---|---|---|
| `Stable` | `STABLE` | 2500 rpm | 0.10 A after `RUN` |
| `Variable load` | `VARIABLE_LOAD` | 2500 rpm | Pseudorandom 0.05–0.25 A every 2–5 s |
| `Variable speed` | `VARIABLE_SPEED` | Pseudorandom 2000–4000 rpm every 2–5 s | 0.10 A after `RUN` |
| `All variable` | `VARIABLE_ALL` | Pseudorandom 2000–4000 rpm every 2–5 s | Pseudorandom 0.05–0.25 A every 2–5 s |

The physical B2 button always starts `VARIABLE_ALL`. It is therefore useful
when the GUI is connected in `Collect only` mode: that mode opens the serial
stream and records it, but sends no motor command. The four serial profiles
use B2's 500 electrical Hz/s ramp and its 25 A/28 A current limits.

The validation control protocol is:

```text
PROFILE,STABLE
PROFILE,VARIABLE_LOAD
PROFILE,VARIABLE_SPEED
PROFILE,VARIABLE_ALL
STOP
```

An accepted profile returns `ACK,PROFILE,<TOKEN>` and an idempotent stop
returns `ACK,STOP`. Invalid commands return `ERR,<reason>`. The GUI exposes the
same choices as visual cards labelled `Collecte seule`, `Profil stable`,
`Charge variable`, `Vitesse variable`, and `Tout variable`. Merely connecting
does not send a command: select one of the four controlled profiles and use
**Démarrer ce profil**, or leave `Collecte seule` selected. **Arrêter le
moteur** sends `STOP` only for a profile launched by this GUI. In collection
mode, it stays disabled: stop a B2 run with B2 itself. Disconnecting or closing
sends no command in collection-only mode; if the GUI launched the active
profile, it attempts a safety `STOP` before releasing the port.

The dashboard separates serial, telemetry, and motor states in its header. It
shows five live KPI cards, including a read-only TB-200S load gauge, above
synchronized temperature, absolute-error, and load histories.

Wire PA5/DAC1_OUT2, the 1 kΩ resistor, 4.7 µF capacitor, and common ground as
shown in the root README before enabling the controller; never connect the
TB-200S `+10V` terminal to the STM32.

## Start the application

Select the port manually in the interface:

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
```

Connect automatically to a port:

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
```

Demo mode without a board:

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo
```

Close the dashboard, Motor Pilot, and any serial terminal before connecting:
a COM port can belong to only one application at a time.

On Windows, a transient `ClearCommError` is retried up to 100 times, with a
150 ms pause between attempts. The recovery window is therefore about
15 seconds, excluding the duration of serial operations. Any other error
or exhausted retries close the connection and report the diagnosis.

## Display and calculations

Both temperatures are shown to one decimal place. The chart retains the
latest 90 seconds and up to 1800 points. The interface considers a reading
stale if it has not been refreshed for more than 2 seconds.

For each sample:

```text
signed_error = prediction - D6T
absolute_error = abs(signed_error)
cumulative_MAE = sum(absolute_errors) / sample_count
```

MAE remains in degrees Celsius. Visual thresholds are:

| Absolute error | Label | Color |
|---|---|---|
| `< 0.5 °C` | EXCELLENT | Green |
| `0.5 °C to < 1.0 °C` | BON | Blue |
| `1.0 °C to 1.5 °C` | ATTENTION | Orange |
| `> 1.5 °C` | ÉLEVÉ | Red |

Non-ASCII, nonnumeric, nonfinite frames, and frames with neither two nor three
fields are ignored and counted as invalid. `ACK` and `ERR` control lines are
not counted as temperature samples.

## CSV recording

Each connection automatically creates a file in `validation/` named:

```text
validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv
```

Each row is flushed to disk immediately and contains:

```text
elapsed_s;d6t_temp_c;predicted_temp_c;signed_error_c;absolute_error_c;cumulative_mae_c;load_setpoint_a
```

Numbers are saved at their calculation precision, with six decimal places.
A two-field frame writes `NaN` in `load_setpoint_a`.
The **Export CSV** button creates a copy elsewhere.
**Reset** clears the displayed data and resets MAE to zero
without interrupting automatic recording for the current connection.

## Verification

The unit tests need neither a board nor a graphical window:

```powershell
.\.venv\Scripts\python.exe .\validation\test\test_temperature_validation_gui.py
```

They cover profile-command validation, control acknowledgments, two- and
three-field telemetry parsing, `ClearCommError` recovery, and immediate
flushing of a CSV sample.
They do not validate the visual rendering, the physical COM port, or the
model's statistical performance.

Run the hardware protocol check from the repository root:

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
```
