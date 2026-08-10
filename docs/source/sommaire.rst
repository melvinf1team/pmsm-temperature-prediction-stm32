Sommaire du projet
==================

Arborescence principale
-----------------------

``datalogging/``
	Interface PC Tkinter et logs CSV bruts. Le script principal est
	``motor_datalog_gui_dashboard.py``. Les acquisitions sont écrites par défaut
	dans ``datalogging/logs``.

``pretraitement/``
	Préparation des logs pour NanoEdge AI Studio. Le script principal est
	``preprocess_logs_ewma.py``. Les CSV traités sont écrits par défaut dans
	``pretraitement/logs_processed_ewma``.

``firmware/``
	Projet STM32CubeIDE et code applicatif embarqué. Il contient le projet MCSDK,
	les fichiers générés par STM32CubeMX/Workbench et les modules utilisateur de
	contrôle, de datalogging et de lecture capteurs.

``docs/``
	Documentation Sphinx locale du projet.

``requirements.txt``
	Dépendances Python nécessaires au dashboard, au prétraitement et à la
	génération de documentation.

Flux fonctionnel
----------------

1. Le firmware attend ``SYNC``, ``CFG``/``START`` pour un essai moteur ou
   ``ACQ_START`` pour une collecte moteur arrêté, puis ``STOP``.
2. Le dashboard ouvre le port série, envoie la séquence choisie et reçoit le
   flux ``#CSV_HEADER`` / ``DATA``.
3. Les logs bruts sont sauvegardés en CSV dans ``datalogging/logs``.
4. Le prétraitement lit ces logs, conserve ``d6t_temp_c`` comme première colonne
	cible, calcule les features physiques et ajoute les EWMA.
5. Les fichiers traités sont importables dans NanoEdge AI Studio pour travailler
	sur une extrapolation de température.
