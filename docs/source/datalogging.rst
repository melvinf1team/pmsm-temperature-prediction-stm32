Data Logging Dashboard
======================

Script purpose
--------------

`datalogging/motor_datalog_gui_dashboard.py` provides the test bench's PC
interface. It combines:

* a dark Tkinter interface with cards showing current values;
* six quick-select test profiles, aligned with the validation GUI;
* custom motor profile management in `motor_profiles.json`;
* serial port discovery and opening;
* independent `PROFILE_SET`, `MOTOR_START`, `MOTOR_STOP`, `LOG_START`, and
  `LOG_STOP` commands, plus `SYNC`, `CFG`, and `LOAD`;
* UART stream reception;
* CSV output in `datalogging/logs`;
* a live chart when `matplotlib` is available.

Startup and configuration
-------------------------

From the repository root:

.. code-block:: powershell

    python .\datalogging\motor_datalog_gui_dashboard.py

`dashboard_config.yaml` is loaded automatically. Supply another file with
`--config`. Path options are:

.. list-table:: Dashboard configuration
    :header-rows: 1

    * - Option
      - Environment variable
      - Default
    * - `--log-dir`
      - `PMSM_DATALOG_LOG_DIR`
      - `datalogging/logs`
    * - `--profile-store`
      - `PMSM_DATALOG_PROFILE_STORE`
      - `datalogging/motor_profiles.json`
    * - `--csv-path`
      - `PMSM_DATALOG_CSV_PATH`
      - timestamped file in the log directory

Command-line options take precedence over environment variables and
configuration files. Relative paths are resolved from the repository root.

Test profiles
-------------

The **Test profile** card offers six tiles, selectable with the mouse or the
arrow keys; hovering a tile shows its description. They reproduce the
validation GUI profiles and add `Custom`:

.. list-table:: Quick-select test profiles
    :header-rows: 1

    * - Profile
      - Speed
      - TB-200S command
      - Command
    * - `Collect only` (default)
      - No motor command
      - Unchanged by logging
      - `LOG_START`
    * - `Stable`
      - 4000 rpm
      - Fixed 0.20 A
      - `PROFILE_SET,STABLE`
    * - `Variable load`
      - 4000 rpm
      - Pseudorandom 0.10--0.20 A every 2--5 s
      - `PROFILE_SET,VARIABLE_LOAD`
    * - `Variable speed`
      - Pseudorandom 3000--4000 rpm every 2--5 s
      - Fixed 0.20 A
      - `PROFILE_SET,VARIABLE_SPEED`
    * - `All variable`
      - Pseudorandom 3000--4000 rpm every 2--5 s
      - Pseudorandom 0.10--0.20 A every 2--5 s
      - `PROFILE_SET,VARIABLE_ALL`
    * - `Custom`
      - User setting
      - User setting
      - `MOTOR_CFG`, `LOAD`, `MOTOR_START`

The four presets are executed by the acquisition firmware, with the B2 ramp
and limits (500 electrical Hz/s, `Iq` 28 A, total 30 A). The panel below the
tiles lists their read-only parameters, or becomes the `Custom` editor. The
module constants `PRESET_*` mirror the firmware defines;
`tests/consistency/validate_motor_limits.py` checks them against both firmware
projects and the validation GUI. `Collect only` is the default so that no
motor command is sent until a motor profile is chosen.

**Apply profile** prepares the selected motor settings on the board without
starting either subsystem. **START MOTOR** and **STOP MOTOR** are independent
of **START LOGGING** and **STOP LOGGING**. Motor settings are locked only
while the motor runs; CSV settings are locked while logging runs. A motor
stop or restart preserves the open CSV, including cooling measurements.
The **Session** card shows logging time, rows and data rate, and turns amber
when no DATA arrives for 2.5 periods (at least two seconds).

Motor configuration
-------------------

The `MotorProfile` class describes the settings of the `Custom` profile sent
to the board: speed, speed unit, `Iq` limit, hard-stop threshold,
acceleration, DATA period, DS18B20 period, and TB-200S load mode and
setpoint. Built-in and user-saved profiles are loaded when the dashboard
starts and listed in the **Saved profile** selector of the `Custom` editor. A
profile without load fields uses a fixed 0.05 A command.

The built-in `Custom` profile starts with 600 rpm, a 2 A
`Iq` limit, a 6 A hard stop, 5 electrical Hz/s, a 100 ms `DATA` period,
and a 1000 ms DS18B20 period. Its TB-200S default is fixed at 0.05 A. The
`Fixed` mode accepts 0.05--0.25 A; `Variable` delegates pseudorandom changes
from 0.10 to 0.20 A every 2--5 seconds to the firmware. Editing a value switches the
selector to unsaved `Custom`; **Save as…** stores the settings under a new
name and **Delete** removes the selected saved profile. Profiles are
serialized as JSON. A missing file simply recreates the built-in profile;
invalid content is reported in the interface log.

Input validation
----------------

Before launch, `validate_form` checks the COM port, baud rate, periods, and
CSV path. With `Custom`, it also checks speed, pole pairs, current limits,
acceleration, load mode, and a finite 0.05--0.25 A fixed setpoint. Motor and
load fields are disabled and ignored for the other profiles.
The dashboard notes that the DS18B20 refreshes at most every 750 ms.

The dashboard and firmware parser enforce the same `Custom` limits: 100 to
4500 rpm, at most 28 A for `Iq`, 30 A for the hard stop, and at most 50 electrical
Hz/s for acceleration. Out-of-range values are rejected before startup. See
:doc:`utilisation` for details and precautions.

Startup sequence
----------------

The serial port opens automatically on the first action. `HELLO` is sent once
per connection to read capabilities and motor state without stopping either
subsystem, including a motor already started with B2. Later actions retain
the connection.

For a preset, **Apply profile** sends `PROFILE_SET,<TOKEN>` and waits for
`ACK,PROFILE_SET,<TOKEN>`. For `Custom`, it sends `MOTOR_CFG` followed by `LOAD`, preserving the acquisition periods.
Neither operation starts the motor or the logger. **START MOTOR** applies
the selected settings and then sends `MOTOR_START`. **STOP MOTOR** sends
`MOTOR_STOP`, leaving an active CSV open.

**START LOGGING** sends `LOG_START,<datalog_ms>,<ds18b20_ms>`. It requires no
valid motor configuration and does not stop a running motor. **STOP LOGGING**
sends `LOG_STOP` and closes the CSV while keeping motor control connected.
A subsequent logging start creates a new CSV. A motor stop/restart during
logging keeps a single header and continuous timestamps in the same CSV.

Every command waits for its matching ACK or an ERR. Startup cancellation
and failures stop only the subsystem being started. **Disconnect** and
closing the dashboard stop both subsystems before closing serial. These
commands require the updated acquisition firmware; legacy combined `START`,
`STOP`, `PROFILE` and `ACQ_START` remain available to other tools.

UART reception and CSV
----------------------

`handle_serial_line` routes incoming lines:

* `#CSV_HEADER` opens the CSV file and sets column order;
* `DATA` writes a row, updates the live cards, and feeds the chart;
* `ACK` and `ERR` synchronize the command threads;
* `#PROFILE,START` and `#B2,...` update the motor status;
* other `#` lines remain firmware diagnostic messages.

`DATA` frames are hidden from the UART log unless **Show DATA frames** is
ticked, so commands and acknowledgments stay readable; **Clear** empties the
log.

The dashboard writes only `CSV_OUTPUT_COLUMNS` so raw files retain a stable
format if firmware adds diagnostic columns. It flushes the file buffer at
least every ten rows or every second, and once more when the session closes
cleanly.

The raw schema has nine columns; `load_setpoint_a` is the instantaneous
command reported by the firmware, not a current feedback measurement:

.. code-block:: text

   stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a;load_setpoint_a

If `#CSV_HEADER` does not announce `load_setpoint_a`, the dashboard still
writes the column. It records the requested value for a fixed motor profile,
where that value is known, and writes `NaN` for variable load or
acquisition-only mode because the instantaneous command cannot be
reconstructed.

Execution model
---------------

Tkinter and graphical updates stay on the main thread. One thread reads the
serial port; separate threads handle startup and shutdown. `queue.Queue`
objects carry GUI events and acknowledgments without direct concurrent access
to widgets. Writes to the serial port and its closure share a lock, and
command/ACK transactions are serialized. **STOP MOTOR** cancels a pending
motor start before sending `MOTOR_STOP`, without stopping the logger.

The chart keeps at most 1500 points per series and refreshes every 250 ms.
Missing Matplotlib does not prevent acquisition; only the chart is unavailable.

Paths
-----

`default_csv_path` builds a `daq_log_YYYYMMDD_HHMMSS.csv` name in
`datalogging/logs` from the script's path, rather than the current directory.
Behavior is therefore the same from VS Code, PowerShell, or a shortcut. The
**New** button proposes such a name; after a session that wrote an auto-named
file, a fresh name is proposed automatically, while a user-chosen name is kept.

Troubleshooting
---------------

* No port: check ST-LINK/VCP and the USB cable, and close other clients.
* `ERR` after `CFG`: check the limits, especially the 100 rpm minimum and
  750 ms DS18B20 minimum.
* `ERR,BAD_LOAD`: select `Fixed` with 0.05--0.25 A or select `Variable`, then
  restart the complete sequence.
* No `ACK,PROFILE_SET,...` with a preset: the board runs an acquisition firmware
  without the independent commands; rebuild and flash it before using this dashboard.
* `DATA` without a CSV file: look for `#CSV_HEADER` in the log first.
* D6T readings of `NaN`: check power, pull-ups, and the I2C PEC, or run
  `tests/bench/check_wiring.py` to locate the fault.
* Abrupt program exit: treat the last buffered block as potentially incomplete
  and start a new file.
