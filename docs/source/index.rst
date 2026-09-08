Datalog PMSM et prédiction thermique sur STM32
==============================================

Ce projet couvre l'acquisition des grandeurs d'un moteur PMSM, la constitution
d'un jeu de données thermique, sa préparation pour NanoEdge AI Studio et la
validation d'un modèle d'extrapolation directement sur STM32.

Le banc documenté utilise une **B-G473E-ZEST1S**, une carte de puissance
**STDES-LVHP01**, un capteur infrarouge **Omron D6T** et un **DS18B20**. La
température ``d6t_temp_c`` constitue la cible ; les mesures moteur et la
température DS18B20 constituent les variables explicatives.

Parcours conseillé
------------------

* :doc:`installation` pour préparer Python, STM32CubeIDE et les deux projets
  embarqués ;
* :doc:`cablage` avant toute mise sous tension ;
* :doc:`utilisation` et :doc:`datalogging` pour acquérir des CSV bruts ;
* :doc:`pretraitement` pour produire les 55 features attendues par le modèle ;
* :doc:`validation_ia` pour contrôler la parité, le modèle et le flux série.

.. warning::

   Le firmware pilote un étage de puissance et un moteur. Vérifier le câblage,
   les limites de courant, la fixation mécanique et les moyens d'arrêt avant
   de lancer une séquence. Le logiciel ne remplace pas les protections du banc.

État de référence
-----------------

Le dépôt contient deux projets embarqués distincts :

* ``firmware_acquisition/tets_motor_dewalt`` pour le pilotage depuis le
  dashboard ;
* ``firmware_validation`` pour le calcul embarqué des features et l'inférence.

L'export NanoEdge AI actuellement versionné est une régression Ridge à
55 entrées, identifiée par ``6a99400cd097fef61cf265dc``. La cadence du firmware
de validation est fixée à 10 Hz.

.. toctree::
   :maxdepth: 2
   :caption: Documentation projet

   sommaire
   etat_projet
   installation
   architecture
   cablage
   utilisation
   datalogging
   pretraitement
   firmware
   validation_ia
   api
