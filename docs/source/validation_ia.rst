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
identifiant ``6a79cf7c7fbdd7bdafe35c6c``. Il cible le Cortex-M4 hard-float et
attend un échantillon de 55 axes.

Mode Serial Emulator
--------------------

Avec la valeur ``0U``, aucune fonction NanoEdge n'est appelée. Chaque ligne
contient exactement les 55 features séparées par ``;``, sans cible D6T, header,
timestamp ou texte. Ce format correspond aux colonnes explicatives des CSV
prétraités et peut être lu directement par le Serial Emulator d'extrapolation.

Dans les deux modes, le flux démarre automatiquement au boot à 115200 bauds,
8N1. Le bouton B2 permet de démarrer ou arrêter le profil moteur autonome sans
ajouter de texte sur l'UART.

Vérification
------------

.. code-block:: powershell

   .\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
   .\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
   .\.venv\Scripts\python.exe .\firmware_validation\tests\validate_preprocess_parity.py

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
session peuvent être exportées en CSV avec leur précision complète. Les
dépendances ``pyserial`` et ``matplotlib`` sont déjà déclarées dans
``requirements.txt`` ; Tkinter est fourni avec Python sous Windows.

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
