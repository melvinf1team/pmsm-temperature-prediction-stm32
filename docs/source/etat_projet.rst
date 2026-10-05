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
   "Dashboard, preprocessing, and validation GUI tests", "Passed", "All unit tests pass"
   "Motor and TB-200S consistency", "Passed", "Dashboard, firmware, PA5/DAC1, IOC, WBDEF, and Workbench checked"
   "Embedded/pandas parity", "Passed", "Maximum scaled relative error about 3.3e-7 against a 1e-6 limit on all logs"
   "Firmware Debug/Release builds", "Passed", "Both projects build in Debug and Release with STM32CubeIDE 2.1.1"
   "USART1 contract on target", "Not run", "Requires a programmed board and COM port"

Open points
-----------

1. Test bench qualification
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**High priority.** Software ceilings are 4500 rpm, 30 A for `Iq`, and 30 A
for total current. The measurement chain represents about 110 A at full
scale, but this does not establish the test bench's electrical, thermal, or
mechanical capacity.

The B2 profile caps the PI `Iq` output at 25 A and both the `Id/Iq`
command magnitude and measured-magnitude shutdown threshold at 28 A. Its
targets are drawn every 2 to 5 seconds from the full 2000–4000 rpm range,
with a 500 electrical Hz/s ramp (15,000 rpm/s with two pole pairs). Before
use, check the motor, power board, supply, wiring, cooling, mounting, and
emergency stop. Begin at reduced current and record temperatures and faults.

Preventive DC bus protection is disabled (`M1_BUS_PROTECTION=false`).
Downward transitions at this ramp rate can feed energy back and raise bus
voltage. Monitor that voltage and validate supply absorption or braking
before running the complete profile.

The TB-200S control is limited to 0.05--0.25 A and uses only about
0.1667--0.8333 V from PA5/DAC1_OUT2. The lowest command is below the
STM32G473 buffered-DAC guaranteed 0.2 V linear range. Measure the ADJ voltage
and actual brake-controller output at both endpoints, and calibrate or revise
the interface if 0.05 A accuracy is required. The logged value is a command,
not independent current feedback.

2. Reproducibility of performance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** Scores `0.9827` and `0.9944` come from NanoEdge
metadata; no versioned command evaluates the model on the CSV files under
`validation`. A script fixing the evaluated dataset, metrics, filters, and
model ID would make performance an acceptance criterion.

3. Coverage and automation
~~~~~~~~~~~~~~~~~~~~~~~~~~

**Medium priority.** There is no CI or single test command.
`validate_motor_limits.py` checks the constants, DAC pin, protocol markers,
and generator files. The unit tests cover profile command serialization,
acknowledgments, startup order and cancellation, two-field telemetry, CSV
writing, the optional load column, and invalid target rows, but not visual
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
