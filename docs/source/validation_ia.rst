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

   <d6t_temp_c>;<predicted_temp_c>

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

Validation sequence
-------------------

1. Check the model files and contract without hardware.
2. Compare simulated float32 calculations with the pandas reference.
3. Build in Serial Emulator mode and inspect all 55 fields on the board.
4. Build in model mode and inspect both temperatures.
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
state. The three validation interface unit tests also pass. The latest
versioned Debug and Release builds produce ELF files in STM32CubeIDE 2.1.1.
The current revision of both motor controllers passes an ARM GCC 14.3 syntax
compilation with the project options; a full relink has not been run.

The full `validate_preprocess_parity.py` check currently exceeds its
tolerance on `daq_log_20260827_080523.csv`: the scaled relative error reaches
`0.000512959` for `speed_power_ewma_6600` at row 60913, against a limit of
`0.0005`. The seven preceding logs pass. This float32 drift needs assessment
before changing the threshold or algorithm; the full test is therefore not
considered passing in its current state.

Live graphical interface
------------------------

`validation/test/temperature_validation_gui.py` provides a dedicated view for
model-enabled mode. It reads `D6T;prediction` lines at 115200 baud and
displays both temperatures to one decimal place.

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo

The instantaneous error is the absolute value of `prediction - D6T`. The
displayed cumulative error is the session MAE, or the running mean of absolute
errors. Both are in °C and use the same display thresholds: green below
0.5 °C, blue from 0.5 to below 1.0 °C, orange from 1.0 to 1.5 °C, and red
above 1.5 °C.

The chart shows the latest 90 seconds. Session readings are automatically
written to `validation/validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv` and
can be exported elsewhere at full precision. `requirements.txt` already
lists `pyserial` and `matplotlib`; Tkinter ships with Python on Windows.

On Windows, a transient `ClearCommError` is retried at most 100 times with
150 ms between attempts, giving about 15 seconds excluding serial operation
time. Non-ASCII, nonnumeric, nonfinite frames, and frames without exactly two
fields are rejected and counted.

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
