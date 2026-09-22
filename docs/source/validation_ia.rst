On-Device NanoEdge AI Validation
================================

Dedicated project
-----------------

The `firmware_validation` directory contains a standalone STM32CubeIDE
project. It reuses motor and sensor acquisition, then computes at 10 Hz the
same 55 features produced offline by `preprocess_logs_ewma.py`.

Select the mode in `firmware_validation/Inc/app_config.h`:

.. code-block:: c

   #define APP_NEAI_MODEL_ENABLED  1U

Model-enabled mode
------------------

With `1U`, the firmware initializes `AI_Model/libneai.a` and calls
`neai_extrapolation` with the 55 features. Each USART1 line contains:

.. code-block:: text

   <d6t_temp_c>;<predicted_temp_c>;<load_setpoint_a>

The third field is the instantaneous TB-200S command in amperes. It does not
enter the current 55-axis regression. The PC GUI remains able to read legacy
two-field firmware streams.

The current model is a Ridge regression exported by NanoEdge AI Studio 5.2,
with ID `6a99400cd097fef61cf265dc`. It targets Cortex-M4 hard-float and
expects one sample of 55 axes. `AI_Model/metadata.json` reports:

* NanoEdge score: `0.9827`;
* main KPI: `0.9944`;
* estimated RAM: 464 bytes;
* estimated Flash: 892 bytes.

These values describe the export's evaluation and resource estimate. They do
not replace independent validation on a held-out dataset.

Serial Emulator mode
--------------------

With `0U`, no NanoEdge function is called. Each line contains exactly 55
semicolon-separated features, without the D6T target, header, timestamp, or
text. This matches the explanatory columns of the preprocessed CSV files and
can be read directly by the extrapolation Serial Emulator.

In both modes, streaming starts automatically at boot at 115200 baud, 8N1.
B2 starts a profile whose `Iq` output is limited to 25 A; the `Id/Iq`
command magnitude and the shutdown threshold on measured magnitude are
limited to 28 A. Its target is drawn directly between 2000 and 4000 rpm every
2 to 5 seconds, using a 500 electrical Hz/s ramp at startup and between
changes.

The validation firmware also accepts a small line-oriented motor-profile
protocol while it continues streaming:

.. code-block:: text

   PROFILE,STABLE
   PROFILE,VARIABLE_LOAD
   PROFILE,VARIABLE_SPEED
   PROFILE,VARIABLE_ALL
   STOP

An accepted profile responds with `ACK,PROFILE,<TOKEN>`. `STOP` is idempotent
and responds with `ACK,STOP`; invalid requests return `ERR,<reason>`.

.. list-table:: Validation profiles
   :header-rows: 1
   :widths: 22 28 25 25

   * - GUI choice
     - Token
     - Speed
     - TB-200S command
   * - `Stable`
     - `STABLE`
     - 2500 rpm
     - Fixed 0.10 A
   * - `Variable load`
     - `VARIABLE_LOAD`
     - 2500 rpm
     - Pseudorandom 0.05--0.25 A every 2--5 s
   * - `Variable speed`
     - `VARIABLE_SPEED`
     - Pseudorandom 2000--4000 rpm every 2--5 s
     - Fixed 0.10 A
   * - `All variable`
     - `VARIABLE_ALL`
     - Pseudorandom 2000--4000 rpm every 2--5 s
     - Pseudorandom 0.05--0.25 A every 2--5 s

Every profile forces the brake command to 0.05 A before motor launch. Fixed
load changes to 0.10 A only after MCSDK reports `RUN`; variable load makes
its first new draw after a 2--5 second delay. B2 always starts
`VARIABLE_ALL`, overriding a previously selected serial profile. B2 and all
four serial profiles use the same 500 electrical Hz/s ramp and 25 A/28 A
current limits.

Validation sequence
-------------------

1. Check the model files and contract without hardware.
2. Compare simulated float32 calculations with the pandas reference.
3. Build in Serial Emulator mode and inspect all 55 fields on the board.
4. Build in model mode and inspect both temperatures plus the load command.
5. Record an independent session with the graphical interface.

Commands without hardware:

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py
   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_motor_limits.py
   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_preprocess_parity.py

Commands with a connected board:

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
   .\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator

Verified repository state
-------------------------

`validate_neai_export.py` and `validate_motor_limits.py` pass in the current
state. The validation-interface, dashboard, and preprocessing schema test
suites also pass. All four current Debug/Release targets produce ELF files in
STM32CubeIDE 2.1.1, and all changed C sources pass ARM GCC 14.3 syntax
compilation with warnings enabled. Target-hardware tests remain to be run for
this revision.

The full `validate_preprocess_parity.py` check currently exceeds its
tolerance on `daq_log_20260827_080523.csv`: the scaled relative error reaches
`0.000512959` for `speed_power_ewma_6600` at row 60913, against a limit of
`0.0005`. The seven preceding logs pass. This float32 drift needs assessment
before changing the threshold or algorithm; the full test is therefore not
considered passing in its current state.

Live graphical interface
------------------------

`validation/test/temperature_validation_gui.py` provides a dedicated view for
model-enabled mode. It reads `D6T;prediction;load` lines at 115200 baud,
accepts the legacy two-field form, and displays both temperatures to one
decimal place. Its command rail contains five visual cards: `Collecte seule`,
`Profil stable`, `Charge variable`, `Vitesse variable`, and `Tout variable`.
Each card includes a compact speed/load signature and keeps selection, pending
command, and active-profile states visually distinct.

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo

Opening a connection only starts reception and CSV recording; it never starts
the motor. With `Collecte seule`, **Démarrer ce profil** stays unavailable and
no control command is sent. **Arrêter le moteur** also stays unavailable; use
B2 itself to stop a run started with B2. This mode is suited to observation or
to a run started physically with B2. For one of the other choices, press
**Démarrer ce profil** to send its `PROFILE,<TOKEN>` command. Press **Arrêter
le moteur** to send `STOP`. A profile request is shown as accepted only after
its matching `ACK,PROFILE,<TOKEN>` is received; this acknowledgment means that
the profile is armed, not necessarily that MCSDK has already reached `RUN`.
An `ERR` line is reported in the status area. Disconnecting or closing a
collection-only session sends no command. If the GUI launched the profile, it
attempts a safety `STOP` before disconnecting or closing.

The dashboard header separates serial-link, telemetry, and motor states. Five
KPI cards show D6T temperature, AI prediction, signed instantaneous error,
session MAE, and the TB-200S setpoint on a read-only gauge. Below them, three
synchronized 90-second plots show temperature, absolute error, and load. The
layout automatically reorganizes the KPI cards on narrower windows.

The instantaneous error is the absolute value of `prediction - D6T`. The
displayed cumulative error is the session MAE, or the running mean of absolute
errors. Both are in °C and use the same display thresholds: green below
0.5 °C, blue from 0.5 to below 1.0 °C, orange from 1.0 to 1.5 °C, and red
above 1.5 °C.

The chart shows the latest 90 seconds. Session readings are automatically
written to `validation/validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv` and
include `load_setpoint_a` as the final column. Legacy two-field samples store
`NaN` for that column. Files can be exported elsewhere at full precision.
`requirements.txt` already
lists `pyserial` and `matplotlib`; Tkinter ships with Python on Windows.

On Windows, a transient `ClearCommError` is retried at most 100 times with
150 ms between attempts, giving about 15 seconds excluding serial operation
time. `ACK`/`ERR` control lines are handled separately. Non-ASCII,
nonnumeric, nonfinite frames, and frames with neither two nor three fields
are rejected and counted.

Replacing the model
-------------------

Replace these items in `firmware_validation/AI_Model`:

* `libneai.a`;
* `NanoEdgeAI.h`;
* `metadata.json`;
* the traceability JSON files in `artifacts`.

Empty `artifacts` before copying to remove the old algorithm's parameters.
Keep `feature_order.txt`: it defines the firmware's required order of
55 features.

The new export must target an STM32G4 Cortex-M4 with a hard-float ABI, keep
`NEAI_INPUT_SIGNAL_LENGTH == 1` and `NEAI_INPUT_AXIS_NUMBER == 55`, and expose
`neai_extrapolation_init` and `neai_extrapolation`. After replacement, perform
a full clean build before flashing the board again.

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py

Previous independent validation values must be accompanied by the CSV file,
training/test split, and calculation script. Without this reproducible
artifact in the repository, they are not an automated acceptance criterion.
