Câblage
=======

Cette section indique où connecter les modules externes utilisés par le
firmware de datalogging. Les deux capteurs doivent être alimentés en 3.3 V et
partager la masse de la carte STM32 B-G473E-ZEST1S.

Capteur IR D6T/D6L-44L-06H
--------------------------

Le module IR est lu en I2C logiciel par le firmware. Il est optionnel : si le
module n'est pas branché ou ne répond pas, la carte envoie un avertissement
série et la colonne ``d6t_temp_c`` vaut ``NaN`` dans le CSV.

.. list-table:: Connexion du module IR
   :header-rows: 1

   * - Signal module IR
     - Pin STM32
     - Remarque
   * - SCL
     - PB8
     - Ligne I2C clock en open-drain
   * - SDA
     - PB9
     - Ligne I2C data en open-drain
   * - VCC
     - 3V3
     - Ne pas alimenter en 5 V
   * - GND
     - GND
     - Masse commune

L'adresse I2C attendue par le firmware est ``0x0A``. Des résistances de tirage
de 4.7 kΩ vers 3.3 V sont recommandées sur ``SCL`` et ``SDA`` si le module n'en
intègre pas déjà.

Capteur DS18B20
---------------

Le DS18B20 est utilisé comme capteur de température externe. Sa ligne de données
est câblée en 1-Wire sur ``PG6``.

.. list-table:: Connexion du DS18B20
   :header-rows: 1

   * - Signal DS18B20
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
pas déjà présente sur le module. Le firmware conserve une période minimale de
750 ms pour obtenir une nouvelle mesure DS18B20 fiable.
