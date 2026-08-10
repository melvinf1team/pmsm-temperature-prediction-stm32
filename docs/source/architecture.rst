Architecture
============

Vue d'ensemble
--------------

Le projet sépare clairement les responsabilités :

* le firmware produit les mesures et applique les sécurités moteur ;
* le dashboard PC pilote la session et enregistre les données brutes ;
* le prétraitement prépare un dataset orienté NanoEdge AI Studio.

.. code-block:: text

   STM32 firmware
      ├─ capteur D6T -> d6t_temp_c target
      ├─ capteur DS18B20 -> ds18b20_temp_c feature
      ├─ MCSDK -> tensions, courants, vitesse
      └─ UART texte -> #CSV_HEADER / DATA

   Python datalogging
      ├─ moteur : SYNC / CFG / START / STOP
      ├─ arrêt : SYNC / ACQ_START / STOP
      ├─ affichage live Tkinter
      └─ CSV brut dans datalogging/logs

   Python prétraitement
      ├─ détection fréquence avec stm32_time_ms
      ├─ features physiques dérivées
      ├─ EWMA remises à l'échelle
      └─ CSV NanoEdge dans pretraitement/logs_processed_ewma

Règle de fréquence des EWMA
---------------------------

Les spans historiques ``1320``, ``3360``, ``6360`` et ``9480`` correspondent à
un datalogging de référence à 2 Hz. Pour conserver les mêmes constantes de temps
lorsque la période DATA change, le prétraitement applique :

.. math::

   span_{nouveau} = span_{2Hz} \times \frac{f_{acquisition}}{2}

Un log à 10 Hz utilise donc les spans ``6600``, ``16800``, ``31800`` et
``47400``.

Contrats de fichiers
--------------------

Le dashboard écrit des CSV séparés par ``;`` avec un header. Le prétraitement
garde le même séparateur. L'option ``WRITE_HEADER`` ou les arguments
``--header`` / ``--no-header`` permettent de choisir si le fichier traité doit
inclure les noms de colonnes.

Les sorties générées ne sont pas destinées à être versionnées. Elles sont
reproductibles à partir des logs bruts et du script de prétraitement.
