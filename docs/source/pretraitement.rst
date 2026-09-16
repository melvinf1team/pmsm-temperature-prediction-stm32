EWMA Preprocessing
==================

Script purpose
--------------

`pretraitement/preprocess_logs_ewma.py` converts raw dashboard logs into files
suitable for NanoEdge AI Studio. By default:

* input: `datalogging/logs/daq_log_*.csv`;
* output: `pretraitement/logs_processed_ewma`;
* CSV separator: `;`;
* header: disabled;
* timestamp: omitted from output.

NanoEdge AI target
------------------

`d6t_temp_c` is the first column of the output file. It is the extrapolation
target and is never used to build EWMAs. The script does not convert it to a
number: a `NaN` string or empty cell read with `keep_default_na=False` stays
unchanged. Check or filter those rows before training.

Explanatory columns
-------------------

The instantaneous input features are:

.. code-block:: text

   ds18b20_temp_c
   motor_ud_v
   motor_uq_v
   motor_speed_mech_rpm
   motor_id_a
   motor_iq_a

Derived physical features
-------------------------

The script adds these quantities:

.. math::

   u_s = \sqrt{u_d^2 + u_q^2}

.. math::

   i_s = \sqrt{i_d^2 + i_q^2}

.. math::

   S_{el} = 1.5 \times u_s \times i_s

.. math::

   speed\_current = motor\_speed\_mech\_rpm \times i_s

.. math::

   speed\_power = motor\_speed\_mech\_rpm \times S_{el}

Output dimensions and order
---------------------------

The eleven explanatory variables are the six raw inputs followed by the five
derived quantities. For each variable, the file contains its instantaneous
value and four EWMAs, giving :math:`11 \times 5 = 55` features.

The order is deterministic:

.. code-block:: text

   d6t_temp_c
   [stm32_time_ms if --include-time]
   ds18b20_temp_c, then its 4 EWMAs
   motor_ud_v, then its 4 EWMAs
   motor_uq_v, then its 4 EWMAs
   motor_speed_mech_rpm, then its 4 EWMAs
   motor_id_a, then its 4 EWMAs
   motor_iq_a, then its 4 EWMAs
   u_s, then its 4 EWMAs
   i_s, then its 4 EWMAs
   S_el, then its 4 EWMAs
   speed_current, then its 4 EWMAs
   speed_power, then its 4 EWMAs

`stm32_time_ms` is not a model input. With `--include-time`, the file
therefore contains one target, an informational timestamp, and 55 features.

EWMA
----

EWMAs are computed from instantaneous and derived features. The script adds
one column per span for each input column. The pandas calculation is:

.. code-block:: python

   series.ewm(span=span, adjust=False).mean()

The acquisition rate is derived from the median of positive differences in
`stm32_time_ms`. If the timestamp cannot be used, `--frequency-hz` can
set the frequency explicitly.

Spans are scaled from the 2 Hz reference:

.. math::

   span = \max\left(1,\operatorname{round}\left(span_{2Hz}
          \frac{f_{\mathrm{acquisition}}}{2}\right)\right)

The four reference spans are `1320`, `3360`, `6360`, and `9480`. At 10 Hz,
they become `6600`, `16800`, `31800`, and `47400`.

Output options
--------------

`--header`
   Writes column names in the processed CSV file.

`--no-header`
   Omits column names for compact import.

`--include-time`
   Keeps `stm32_time_ms` immediately after the target.

`--frequency-hz`
   Sets the acquisition rate and therefore the EWMA spans.

`--input-dir` and `--output-dir`
   Override the directories in `preprocess_ewma.yaml`.

`--pattern`
   Overrides the `daq_log_*.csv` pattern.

`--config`
   Loads an explicit YAML file.

Equivalent environment variables are `PMSM_PREPROCESS_INPUT_DIR`,
`PMSM_PREPROCESS_OUTPUT_DIR`, and `PMSM_PREPROCESS_PATTERN`. Even with
`--frequency-hz`, the input CSV must contain `stm32_time_ms`.

Numeric cleanup
---------------

`stm32_time_ms` and the six explanatory inputs are converted with
`errors="coerce"`. Derived values and EWMAs are then computed, infinite
values become missing, and missing numeric values are replaced with `0.0`.

The target is deliberately excluded from numeric conversion. With the
current read options, invalid text markers in it are therefore not replaced
by `fillna(0.0)`. This preserves evidence of a missing D6T measurement but
requires an explicit check before import.

Reproducibility and precautions
-------------------------------

The output file has the same name as the input file. Another run in the same
directory therefore overwrites the previous result. To preserve a dataset:

1. keep the raw CSV files and YAML configuration used;
2. record any forced frequency and the header setting;
3. check the number and order of columns;
4. compare the order with the 55 lines in
   `firmware_validation/AI_Model/feature_order.txt`;
5. archive metrics with the corresponding NanoEdge export.
