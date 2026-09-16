Operation
=========

Recommended sequence
--------------------

1. Switch off power and inspect the setup described in :doc:`cablage`.
2. Build and flash the acquisition firmware from STM32CubeIDE.
3. Power the logic, connect the sensors, and identify the COM port.
4. Start `datalogging/motor_datalog_gui_dashboard.py`.
5. Select the port, 115200 baud, and session mode.
6. In motor mode, choose or create a profile; in acquisition-only mode, enter
   only the periods.
7. Check the CSV path, then start the session.
8. Stop with the dashboard button, which sends `STOP` before closing resources.
9. Inspect the raw CSV file before running preprocessing.

Operating ranges
----------------

The dashboard enforces the following ranges. The firmware remains the final
authority for commands sent directly over UART:

.. csv-table:: Acquisition parameters
   :header: "Parameter", "Range", "Note"
   :widths: 25, 25, 50

   "Target speed", "100 to 4500 rpm", "Values below 100 rpm are rejected"
   "Iq limit", "greater than 0 to 30 A", "Startup begins at no more than 4.5 A, then follows a ramp"
   "Total-current shutdown", "greater than 0 to 30 A", "Application protection in addition to MCSDK faults"
   "Acceleration", "greater than 0 to 50 electrical Hz/s", "Same limit in dashboard and firmware"
   "DATA period", "1 to 10,000 ms", "Sets the raw CSV sampling rate"
   "DS18B20 period", "750 to 10,000 ms", "Firmware raises shorter UART requests to 750 ms"

.. danger::

   Do not treat these maxima as recommended operating values. A run at
   4500 rpm or 30 A requires prior validation of the motor, power stage,
   supply, wiring, cooling, and mechanical protection. Preventive DC bus
   protection is disabled (`M1_BUS_PROTECTION=false`); also monitor
   regenerative overvoltage during rapid downward transitions and validate
   the test bench's braking or energy absorption capability.

Standalone B2 profile
---------------------

In both firmware projects, the first press of B2 starts this profile:

* the first target is drawn directly between 2000 and 4000 rpm;
* a new target different from the previous one is drawn after a
  pseudorandom delay of 2 to 5 seconds;
* targets cover the entire 2000–4000 rpm range without step limits;
* startup and transitions use a ramp of 500 electrical Hz/s, or
  15,000 rpm/s with two pole pairs;
* the PI `Iq` output is limited to 25 A, while the `Id/Iq` command
  magnitude and shutdown threshold on measured magnitude are limited to 28 A.

The pseudorandom generator is seeded with the time of the B2 press. The new
setpoint is processed in the main loop, not in the interrupt. A second press
stops the motor and disables the profile. Startup polarization stays at 14 A.
The 500 electrical Hz/s B2 ramp is internal; UART profiles and the dashboard
remain capped at 50 electrical Hz/s. A new UART configuration disables the B2
profile and takes control immediately.

Raw dashboard columns
---------------------

The firmware announces columns with `#CSV_HEADER`. The dashboard retains
these columns in this order:

.. code-block:: text

   stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a

`stm32_time_ms`
   Board timestamp in milliseconds. Preprocessing uses it to derive the
   actual acquisition rate.

`d6t_temp_c`
   Target infrared temperature. It becomes the first column of the processed
   file and is not smoothed. An unavailable reading is emitted as `NaN`.

`ds18b20_temp_c`
   External reference temperature, retained as a feature.

`motor_ud_v` and `motor_uq_v`
   d/q voltages reconstructed from modulation output and the DC bus.

`motor_speed_mech_rpm`
   Mechanical speed in rpm.

`motor_id_a` and `motor_iq_a`
   Motor d/q currents in amperes.

Serial protocol
---------------

Commands sent by the dashboard:

.. code-block:: text

   SYNC
   CFG,<target_rpm>,<iq_limit_a>,<hard_limit_a>,<accel_elec_hz_s>,<datalog_ms>,<ds18b20_ms>
   START
   ACQ_START,<datalog_ms>,<ds18b20_ms>
   STOP

Expected responses and messages:

.. code-block:: text

   ACK,SYNC
   ACK,CFG
   ACK,START
   ACK,ACQ_START
   ACK,STOP
   ERR,<reason>
   #CSV_HEADER,<columns>
   DATA,<values>

The dashboard ignores `DATA` lines received before `#CSV_HEADER` to avoid
writing an inconsistent CSV file.

`ACQ_START` is used by itself after `SYNC` to record cooling while the motor
is stopped. In this state, the firmware explicitly reports zero for voltages,
currents, and speed instead of reusing the last MCSDK sample held before stop.

Stop and recovery after errors
------------------------------

On `ERR`, acknowledgment timeout, or serial connection loss, stop the session
and inspect the dashboard log. Close other software using the port, restore
the connection, then start a full new session from `SYNC`. Do not concatenate
an incomplete file manually with a new acquisition; separate sessions make
timestamp checks easier.

Before preprocessing, check at least:

* that all eight expected columns are present;
* that `stm32_time_ms` increases mostly monotonically;
* the test's sampling rate and duration;
* `NaN` or empty lines in the D6T target;
* unit consistency and obvious saturation.

NanoEdge AI Studio import
-------------------------

After preprocessing, the CSV file begins with `d6t_temp_c`. Use this first
column as the extrapolation target. The other columns are instantaneous,
derived, and EWMA-smoothed features.

By default, the processed file has no header. Use `--header` to inspect it,
then compare its final order with
`firmware_validation/AI_Model/feature_order.txt` before replacing the model.

On-device validation
--------------------

The `firmware_validation` project is not controlled by dashboard commands.
Its stream starts automatically at boot:

* model enabled: `d6t_temp_c;predicted_temp_c`;
* model disabled: 55 numeric values for the Serial Emulator.

Select the mode with `APP_NEAI_MODEL_ENABLED` in `Inc/app_config.h`. Perform a
clean build and flash the board after each change. See :doc:`validation_ia`
for the full procedure and model replacement.
