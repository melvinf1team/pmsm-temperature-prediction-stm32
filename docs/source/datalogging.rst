Dashboard de datalogging
========================

Rôle du script
--------------

``datalogging/motor_datalog_gui_dashboard.py`` fournit l'interface PC du banc.
Il combine :

* une interface Tkinter sombre avec cartes de valeurs instantanées ;
* la gestion des profils moteur dans ``motor_profiles.json`` ;
* la détection et l'ouverture du port série ;
* l'envoi des commandes ``SYNC``, ``CFG``, ``START``, ``ACQ_START`` et ``STOP`` ;
* la réception du flux UART ;
* l'écriture CSV dans ``datalogging/logs`` ;
* un graphe temps réel si ``matplotlib`` est disponible.

Configuration moteur
--------------------

La classe ``MotorProfile`` décrit les paramètres envoyés à la carte : vitesse,
unité de vitesse, limite ``Iq``, seuil hard stop, accélération, période DATA et
période DS18B20. Les profils intégrés et ceux sauvegardés par l'utilisateur sont
chargés au démarrage du dashboard.

Validation utilisateur
----------------------

Avant le lancement, ``validate_form`` contrôle le port COM, le baudrate, les
périodes et le chemin CSV. En mode moteur, il contrôle également la vitesse, les
paires de pôles, les limites courant et l'accélération. Les champs moteur sont
désactivés et ignorés en mode collecte seule. Le dashboard signale notamment
qu'un DS18B20 ne peut pas fournir une nouvelle mesure fiable sous 750 ms.

Séquence de lancement
---------------------

``start_run`` valide la configuration, prépare le fichier CSV, ouvre le port
série et démarre le thread lecteur. ``launch_sequence_thread`` envoie ensuite :

.. code-block:: text

   SYNC
   CFG,<rpm>,<iq_limit>,<hard_limit>,<accel>,<datalog_ms>,<ds18b20_ms>
   START

Chaque commande attend un ``ACK`` ou un ``ERR``. En cas d'échec, le dashboard
remonte l'erreur et ferme proprement les ressources.

En mode ``Collecte seule (moteur arrêté)``, la séquence devient :

.. code-block:: text

   SYNC
   ACQ_START,<datalog_ms>,<ds18b20_ms>

Cette commande ne dépend d'aucun ``CFG`` moteur et force le moteur à l'arrêt
avant d'armer le logger.

Réception UART et CSV
---------------------

``handle_serial_line`` route les lignes reçues :

* ``#CSV_HEADER`` ouvre le CSV et fixe l'ordre des colonnes ;
* ``DATA`` écrit une ligne dans le CSV, met à jour les cartes live et alimente le
  graphe ;
* ``ACK`` et ``ERR`` servent à synchroniser les threads de commande ;
* les autres lignes ``#`` restent des messages de diagnostic firmware.

Le dashboard écrit uniquement ``CSV_OUTPUT_COLUMNS`` afin que les fichiers bruts
gardent un format stable même si le firmware ajoute des colonnes de diagnostic.

Chemins
-------

``default_csv_path`` construit un nom ``daq_log_YYYYMMDD_HHMMSS.csv`` dans
``datalogging/logs`` à partir du chemin du script, pas du répertoire courant. Le
comportement est donc identique depuis VS Code, PowerShell ou un raccourci.
