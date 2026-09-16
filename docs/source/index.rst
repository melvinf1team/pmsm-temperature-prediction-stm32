PMSM Data Logging and Thermal Prediction on STM32
==================================================

This project covers PMSM motor data acquisition, thermal dataset creation,
preparation for NanoEdge AI Studio, and validation of an extrapolation model
directly on an STM32.

The documented test bench uses a **B-G473E-ZEST1S**, an **STDES-LVHP01** power
board, an **Omron D6T** infrared sensor, and a **DS18B20**. The
`d6t_temp_c` temperature is the target; motor measurements and the
DS18B20 temperature are the explanatory variables.

Recommended reading
-------------------

* :doc:`installation` to prepare Python, STM32CubeIDE, and both firmware projects;
* :doc:`cablage` before applying power;
* :doc:`utilisation` and :doc:`datalogging` to acquire raw CSV files;
* :doc:`pretraitement` to produce the 55 features expected by the model;
* :doc:`validation_ia` to check feature parity, the model, and the serial stream.

.. warning::

  The firmware permits up to 4500 rpm and 30 A. These software limits do not
  certify the motor or power stage. Check the wiring, cooling, mechanical
  mounting, power supply, and shutdown provisions before starting a run.
  Software does not replace the test bench's protective equipment.

Reference configuration
-----------------------

The repository contains two separate firmware projects:

* `firmware_acquisition/tets_motor_dewalt` for control from the dashboard;
* `firmware_validation` for on-device feature computation and inference.

The versioned NanoEdge AI export is a 55-input Ridge regression model with ID
`6a99400cd097fef61cf265dc`. The validation firmware runs at 10 Hz.

.. toctree::
   :maxdepth: 2
   :caption: Project documentation

   sommaire
   etat_projet
   installation
   architecture
   cablage
   utilisation
   datalogging
   pretraitement
   firmware
   validation_ia
   api
