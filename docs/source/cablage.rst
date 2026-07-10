Câblage
=======

Le montage cible une carte STM32 B-G473E-ZEST1S associée à une power board
STDES-LVHP01. Les capteurs externes partagent la masse de la carte et doivent
être alimentés en 3.3 V.

Capteur IR D6T
--------------

La colonne ``d6t_temp_c`` est la target du dataset NanoEdge AI. Le firmware lit
un pixel du module IR D6T par I2C logiciel sur ``PB8`` et ``PB9``.

+--------+-----------+-------------------------------+
| Signal | Pin STM32 | Remarque                      |
+========+===========+===============================+
| SCL    | PB8       | Ligne I2C clock open-drain    |
+--------+-----------+-------------------------------+
| SDA    | PB9       | Ligne I2C data open-drain     |
+--------+-----------+-------------------------------+
| VCC    | 3V3       | Ne pas alimenter en 5 V       |
+--------+-----------+-------------------------------+
| GND    | GND       | Masse commune                 |
+--------+-----------+-------------------------------+

L'adresse I2C attendue est ``0x0A``. Le firmware lit la commande ``0x4C`` et
vérifie le PEC du frame. Le pixel loggé est configuré par
``D6TIR_SELECTED_PIXEL_INDEX`` dans ``d6t_ir.c``. Si le capteur est absent ou si
aucune mesure valide n'a encore été reçue, la valeur CSV est ``NaN``.

Capteur DS18B20
---------------

Le DS18B20 fournit une température externe utilisée comme feature explicative.
Il est connecté en 1-Wire sur ``PG6``.

.. list-table:: Connexion DS18B20
   :header-rows: 1

   * - Signal
     - Pin STM32
     - Remarque
   * - DQ
     - PG6
     - Ligne 1-Wire, open-drain
   * - VCC
     - 3V3
     - Alimentation capteur
   * - GND
     - GND
     - Masse commune

Ajouter une résistance de tirage de 4.7 kΩ entre ``DQ`` et ``3V3`` si elle n'est
pas déjà présente. Le firmware force une période minimale de 750 ms pour rester
compatible avec la conversion 12 bits du DS18B20.

UART PC
-------

Le dashboard communique avec la carte via ``USART1`` exposé côté PC comme port
COM. Le baudrate par défaut est ``115200``. Le protocole applicatif est textuel,
ligne par ligne, afin de faciliter le diagnostic dans un terminal série.
