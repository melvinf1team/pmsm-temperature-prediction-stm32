Installation
============

Prérequis logiciels
-------------------

Le projet utilise Python pour le PC et STM32CubeIDE pour l'embarqué.

Installer côté PC :

* Python 3.10 ou plus récent ;
* un environnement capable d'importer ``tkinter`` ;
* STM32CubeIDE pour ouvrir et compiler le firmware ;
* un driver ST-LINK et l'accès au port COM exposé par la carte.

Installation Python
-------------------

Depuis la racine du projet :

.. code-block:: powershell

   python -m venv .venv
   .\.venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt

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

Générer la documentation locale
-------------------------------

.. code-block:: powershell

   python -m sphinx -b html .\docs\source .\docs\build\html

Le HTML généré se trouve ensuite dans ``docs/build/html``.