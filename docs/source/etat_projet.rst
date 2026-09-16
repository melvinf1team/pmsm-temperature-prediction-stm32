Project Assessment and Status
=============================

This page summarizes the repository audit updated on September 15, 2026.
It separates locally verified findings from checks requiring the physical
test bench or STM32CubeIDE.

Scope reviewed
--------------

The assessment covers:

* the Python applications for data logging, preprocessing, and validation;
* YAML files, motor profiles, and CSV contracts;
* project-specific application modules in both firmware projects;
* configuration headers, the NanoEdge export, and its metadata;
* Python tests, build procedures, and all project documentation.

Generated or third-party CMSIS, HAL, and MCSDK libraries were not reviewed
line by line. Their integration, call points, and governing project constants
were examined.

Summary
-------

The workflow is complete and understandable: the board acquires measurements,
the dashboard logs them, the Python script produces 55 features, and a second
firmware reproduces this calculation before inference. Essential contracts
are embodied in `CSV_OUTPUT_COLUMNS`, `feature_order.txt`, and the NanoEdge
header dimensions.

Limits in the dashboard, both firmware projects, and Workbench files are now
aligned at 4500 rpm and 30 A. The main risk is qualifying these limits on
the actual test bench, followed by numeric drift just above the parity
threshold and the lack of a strict policy for invalid D6T targets.

Verification status
-------------------

.. csv-table:: Results as of September 15, 2026
   :header: "Check", "Status", "Result"
   :widths: 25, 20, 55

   "Strict Sphinx build", "Passed", "No warnings with -W --keep-going"
   "NanoEdge export consistency", "Passed", "Valid ID, ABI, symbols, dimensions, and Ridge artifacts"
   "Temperature interface tests", "Passed", "3 tests run"
   "Motor limit consistency", "Passed", "Dashboard, firmware, IOC, WBDEF, and Workbench checked"
   "Python/float32 parity", "Failed", "0.000512959 against a 0.0005 limit on the latest log"
   "Motor controllers", "ARM syntax passed", "Both app_motor_control.c files pass ARM GCC 14.3 with -Ofast, -Wall, -Wextra, and -Wpedantic; no full relink"
   "USART1 contract on target", "Not run", "Requires a programmed board and COM port"

Strengths
---------

Separation of responsibilities
   Interactive control and standalone validation use separate projects.
   Sensor, motor, data logging, and model modules have clear roles.

Robust acquisition
   The firmware uses interrupt-driven reception and nonblocking transmission.
   The dashboard separates Tkinter access from the serial thread and flushes
   the CSV regularly.

Explicit contracts
   The eight raw columns, 55 input axes, and two validation output formats
   are defined and can be checked.

Defense in depth
   Limits are checked on the PC and in firmware. Motor control monitors
   MCSDK faults, total current, and overspeed.

Model traceability
   The export contains its header, metadata, Ridge parameters, and a test
   checking the expected identity, dimensions, ABI, and symbols.

Priority risks
--------------

1. Qualification of the new motor limits
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**High priority.** Software ceilings are now 4500 rpm, 30 A for `Iq`, and
30 A for total current. The measurement chain represents about 110 A at
full scale and both Debug builds succeed, but these facts do not establish
the test bench's electrical, thermal, or mechanical capacity.

The B2 profile caps the PI `Iq` output at 25 A and both the `Id/Iq`
command magnitude and measured-magnitude shutdown threshold at 28 A. Its
first target and subsequent targets every 2 to 5 seconds are drawn directly
from the full 2000–4000 rpm range. The 500 electrical Hz/s ramp is
15,000 rpm/s with two pole pairs. Before use, check the motor, power board,
supply, wiring, cooling, mounting, and emergency stop. Begin at reduced
current and record temperatures and faults. Polarization remains limited to
14 A and global ceilings outside B2 are unchanged.

Preventive DC bus protection is disabled in the current Workbench configuration
(`M1_BUS_PROTECTION=false`). Downward transitions at this ramp rate can feed
energy back and raise bus voltage. Monitor that voltage and validate supply
absorption or braking before running the complete profile.

2. Numeric parity above the threshold
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**High priority.** `validate_preprocess_parity.py` sets tolerance to
`5e-4`. The file `daq_log_20260827_080523.csv` reaches `0.000512959` on
`speed_power_ewma_6600` at row 60913. The test therefore stops before its
success message.

Its closeness to the threshold is consistent with accumulated float32
differences, but that explanation remains a hypothesis. Locate the first
significant divergence, measure its effect on predictions, and justify
either a recurrence correction or a new tolerance.

3. Invalid target policy
~~~~~~~~~~~~~~~~~~~~~~~~

**High priority.** Preprocessing converts explanatory variables to numbers
but retains `d6t_temp_c` as read. With `keep_default_na=False`, a `NaN`
string or empty cell remains in the output. The file can therefore have the
right dimensions while containing an unusable target.

Define an explicit policy before training: reject the file, remove affected
rows, or impute the target with a validated method. Avoid silently replacing
the target with zero.

4. Reproducibility of performance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** Scores `0.9827` and `0.9944` come from NanoEdge
metadata, but no versioned command reproduces an independent model evaluation
on CSV files under `validation`. A script fixing the evaluated dataset,
metrics, filters, and model version would make performance an acceptance
criterion.

5. Coverage and automation
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** There is no CI or single test command.
`validate_motor_limits.py` fixes the constants and generator files, and
the three interface tests cover serial recovery and CSV writing, but not
visual thresholds, all parser cases, dashboard arguments, or preprocessing
of invalid files. Headless builds were run, but their command is not
versioned and the state machines have no host unit tests.

6. Dependencies and privacy
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** `requirements.txt` pins no versions. Updates to
pandas, NumPy, PySerial, or Sphinx may therefore change behavior or the
documentation build. A constraints file or compatible version ranges
would improve reproducibility.

`AI_Model/metadata.json` also contains the export author's name and email
address. Review this information before publishing the repository, or
automate its removal during export.

Recommended action plan
-----------------------

1. Qualify 4500 rpm and 30 A gradually on an instrumented test bench, then
   validate the 25 A/28 A B2 profile and its 500 electrical Hz/s ramp
   across the full range with shutdown measures active.
2. Diagnose the `speed_power_ewma_6600` divergence in the August 27 log
   before changing parity tolerance.
3. Validate `d6t_temp_c` explicitly and report rows rejected during
   preprocessing.
4. Add a reproducible performance test tied to the library ID and a
   dataset manifest.
5. Provide a single command for host tests and strict Sphinx build, plus
   Debug/Release firmware builds, then run it in continuous integration.
6. Pin validated Python versions and document a reproducible firmware build
   independent of local STM32CubeIDE state.

Proposed acceptance criteria
----------------------------

A version may be considered validated when:

* the strict Sphinx build reports no warnings;
* NanoEdge export validation and all Python tests pass;
* parity meets a technically justified tolerance on all reference logs;
* Debug and Release configurations of both firmware projects build cleanly;
* `model` and `emulator` modes pass the on-board serial contract test;
* an independent thermal session yields archived metrics with the exact
  model ID and dataset manifest.
