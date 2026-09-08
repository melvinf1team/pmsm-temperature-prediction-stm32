Validation NanoEdge AI embarquée
================================

Projet dédié
------------

Le dossier ``firmware_validation`` contient un projet STM32CubeIDE autonome. Il
réutilise les acquisitions moteur et capteurs, puis calcule à 10 Hz les 55
features produites hors ligne par ``preprocess_logs_ewma.py``.

Le mode est choisi dans ``firmware_validation/Inc/app_config.h`` :

.. code-block:: c

   #define APP_NEAI_MODEL_ENABLED  1U

Mode modèle activé
------------------

Avec la valeur ``1U``, le firmware initialise ``AI_Model/libneai.a`` et appelle
``neai_extrapolation`` avec les 55 features. Chaque ligne USART1 contient :

.. code-block:: text

   <d6t_temp_c>;<predicted_temp_c>

Le modèle courant est une régression Ridge exportée par NanoEdge AI Studio 5.2,
identifiant ``6a99400cd097fef61cf265dc``. Il cible le Cortex-M4 hard-float et
attend un échantillon de 55 axes. ``AI_Model/metadata.json`` indique :

* score NanoEdge : ``0.9827`` ;
* KPI principal : ``0.9944`` ;
* RAM estimée : 464 octets ;
* Flash estimée : 892 octets.

Ces valeurs décrivent l'évaluation et l'estimation de ressources de l'export.
Elles ne remplacent pas une validation indépendante sur un jeu tenu à l'écart.

Mode Serial Emulator
--------------------

Avec la valeur ``0U``, aucune fonction NanoEdge n'est appelée. Chaque ligne
contient exactement les 55 features séparées par ``;``, sans cible D6T, header,
timestamp ou texte. Ce format correspond aux colonnes explicatives des CSV
prétraités et peut être lu directement par le Serial Emulator d'extrapolation.

Dans les deux modes, le flux démarre automatiquement au boot à 115200 bauds,
8N1. Le bouton B2 démarre un profil à 30 A maximum dont la cible varie doucement
entre 2000 et 4000 rpm, par pas de 200 à 500 rpm toutes les 10 à 30 secondes.

Ordre de validation
-------------------

1. Vérifier les fichiers et le contrat du modèle sans matériel.
2. Comparer le calcul float32 simulé à la référence pandas.
3. Compiler en mode Serial Emulator et contrôler les 55 champs sur la carte.
4. Compiler en mode modèle et contrôler les deux températures.
5. Enregistrer une session indépendante avec l'interface graphique.

Commandes sans matériel :

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py
   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_motor_limits.py
   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_preprocess_parity.py

Commandes avec une carte connectée :

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
   .\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator

État vérifié du dépôt
---------------------

Les contrôles ``validate_neai_export.py`` et ``validate_motor_limits.py``
réussissent avec l'état versionné. Les trois tests unitaires de l'interface de
validation réussissent également. Les builds Debug et Release des deux
firmwares produisent leurs ELF sous STM32CubeIDE 2.1.1 ; les contrôleurs moteur
modifiés compilent sans avertissement.

Le contrôle global ``validate_preprocess_parity.py`` dépasse actuellement sa
tolérance sur ``daq_log_20260827_080523.csv`` : l'erreur relative mise à
l'échelle atteint ``0.000512959`` sur ``speed_power_ewma_6600`` à la ligne
60913, pour une limite fixée à ``0.0005``. Les sept logs précédents passent.
Cette dérive float32 doit être qualifiée avant de modifier le seuil ou
l'algorithme ; le test global n'est donc pas considéré comme passant en l'état.

Interface graphique temps réel
------------------------------

``validation/test/temperature_validation_gui.py`` fournit une vue dédiée au
mode modèle activé. Elle lit les lignes ``D6T;prediction`` à 115200 bauds et
met en avant les deux températures avec une seule décimale.

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
   .\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo

L'erreur instantanée est la valeur absolue de ``prediction - D6T``. L'erreur
cumulée affichée est la MAE de la session, donc la moyenne cumulée des erreurs
absolues. Cette définition conserve l'unité °C et permet d'appliquer les mêmes
seuils aux deux indicateurs : vert sous 0,5 °C, bleu de 0,5 à moins de 1,0 °C,
orange de 1,0 à 1,5 °C et rouge au-dessus de 1,5 °C.

Le graphique conserve les 90 dernières secondes visibles. Les mesures de la
session sont écrites automatiquement dans
``validation/validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv`` et peuvent être
exportées vers un autre chemin avec leur précision complète. Les
dépendances ``pyserial`` et ``matplotlib`` sont déjà déclarées dans
``requirements.txt`` ; Tkinter est fourni avec Python sous Windows.

Sous Windows, une erreur transitoire ``ClearCommError`` est retentée au plus
100 fois avec un délai de 150 ms entre tentatives, soit environ 15 secondes
hors durée des opérations série. Les trames non ASCII, non numériques, non
finies ou ne contenant pas exactement deux champs sont rejetées et comptées.

Remplacement du modèle
----------------------

Remplacer les éléments suivants dans ``firmware_validation/AI_Model`` :

* ``libneai.a`` ;
* ``NanoEdgeAI.h`` ;
* ``metadata.json`` ;
* les JSON de traçabilité dans ``artifacts``.

Vider ``artifacts`` avant la copie pour retirer les paramètres de l'ancien
algorithme. Conserver ``feature_order.txt`` : il représente le contrat d'ordre
des 55 features calculées par le firmware.

Le nouvel export doit cibler un STM32G4 Cortex-M4 avec ABI hard-float, conserver
``NEAI_INPUT_SIGNAL_LENGTH == 1`` et ``NEAI_INPUT_AXIS_NUMBER == 55``, et exposer
``neai_extrapolation_init`` ainsi que ``neai_extrapolation``. Après remplacement,
effectuer un clean build complet avant de reflasher la carte.

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py

Les anciennes valeurs de validation indépendante doivent être accompagnées du
CSV, du découpage apprentissage/test et du script de calcul correspondant. En
l'absence de cet artefact reproductible dans le dépôt, elles ne constituent pas
un critère automatisé de validation.
