Sommaire du projet
==================

Arborescence principale
-----------------------

``datalogging/``
	Interface Tkinter, profils moteur et logs bruts. Le point d'entrée est
	``motor_datalog_gui_dashboard.py``.

``pretraitement/``
	Préparation des logs pour NanoEdge AI Studio avec
	``preprocess_logs_ewma.py``. Les résultats sont écrits par défaut dans
	``pretraitement/logs_processed_ewma``.

``firmware_acquisition/tets_motor_dewalt/``
	Projet STM32CubeIDE/MCSDK utilisé avec le dashboard. Il reçoit les commandes
	du PC, commande le moteur, échantillonne les capteurs et publie le flux brut.

``firmware_validation/``
	Projet STM32CubeIDE autonome. Il reproduit le prétraitement à 10 Hz et
	publie soit 55 features, soit la mesure D6T et la prédiction du modèle.

``validation/``
	Interface PC de comparaison thermique, tests unitaires associés et exports
	CSV des sessions de validation.

``inventories/``
	Outils de génération d'inventaires sur les journaux et jeux de données.

``docs/``
	Sources Sphinx dans ``docs/source`` et sortie HTML dans ``docs/build/html``.

``dashboard_config.yaml`` et ``preprocess_ewma.yaml``
	Valeurs par défaut des chemins du dashboard et du prétraitement.

``requirements.txt``
	Dépendances Python du dashboard, du prétraitement, des tests et de Sphinx.

Flux fonctionnel
----------------

1. Le firmware d'acquisition attend une séquence de commandes du dashboard.
2. Le dashboard reçoit ``#CSV_HEADER`` puis les lignes ``DATA`` et écrit un CSV
	brut séparé par des points-virgules.
3. Le prétraitement conserve ``d6t_temp_c`` comme cible, calcule cinq grandeurs
	physiques et quatre EWMA pour chacune des onze variables explicatives.
4. Le fichier cible plus 55 features est importé dans NanoEdge AI Studio.
5. L'export du modèle est intégré au firmware de validation.
6. La parité des features puis les sorties série sont contrôlées avant de
	comparer la prédiction à la température D6T.

Fichiers générés et sources de vérité
-------------------------------------

Les dossiers ``datalogging/logs``, ``pretraitement/logs_processed_ewma`` et les
CSV de ``validation`` contiennent des données générées. Les sources de vérité
pour reproduire la chaîne sont :

* les scripts Python et les deux fichiers YAML ;
* les modules applicatifs des deux firmwares ;
* ``firmware_validation/AI_Model/metadata.json`` pour l'identité et les
  caractéristiques de l'export NanoEdge AI ;
* ``firmware_validation/AI_Model/feature_order.txt`` pour l'ordre contractuel
  des 55 features.

Les bibliothèques MCSDK, CMSIS et HAL sont des dépendances générées ou tierces.
La logique propre au projet se trouve principalement dans les dossiers
``STM32CubeIDE/Application/User`` et dans les en-têtes ``Inc``.

Périmètre des vérifications
---------------------------

Les contrôles sans matériel couvrent la parité du prétraitement, la cohérence
de l'export NanoEdge et les calculs de l'interface de validation. Le test du
contrat UART et les builds STM32 nécessitent respectivement une carte connectée
et STM32CubeIDE. Aucun pipeline d'intégration continue n'est versionné.
