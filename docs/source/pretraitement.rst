Prétraitement EWMA
==================

Rôle du script
--------------

``pretraitement/preprocess_logs_ewma.py`` transforme les logs bruts du dashboard
en fichiers adaptés à NanoEdge AI Studio. Par défaut :

* entrée : ``datalogging/logs/daq_log_*.csv`` ;
* sortie : ``pretraitement/logs_processed_ewma`` ;
* séparateur CSV : ``;`` ;
* en-tête désactivé ;
* timestamp exclu de la sortie.

Target NanoEdge AI
------------------

``d6t_temp_c`` est la première colonne du fichier de sortie. Elle représente la
target d'extrapolation et n'est jamais utilisée pour construire des EWMA. Le
script ne la convertit pas en numérique : une chaîne ``NaN`` ou une cellule
vide lue avec ``keep_default_na=False`` est conservée telle quelle. Ces lignes
doivent être contrôlées ou filtrées avant l'apprentissage.

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

Dimension et ordre de sortie
----------------------------

Les onze variables explicatives sont les six entrées brutes suivies des cinq
grandeurs dérivées. Pour chacune, le fichier contient la valeur instantanée,
puis ses quatre EWMA. Il contient donc :math:`11 \times 5 = 55` features.

L'ordre est déterministe :

.. code-block:: text

   d6t_temp_c
   [stm32_time_ms si --include-time]
   ds18b20_temp_c, puis ses 4 EWMA
   motor_ud_v, puis ses 4 EWMA
   motor_uq_v, puis ses 4 EWMA
   motor_speed_mech_rpm, puis ses 4 EWMA
   motor_id_a, puis ses 4 EWMA
   motor_iq_a, puis ses 4 EWMA
   u_s, puis ses 4 EWMA
   i_s, puis ses 4 EWMA
   S_el, puis ses 4 EWMA
   speed_current, puis ses 4 EWMA
   speed_power, puis ses 4 EWMA

``stm32_time_ms`` n'est pas une entrée du modèle. Avec ``--include-time``, le
fichier contient donc une cible, un timestamp informatif et 55 features.

EWMA
----

Les EWMA sont calculées sur les features instantanées et dérivées. Pour chaque
colonne, le script ajoute une colonne par span. Le calcul pandas utilise :

.. code-block:: python

   series.ewm(span=span, adjust=False).mean()

La fréquence d'acquisition est déduite de la médiane des écarts positifs de
``stm32_time_ms``. Si le timestamp n'est pas utilisable, l'option
``--frequency-hz`` permet de forcer la fréquence.

Les spans sont remis à l'échelle depuis la référence à 2 Hz :

.. math::

   span = \max\left(1,\operatorname{round}\left(span_{2Hz}
          \frac{f_{acquisition}}{2}\right)\right)

Les quatre références sont ``1320``, ``3360``, ``6360`` et ``9480``. À 10 Hz,
les spans produits sont ``6600``, ``16800``, ``31800`` et ``47400``.

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

``--input-dir`` et ``--output-dir``
   Remplacent les dossiers configurés dans ``preprocess_ewma.yaml``.

``--pattern``
   Remplace le motif ``daq_log_*.csv``.

``--config``
   Charge un fichier YAML explicite.

Les variables d'environnement équivalentes sont
``PMSM_PREPROCESS_INPUT_DIR``, ``PMSM_PREPROCESS_OUTPUT_DIR`` et
``PMSM_PREPROCESS_PATTERN``. Même avec ``--frequency-hz``, la colonne
``stm32_time_ms`` reste obligatoire dans le CSV d'entrée.

Nettoyage numérique
-------------------

``stm32_time_ms`` et les six entrées explicatives sont convertis avec
``errors="coerce"``. Les grandeurs dérivées et EWMA sont ensuite calculées, les
valeurs infinies deviennent manquantes, puis les valeurs manquantes numériques
sont remplacées par ``0.0``.

La cible est volontairement exclue de la conversion numérique. Avec les options
de lecture actuelles, ses marqueurs texte invalides ne sont donc pas remplacés
par ``fillna(0.0)``. Cette asymétrie préserve l'information d'une mesure D6T
absente, mais impose un contrôle explicite avant l'import.

Reproductibilité et précautions
-------------------------------

Le nom du fichier de sortie est identique au nom d'entrée. Une nouvelle
exécution dans le même dossier remplace donc le résultat précédent. Pour figer
un dataset :

1. conserver les CSV bruts et la configuration YAML utilisée ;
2. noter la fréquence forcée éventuelle et le choix d'en-tête ;
3. vérifier le nombre et l'ordre des colonnes ;
4. comparer l'ordre aux 55 lignes de
   ``firmware_validation/AI_Model/feature_order.txt`` ;
5. archiver les métriques avec l'export NanoEdge correspondant.