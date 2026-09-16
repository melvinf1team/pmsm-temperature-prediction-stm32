Project Overview
================

Main directories
----------------

`datalogging/`
   Tkinter interface, motor profiles, and raw logs. The entry point is
   `motor_datalog_gui_dashboard.py`.

`pretraitement/`
   Prepares logs for NanoEdge AI Studio with
   `preprocess_logs_ewma.py`. By default, results are written to
   `pretraitement/logs_processed_ewma`.

`firmware_acquisition/tets_motor_dewalt/`
   STM32CubeIDE/MCSDK project used with the dashboard. It receives PC commands,
   controls the motor, samples the sensors, and publishes the raw stream.

`firmware_validation/`
   Standalone STM32CubeIDE project. It reproduces preprocessing at 10 Hz and
   publishes either 55 features or the D6T measurement and model prediction.

`validation/`
   PC interface for temperature comparison, associated unit tests, and CSV
   exports of validation sessions.

`inventories/`
   Tools for generating inventories of logs and datasets.

`docs/`
   Sphinx sources in `docs/source` and HTML output in `docs/build/html`.

`dashboard_config.yaml` and `preprocess_ewma.yaml`
   Default paths for the dashboard and preprocessing.

`requirements.txt`
   Python dependencies for the dashboard, preprocessing, tests, and Sphinx.

Processing flow
---------------

1. The acquisition firmware waits for a command sequence from the dashboard.
2. The dashboard receives `#CSV_HEADER` followed by `DATA` rows and writes a
   semicolon-separated raw CSV file.
3. Preprocessing keeps `d6t_temp_c` as the target, computes five physical
   quantities, and adds four EWMAs for each of eleven explanatory variables.
4. The target plus 55 features are imported into NanoEdge AI Studio.
5. The model export is integrated into the validation firmware.
6. Feature parity and serial output are checked before comparing the prediction
   with the D6T temperature.

Generated files and sources of truth
------------------------------------

The `datalogging/logs` and `pretraitement/logs_processed_ewma` directories,
along with CSV files under `validation`, contain generated data. The sources
of truth for reproducing the pipeline are:

* the Python scripts and two YAML files;
* the application modules of both firmware projects;
* `firmware_validation/AI_Model/metadata.json` for the identity and properties
  of the NanoEdge AI export;
* `firmware_validation/AI_Model/feature_order.txt` for the required order of
  the 55 features.

MCSDK, CMSIS, and HAL libraries are generated or third-party dependencies.
Project-specific logic is mainly in the `STM32CubeIDE/Application/User`
directories and `Inc` headers.

Scope of verification
---------------------

Checks that require no hardware cover preprocessing parity, NanoEdge export
consistency, and validation interface calculations. The UART contract test
requires a connected board, and firmware builds require STM32CubeIDE. No
continuous integration pipeline is versioned.
