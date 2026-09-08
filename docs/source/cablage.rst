Câblage
=======

Le montage cible une carte STM32 B-G473E-ZEST1S associée à une power board
STDES-LVHP01. Les capteurs externes partagent la masse de la carte et doivent
être alimentés à la tension requise par leur fiche technique.

.. danger::

  Couper l'alimentation de puissance avant toute modification du câblage.
  Vérifier la révision des cartes, le brochage des connecteurs et les niveaux
  électriques dans leurs manuels officiels. Les repères CN ci-dessous décrivent
  le banc de ce dépôt et ne remplacent pas les schémas constructeur.

Capteur IR D6T
--------------

La colonne ``d6t_temp_c`` est la target du dataset NanoEdge AI. Le firmware lit
un pixel du module IR D6T-44L-06 par I2C logiciel sur ``PB6`` et ``PB9``.

+-------------+--------+-----------+-----------+--------------------------------+
| Broche D6T  | Signal | Pin STM32 | Carte     | Remarque                       |
+=============+========+===========+===========+================================+
| 4           | SCL    | PB6       | CN10-27   | Ligne I2C clock open-drain     |
+-------------+--------+-----------+-----------+--------------------------------+
| 3           | SDA    | PB9       | CN10-24   | CN10-26 est le même signal PB9 |
+-------------+--------+-----------+-----------+--------------------------------+
| 2           | VCC    | -         | CN7-18    | Alimentation +5 V              |
+-------------+--------+-----------+-----------+--------------------------------+
| 1           | GND    | -         | CN7-20    | CN7-22 convient également      |
+-------------+--------+-----------+-----------+--------------------------------+

Ajouter une résistance de tirage de 4,7 kΩ entre ``SCL`` et ``3.3V``, et une
seconde entre ``SDA`` et ``3.3V``. Les broches ``PB6`` et ``PB9`` sont utilisées
en sortie open-drain et sans pull-up interne. Ne pas tirer ces lignes vers 5 V
sans avoir vérifié la tolérance des entrées utilisées. Les deux positions
``PB9`` visibles sur la carte correspondent au même signal, routé vers
``CN10-24`` et ``CN10-26``
pour la compatibilité avec plusieurs cartes d'extension moteur.

L'adresse I2C attendue est ``0x0A``. Le firmware lit la commande ``0x4C`` et
vérifie le PEC de la trame. Le pixel journalisé est configuré par
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

Ajouter une résistance de tirage de 4,7 kΩ entre ``DQ`` et ``3V3`` si elle n'est
pas déjà présente. Le firmware force une période minimale de 750 ms pour rester
compatible avec la conversion 12 bits du DS18B20.

UART PC
-------

Le dashboard communique avec la carte via ``USART1`` exposé côté PC comme port
COM. Le baudrate par défaut est ``115200``. Le protocole applicatif est textuel,
ligne par ligne, afin de faciliter le diagnostic dans un terminal série.

Dans les deux firmwares, l'application reprend l'USART1 au protocole ASPEP. Ne
pas ouvrir simultanément le même port dans Motor Pilot, un terminal et le
dashboard : un seul processus PC doit posséder le port COM.

Contrôle avant mise sous tension
--------------------------------

* Vérifier la masse commune et l'absence de court-circuit entre alimentation
  capteur, ``3V3``, ``5V`` et masse.

* Confirmer les résistances de tirage avec la carte hors tension.

* Vérifier que le moteur et la carte de puissance sont mécaniquement sécurisés.

* Alimenter d'abord la logique et confirmer l'apparition du port COM.

* Contrôler les messages de démarrage et la présence de mesures capteur avant
  d'autoriser une séquence moteur.

Un D6T absent produit ``NaN``. Le DS18B20 peut conserver sa dernière valeur
valide après un échec ponctuel ; une valeur stable ne prouve donc pas à elle
seule que chaque conversion 1-Wire réussit.
