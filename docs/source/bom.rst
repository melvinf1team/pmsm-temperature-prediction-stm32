Test Bench Bill of Materials (BOM)
==================================

This bill of materials reproduces the component references, quantities,
prices, and supplier links from slide 12, **Test bench BOM**, of
*PMSM motor internal temperature prediction AIS CoE.pptx*.
See :doc:`cablage` for the electrical connections.

Component list
--------------

Prices below are the amounts recorded in the presentation on **June 5, 2026**.
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
     - EUR 171.97
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
     - EUR 60.81
     - `Farnell, order code 3406470 <https://fr.farnell.com/omron/d6t-44l-06h/capteur-ir-therm-5-200-c-i2c/dp/3406470>`__
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
   * - Flexible shaft coupling set
     - McMaster-Carr 9939T41
     - 1 set
     - USD 183.87 (approximately EUR 157.96)
     - `McMaster-Carr coupling catalogue <https://www.mcmaster.com/products/couplings/shaft-diameter~20-mm/set-screw-vibration-damping-precision-flexible-shaft-couplings~~/shaft-diameter~12-mm/>`__
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

The presentation lists **11 items**, counting the coupling set as one item.
The total of the nine items with numeric prices is **EUR 808.42**, using the
presentation's approximate EUR 157.96 equivalent for the coupling set.

The two ST boards are marked **ST internal** in the source. Their prices
are unspecified and excluded from this total.

For the coupling set, use ordering reference **9939T41**. The hyperlink in
the presentation opens the McMaster-Carr catalogue filtered for 12 mm and
20 mm shafts.

Additional wiring components
----------------------------

The wiring documented in :doc:`cablage` also uses a **DS18B20 temperature
sensor**, **4.7 kOhm pull-up resistors**, a **1 kOhm resistor**, and a
**4.7 microfarad capacitor**. The presentation's BOM does not provide their
exact procurement references, prices, or supplier URLs. Add these details
when available. These components are excluded from the total above.
