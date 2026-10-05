Architecture
============

Overview
--------

The repository separates the PC-controlled acquisition flow from standalone
validation. Both firmware projects use the same peripherals and measurements,
but have different operating protocols and required sampling rates.

Responsibilities:

* the acquisition firmware controls the motor, reads sensors, enforces safety
  limits, drives the TB-200S DAC setpoint, and responds to dashboard commands;
* the dashboard manages the serial session, display, and raw CSV file;
* preprocessing turns measurements into a training dataset;
* the validation firmware reproduces the 55 features and, depending on its
  configuration, calls the NanoEdge AI library.

.. code-block:: text

   D6T -----------+                         +--> raw CSV
                  |                         |
   DS18B20 -------+--> acquisition firmware +--> Tkinter dashboard
                  |            ^            |
   MCSDK ---------+            | USART1     +--> live display
   TB-200S <--- DAC1_OUT2      |
                               |
                          PC commands

   raw CSV --> Python preprocessing --> target + 55 features --> NanoEdge AI
                                                                   |
                                                                   v
   D6T + DS18B20 + MCSDK --> validation firmware --> features or prediction
                                 |
                                 +-- DAC1_OUT2 --> TB-200S

Acquisition firmware
--------------------

The entry point `firmware_acquisition/tets_motor_dewalt/Src/main.c`
initializes HAL, CubeMX peripherals, MCSDK, data logging, motor control, and
the serial protocol. The main loop then calls their cooperative tasks.

Application modules are in
`firmware_acquisition/tets_motor_dewalt/STM32CubeIDE/Application/User`:

* `app_serial_control.c` uses interrupt-driven reception and a character
  queue to parse ASCII commands;
* `app_motor_control.c` applies MCSDK ramps, limits, and shutdowns, converts
  the 0.05--0.25 A TB-200S command to a DAC value, and schedules fixed or
  variable load operation;
* `app_datalog.c` schedules the sensors and feeds a nonblocking UART
  transmit queue;
* `d6t_ir.c` and `ds18b20.c` isolate sensor protocols.

The dashboard keeps Tkinter on the main thread. Serial reads and startup and
shutdown sequences use threads that communicate with the interface through
`queue.Queue` objects. This boundary avoids accessing Tkinter from a
communication thread.

Validation firmware
-------------------

The entry point `firmware_validation/Src/main.c` initializes motor control and
standalone data logging. The feature period is fixed at 100 ms (10 Hz) in
`Inc/preprocess_ewma.h`.

`APP_NEAI_MODEL_ENABLED` selects the UART contract at compile time:

* `0U`: 55 numeric values for the Serial Emulator;
* `1U`: `d6t_temp_c;predicted_temp_c;load_setpoint_a` for the validation
  interface.

The validation logger also owns USART1 reception. Its deliberately small
control protocol accepts `PROFILE,<TOKEN>`, where the token is `STABLE`,
`VARIABLE_LOAD`, `VARIABLE_SPEED`, or `VARIABLE_ALL`, plus `STOP`. It does not
expose the acquisition firmware's broader configurable motor protocol.
Connecting the validation GUI alone sends no motor command.

Changing modes requires a clean build because the C preprocessor makes the
selection.

Data contracts
--------------

The acquisition firmware announces columns with `#CSV_HEADER` and then emits
`DATA` rows. The dashboard keeps these nine columns:

.. code-block:: text

   stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a;load_setpoint_a

Python writes semicolon-separated files. By contrast, the acquisition
firmware's UART protocol uses commas. The dashboard converts the separators
when writing the file.

`load_setpoint_a` is deliberately outside the 55-axis model contract. It is
the commanded brake current and is retained in the raw data for experiment
traceability. Offline preprocessing omits it by default; `INCLUDE_LOAD_SETPOINT`
or `--include-load-setpoint` keeps it after all 55 axes without EWMA.

Feature construction
--------------------

Six explanatory measurements are used directly: DS18B20 temperature, d/q
voltages, mechanical speed, and d/q currents. Five quantities are derived:

.. math::

   u_s &= \sqrt{u_d^2 + u_q^2} \\
   i_s &= \sqrt{i_d^2 + i_q^2} \\
   S_{el} &= 1.5\,u_s i_s \\
   speed\_current &= n\,i_s \\
   speed\_power &= n\,S_{el}

Each of these eleven signals is kept as an instantaneous value and given four
EWMAs: :math:`11 \times (1 + 4) = 55` features. The D6T target has no EWMA.

EWMA sampling rate
------------------

The reference spans `1320`, `3360`, `6360`, and `9480` correspond to a
logging rate of 2 Hz. To preserve the same time constants at another DATA
period, preprocessing applies:

.. math::

   span = span_{2Hz} \times \frac{f_{\mathrm{acquisition}}}{2}

A 10 Hz log therefore uses spans `6600`, `16800`, `31800`, and `47400`.

The Python script derives :math:`f_{\mathrm{acquisition}}` from the median of
strictly positive differences in `stm32_time_ms`. The validation firmware
uses the 10 Hz spans directly and keeps the EWMA states in double precision.
The parity test checks order and recurrence between the two implementations.

Architecture limitations
------------------------

* There is no versioned standalone firmware build outside STM32CubeIDE.
* A replacement model is valid only if the 55 axes, their order, the
  hard-float ABI, and the NanoEdge API remain compatible.
* Python rescaling assumes the timestamps represent the sampling rate; an
  incorrect forced frequency changes the EWMAs' temporal memory.
* Replacing invalid feature values with zero may conceal a missing sensor
  reading. Rows with an invalid D6T target are dropped from the output.
