STM32 Firmware
==============

Organization
------------

The acquisition firmware is in
`firmware_acquisition/tets_motor_dewalt`. It combines a generated
STM32CubeIDE/MCSDK project with user modules in
`STM32CubeIDE/Application/User` and declarations in `Inc`. Import the
`STM32CubeIDE` subdirectory as the project.

The main application modules are:

`app_serial_control.c`
   UART reception, PC command parsing, parameter validation, and `ACK` /
   `ERR` responses.

`app_motor_control.c`
   Motor startup, shutdown, runtime configuration, current and overspeed
   protections, and TB-200S DAC conversion and scheduling.

`app_datalog.c`
   USART ownership, nonblocking TX queue, CSV header, and `DATA` rows.

`d6t_ir.c`
   Software I2C reads from the D6T infrared sensor and formatting of
   `d6t_temp_c`.

`ds18b20.c`
   DS18B20 1-Wire driver with a cache of the last valid value.

Initialization
--------------

In `Src/main.c`, application initialization occurs in this order:

.. code-block:: c

   AppDatalog_Init();
   AppMotorControl_Init();
   AppSerialControl_Init();

The main loop then calls:

.. code-block:: c

   AppSerialControl_Task();
   AppMotorControl_Task();
   AppDatalog_Task();

`AppDatalog_Init` disables ASPEP/DMA use of `USART1` so the project's ASCII
protocol can use it. Boot messages report logger and D6T sensor status.

Serial control
--------------

`AppSerialControl_OnUsart1Irq` reads received bytes into a circular queue.
`AppSerialControl_Task` rebuilds ASCII lines and finds known commands even
when stray bytes precede them.

`CFG` validates target speed, `Iq` limit, hard stop, acceleration, DATA
period, and DS18B20 period against their limits. A valid configuration calls
`AppMotorControl_SetRuntimeConfig` and `AppDatalog_SetRuntimePeriods`.
`ACQ_START` validates only the two periods, forces the motor to stop, and
arms the logger without requiring a motor configuration.

`LOAD,<amps>` selects a fixed 0.05--0.25 A TB-200S command;
`LOAD,VARIABLE` selects pseudorandom values in the same range every 2--5
seconds. Both forms answer `ACK,LOAD`. The dashboard sends the load command
after `CFG` and before `START`.

Protocol limits are 100 to 4500 rpm, at most 30 A for `Iq` and the hard
stop, at most 50 electrical Hz/s for acceleration, 1 to 10,000 ms for
`DATA`, and at most 10,000 ms for the DS18B20. A requested DS18B20 period
below 750 ms is accepted and raised to 750 ms.

Motor control
-------------

`AppMotorControl_Start` applies the runtime configuration, adjusts the speed
PI controller, and starts the motor through MCSDK with polarization. The
`Iq` limit is ramped during RUN to avoid a sudden torque request.
`AppMotorControl_Task` monitors MCSDK faults, overcurrent, and overspeed.

B2 on `PC13` places a request in the interrupt, then the main loop draws the
first target directly from the full 2000–4000 rpm range. It caps the PI
`Iq` output at 25 A and the `Id/Iq` command magnitude at 28 A, and
triggers application shutdown if the measured magnitude exceeds 28 A.
Every 2 to 5 seconds, a new pseudorandom target different from the previous
one is drawn from the full range without step limits. Startup and every
transition use the fast MCSDK ramp of 500 electrical Hz/s; with two pole
pairs, this is 15,000 mechanical rpm/s. A second press stops the motor.
Deferred processing and 250 ms debounce avoid calling MCSDK from the
interrupt.

B2 always enables variable TB-200S load: the DAC is forced to 0.05 A before
MCSDK starts, then a different value from 0.05 to 0.25 A is drawn every
2--5 seconds. Pressing B2 overrides any earlier serial load/profile choice,
so the physical button consistently starts the fully variable speed-and-load
profile. Every stop returns the DAC command to 0.05 A.

The B2 profile is an internal path: it does not raise the 50 electrical
Hz/s ceiling for configurations received over UART. A `CFG` command
disables the standalone profile and takes control. During a
downward transition, overspeed protection follows the setpoint actually
being ramped by MCSDK while remaining capped at 4500 rpm. It therefore does
not mistake normal ramp inertia for runaway speed.

Preventive DC bus protection is disabled
(`M1_BUS_PROTECTION=false`). Rapid deceleration can regenerate energy into
the bus without dedicated software clamping. Qualify the bus voltage and
the test bench's absorption or braking capacity before use.

The initial setpoint is reapplied when MCSDK actually reports `RUN`. If a new
press requests startup during asynchronous shutdown, the request waits for
`IDLE` and is retried every 100 ms. Deadline comparisons use signed
subtraction and remain valid across 32-bit tick wraparound.

On-device data logging
----------------------

`AppDatalog_StartLogging` arms the logger after `START` is received. The
header is:

.. code-block:: text

   #CSV_HEADER,stm32_time_ms,d6t_temp_c,ds18b20_temp_c,motor_ud_v,motor_uq_v,motor_speed_mech_rpm,motor_id_a,motor_iq_a,load_setpoint_a

Each `DATA` row contains the STM32 tick, temperatures, reconstructed d/q
voltages, mechanical speed, d/q currents, and the instantaneous commanded
TB-200S current. The d/q voltages come from
`CurrCtrl_M1.Ddq_out_pu` and the DC bus voltage. Outside RUN, motor values
are set to zero to avoid logging stale MCSDK values.

D6T sensor
----------

`d6t_ir.c` uses software I2C on `PB6`/`PB9`, available at `CN10-27` and
`CN10-24` respectively. The module reads a 35-byte frame, checks its PEC,
and extracts pixel `D6TIR_SELECTED_PIXEL_INDEX`. It formats the value in
degrees Celsius to one decimal place. Until a valid reading exists,
`D6TIR_GetCsvValue` returns `NaN`.

DS18B20 sensor
--------------

`ds18b20.c` drives the 1-Wire bus with very short critical sections to avoid
disrupting motor control. A 12-bit conversion takes 750 ms. If a fresh read
fails but an older valid value exists, the driver returns the last known
value to keep the CSV usable.

Safety controls
---------------

Safety checks are split between the PC and firmware. The dashboard validates
operator input. The firmware enforces final limits of 4500 rpm and 30 A and
stops the motor on MCSDK fault, excessive total current, or overspeed.
Workbench sources, `.ioc`, `.wbdef`, and generated C files use the same
ceilings, so a regeneration keeps them consistent.

The overspeed threshold follows the setpoint with a margin but remains
bounded by the absolute 4500 rpm ceiling. Direct configuration calls also
normalize `NaN` or infinite values to defaults before passing them to MCSDK.

Nonfinite MCSDK current or speed telemetry triggers fail-safe behavior:
immediate shutdown, B2 profile deactivation, and a fault in the application
state machine. Such values cannot bypass overcurrent or overspeed checks.

The calculated full scale of the current sensor is about 110 A with a 1 mΩ
shunt and gain of 15. This representation range does not validate the board
thermally. The PolPulse setpoint is 14 A, so the startup pulse stays well
below the 30 A ceiling. The software threshold active during PolPulse and the
DC profiler's maximum current are capped at 30 A.

Build and programming
---------------------

Import `firmware_acquisition/tets_motor_dewalt/STM32CubeIDE` as an existing
project. Choose `Debug` or `Release`, run a clean build, then program the
B-G473E-ZEST1S with ST-LINK. Sources under `Drivers`,
`MCSDK_v6.4.2-Full`, and some of `Src`/`Inc` are generated or third-party
code; keep project-specific functional changes in
`STM32CubeIDE/Application/User` and associated application interfaces.

Review any regeneration from STM32CubeMX or Motor Control Workbench before
building: it may change generated files, pin assignments, and current
constants.

The CMSIS/DSP include path in the acquisition project's `.cproject` is
relative to the repository.

AI validation firmware
----------------------

`firmware_validation` is a second standalone project, simplified for
NanoEdge validation. It does not include the full dashboard motor protocol or
ASCII debug module. The UART stream starts automatically, and its format
depends on `APP_NEAI_MODEL_ENABLED`: D6T temperature, prediction, and
`load_setpoint_a` with the model enabled, or 55 features with the model
disabled. Its receive path accepts four predefined `PROFILE,<TOKEN>` commands
plus `STOP` and is used by `temperature_validation_gui.py`. An accepted
profile answers `ACK,PROFILE,<TOKEN>`; a stop answers `ACK,STOP`.
The firmware also accepts `LOAD,<amps>` and `LOAD,VARIABLE`. The validation
GUI does not use them, and a profile command or B2 replaces the load mode
they set.

The library is stored in `firmware_validation/AI_Model` and linked by both
Debug and Release configurations. At compile time, `app_ai_model.c` checks
that the header declares a signal length of 1 and 55 axes, then checks the
dimensions returned by the library again before initialization.

B2 places a persistent startup intent in the application state machine. If
MCSDK is still in `STOP` or `FAULT_OVER`, this intent waits for a real return
to `IDLE` instead of being lost. Completed faults are acknowledged and
startup is retried at a limited rate, without blocking. `MC_StopMotor1` is
issued only once when entering a fault so MCSDK can reach an acknowledgeable
state.

The 44 EWMA states are kept in double precision: with spans up to 47400, the
float32 increment `alpha * (x - mean)` would fall below the resolution of
large signals such as `speed_power`. Features are output as float32. The
state is saved after each sample in two alternating
snapshots in SRAM section `.noinit`. A signature, version, sequence number,
and CRC32 allow restoration of the latest complete snapshot after a CPU/NRST
reset while the board remains powered. Power loss or an inconsistent snapshot
causes a clean state reset. This strategy does not write to Flash.

The USART1 pump also re-enables the peripheral when necessary and clears
`ORE`, `FE`, and `NE` flags before continuing the nonblocking TX queue.

Import the validation firmware separately from
`firmware_validation/STM32CubeIDE`. Its UART mode is chosen at compile time,
so a clean build and reflash are required after changing
`APP_NEAI_MODEL_ENABLED`.

Motor control and the random B2 profile use the same limits and settings as
the acquisition firmware. B2 always selects `VARIABLE_ALL`, regardless of a
previous serial profile. Speed and load changes add no text to UART,
preserving the NanoEdge data contract.

Both projects configure `PA5 / DAC1_OUT2` for the TB-200S. The DAC uses its
external buffered output and the 0--10 V / 0--3 A nominal conversion, while
software restricts commands to 0.05--0.25 A. See :doc:`cablage` for CN7-32,
the 1 kOhm/4.7 uF network, common ground, and the low-voltage calibration
warning.
