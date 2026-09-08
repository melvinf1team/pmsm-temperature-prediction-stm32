Architecture
============

Vue d'ensemble
--------------

Le dépôt sépare le flux d'acquisition piloté par le PC du flux de validation
autonome. Les deux firmwares partagent les mêmes périphériques et mesures, mais
n'ont ni le même protocole d'exploitation ni la même cadence contractuelle.

Responsabilités :

* le firmware d'acquisition pilote le moteur, lit les capteurs, applique les
  sécurités et répond aux commandes du dashboard ;
* le dashboard gère la session série, l'affichage et le CSV brut ;
* le prétraitement transforme les mesures en dataset d'apprentissage ;
* le firmware de validation reproduit les 55 features et appelle, selon sa
  configuration, la bibliothèque NanoEdge AI.

.. code-block:: text

   D6T -----------+                         +--> CSV brut
                  |                         |
   DS18B20 -------+--> firmware acquisition +--> dashboard Tkinter
                  |            ^            |
   MCSDK ---------+            | USART1     +--> affichage temps réel
                               |
                         commandes du PC

   CSV brut --> prétraitement Python --> cible + 55 features --> NanoEdge AI
                                                                   |
                                                                   v
   D6T + DS18B20 + MCSDK --> firmware validation --> features ou prédiction

Firmware d'acquisition
----------------------

Le point d'entrée ``firmware_acquisition/tets_motor_dewalt/Src/main.c``
initialise HAL, les périphériques CubeMX, MCSDK, le datalogging, le contrôle
moteur et le protocole série. La boucle principale appelle ensuite leurs tâches
coopératives.

Les modules applicatifs sont placés dans
``firmware_acquisition/tets_motor_dewalt/STM32CubeIDE/Application/User`` :

* ``app_serial_control.c`` utilise une réception interrompue et une file de
  caractères pour parser les commandes ASCII ;
* ``app_motor_control.c`` applique les rampes, limites et arrêts MCSDK ;
* ``app_datalog.c`` planifie les capteurs et alimente une file de transmission
  UART non bloquante ;
* ``d6t_ir.c`` et ``ds18b20.c`` isolent les protocoles des capteurs.

Le dashboard conserve Tkinter dans le thread principal. Les lectures série et
les séquences de démarrage/arrêt utilisent des threads et communiquent avec
l'interface par des ``queue.Queue``. Cette frontière évite les accès Tkinter
depuis un thread de communication.

Firmware de validation
----------------------

Le point d'entrée ``firmware_validation/Src/main.c`` initialise le contrôle
moteur et le datalogging autonome. La période des features est fixée à 100 ms,
soit 10 Hz, dans ``Inc/preprocess_ewma.h``.

``APP_NEAI_MODEL_ENABLED`` sélectionne le contrat UART à la compilation :

* ``0U`` : 55 valeurs numériques pour le Serial Emulator ;
* ``1U`` : ``d6t_temp_c;predicted_temp_c`` pour l'interface de validation.

Le changement de mode exige un clean build, car la sélection est faite par le
préprocesseur C.

Contrats de données
-------------------

Le firmware d'acquisition annonce les colonnes avec ``#CSV_HEADER`` puis émet
des lignes ``DATA``. Le dashboard ne conserve que les huit colonnes suivantes :

.. code-block:: text

   stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a

Le point-virgule est le séparateur des fichiers écrits par Python. Le protocole
UART du firmware d'acquisition utilise en revanche des virgules. Le dashboard
fait cette conversion lors de l'écriture.

Construction des features
--------------------------

Six mesures explicatives sont utilisées directement : température DS18B20,
tensions ``d/q``, vitesse mécanique et courants ``d/q``. Cinq grandeurs sont
dérivées :

.. math::

   u_s &= \sqrt{u_d^2 + u_q^2} \\
   i_s &= \sqrt{i_d^2 + i_q^2} \\
   S_{el} &= 1.5\,u_s i_s \\
   speed\_current &= n\,i_s \\
   speed\_power &= n\,S_{el}

Chaque grandeur parmi ces onze signaux est conservée instantanément et déclinée
avec quatre EWMA : :math:`11 \times (1 + 4) = 55` features. La cible D6T ne
reçoit aucune EWMA.

Règle de fréquence des EWMA
---------------------------

Les spans historiques ``1320``, ``3360``, ``6360`` et ``9480`` correspondent à
un datalogging de référence à 2 Hz. Pour conserver les mêmes constantes de temps
lorsque la période DATA change, le prétraitement applique :

.. math::

   span_{nouveau} = span_{2Hz} \times \frac{f_{acquisition}}{2}

Un log à 10 Hz utilise donc les spans ``6600``, ``16800``, ``31800`` et
``47400``.

Le script Python déduit :math:`f_{acquisition}` de la médiane des écarts
strictement positifs de ``stm32_time_ms``. Le firmware de validation utilise
directement les spans à 10 Hz. Le test de parité vérifie l'ordre et la
récurrence entre les deux implémentations.

Limites d'architecture
----------------------

* Il n'existe pas de build firmware autonome versionné hors STM32CubeIDE.
* Le changement de modèle est valide seulement si les 55 axes, leur ordre,
   l'ABI hard-float et l'API NanoEdge restent compatibles.
* La remise à l'échelle Python suppose une cadence représentative dans les
   timestamps ; une fréquence forcée incorrecte modifie la mémoire temporelle
   des EWMA.
* Le remplacement global des valeurs manquantes par zéro peut masquer une
   mesure D6T absente ; ce point doit être contrôlé avant l'apprentissage.
