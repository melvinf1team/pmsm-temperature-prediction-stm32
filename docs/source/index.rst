PMSM Temperature Prediction STM32
=================================

Ce projet regroupe toute la chaîne de mesure et de préparation de données pour
prédire ou extrapoler une température moteur PMSM avec NanoEdge AI Studio.

La chaîne complète est composée de trois blocs :

* un firmware STM32 qui pilote le moteur, lit les capteurs et publie des lignes
  UART textuelles ;
* une interface Python de datalogging qui configure la carte, affiche les
  mesures en temps réel et écrit les CSV bruts ;
* un script de prétraitement qui transforme les logs bruts en dataset NanoEdge
  AI, avec ``d6t_temp_c`` comme target et des EWMA adaptées à la fréquence réelle
  d'acquisition.

.. toctree::
   :maxdepth: 2
   :caption: Documentation projet

   sommaire
   installation
   architecture
   cablage
   utilisation
   datalogging
   pretraitement
   firmware
   api