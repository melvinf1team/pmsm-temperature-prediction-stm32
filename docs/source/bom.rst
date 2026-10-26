Test Bench Bill of Materials (BOM)
==================================

This bill of materials lists the hardware, construction materials, and
wiring for the PMSM temperature prediction test bench. The setup uses the
B-G473E-ZEST1S control board, STDES-LVHP01 power board, and DeWalt motor.
See :doc:`cablage` for the electrical connections and cable sizing
requirements.

Component list
--------------

Indicative unit prices are dated **June 8, 2026**.
They may vary with shipping costs, VAT, and USD-to-EUR exchange rates.

.. list-table:: Test bench components
   :header-rows: 1
   :widths: 26 26 5 18 25

   * - Component
     - Exact reference
     - Qty
     - Unit price
     - Supplier / product URL
   * - PMSM motor, 18 V
     - DeWalt DCD996NT-XJ
     - 1
     - EUR 171.70
     - `DeWalt on Amazon <https://www.amazon.fr/dp/B01MAW4N6B>`__
   * - Motor control MCU board
     - B-G473E-ZEST1S
     - 1
     - ST internal
     - `B-G473E-ZEST1S <https://www.st.com/en/evaluation-tools/b-g473e-zest1s.html>`__
   * - Motor power board
     - STDES-LVHP01
     - 1
     - ST internal
     - `STDES-LVHP01 <https://www.st.com/en/evaluation-tools/stdes-lvhp01.html>`__
   * - Magnetic powder brake, 12 Nm / 24 V
     - AMEHRU PB-B2-1.2
     - 1
     - EUR 189.60
     - `AMEHRU brake on Amazon <https://www.amazon.fr/dp/B0F1Y5YYDD>`__
   * - Infrared temperature sensor
     - Omron D6T-44L-06H
     - 1
     - EUR 55.03
     - `Farnell, order code 3406470 <https://fr.farnell.com/omron/d6t-44l-06h/capteur-ir-therm-5-200-c-i2c/dp/3406470>`__
   * - D6T sensor cable harness
     - D6T-HARNESS-02
     - 1
     - EUR 11.24
     - `Mouser, D6T-HARNESS-02 <https://www.mouser.fr/fr/ProductDetail/Aratas/D6T-HARNESS-02?qs=wd5RIQLrsJgUcCB1RCLBDA%3D%3D>`__
   * - External temperature sensor
     - Analog Devices DS18B20
     - 1
     - EUR 8.11
     - `Farnell, order code 2515553 <https://fr.farnell.com/analog-devices/ds18b20/capteur-de-temp-rature-0-5-c-to/dp/2515553>`__
   * - Voltage controller
     - TB-200S
     - 1
     - EUR 89.79
     - `TB-200S on Amazon <https://www.amazon.fr/dp/B0DFCJ8PM4>`__
   * - Keyed shaft, diameter 12 mm
     - Keyshaft GKS-12-1045
     - 1
     - EUR 34.56
     - `GKS-12-1045 <https://www.raptorsupplies.fr/pd/keyshaft/12mm-gks-1045-300>`__
   * - Keyed shaft, diameter 20 mm
     - Keyshaft GKS-20-1045
     - 1
     - EUR 50.78
     - `GKS-20-1045 <https://www.raptorsupplies.fr/pd/keyshaft/20mm-gks-1045-300>`__
   * - Tapered hub, 12 mm bore
     - EDI tapered hub 1008 (12 mm)
     - 1
     - EUR 6.42
     - `EDI 1008-012 <https://shop.expert-distribution.com/moyeu-amovible-1008-012-c2x31962983>`__
   * - Tapered hub, 20 mm bore
     - EDI tapered hub 1008 (20 mm)
     - 1
     - EUR 6.42
     - `EDI 1008-020 <https://shop.expert-distribution.com/moyeu-amovible-1008-020-c2x31962989>`__
   * - Flexible shaft coupling
     - EDI flexible coupling HRC 70 1008
     - 1
     - EUR 21.74
     - `EDI HRC 70, type FF 1008 <https://shop.expert-distribution.com/accouplement-hrc-70-complet-type-ff-1008-challenge-c2x31961108>`__
   * - Parallel drive shaft wrench
     - Yosoo Gearh87mgpbgzx6579
     - 1
     - EUR 12.54
     - `Yosoo on Amazon <https://www.amazon.fr/dp/B08XM4F1Y5>`__
   * - Power supply, 18 V / 33.3 A
     - MEISHILE ES-18V-600W
     - 1
     - EUR 40.41
     - `MEISHILE power supply on Amazon <https://www.amazon.fr/dp/B0DJQPX9DB>`__

Cost summary
------------

The **15 components** listed above have a priced subtotal of **EUR 698.34**.
The two ST boards have unspecified internal prices and are excluded from
this amount. Additional construction and wiring materials are also excluded.

Additional construction and wiring materials
--------------------------------------------

The acrylic and polypropylene panels are used in the bench construction.
The sensor and DAC components below follow :doc:`cablage`. Cable lengths,
panel dimensions, mounting hardware quantities, and the installed protection
devices must be recorded for the finished assembly.

**Exact procurement references, unit prices, and supplier URLs remain to be
specified for the materials below**. They are excluded from the subtotal.
Quantities marked **To confirm** depend on the finished assembly.

.. list-table:: Construction and wiring materials to complete
   :header-rows: 1
   :widths: 26 34 40

   * - Component
     - Quantity / specification
     - Use / information to record
   * - Acrylic (PMMA / Plexiglas) panels
     - To confirm: count, dimensions, and thickness
     - Bench panels and covers. Record the cut sizes and material reference.
   * - Polypropylene (PP) panels
     - To confirm: count, dimensions, and thickness
     - Bench support and mounting panels. Record the material reference.
   * - Screws, nuts, washers, and spacers
     - To confirm: sizes and quantities
     - Secure the motor, brake, boards, and panels. Record each fastener type.
   * - Motor phase cables
     - Three conductors: U, V, and W. Length and conductor cross-section to confirm.
     - STDES-LVHP01 to motor. Select current, insulation, and temperature ratings as described in :ref:`power-cable-sizing`.
   * - DC power cables
     - Positive and return conductors. Length and conductor cross-section to confirm.
     - 18 V supply output to STDES-LVHP01. Both conductors and their terminals must carry the design current.
   * - Mains supply cable
     - To confirm for the installed 230 V AC supply and its protective-earth connection.
     - Supply input only. Record voltage/current ratings, termination, and strain relief.
   * - Brake power cable
     - Two conductors. Length and conductor cross-section to confirm.
     - TB-200S OUT+/OUT- to the magnetic powder brake. Match the controller and coil ratings.
   * - Low-voltage signal wires
     - To confirm: lengths and connector types
     - D6T I2C, DS18B20 1-Wire, DAC ADJ, sensor supplies, and common signal ground.
   * - USB / PC serial connection cable
     - To confirm: connector type and length
     - Connect the board's PC interface for flashing and serial acquisition.
   * - 4.7 kOhm pull-up resistors
     - Two for D6T SCL/SDA, plus one for DS18B20 DQ if not already fitted.
     - Pull the signal lines up to 3.3 V. Record tolerance, power rating, and package.
   * - 1 kOhm series resistor
     - One
     - PA5/DAC1_OUT2 to TB-200S ADJ. Record tolerance, power rating, and package.
   * - 4.7 microfarad capacitor
     - One, with a voltage rating suitable for the circuit
     - TB-200S ADJ to signal GND. Observe polarity if using a polarized capacitor.
   * - Terminals, cable lugs, and ferrules
     - To confirm for the installed connectors and conductor sizes
     - Power and signal terminations. Record current ratings and compatible crimp tooling.
   * - Heat-shrink tubing, cable ties, and strain relief
     - To confirm: sizes and quantities
     - Insulate terminations and secure cable routing away from moving or hot parts.
   * - Fuses / circuit protection and holders
     - Installed references and ratings to confirm
     - Coordinate protection with cable and connector ratings. See :ref:`power-cable-sizing`.
