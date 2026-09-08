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

Lancement et configuration
--------------------------

Depuis la racine du dépôt :

.. code-block:: powershell

    python .\datalogging\motor_datalog_gui_dashboard.py

``dashboard_config.yaml`` est chargé automatiquement. Un autre fichier peut
être fourni avec ``--config``. Les options de chemin sont :

.. list-table:: Configuration du dashboard
    :header-rows: 1

    * - Option
      - Variable d'environnement
      - Valeur par défaut
    * - ``--log-dir``
      - ``PMSM_DATALOG_LOG_DIR``
      - ``datalogging/logs``
    * - ``--profile-store``
      - ``PMSM_DATALOG_PROFILE_STORE``
      - ``datalogging/motor_profiles.json``
    * - ``--csv-path``
      - ``PMSM_DATALOG_CSV_PATH``
      - fichier horodaté dans le dossier des logs

Les options de ligne de commande ont priorité sur les variables
d'environnement et les fichiers de configuration. Les chemins relatifs sont
résolus depuis la racine du projet.

Configuration moteur
--------------------

La classe ``MotorProfile`` décrit les paramètres envoyés à la carte : vitesse,
unité de vitesse, limite ``Iq``, seuil hard stop, accélération, période DATA et
période DS18B20. Les profils intégrés et ceux sauvegardés par l'utilisateur sont
chargés au démarrage du dashboard.

Le profil intégré ``Personnalisé`` fournit une base de 600 rpm, 2 A sur ``Iq``,
8 A de hard stop, 5 Hz électriques/s, 100 ms pour ``DATA`` et 1000 ms pour le
DS18B20. Les profils ajoutés dans l'interface sont sérialisés en JSON. Un fichier
absent recrée simplement le profil intégré ; un contenu invalide est signalé
dans le journal de l'interface.

Validation utilisateur
----------------------

Avant le lancement, ``validate_form`` contrôle le port COM, le baudrate, les
périodes et le chemin CSV. En mode moteur, il contrôle également la vitesse, les
paires de pôles, les limites courant et l'accélération. Les champs moteur sont
désactivés et ignorés en mode collecte seule. Le dashboard signale notamment
qu'un DS18B20 ne peut pas fournir une nouvelle mesure fiable sous 750 ms.

Le dashboard et le parseur firmware appliquent les mêmes bornes : 100 à
4500 rpm, 30 A maximum pour ``Iq`` et le hard stop, et 50 Hz électriques/s
maximum pour l'accélération. Les valeurs hors plage sont refusées avant le
démarrage. Les détails et précautions sont donnés dans :doc:`utilisation`.

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
Le tampon du fichier est vidé au plus tard toutes les dix lignes ou toutes les
secondes, puis une dernière fois lors de la fermeture propre de la session.

Modèle d'exécution
------------------

Tkinter et les mises à jour graphiques restent dans le thread principal. Un
thread lit le port série ; les séquences de lancement et d'arrêt utilisent des
threads distincts. Des files ``queue.Queue`` transportent les événements GUI et
les acquittements sans accès concurrent direct aux widgets.

Le graphe conserve au maximum 1500 points par série et se rafraîchit toutes les
250 ms. L'absence de Matplotlib ne bloque pas l'acquisition : seule la zone de
tracé est indisponible.

Chemins
-------

``default_csv_path`` construit un nom ``daq_log_YYYYMMDD_HHMMSS.csv`` dans
``datalogging/logs`` à partir du chemin du script, pas du répertoire courant. Le
comportement est donc identique depuis VS Code, PowerShell ou un raccourci.

Diagnostic
----------

* Aucun port : vérifier ST-LINK/VCP, le câble USB et fermer les autres clients.
* ``ERR`` après ``CFG`` : contrôler les bornes, notamment 100 rpm minimum et
   750 ms minimum pour le DS18B20.
* ``DATA`` sans CSV : rechercher d'abord ``#CSV_HEADER`` dans le journal.
* Mesures D6T à ``NaN`` : vérifier l'alimentation, les pull-up et le PEC I2C.
* Arrêt brutal du programme : considérer le dernier bloc tamponné comme
   potentiellement incomplet et repartir dans un nouveau fichier.
