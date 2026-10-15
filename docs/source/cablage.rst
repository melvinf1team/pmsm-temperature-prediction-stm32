Wiring
======

The setup uses an STM32 B-G473E-ZEST1S board with an STDES-LVHP01 power board.
External sensors share the board's ground and must be supplied at the voltage
specified in their datasheets.

.. danger::

  Disconnect the power supply before changing any wiring. Verify board
  revisions, connector pinouts, and voltage levels in the official manuals.
  The CN references below describe this repository's test bench and do not
  replace manufacturer schematics. The 30 A and 4500 rpm limits require full
  electrical, thermal, and mechanical qualification before use.

D6T infrared sensor
-------------------

The `d6t_temp_c` column is the NanoEdge AI dataset target. The firmware reads
one pixel from the D6T-44L-06 infrared module over software I2C on `PB6` and
`PB9`.

.. list-table:: D6T connections
   :header-rows: 1

   * - D6T pin
     - Signal
     - STM32 pin
     - Board connector
     - Note
   * - 4
     - SCL
     - PB6
     - CN10-27
     - Open-drain I2C clock line
   * - 3
     - SDA
     - PB9
     - CN10-24
     - CN10-26 carries the same PB9 signal
   * - 2
     - VCC
     - -
     - CN7-18
     - +5 V supply
   * - 1
     - GND
     - -
     - CN7-20
     - CN7-22 is also suitable

Add a 4.7 kΩ pull-up resistor from `SCL` to `3.3V` and another from `SDA`
to `3.3V`. Pins `PB6` and `PB9` are configured as open-drain outputs without
internal pull-ups. Do not pull these lines up to 5 V without checking the
tolerance of the inputs used. The two `PB9` positions visible on the board
carry the same signal, routed to `CN10-24` and `CN10-26` for compatibility
with multiple motor expansion boards.

The expected I2C address is `0x0A`. The firmware reads command `0x4C` and
checks the frame's PEC. The logged pixel is selected by
`D6TIR_SELECTED_PIXEL_INDEX` in the `d6t_ir.c` file of each firmware; choose
it with the live map of `tests/bench/d6t_calibration.py` (see
:ref:`d6t-calibration`). If the sensor is absent or no
valid measurement has yet been received, the CSV value is `NaN`.

DS18B20 sensor
--------------

The DS18B20 provides an external temperature used as an explanatory feature.
It is connected by 1-Wire on `PG6`.

.. list-table:: DS18B20 connections
   :header-rows: 1

   * - Signal
     - STM32 pin
     - Note
   * - DQ
     - PG6
     - Open-drain 1-Wire line
   * - VCC
     - 3V3
     - Sensor supply
   * - GND
     - GND
     - Common ground

Add a 4.7 kΩ pull-up resistor from `DQ` to `3V3` if one is not already
present. The firmware enforces a minimum period of 750 ms to support the
DS18B20's 12-bit conversion.

TB-200S load controller
-----------------------

The TB-200S is driven through its external `ADJ` input. The controller must
be configured for the **0--10 V external-input mode**. Its nominal transfer
(0--10 V for 0--3 A, i.e. :math:`V = I \cdot 10/3`) is not met on the bench:
the measured TB-200S response is 0.2567 A/V above a 0.194 V threshold, so the
nominal command gave about half the expected current and 0.00 A at 0.05 A.
The ADJ input does not load the 1 kOhm resistor (ADJ reads 98 % of PA5): the
difference comes from the TB-200S itself.
The firmware therefore uses a calibration line between the PA5 voltage and
the current displayed by the TB-200S:

.. math::

   V_{PA5} = V_{low} + (I_{load} - I_{low})
   \frac{V_{high} - V_{low}}{I_{high} - I_{low}}

The points are `APP_TB200S_CAL_LOW_V/_A` and `APP_TB200S_CAL_HIGH_V/_A` in
both `app_motor_control.c` files: 0.05 A maps to 0.3892 V and 0.25 A to
1.1683 V. They come from a least-squares fit of 31 readings recorded by
`tests/bench/calibrate_tb200s.py` (2 up/down cycles over 0.05--0.225 A,
maximum deviation 0.003 A, no hysteresis). To recalibrate, run this script
again and enter the `#define` lines of its report in both projects. No
0--10 V gain stage is fitted.

DAC outputs of the B-G473E-ZEST1S and their use in both CubeMX projects:

.. list-table:: External DAC outputs
   :header-rows: 1

   * - STM32 signal
     - Board access
     - Project status
     - Decision
   * - PA4 / DAC1_OUT1
     - CN5-B28 (`M1_DAC_1_OUT1`)
     - Free in both `.ioc` files
     - Not selected because it is on the MC V2 power-board connector
   * - PA5 / DAC1_OUT2
     - CN7-32 (also routed as the Motor2 current reference on CN5-B53)
     - Free in both `.ioc` files
     - **Selected for TB-200S ADJ**
   * - PA6 / DAC2_OUT1
     - CN10-29
     - Free in both `.ioc` files
     - Available alternative
   * - DAC3 and DAC4 channels
     - Internal routes
     - Used by the motor overcurrent comparators
     - Do not reuse

The selected Morpho signal is shared with Motor2 resources. This repository
uses Motor1; do not enable Motor2 on the MC V2 connector while PA5 is used for
the TB-200S.

.. list-table:: TB-200S control wiring
   :header-rows: 1

   * - From
     - Component
     - To
     - Note
   * - B-G473E-ZEST1S CN7-32 (PA5/DAC1_OUT2)
     - 1 kΩ series resistor
     - TB-200S `ADJ`
     - Place the resistor before the capacitor node
   * - B-G473E-ZEST1S CN7-20 or CN7-22
     - Direct wire
     - TB-200S `GND`
     - Mandatory common signal ground
   * - TB-200S `ADJ`
     - 4.7 µF capacitor
     - TB-200S `GND`
     - For a polarized capacitor, `+` goes to ADJ and `-` to GND

The 1 kΩ/4.7 µF network has a 4.7 ms time constant (about 34 Hz cutoff),
well above the 2--5 second command interval. Wire it as follows:

.. code-block:: text

   CN7-32 / PA5 / DAC1_OUT2 ---- 1 kΩ ----+---- ADJ (TB-200S)
                                           |
                                         4.7 µF
                                           |
   CN7-20 or CN7-22 / GND ----------------+---- GND (TB-200S)

Remove the TB-200S factory link used for front-panel/potentiometer control
before applying an external voltage to `ADJ`; verify the exact link position
against the controller revision. Never connect the TB-200S `+10V` terminal to
the STM32 DAC or 3V3 rail. Power and earth the TB-200S and connect its
`OUT+`/`OUT-` terminals to the powder brake according to its own manual.
The `GND` in this circuit is the controller's signal ground, not protective
earth. With power removed, identify it from the exact controller manual and
check that no hazardous potential exists before joining it to the board ground.

.. warning::

   The STM32G473 datasheet guarantees the buffered DAC output range only from
   0.2 V to VREF+ - 0.2 V; keep every calibrated command inside it. The
   TB-200S output saturates at about 0.24 A on this bench: a 0.25 A command
   gives 0.24 A. Check the brake coil resistance (the 24 V output cannot
   drive more than 24 V / R) and the TB-200S current limit.
   Firmware setpoints are commands, not independent current measurements.

PC UART
-------

The dashboard communicates with the board through `USART1`, exposed to the PC
as a COM port. The default baud rate is `115200`. The application protocol is
line-oriented text to simplify diagnosis in a serial terminal.

In both firmware projects, the application takes USART1 over from ASPEP.
Do not open the same port simultaneously in Motor Pilot, a terminal, and the
dashboard: only one PC process can own the COM port.

Checks before applying power
----------------------------

* Verify common ground and the absence of shorts between sensor supplies,
  `3V3`, `5V`, and ground.
* Confirm the pull-up resistors while the board is unpowered.
* Confirm that the TB-200S external-control link is removed, its `+10V`
  terminal is isolated from the STM32, and the 4.7 µF capacitor polarity is
  correct.
* Confirm that the TB-200S signal ground has been identified correctly and is
  safe to bond to the board ground; never use protective earth as a substitute.
* Power the logic without starting the motor and measure the TB-200S ADJ node
  at the 0.05 A command before enabling the brake output.
* Check that the motor and power board are mechanically secure.
* Power the logic first and confirm that the COM port appears.
* Run `python .\tests\bench\check_wiring.py --port COMx` with the motor
  stopped and fix every `FAIL` line; add `--motor` to check the U/V/W phases
  and the brake response (see :doc:`utilisation`).
* Check boot messages and sensor readings before starting a motor sequence.

An absent D6T produces `NaN`. After a transient failure, the DS18B20 may keep
its last valid value; a stable reading alone does not prove every 1-Wire
conversion succeeded.
