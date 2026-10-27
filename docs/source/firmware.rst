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

`app_fault_diag.c`
   Source diagnostic of a hardware break (both firmware projects): reads
   the break sources in the TIM8 break interrupt and keeps the last 2 ms of
   phase currents (see `Hardware break diagnostic`_).

`app_foc_timing.c`
   FOC timing (both firmware projects): duration and start delay of every
   FOC period, capture of an `MC_DURATION` overrun, and interrupt-free
   windows placed between two FOC runs for the DS18B20 (see
   `FOC timing and MC_DURATION`_).

`app_wiring_diag.c`
   Wiring diagnostics of the sensor and TB-200S lines (`DIAG`) and motor
   status report (`STATUS`).

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
The dashboard uses independent commands: `PROFILE_SET,<TOKEN>` stores a
preset for the next `MOTOR_START`, `MOTOR_STOP` stops only the motor, and
`LOG_START,<datalog_ms>,<ds18b20_ms>` / `LOG_STOP` control only the logger.
A Custom configuration uses `MOTOR_CFG` and `LOAD`, without changing the acquisition periods. The port stays open
across these actions, and a motor stop/restart does not reset the CSV.
The legacy combined commands below remain supported.

`ACQ_START` validates only the two periods, forces the motor to stop, and
arms the logger without requiring a motor configuration.

`LOAD,<amps>` selects a fixed 0.05--0.25 A TB-200S command;
`LOAD,VARIABLE` selects pseudorandom values from 0.10 to 0.20 A every 2--5
seconds. Both forms answer `ACK,LOAD`. The dashboard sends the load command
after `CFG` and before `MOTOR_START`.

`PROFILE,<TOKEN>[,<datalog_ms>,<ds18b20_ms>]` runs the same four presets as
the validation firmware (`STABLE`, `VARIABLE_LOAD`, `VARIABLE_SPEED`,
`VARIABLE_ALL`). It validates the optional periods like `ACQ_START`, stops the
motor and logger, clears the received `CFG`, restarts logging, and calls
`AppMotorControl_StartProfile`; it answers `ACK,PROFILE,<TOKEN>`, or
`ERR,BAD_PROFILE`, `ERR,PROFILE_VALUE_OUT_OF_RANGE`, `ERR,LOAD_DAC_FAILED`.
`PROFILE` is matched before `LOAD,` because `PROFILE,VARIABLE_LOAD,...`
contains that substring.

Protocol limits are 100 to 4500 rpm, at most 28 A for `Iq` and 30 A for the
hard stop, at most 50 electrical Hz/s for acceleration, 1 to 10,000 ms for
`DATA`, and at most 10,000 ms for the DS18B20. A requested DS18B20 period
below 750 ms is accepted and raised to 750 ms.

Wiring diagnostics
------------------

`DIAG` stops the motor and logging, clears the received configuration, runs
`AppWiringDiag_Run`, then answers `ACK,DIAG`. The run lasts a few seconds and
sends one line per measurement:

.. code-block:: text

   DIAG,BEGIN,version=1
   DIAG,POWER,app_state=IDLE,fault_reason=NONE,mc_state=0,faults_now=0x0000,faults_occurred=0x0000,vbus_mv=24000,speed_rpm=0,load_ma=50
   DIAG,PIN,name=PB6,role=SCL,pd=1,pu=1
   DIAG,SHORT,a=PB6,b=PB9,shorted=0
   DIAG,I2C,scl=PB6,sda=PB9,ack=1
   DIAG,I2C_SCAN,devices=0A
   DIAG,OW,pin=PG6,presence=1
   DIAG,DS18B20,power=external,conv_ms=600,crc=1,temp_centi=2350
   DIAG,DAC,adc=1,drive_code=1450,v_drive_mv=1168,v_hold_mv=1150,v_zero_mv=5,rise_us=230000,v_pu_end_mv=2400,v_pd_end_mv=15
   DIAG,RC,pin=PA4,rise_us=40
   DIAG,D6T,read=1,temp_c=24.5
   DIAG,END

`PIN` reads each line with the internal pull-down then the internal pull-up:
`pd=1,pu=1` means an external pull-up, `pd=0,pu=1` a floating line, and
`pd=0,pu=0` a line held low. The free Morpho pins next to CN10-27, CN10-24,
CN7-1, and CN7-32 are read the same way. Lines found pulled up are added to
the I2C probe at address `0x0A` in every SCL/SDA order and to the 1-Wire
presence test, which locates a swapped or misplaced sensor wire.

The PA5 test drives the DAC at the 0.25 A code, reads PA5 back through ADC2
channel 13, releases the pin, and times its rise through the internal
pull-up. The R1/C1 network keeps PA5 low for hundreds of milliseconds; an open
pin rises in microseconds. The same timing is measured on PA4 and PE10 to find
the network on a neighbouring pin. During this test ADJ can reach about 3 V
for less than one second; the motor is stopped. The DAC, PG6, and the D6T
driver are restored at the end.

`STATUS` sends `STATUS,` followed by the same fields as `DIAG,POWER`, then
`ACK,STATUS`. `fault_reason` gives the cause of the last application fault:
`MCSDK_FAULT`, `MCSDK_NOT_IDLE`, `LOAD_DAC`, `START_REJECTED`, `STARTUP_TIMEOUT`,
`NONFINITE_CURRENT`, `HARD_OVERCURRENT`, `NONFINITE_SPEED`, or `OVERSPEED`.
It returns to `NONE` on the next successful start.

`tests/bench/check_wiring.py` turns these answers into a diagnosis; see
:doc:`utilisation`.

Motor control
-------------

`AppMotorControl_Start` applies the runtime configuration, adjusts the speed
PI controller, and starts the motor through MCSDK with polarization. It first
waits up to 1 s for MCSDK to reach `IDLE`, acknowledging latched faults (for
example an undervoltage recorded before the motor supply was switched on) and
covering the 400 ms `STOP` permanency, so a serial `START` behaves like B2. The
`Iq` limit is ramped during RUN to avoid a sudden torque request.
`AppMotorControl_Task` monitors MCSDK faults, overcurrent, overspeed, and
the D6T motor temperature independently of logging and AI inference.

Custom starts in both projects use a staged speed/brake sequence configured
by ``APP_CUSTOM_*`` in ``Inc/app_motor_control.h``. Start at min(2000 rpm,
requested speed) with the B2 acceleration and 0.05 A brake. After two stable
seconds, ramp speed at up to 500 rpm/s (respecting a lower configured
acceleration), wait one stable second at the final speed, then ramp the
fixed brake at 0.04 A/s. Speed must track within 5% (minimum tolerance
50 rpm). Speed loss pauses growth; brake growth resumes after one stable
second. Five continuous seconds outside tolerance stops the motor with
``CUSTOM_SPEED_TIMEOUT``. STOP, DAC errors and MCSDK stops cancel the
sequence and restore 0.05 A. UART markers are ``#CUSTOM,START`` and
``#CUSTOM,READY``. The 4500 rpm / 0.25 A sequence takes about 13 seconds
plus initial startup/tracking delays. B2/PROFILE presets are unchanged.
Runtime configuration restages a transition from the current reference;
Custom fixed-load increases use the brake ramp and decreases are immediate.

Both projects emit ``#FAULT,<reason>,...`` on every protection stop. The
snapshot precedes STOP: ``speed_mrpm`` is rpm multiplied by 1000;
``speed_limit_rpm`` is the shutdown threshold; ``iq_ma``, ``id_ma``,
``hard_ma`` and ``load_ma`` are currents/commands in mA. ``mc_now`` and
``mc_occurred`` are hexadecimal MCSDK masks. Application faults may have
zero SDK masks. Full UART queues defer a whole report for retry; persistent
faults do not flood the link. The dashboard explains these lines in the
console and retains a red motor-error state while logging continues.
The current 5000 rpm absolute overspeed cap also applies to a 4500 rpm
target: exceeding it for 100 ms stops the motor.

Hardware break diagnostic
~~~~~~~~~~~~~~~~~~~~~~~~~

``mc_now=0x0040`` (``MC_OVER_CURR``) only says that TIM8 received a break.
The generated project merges two sources on that break input: the external
``BKIN`` pin ``PB7``, active low, wired to the fault line of the STDES-LVHP01
power board (L6491 drivers), and the internal comparators ``COMP4``,
``COMP6`` and ``COMP7`` (phases U, V, W) compared with the DAC threshold of
about 110 A (``WB_UI_OCP_THRESHOLD``). The MCSDK does not record which source
fired.

Both firmware projects add ``app_fault_diag.c`` for this. It is called
from three places, the first two inside ``USER CODE`` sections so that a
Workbench regeneration keeps them:

* ``TIMx_BRK_M1_IRQHandler`` (``Src/stm32g4xx_mc_it.c``) calls
  ``AppFaultDiag_OnBreakIrq()`` first, before the MCSDK handler. It latches
  the ``PB7`` level, the three comparator outputs, ``TIM8->SR`` and the time.
* ``TSK_HighFrequencyTask`` (``Src/mc_tasks.c``) calls
  ``AppFaultDiag_OnHighFrequencyTask()`` once per FOC period (16 kHz). It
  stores the phase currents and Vbus in a 32-sample ring buffer (2 ms), keeps
  the peak since the start and two alternating 10 ms peak windows. The ring
  buffer freezes when the break is latched.
* ``app_motor_control.c`` calls ``AppFaultDiag_Arm()`` before each motor start,
  ``AppFaultDiag_PrepareReport()`` when it builds the ``#FAULT`` line, and
  sends the prepared lines right after it.

Each ``#FAULT`` is followed by one ``#FAULT_DIAG`` line and, when a break was
captured, four ``#FAULT_TRACE`` lines:

.. code-block:: text

   #FAULT_DIAG,break=1,t_break_ms=406671,t_now_ms=406684,irq_count=1,tim_sr=0x0081,bkin_low=0,comp=0x0,bkin_now_low=0,comp_now=0x0,peak_run_da=168,peak_recent_da=131,ocp_da=1100,vbus_dv=181,trace_n=32,trace_period_us=62,foc_period_ns=62494,foc_max_ns=30900,foc_late_max_ns=1200,foc_overruns=0,foc_overrun_ns=0,foc_overrun_late_ns=0,quiet_timeouts=0
   #FAULT_TRACE,-32,118:-61:-57:181,...

.. csv-table:: ``#FAULT_DIAG`` fields
   :header: "Field", "Meaning"
   :widths: 25, 75

   "break", "1 if the TIM8 break interrupt ran since the last start, 0 for an application fault"
   "bkin_low / bkin_now_low", "``PB7`` low (board fault asserted) at the interrupt / when the report is built"
   "comp / comp_now", "Bit mask of comparators above threshold (1 = U, 2 = V, 4 = W), same two instants"
   "tim_sr", "``TIM8->SR`` read at the interrupt (``BIF`` 0x0080, ``B2IF`` 0x0100, ``SBIF`` 0x2000)"
   "irq_count", "Number of break interrupts since the start; more than 1 means a chattering source"
   "peak_run_da, peak_recent_da", "Largest phase current since the start / in the last 10-20 ms, in 0.1 A"
   "ocp_da", "Hardware threshold, 0.1 A (``APP_FAULT_DIAG_OCP_THRESHOLD_A``)"
   "vbus_dv", "Bus voltage when the report is built, 0.1 V"
   "trace_n, trace_period_us", "Number of trace samples and the period between them"
   "foc_period_ns", "Nominal FOC period measured from TIM8 (about 62,500 ns at 16 kHz)"
   "foc_max_ns, foc_late_max_ns", "Longest ``TSK_HighFrequencyTask`` and largest start delay since the start"
   "foc_overruns", "Number of FOC periods that ended in ``MC_DURATION`` since the start"
   "foc_overrun_ns, foc_overrun_late_ns", "Duration and start delay of the first of these periods, 0 if none"
   "quiet_timeouts", "DS18B20 1-Wire slots dropped because no FOC-free window was found"

``#FAULT_TRACE,<first>,u:v:w:vbus,...`` carries up to 8 samples per line from
the oldest to the newest; ``<first>`` is the index of the first sample
relative to the break (-1 is the last sample before it). Currents are in
0.1 A, voltages in 0.1 V. The phase currents are the values reconstructed
from the three shunts at the FOC rate, so a spike shorter than a PWM period
may not appear in them even when the comparator saw it.

The phase current and bus voltage scales come from ``CURRENT_SCALE`` and
``VOLTAGE_SCALE`` in ``drive_parameters.h``. ``firmware_validation`` contains
the same module, with English comments, and sends the same lines.

FOC timing and MC_DURATION
~~~~~~~~~~~~~~~~~~~~~~~~~~

``mc_occurred=0x0001`` (``MC_DURATION``) is not an electrical fault. With
``OVS_COUNT = 4``, the FOC runs in the ``DMA2_Channel1`` interrupt (priority 1)
once per PWM period. ``R3_GetMeasurements()`` clears the TIM8 update flag when
it reads the currents, and ``R3_WriteTIMRegisters()`` checks it again after
writing the duty cycles. If the next TIM8 update came in between, the duty
cycles were written too late for the next PWM period and the MCSDK stops the
motor. Inside the FOC, no interrupt can preempt it, so the overrun comes either
from a FOC started late, because some code masked interrupts just before
(``__disable_irq``), or from a computation that took too long.

``app_foc_timing.c`` measures both with the DWT cycle counter, from two hooks
in the ``USER CODE`` sections of ``TSK_HighFrequencyTask`` (``Src/mc_tasks.c``):

* ``AppFocTiming_OnHighFrequencyStart()`` takes the start time. The start
  delay is measured against the ideal start schedule, which is strictly
  periodic (2 x TIM8 ARR cycles): an on-time start becomes the reference, a
  later one is counted as late.
* ``AppFocTiming_OnHighFrequencyEnd(hFOCreturn == MC_DURATION)`` takes the
  duration, keeps the maxima since the start and captures the duration and
  delay of the first period that ended in ``MC_DURATION``.

``AppFocTiming_Init()`` runs in ``main.c`` before the sensors are initialized,
and ``AppFocTiming_Arm()`` clears the maxima before each motor start. The
values are sent in ``#FAULT_DIAG`` (see the table above).

The 27 October 2026 random-profile session stopped with ``MC_DURATION`` at
about 3500 rpm. The likely cause was the DS18B20 driver, which masked
interrupts for up to about 13 us on every 1-Wire bit, dozens of times per
second, with a 62.5 us FOC period. ``ds18b20.c`` now asks
``AppFocTiming_EnterQuietWindow()`` for each short critical window (8 us for a
written 1, 15 us for a read bit). The function masks interrupts only when the
next FOC start is far enough away, leaving a 3 us guard
(``APP_FOC_TIMING_GUARD_US``). If no such window appears within four FOC
periods, the bit is dropped and the transaction fails, so the driver retries
or keeps its cached value instead of delaying the FOC. ``quiet_timeouts``
counts these drops. The drivers no longer reset ``DWT->CYCCNT``, which the
timing module uses as its time base.

Both projects define the heating settings in ``Inc/app_motor_control.h``:
``APP_ID_TARGET_A=0.0f`` and ``APP_ID_RAMP_A_PER_S=0.25f``. Heating injection
is disabled. If a positive target is configured, Id remains zero
during startup, including the complete Custom speed/brake sequence.
After one second within 5% of the speed target (minimum
tolerance 50 rpm), it increases at 0.25 A/s. Speed transitions pause the
increase; stops, faults and runtime reconfiguration reset Id to zero.
Current headroom is bounded by the hard-stop limit and the global 30 A
ceiling. Setting the Id target to zero disables this heating current.

A selected D6T pixel strictly above 70 C stops the motor and latches an
application fault. A missing reading or a last valid reading older than
1000 ms also stops/prevents startup. A new start is accepted only after a
fresh reading at or below 65 C. Pending starts/button actions are cleared;
cooling never starts the motor automatically. Fault UART events are
``#FAULT,D6T_OVERTEMP`` and ``#FAULT,D6T_UNAVAILABLE``. A blocked ``PROFILE``
returns ``ERR,THERMAL_PROTECTION``. Logging can continue after the stop.

The separate MCSDK NTC protection for the power board trips at 120 C, with
10 C hysteresis. The generated headers and Workbench/CubeMX files contain
the same threshold.

B2 on `PC13` places a request in the interrupt, then the main loop draws the
first target directly from the full 3000–4000 rpm range. It caps the PI
`Iq` output at 28 A and the `Id/Iq` command magnitude at 30 A, and
triggers application shutdown if the measured magnitude exceeds 30 A.
Every 2 to 5 seconds, a new pseudorandom target different from the previous
one is drawn from the full range without step limits. Startup and every
transition use the fast MCSDK ramp of 500 electrical Hz/s; with two pole
pairs, this is 15,000 mechanical rpm/s. A second press stops the motor.
Deferred processing and 250 ms debounce avoid calling MCSDK from the
interrupt.

B2 always enables variable TB-200S load: the DAC is forced to 0.05 A before
MCSDK starts, then a different value from 0.10 to 0.20 A is drawn every
2--5 seconds. Pressing B2 overrides any earlier serial load/profile choice,
so the physical button consistently starts the fully variable speed-and-load
profile. Every stop returns the DAC command to 0.05 A.

The B2 profile is an internal path: it does not raise the 50 electrical
Hz/s ceiling for configurations received through `CFG`. The serial `PROFILE`
presets share this path through `AppMotorControl_StartProfile`: `STABLE` and
`VARIABLE_LOAD` keep 4000 rpm, `VARIABLE_SPEED` and `VARIABLE_ALL` draw speed
targets like B2, and the load is either fixed at 0.20 A after RUN or variable.
When MCSDK accepts the start, the firmware sends `#PROFILE,START,<TOKEN>,...`
(`#B2,START,...` for the button). A `CFG` command
disables the standalone profile and takes control. During a
downward transition, overspeed protection follows the setpoint actually
being ramped by MCSDK while remaining capped at 5000 rpm. It therefore does
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
`CN10-24` respectively. Every 250 ms, the module reads a 35-byte frame (PTAT,
16 pixels, PEC), checks its PEC, and extracts pixel
`D6TIR_SELECTED_PIXEL_INDEX`. It formats the value in degrees Celsius to one
decimal place. Until a valid reading exists, `D6TIR_GetCsvValue` returns
`NaN`. The same macro, in the same file of `firmware_validation`, selects the
pixel used by the model; both values must match.

The driver also keeps the last valid frame. `D6T_FRAME` returns it without a
new I2C transfer and without touching the motor:

.. code-block:: text

   D6T_FRAME
   D6T_FRAME,ok=1,selected=10,ptat=253,px=241:243:...:250
   ACK,D6T_FRAME

Values are tenths of a degree; `selected` is the compiled
`D6TIR_SELECTED_PIXEL_INDEX`, and `ok=0` means that the last read failed.
`tests/bench/d6t_calibration.py` polls this command to display the
live map and writes the chosen pixel into both firmwares (see
:ref:`d6t-calibration`).

DS18B20 sensor
--------------

`ds18b20.c` drives the 1-Wire bus with very short critical sections to avoid
disrupting motor control. Each critical section is placed between two FOC runs
by `AppFocTiming_EnterQuietWindow()` (see `FOC timing and MC_DURATION`_).
A 12-bit conversion takes 750 ms. If a fresh read fails but an older valid
value exists, the driver returns the last known value to keep the CSV
usable.

Safety controls
---------------

Safety checks are split between the PC and firmware. The dashboard validates
operator input. The firmware enforces final limits of 4500 rpm and 30 A and
stops the motor on MCSDK fault, excessive total current, or overspeed.
Workbench sources, `.ioc`, `.wbdef`, and generated C files use the same
ceilings, so a regeneration keeps them consistent.

The overspeed threshold follows the setpoint with a margin but remains
bounded by the absolute 5000 rpm ceiling. Direct configuration calls also
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
external buffered output and the bench-calibrated TB-200S transfer, while
software restricts commands to 0.05--0.25 A. See :doc:`cablage` for CN7-32,
the 1 kOhm/4.7 uF network, common ground, and the low-voltage calibration
warning.
