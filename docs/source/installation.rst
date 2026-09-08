Installation
============

Prérequis logiciels
-------------------

Le projet utilise Python côté PC et STM32CubeIDE côté embarqué. Prévoir :

* Python 3.10 ou plus récent recommandé, avec ``venv`` et ``tkinter`` ;
* STM32CubeIDE et la chaîne GNU Arm pour Cortex-M4 hard-float ;
* STM32CubeProgrammer et le pilote ST-LINK ;
* NanoEdge AI Studio pour entraîner ou remplacer le modèle ;
* un accès en lecture/écriture au port COM de la carte.

Installation Python
-------------------

Depuis la racine du projet :

.. code-block:: powershell

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt

Si la politique PowerShell interdit l'activation, appeler directement
``.\.venv\Scripts\python.exe`` pour les commandes suivantes.

Configuration des chemins
--------------------------

Le dashboard charge automatiquement ``dashboard_config.yaml``. Les valeurs
peuvent être remplacées avec ``--config`` ou les variables :

* ``PMSM_DATALOG_LOG_DIR`` ;
* ``PMSM_DATALOG_PROFILE_STORE`` ;
* ``PMSM_DATALOG_CSV_PATH``.

Le prétraitement charge ``preprocess_ewma.yaml``. Il accepte également
``--config`` ainsi que :

* ``PMSM_PREPROCESS_INPUT_DIR`` ;
* ``PMSM_PREPROCESS_OUTPUT_DIR`` ;
* ``PMSM_PREPROCESS_PATTERN``.

Les chemins relatifs sont résolus depuis la racine du dépôt, indépendamment du
dossier courant utilisé pour lancer le script.

Lancer le dashboard
-------------------

.. code-block:: powershell

   python .\datalogging\motor_datalog_gui_dashboard.py

Le fichier CSV proposé par défaut est créé dans ``datalogging/logs``. Ce chemin
reste valide même si le script est lancé depuis la racine du projet, depuis le
dossier ``datalogging`` ou depuis VS Code.

Lancer le prétraitement
-----------------------

.. code-block:: powershell

   python .\pretraitement\preprocess_logs_ewma.py

Options utiles :

.. code-block:: powershell

   python .\pretraitement\preprocess_logs_ewma.py --header
   python .\pretraitement\preprocess_logs_ewma.py --no-header
   python .\pretraitement\preprocess_logs_ewma.py --frequency-hz 10
   python .\pretraitement\preprocess_logs_ewma.py --include-time

Importer et compiler les firmwares
----------------------------------

Dans STM32CubeIDE, utiliser **File > Import > Existing Projects into
Workspace**, puis sélectionner l'un des dossiers suivants :

* ``firmware_acquisition/tets_motor_dewalt/STM32CubeIDE`` ;
* ``firmware_validation/STM32CubeIDE``.

Sélectionner la configuration ``Debug`` ou ``Release``, puis exécuter **Clean
Project** et **Build Project**. Programmer la cible avec ST-LINK. Le projet de
validation référence ``AI_Model/libneai.a`` dans les deux configurations.

Après une modification de ``APP_NEAI_MODEL_ENABLED`` ou le remplacement d'un
export NanoEdge, effectuer systématiquement un clean build.

Vérifications sans matériel
---------------------------

Depuis la racine du dépôt :

.. code-block:: powershell

   python .\firmware_validation\tests\validate_preprocess_parity.py
   python .\firmware_validation\tests\validate_neai_export.py
   python .\validation\test\test_temperature_validation_gui.py

Les deux dernières commandes réussissent avec l'état documenté. Le test de
parité dépasse actuellement sa tolérance sur le log du 27 août 2026 ; consulter
:doc:`validation_ia` pour le résultat exact avant de l'utiliser comme critère de
recette.

Le contrôle UART suivant nécessite une carte programmée et adapte le contrat au
mode compilé :

.. code-block:: powershell

   python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
   python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator

Générer la documentation locale
-------------------------------

.. code-block:: powershell

   python -m sphinx -b html .\docs\source .\docs\build\html

Le HTML généré se trouve dans ``docs/build/html/index.html``. Pour traiter les
avertissements Sphinx comme des erreurs lors d'une revue documentaire :

.. code-block:: powershell

   python -m sphinx -W --keep-going -b html .\docs\source .\docs\build\html