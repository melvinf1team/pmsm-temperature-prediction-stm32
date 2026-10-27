Project Status
==============

This page lists the verification status of the repository and the open points
to close before the pipeline can be considered validated.

Verification status
-------------------

.. csv-table:: Verification results
   :header: "Check", "Status", "Result"
   :widths: 25, 20, 55

   "Strict Sphinx build", "Passed", "No warnings with -W --keep-going"
   "NanoEdge export consistency", "Passed", "Valid ID, ABI, symbols, dimensions, and Ridge artifacts"
   "Dashboard, preprocessing, wiring-check, D6T calibration, and validation GUI tests", "Passed", "All unit tests in tests/unit pass"
   "Motor and TB-200S consistency", "Passed", "Dashboard, firmware, PA5/DAC1, IOC, WBDEF, and Workbench checked"
   "Embedded/pandas parity", "Passed", "Maximum scaled relative error about 3.3e-7 against a 1e-6 limit on all logs"
   "Firmware Debug/Release builds", "Passed", "Both projects build in Debug and Release with STM32CubeIDE 2.1.1"
   "USART1 contract on target", "Passed", "Verified on the programmed board"
   "Wiring check (DIAG) on target", "Passed", "Diagnosis rules confirmed on the bench"
   "D6T pixel calibration on target", "Passed", "Live map session completed on the bench"

Open points
-----------

0. Fault source diagnostic on target
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**High priority.** The `#FAULT_DIAG` / `#FAULT_TRACE` diagnostic of the
acquisition firmware (`app_fault_diag.c`) and its dashboard analysis compile
with the STM32CubeIDE toolchain and pass the unit tests with synthetic data,
but have not been run on the programmed board. The 27 October 2026 session
stopped with `mc_now=0x0040` at 3184 rpm while the sampled Id/Iq were far
below 110 A, and its source is still unknown. Flash the firmware, repeat the
`All variable` profile, and record the verdict before concluding on the cause.

1. Test bench qualification
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**High priority.** Software ceilings are 4500 rpm, 28 A for `Iq`, and 30 A
for total current. The measurement chain represents about 110 A at full
scale, but this does not establish the test bench's electrical, thermal, or
mechanical capacity.

The B2 profile caps the PI `Iq` output at 28 A and both the `Id/Iq`
command magnitude and measured-magnitude shutdown threshold at 30 A. Its
targets are drawn every 2 to 5 seconds from the full 3000–4000 rpm range,
with a 500 electrical Hz/s ramp (15,000 rpm/s with two pole pairs). Before
use, check the motor, power board, supply, wiring, cooling, mounting, and
emergency stop. Begin at reduced current and record temperatures and faults.

Preventive DC bus protection is disabled (`M1_BUS_PROTECTION=false`).
Downward transitions at this ramp rate can feed energy back and raise bus
voltage. Monitor that voltage and validate supply absorption or braking
before running the complete profile.

The TB-200S control is limited to 0.05--0.25 A. The nominal 0--10 V / 0--3 A
transfer gave about half the commanded current, so both firmwares use a bench
calibration (0.3892--1.1683 V on PA5/DAC1_OUT2), fitted on 31 readings over
0.05--0.225 A with a maximum deviation of 0.003 A. The TB-200S output
saturates at about 0.24 A, so a 0.25 A command gives 0.24 A. The logged value is a command, not
independent current feedback.

2. Reproducibility of performance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** Scores `0.9827` and `0.9944` come from NanoEdge
metadata; no versioned command evaluates the model on the CSV files under
`validation`. A script fixing the evaluated dataset, metrics, filters, and
model ID would make performance an acceptance criterion.

3. Coverage and automation
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** There is no CI; `python .\tests\run_all.py` runs every
check that needs no board.
`validate_motor_limits.py` checks the constants, DAC pin, protocol markers,
and generator files, and that the dashboard presets match both firmwares and
the validation GUI. The unit tests cover profile command serialization,
acknowledgments, startup order and cancellation, preset and collect-only
startup, two-field telemetry, CSV
writing, the optional load column, invalid target rows, the wiring-check
diagnosis rules, and the D6T calibration logic, but not visual
thresholds, all parser cases, dashboard arguments, or the firmware state
machines on a host. Firmware builds have no command-line procedure.

4. Dependencies and privacy
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** `requirements.txt` pins no versions. Updates to
pandas, NumPy, PySerial, or Sphinx may therefore change behavior or the
documentation build. A constraints file or compatible version ranges
would improve reproducibility.

`AI_Model/metadata.json` contains the export author's name and email
address. Review this information before publishing the repository.

Acceptance criteria
-------------------

A version may be considered validated when:

* the strict Sphinx build reports no warnings;
* NanoEdge export validation and all Python tests pass;
* parity meets a technically justified tolerance on all reference logs;
* Debug and Release configurations of both firmware projects build cleanly;
* `model` and `emulator` modes pass the on-board serial contract test;
* an independent thermal session yields archived metrics with the exact
  model ID and dataset manifest.
