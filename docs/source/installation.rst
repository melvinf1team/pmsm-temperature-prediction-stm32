Installation
============

Software prerequisites
----------------------

The project uses Python on the PC and STM32CubeIDE for embedded development.
You need:

* Python 3.10 or newer (recommended), with `venv` and `tkinter`;
* STM32CubeIDE and the GNU Arm toolchain for Cortex-M4 hard-float;
* STM32CubeProgrammer and the ST-LINK driver;
* NanoEdge AI Studio to train or replace the model;
* read/write access to the board's COM port.

Python installation
-------------------

From the repository root:

.. code-block:: powershell

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt

If PowerShell policy prevents activation, call
`.\.venv\Scripts\python.exe` directly for subsequent commands.

Path configuration
------------------

The dashboard loads `dashboard_config.yaml` automatically. Override its values
with `--config` or these environment variables:

* `PMSM_DATALOG_LOG_DIR`;
* `PMSM_DATALOG_PROFILE_STORE`;
* `PMSM_DATALOG_CSV_PATH`.

Preprocessing loads `preprocess_ewma.yaml`. It also accepts `--config` and:

* `PMSM_PREPROCESS_INPUT_DIR`;
* `PMSM_PREPROCESS_OUTPUT_DIR`;
* `PMSM_PREPROCESS_PATTERN`.

Relative paths are resolved from the repository root, regardless of the
working directory used to launch the script.

Start the dashboard
-------------------

.. code-block:: powershell

   python .\datalogging\motor_datalog_gui_dashboard.py

The default CSV file is created in `datalogging/logs`. This path works whether
the script is launched from the repository root, the `datalogging` directory,
or VS Code.

Run preprocessing
-----------------

.. code-block:: powershell

   python .\pretraitement\preprocess_logs_ewma.py

Useful options:

.. code-block:: powershell

   python .\pretraitement\preprocess_logs_ewma.py --header
   python .\pretraitement\preprocess_logs_ewma.py --no-header
   python .\pretraitement\preprocess_logs_ewma.py --frequency-hz 10
   python .\pretraitement\preprocess_logs_ewma.py --include-time

Import and build the firmware projects
--------------------------------------

In STM32CubeIDE, use **File > Import > Existing Projects into Workspace** and
select one of these directories:

* `firmware_acquisition/tets_motor_dewalt/STM32CubeIDE`;
* `firmware_validation/STM32CubeIDE`.

Select the `Debug` or `Release` configuration, then run **Clean Project** and
**Build Project**. Program the board with ST-LINK. Both configurations of the
validation project reference `AI_Model/libneai.a`.

Always perform a clean build after changing `APP_NEAI_MODEL_ENABLED` or
replacing a NanoEdge export.

Checks without hardware
-----------------------

From the repository root:

.. code-block:: powershell

   python .\firmware_validation\tests\validate_preprocess_parity.py
   python .\firmware_validation\tests\validate_neai_export.py
   python .\firmware_validation\tests\validate_motor_limits.py
   python .\validation\test\test_temperature_validation_gui.py

The last three commands pass for the documented state. The parity test
currently exceeds its tolerance on the August 27, 2026 log; see
:doc:`validation_ia` for the exact result before using it as an acceptance
criterion.

The following UART checks require a programmed board and must match the
compiled mode:

.. code-block:: powershell

   python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
   python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator

Build the local documentation
-----------------------------

.. code-block:: powershell

   python -m sphinx -b html .\docs\source .\docs\build\html

The generated HTML is at `docs/build/html/index.html`. To treat Sphinx warnings
as errors during a documentation review:

.. code-block:: powershell

   python -m sphinx -W --keep-going -b html .\docs\source .\docs\build\html
