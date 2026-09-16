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
`D6TIR_SELECTED_PIXEL_INDEX` in `d6t_ir.c`. If the sensor is absent or no
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
* Check that the motor and power board are mechanically secure.
* Power the logic first and confirm that the COM port appears.
* Check boot messages and sensor readings before starting a motor sequence.

An absent D6T produces `NaN`. After a transient failure, the DS18B20 may keep
its last valid value; a stable reading alone does not prove every 1-Wire
conversion succeeded.
