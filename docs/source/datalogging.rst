Data Logging Dashboard
======================

Script purpose
--------------

`datalogging/motor_datalog_gui_dashboard.py` provides the test bench's PC
interface. It combines:

* a dark Tkinter interface with cards showing current values;
* motor profile management in `motor_profiles.json`;
* serial port discovery and opening;
* commands `SYNC`, `CFG`, `START`, `ACQ_START`, and `STOP`;
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

Motor configuration
-------------------

The `MotorProfile` class describes the settings sent to the board: speed,
speed unit, `Iq` limit, hard-stop threshold, acceleration, DATA period, and
DS18B20 period. Built-in and user-saved profiles are loaded when the dashboard
starts.

The built-in `Personnalisé` ("Custom") profile starts with 600 rpm, a 2 A
`Iq` limit, an 8 A hard stop, 5 electrical Hz/s, a 100 ms `DATA` period,
and a 1000 ms DS18B20 period. Profiles added in the interface are serialized
as JSON. A missing file simply recreates the built-in profile; invalid
content is reported in the interface log.

Input validation
----------------

Before launch, `validate_form` checks the COM port, baud rate, periods, and
CSV path. In motor mode, it also checks speed, pole pairs, current limits, and
acceleration. Motor fields are disabled and ignored in acquisition-only mode.
The dashboard warns that the DS18B20 cannot provide a reliable new reading
in less than 750 ms.

The dashboard and firmware parser enforce the same limits: 100 to 4500 rpm,
at most 30 A for `Iq` and the hard stop, and at most 50 electrical Hz/s for
acceleration. Out-of-range values are rejected before startup. See
:doc:`utilisation` for details and precautions.

Startup sequence
----------------

`start_run` validates the settings, prepares the CSV file, opens the serial
port, and starts the reader thread. `launch_sequence_thread` then sends:

.. code-block:: text

   SYNC
   CFG,<rpm>,<iq_limit>,<hard_limit>,<accel>,<datalog_ms>,<ds18b20_ms>
   START

Each command waits for an `ACK` or `ERR`. On failure, the dashboard reports
the error and closes resources cleanly.

In `Collecte seule (moteur arrêté)` ("Acquisition only, motor stopped") mode,
the sequence is:

.. code-block:: text

   SYNC
   ACQ_START,<datalog_ms>,<ds18b20_ms>

This command does not depend on a motor `CFG` and forces the motor to stop
before arming the logger.

UART reception and CSV
----------------------

`handle_serial_line` routes incoming lines:

* `#CSV_HEADER` opens the CSV file and sets column order;
* `DATA` writes a row, updates the live cards, and feeds the chart;
* `ACK` and `ERR` synchronize the command threads;
* other `#` lines remain firmware diagnostic messages.

The dashboard writes only `CSV_OUTPUT_COLUMNS` so raw files retain a stable
format if firmware adds diagnostic columns. It flushes the file buffer at
least every ten rows or every second, and once more when the session closes
cleanly.

Execution model
---------------

Tkinter and graphical updates stay on the main thread. One thread reads the
serial port; separate threads handle startup and shutdown. `queue.Queue`
objects carry GUI events and acknowledgments without direct concurrent access
to widgets.

The chart keeps at most 1500 points per series and refreshes every 250 ms.
Missing Matplotlib does not prevent acquisition; only the chart is unavailable.

Paths
-----

`default_csv_path` builds a `daq_log_YYYYMMDD_HHMMSS.csv` name in
`datalogging/logs` from the script's path, rather than the current directory.
Behavior is therefore the same from VS Code, PowerShell, or a shortcut.

Troubleshooting
---------------

* No port: check ST-LINK/VCP and the USB cable, and close other clients.
* `ERR` after `CFG`: check the limits, especially the 100 rpm minimum and
  750 ms DS18B20 minimum.
* `DATA` without a CSV file: look for `#CSV_HEADER` in the log first.
* D6T readings of `NaN`: check power, pull-ups, and the I2C PEC.
* Abrupt program exit: treat the last buffered block as potentially incomplete
  and start a new file.
