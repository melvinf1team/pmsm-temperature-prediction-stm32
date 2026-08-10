Utilisation
===========

Séquence recommandée
--------------------

1. Flasher le firmware STM32 depuis STM32CubeIDE.
2. Brancher les capteurs et vérifier le port COM de la carte.
3. Lancer ``datalogging/motor_datalog_gui_dashboard.py``.
4. Sélectionner le port COM, le baudrate et le mode de session.
5. En mode moteur, choisir le profil ; en mode collecte seule, aucun paramètre
   moteur n'est requis.
6. Choisir la période ``DATA`` et la période DS18B20, puis lancer la session.
7. Arrêter la session avec ``STOP``.
8. Exécuter ``pretraitement/preprocess_logs_ewma.py`` pour générer les fichiers
   exploitables par NanoEdge AI Studio.

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
   n'est pas lissée.

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

Import NanoEdge AI Studio
-------------------------

Après prétraitement, le fichier CSV commence par ``d6t_temp_c``. Cette première
colonne doit être utilisée comme target d'extrapolation. Les autres colonnes
représentent les features instantanées, dérivées et lissées par EWMA.
