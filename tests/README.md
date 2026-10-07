# Tests, bench tools, and calibration

Every Python check of the project lives here, sorted by what it needs. Run the
commands from the repository root with the project environment
(`.\.venv\Scripts\python.exe`, or `python` once the environment is activated);
the scripts also work from any other directory.

```text
tests/
├── run_all.py        One command for every check that needs no board
├── bench/            Tools that talk to the board
│   ├── check_wiring.py           Wiring diagnosis (firmware_acquisition)
│   ├── d6t_calibration.py        Live D6T map and pixel choice (firmware_acquisition)
│   └── check_nanoedge_serial.py  UART contract of firmware_validation
├── consistency/      Cross-checks between Python, firmwares, and generated files
│   ├── validate_motor_limits.py       Motor, B2, and TB-200S limits everywhere
│   ├── validate_neai_export.py        NanoEdge AI export structure
│   └── validate_preprocess_parity.py  Embedded EWMA features against pandas
└── unit/             Unit tests of the Python tools (no board, no window)
    ├── test_check_wiring.py
    ├── test_d6t_calibration.py
    ├── test_motor_datalog_gui_dashboard.py
    ├── test_preprocess_logs_ewma.py
    └── test_temperature_validation_gui.py
```

## Quick reference

| I want to... | Command |
|---|---|
| Check everything that needs no board | `python .\tests\run_all.py` |
| Check the bench wiring | `python .\tests\bench\check_wiring.py --port COM5` |
| Also check the motor phases and the brake | `python .\tests\bench\check_wiring.py --port COM5 --motor` |
| Choose the D6T pixel logged as `d6t_temp_c` | `python .\tests\bench\d6t_calibration.py --port COM5` |
| Try the D6T tool without a board | `python .\tests\bench\d6t_calibration.py --demo` |
| Check the validation firmware stream | `python .\tests\bench\check_nanoedge_serial.py --port COM5 --mode model` |
| Run a single check | `python .\tests\unit\test_check_wiring.py` (any script runs alone) |

## Checks without a board

`run_all.py` runs the five unit-test files and the three consistency checks,
each in its own process, and prints one `PASS`/`FAIL` line per script with
its duration. The output of a failing script is printed below its line;
`--verbose` prints it for every script. The exit code is `0` when everything
passes.

```text
  unit/test_check_wiring.py                   PASS    1.6 s
  ...
  consistency/validate_preprocess_parity.py   PASS    4.0 s

8/8 passed in 10 s.
```

`validate_preprocess_parity.py` replays all logs of `datalogging/logs` through
the embedded preprocessing. `test_d6t_calibration.py` also fails if the two
firmwares log different D6T pixels.

## D6T pixel calibration

The Omron D6T-44L-06 measures a 4x4 matrix of 16 pixels. Both firmwares log
only one of them as `d6t_temp_c`, the NanoEdge AI target. `d6t_calibration.py`
shows the 16 pixels live so that you can pick the one that sees the motor, then
writes it into both firmware projects.

### Before you start

1. Flash the current `firmware_acquisition`: it provides the `D6T_FRAME`
   command used by the tool. `firmware_validation` cannot be used for the
   calibration, because its UART is reserved for the NanoEdge data stream.
2. Close the dashboard, `check_wiring.py`, and any serial terminal: only one
   program can open the COM port.
3. Make the motor warmer than its surroundings so that it stands out, for
   example by running it with B2 for a few minutes. The tool never sends a
   motor command, so the motor can keep running while it is connected.

### Procedure

1. Start the tool:

   ```powershell
   python .\tests\bench\d6t_calibration.py --port COM5
   ```

   Without `--port`, select the ST-LINK port in the header and click
   **Connect**. `--demo` shows simulated frames, without a board.
2. Match the map to the sensor's field of view with **Rotate 90°** and
   **Mirror**: move a warm hand in front of the sensor and check that the warm
   cells follow it. These buttons change only the display; the pixel numbers
   stay those of the firmware.
3. Click the pixel that covers the motor, or press **Select hottest**. The
   *Selected pixel* card shows its live value, its mean and range over the last
   5 s, its difference from the sensor's internal reference (PTAT), and its
   rank. Prefer a pixel fully inside the motor area with a small range: a pixel
   on the edge of the motor mixes the motor and the background.
4. Click **Write pixel N to both firmwares** and confirm. The tool updates
   `D6TIR_SELECTED_PIXEL_INDEX` and its comment in both `d6t_ir.c` files,
   keeping their line endings.
5. Rebuild and flash `firmware_acquisition` and `firmware_validation` in
   STM32CubeIDE. Reconnect the tool: the *Firmware* card shows the pixel that
   the flashed `firmware_acquisition` logs.

> Changing the pixel changes `d6t_temp_c`. Logs recorded with another pixel,
> and a NanoEdge AI model trained on them, no longer describe the same target:
> record new logs and retrain the model after a change.

### Reading the interface

- **Thermal map**: dark violet is the coldest pixel and pale yellow the
  hottest. The colour scale follows the minimum and maximum of the last 5 s;
  **Lock scale** freezes it. The cyan outline marks the selection, the
  `HOTTEST` tag the pixel with the highest 5 s mean, and the `FIRMWARE` tag the
  pixel currently written in `firmware_acquisition`.
- **Header**: connection status, frame rate (about 4 frames/s), and PTAT.
- **Firmware card**: pixel found in each firmware source, pixel reported by the
  connected board, and the write button. The button is disabled when both
  sources already use the selected pixel.
- **Keyboard**: arrow keys move the selection, `H` selects the hottest pixel,
  `R` rotates the view, and `M` mirrors it.

| Message | Meaning |
|---|---|
| `No answer to D6T_FRAME` | Old `firmware_acquisition` without the command, or wrong COM port |
| `firmware_validation is flashed` | Flash `firmware_acquisition` to calibrate |
| `Sensor silent` | The board answers but the D6T does not: run `check_wiring.py` |
| `Cannot open COMx` | The port is used by another program |

### Pixel numbering

The index is the position of the pixel in the D6T frame, from 0 to 15, row by
row: `index = 4 x row + column`, with rows and columns counted from 0. The
firmware comment shows the same position counted from 1, for example
`10 = ligne 3, colonne 3`.

### `D6T_FRAME` command

```text
D6T_FRAME
D6T_FRAME,ok=1,selected=10,ptat=253,px=241:243:...:250
ACK,D6T_FRAME
```

Values are tenths of a degree Celsius. `selected` is the pixel logged by the
flashed firmware, `ptat` the sensor's internal reference temperature, and `px`
the 16 pixels in frame order. `ok=0` means that the sensor did not answer its
last read. The firmware returns the last frame read by its periodic D6T task,
so the command adds no I2C traffic.

### Where the D6T settings are in the firmwares

Both projects use the same driver. Each setting is a `#define` at the top of
`d6t_ir.c`:

- `firmware_acquisition/tets_motor_dewalt/STM32CubeIDE/Application/User/d6t_ir.c`
- `firmware_validation/STM32CubeIDE/Application/User/d6t_ir.c`

| Setting | Macro or location | Value |
|---|---|---|
| Logged pixel | `D6TIR_SELECTED_PIXEL_INDEX` | `10U` (0 to 15) |
| I2C pins | `D6TIR_SCL_GPIO_Port` / `D6TIR_SCL_Pin`, `D6TIR_SDA_GPIO_Port` / `D6TIR_SDA_Pin` | `PB6` (CN10-27), `PB9` (CN10-24) |
| I2C address and read command | `D6TIR_I2C_ADDR_7BIT`, `D6TIR_READ_COMMAND` | `0x0A`, `0x4C` |
| Frame size | `D6TIR_FRAME_SIZE` | 35 bytes: PTAT, 16 pixels, PEC |
| Read period | `D6TIR_PERIOD_PRESENT_MS`, `D6TIR_PERIOD_MISSING_MS` | 250 ms, 2000 ms while the sensor is missing |
| I2C bit timing | `D6TIR_HALF_PERIOD_US`, `D6TIR_SCL_WAIT_US` | 5 us, 100 us |
| Accepted range | `D6TIR_ReadTemperature()` | -40.0 to 200.0 °C |
| Pixel count | `D6TIR_PIXEL_COUNT` in `firmware_acquisition/tets_motor_dewalt/Inc/d6t_ir.h` | 16 |

Change a value by hand in both files in the same way, then rebuild and flash
both projects. The calibration tool edits only `D6TIR_SELECTED_PIXEL_INDEX`.

## Wiring check

```powershell
python .\tests\bench\check_wiring.py --port COM5
python .\tests\bench\check_wiring.py --port COM5 --motor
```

The first command keeps the motor stopped, sends `DIAG`, records 3 seconds of
logging, and reports `OK`, `WARN`, `FAIL`, or `SKIP` with the probable causes
for the D6T, the DS18B20, the TB-200S command, and the power stage. `--motor`
asks for confirmation, then runs the motor at 1500 rpm to check the U/V/W
phases and the brake response. Useful options: `--speed`, `--iq-limit`,
`--hard-limit`, `--accel`, `--yes` (no confirmation), and
`--no-direction-prompt`. Exit code: `0` without failure, `1` with a failure,
`2` when the port cannot be opened. See `docs/source/utilisation.rst` for the
detected cases.

## NanoEdge AI serial contract

With `firmware_validation` flashed in the matching mode:

```powershell
python .\tests\bench\check_nanoedge_serial.py --port COM5 --mode model
python .\tests\bench\check_nanoedge_serial.py --port COM5 --mode emulator
```
