Prétraitement EWMA
==================

Rôle du script
--------------

``pretraitement/preprocess_logs_ewma.py`` transforme les logs bruts du dashboard
en fichiers adaptés à NanoEdge AI Studio. Par défaut :

* entrée : ``datalogging/logs/daq_log_*.csv`` ;
* sortie : ``pretraitement/logs_processed_ewma`` ;
* séparateur CSV : ``;`` ;
* header activé ou désactivé via ``WRITE_HEADER`` ou les options CLI.

Target NanoEdge AI
------------------

``d6t_temp_c`` est la première colonne du fichier de sortie. Elle représente la
target d'extrapolation et n'est jamais utilisée pour construire des EWMA. Le
script la conserve brute, y compris si la valeur source est ``NaN``.

Colonnes explicatives
---------------------

Les colonnes instantanées utilisées comme features sont :

.. code-block:: text

   ds18b20_temp_c
   motor_ud_v
   motor_uq_v
   motor_speed_mech_rpm
   motor_id_a
   motor_iq_a

Features physiques dérivées
---------------------------

Le script ajoute les grandeurs suivantes :

.. math::

   u_s = \sqrt{u_d^2 + u_q^2}

.. math::

   i_s = \sqrt{i_d^2 + i_q^2}

.. math::

   S_{el} = 1.5 \times u_s \times i_s

.. math::

   speed\_current = motor\_speed\_mech\_rpm \times i_s

.. math::

   speed\_power = motor\_speed\_mech\_rpm \times S_{el}

EWMA
----

Les EWMA sont calculées sur les features instantanées et dérivées. Pour chaque
colonne, le script ajoute une colonne par span. Le calcul pandas utilise :

.. code-block:: python

   series.ewm(span=span, adjust=False).mean()

La fréquence d'acquisition est déduite de la médiane des écarts positifs de
``stm32_time_ms``. Si le timestamp n'est pas utilisable, l'option
``--frequency-hz`` permet de forcer la fréquence.

Options de sortie
-----------------

``--header``
   Écrit les noms de colonnes dans le CSV traité.

``--no-header``
   Supprime les noms de colonnes pour un import compact.

``--include-time``
   Conserve ``stm32_time_ms`` juste après la target.

``--frequency-hz``
   Force la fréquence d'acquisition et donc les spans EWMA.

Nettoyage numérique
-------------------

Les features sont converties en numérique. Les ``inf`` et ``NaN`` côté features
sont remplacés par ``0.0`` pour éviter des fichiers invalides. La target
``d6t_temp_c`` est exclue de ce nettoyage global.