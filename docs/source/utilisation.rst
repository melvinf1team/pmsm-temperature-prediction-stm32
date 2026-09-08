Utilisation
===========

Séquence recommandée
--------------------

1. Couper la puissance et contrôler le montage décrit dans :doc:`cablage`.
2. Compiler et flasher le firmware d'acquisition depuis STM32CubeIDE.
3. Alimenter la logique, brancher les capteurs et identifier le port COM.
4. Lancer ``datalogging/motor_datalog_gui_dashboard.py``.
5. Sélectionner le port, 115200 bauds et le mode de session.
6. En mode moteur, choisir ou créer un profil ; en collecte seule, renseigner
    uniquement les périodes.
7. Vérifier le chemin du CSV, puis lancer la session.
8. Arrêter avec le bouton du dashboard, qui envoie ``STOP`` avant de fermer les
    ressources.
9. Contrôler le CSV brut avant d'exécuter le prétraitement.

Plages opératoires
------------------

Le dashboard applique les plages suivantes. Le firmware reste l'autorité
finale pour les commandes reçues directement sur l'UART :

.. list-table:: Paramètres d'acquisition
    :header-rows: 1

    * - Paramètre
      - Plage
      - Remarque
    * - Vitesse cible
      - 100 à 2500 rpm
      - Une valeur inférieure à 100 rpm est refusée par le firmware
    * - Limite ``Iq``
      - strictement positive à 12 A
      - La montée démarre au plus à 4,5 A puis suit une rampe
    * - Arrêt sur courant total
      - strictement positif à 14 A
      - Protection applicative en plus des défauts MCSDK
    * - Accélération
      - strictement positive à 50 Hz électriques/s
      - Limite effectivement appliquée par le contrôle moteur
    * - Période ``DATA``
      - 1 à 10 000 ms
      - Détermine la cadence du CSV brut
    * - Période DS18B20
      - 750 à 10 000 ms
      - Le dashboard refuse une valeur inférieure ; le firmware la ramène à 750 ms

Colonnes brutes du dashboard
----------------------------

Le firmware annonce les colonnes avec ``#CSV_HEADER``. Le dashboard conserve les
colonnes suivantes dans cet ordre :

.. code-block:: text

   stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a

``stm32_time_ms``
   Timestamp carte en millisecondes. Il sert au prétraitement pour déduire la
   fréquence réelle d'acquisition.

``d6t_temp_c``
   Température IR cible. Elle devient la première colonne du fichier traité et
   n'est pas lissée. Une mesure indisponible est publiée sous la forme ``NaN``.

``ds18b20_temp_c``
   Température externe de référence, conservée comme feature.

``motor_ud_v`` et ``motor_uq_v``
   Tensions d/q reconstruites depuis la sortie de modulation et le bus DC.

``motor_speed_mech_rpm``
   Vitesse mécanique en rpm.

``motor_id_a`` et ``motor_iq_a``
   Courants d/q moteur en ampères.

Protocole série
---------------

Commandes envoyées par le dashboard :

.. code-block:: text

   SYNC
   CFG,<target_rpm>,<iq_limit_a>,<hard_limit_a>,<accel_elec_hz_s>,<datalog_ms>,<ds18b20_ms>
   START
   ACQ_START,<datalog_ms>,<ds18b20_ms>
   STOP

Réponses et messages attendus :

.. code-block:: text

   ACK,SYNC
   ACK,CFG
   ACK,START
   ACK,ACQ_START
   ACK,STOP
   ERR,<raison>
   #CSV_HEADER,<colonnes>
   DATA,<valeurs>

Le dashboard ignore les lignes ``DATA`` reçues avant ``#CSV_HEADER`` afin de ne
pas écrire un CSV incohérent.

``ACQ_START`` est utilisé seul après ``SYNC`` pour enregistrer un refroidissement
moteur arrêté. Dans cet état, le firmware publie explicitement zéro pour les
tensions, courants et vitesse afin de ne pas réutiliser le dernier échantillon
MCSDK mémorisé avant l'arrêt.

Arrêt et reprise après erreur
-----------------------------

En cas de ``ERR``, de timeout d'acquittement ou de perte série, arrêter la
session et vérifier le journal du dashboard. Fermer les autres logiciels qui
utilisent le port, rétablir la liaison, puis relancer une session complète à
partir de ``SYNC``. Ne pas concaténer manuellement un fichier incomplet avec une
nouvelle acquisition : conserver des sessions séparées facilite le contrôle des
timestamps.

Avant prétraitement, vérifier au minimum :

* la présence des huit colonnes attendues ;
* une progression majoritairement monotone de ``stm32_time_ms`` ;
* la cadence et la durée de l'essai ;
* les lignes ``NaN`` ou vides sur la cible D6T ;
* la cohérence des unités et l'absence de saturation évidente.

Import NanoEdge AI Studio
-------------------------

Après prétraitement, le fichier CSV commence par ``d6t_temp_c``. Cette première
colonne doit être utilisée comme target d'extrapolation. Les autres colonnes
représentent les features instantanées, dérivées et lissées par EWMA.

Par défaut, le fichier traité n'a pas d'en-tête. Utiliser ``--header`` pour
l'inspecter, puis vérifier l'ordre final avec
``firmware_validation/AI_Model/feature_order.txt`` avant de remplacer le modèle.

Validation embarquée
--------------------

Le projet ``firmware_validation`` ne se pilote pas avec les commandes du
dashboard. Son flux commence automatiquement au boot :

* modèle activé : ``d6t_temp_c;predicted_temp_c`` ;
* modèle désactivé : 55 valeurs numériques pour le Serial Emulator.

Le choix se fait avec ``APP_NEAI_MODEL_ENABLED`` dans ``Inc/app_config.h``.
Effectuer un clean build et reflasher la carte après chaque changement. La page
:doc:`validation_ia` décrit la procédure complète et le remplacement du modèle.
