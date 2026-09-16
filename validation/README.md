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
starts automatically at boot at 115200 baud, 8N1. A valid frame contains
exactly two finite numbers separated by a semicolon:

```text
<d6t_temp_c>;<predicted_temp_c>
```

Example:

```text
31.400000;30.872314
```

In this firmware, B2 starts a motor profile with `Iq` limited to 25 A; the
`Id/Iq` command magnitude and shutdown threshold on measured magnitude are
limited to 28 A. The first target, then a new target every 2 to 5 seconds,
are drawn directly and pseudorandomly between 2000 and 4000 rpm. Startup
and transition ramps are 500 electrical Hz/s, or 15,000 rpm/s with two
pole pairs. These changes do not alter the serial protocol above. Use them
only after electrical, thermal, and mechanical qualification of the test bench.
Preventive DC bus protection is disabled (`M1_BUS_PROTECTION=false`):
monitor regenerative overvoltage during rapid deceleration and validate
absorption or braking capacity.

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

| Absolute error | Class | Color |
|---|---|---|
| `< 0.5 °C` | Excellent | Green |
| `0.5 °C to < 1.0 °C` | Good | Blue |
| `1.0 °C to 1.5 °C` | Monitor | Orange |
| `> 1.5 °C` | High error | Red |

Non-ASCII, nonnumeric, nonfinite frames, and frames without exactly two
fields are ignored and counted as invalid.

## CSV recording

Each connection automatically creates a file in `validation/` named:

```text
validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv
```

Each row is flushed to disk immediately and contains:

```text
elapsed_s;d6t_temp_c;predicted_temp_c;signed_error_c;absolute_error_c;cumulative_mae_c
```

Numbers are saved at their calculation precision, with six decimal places.
The **Export CSV** button creates a copy elsewhere.
**Reset** clears the displayed data and resets MAE to zero
without interrupting automatic recording for the current connection.

## Verification

The unit tests need neither a board nor a graphical window:

```powershell
.\.venv\Scripts\python.exe .\validation\test\test_temperature_validation_gui.py
```

They currently check three behaviors: detection of `ClearCommError`,
continued reading after that error, and immediate flushing of a CSV sample.
They do not validate the visual rendering, the physical COM port, or the
model's statistical performance.

Run the hardware protocol check from the repository root:

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
```
