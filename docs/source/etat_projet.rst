Analyse et état du projet
=========================

Cette page synthétise l'audit du dépôt réalisé le 8 septembre 2026. Elle
distingue les constats vérifiés localement des contrôles qui nécessitent le banc
matériel ou STM32CubeIDE.

Périmètre examiné
-----------------

L'analyse couvre :

* les applications Python de datalogging, prétraitement et validation ;
* les fichiers YAML, les profils moteur et les contrats CSV ;
* les modules applicatifs propres aux firmwares d'acquisition et de validation ;
* les en-têtes de configuration, l'export NanoEdge et ses métadonnées ;
* les tests Python, les procédures de build et toute la documentation projet.

Les bibliothèques CMSIS, HAL et MCSDK générées ou tierces n'ont pas fait l'objet
d'une revue ligne par ligne. Leur intégration, leurs points d'appel et les
constantes projet qui les encadrent ont été examinés.

Synthèse
--------

La chaîne fonctionnelle est complète et compréhensible : la carte acquiert les
mesures, le dashboard les journalise, le script Python produit les 55 features,
et un second firmware reproduit ce calcul avant l'inférence. Les contrats
essentiels sont matérialisés par ``CSV_OUTPUT_COLUMNS``, ``feature_order.txt`` et
les dimensions du header NanoEdge.

Les principaux risques ne concernent pas l'organisation générale, mais la
cohérence aux frontières : validations différentes entre le dashboard et le
firmware, dérive numérique légèrement supérieure au seuil de parité sur le log
le plus récent, et absence de politique stricte pour les cibles D6T invalides.

État des vérifications
----------------------

.. list-table:: Résultats au 8 septembre 2026
   :header-rows: 1

   * - Vérification
     - État
     - Résultat
   * - Build Sphinx strict
     - Réussi
     - Aucun avertissement avec ``-W --keep-going``
   * - Cohérence export NanoEdge
     - Réussie
     - ID, ABI, symboles, dimensions et artefacts Ridge valides
   * - Tests de l'interface thermique
     - Réussis
     - 3 tests exécutés
   * - Parité Python/float32
     - En échec
     - ``0.000512959`` pour une limite de ``0.0005`` sur le dernier log
   * - Build des firmwares
     - Non exécuté
     - STM32CubeIDE et la chaîne embarquée sont requis
   * - Contrat USART1 sur cible
     - Non exécuté
     - Carte programmée et port COM requis

Points forts
------------

Séparation des responsabilités
   Le pilotage interactif et la validation autonome utilisent deux projets
   distincts. Les modules capteurs, moteur, datalogging et modèle ont des rôles
   identifiables.

Robustesse de l'acquisition
   Le firmware utilise une réception interrompue et une émission non bloquante.
   Le dashboard isole les accès Tkinter du thread série et vide régulièrement
   le CSV.

Contrats explicites
   Les huit colonnes brutes, les 55 axes d'entrée et les deux formats de sortie
   de validation sont définis et contrôlables.

Défense en profondeur
   Les limites sont vérifiées côté PC et côté firmware. Le contrôle moteur
   surveille les défauts MCSDK, le courant total et la survitesse.

Traçabilité du modèle
   L'export contient son header, ses métadonnées, ses paramètres Ridge et un
   test qui vérifie l'identité, les dimensions, l'ABI et les symboles attendus.

Risques prioritaires
--------------------

1. Contrat de configuration divergent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Priorité élevée.** Le dashboard accepte une vitesse strictement positive,
alors que le firmware refuse moins de 100 rpm. Le dashboard ne borne pas
l'accélération ; le parseur UART accepte jusqu'à 2000 Hz électriques/s, puis le
contrôle moteur applique au maximum 50 Hz électriques/s. Enfin, une commande
UART directe avec une période DS18B20 inférieure à 750 ms est acquittée puis
ramenée à 750 ms.

Conséquence : une valeur peut être acceptée par une couche, rejetée ou modifiée
par la suivante, et l'opérateur peut croire qu'une configuration différente est
active. Centraliser les bornes ou renvoyer systématiquement les valeurs
effectivement appliquées supprimerait cette ambiguïté.

2. Parité numérique au-delà du seuil
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Priorité élevée.** ``validate_preprocess_parity.py`` fixe la tolérance à
``5e-4``. Le fichier ``daq_log_20260827_080523.csv`` atteint ``0.000512959`` sur
``speed_power_ewma_6600`` à la ligne 60913. Le test s'arrête donc avant son
message de succès.

La proximité du seuil est compatible avec une accumulation d'écarts float32,
mais cette explication reste une hypothèse. Il faut localiser la première
divergence significative, mesurer son effet sur la prédiction et justifier soit
une correction de la récurrence, soit une nouvelle tolérance.

3. Politique de cible invalide
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Priorité élevée.** Le prétraitement convertit les variables explicatives en
numérique, mais conserve ``d6t_temp_c`` telle que lue. Avec
``keep_default_na=False``, une chaîne ``NaN`` ou une cellule vide reste dans la
sortie. Le fichier peut alors respecter sa dimension tout en contenant une cible
inexploitable.

Définir une politique explicite avant l'entraînement : rejeter le fichier,
supprimer les lignes concernées ou imputer la cible selon une méthode validée.
Le remplacement silencieux de la cible par zéro est à éviter.

4. Reproductibilité des performances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Priorité moyenne.** Les scores ``0.9827`` et ``0.9944`` proviennent des
métadonnées NanoEdge, mais aucune commande versionnée ne reproduit une
évaluation indépendante du modèle sur les CSV de ``validation``. Ajouter un
script qui fixe le jeu testé, les métriques, les filtres et la version du modèle
permettrait de transformer les performances en critère de recette.

5. Couverture et automatisation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Priorité moyenne.** Il n'existe ni CI ni commande de test unique. Les trois
tests de l'interface couvrent la reprise série et l'écriture CSV, mais pas les
seuils visuels, tous les cas du parseur, les arguments du dashboard ou le
prétraitement de fichiers invalides. Les firmwares n'ont pas de build headless
versionné ni de tests unitaires hôte sur les parseurs et machines d'états.

6. Dépendances et confidentialité
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Priorité moyenne.** ``requirements.txt`` ne fixe aucune version ; une mise à
jour de pandas, NumPy, PySerial ou Sphinx peut donc changer le comportement ou
le build documentaire. Un fichier de contraintes ou des versions compatibles
améliorerait la reproductibilité.

``AI_Model/metadata.json`` contient également le nom et l'adresse électronique
de l'auteur de l'export. Vérifier cette information avant toute publication du
dépôt ou automatiser sa suppression dans le processus d'export.

Plan d'action recommandé
------------------------

1. Aligner les bornes du dashboard, du parseur UART et du contrôle moteur, puis
   tester les valeurs limites et les valeurs juste hors plage.
2. Diagnostiquer la divergence de ``speed_power_ewma_6600`` sur le log du
   27 août avant de modifier la tolérance de parité.
3. Valider explicitement ``d6t_temp_c`` et produire un rapport des lignes
   rejetées pendant le prétraitement.
4. Ajouter un test de performance reproductible lié à l'ID de bibliothèque et
   à un manifeste de dataset.
5. Fournir une commande unique pour les tests hôte et le build Sphinx strict,
   puis l'exécuter en intégration continue.
6. Figer les versions Python validées et documenter un build firmware
   reproductible en dehors de l'état local de STM32CubeIDE.

Critères de recette proposés
----------------------------

Une version peut être considérée comme validée lorsque :

* le build Sphinx strict ne produit aucun avertissement ;
* le contrôle de l'export NanoEdge et tous les tests Python réussissent ;
* la parité respecte une tolérance techniquement justifiée sur tous les logs de
  référence ;
* les configurations Debug et Release des deux firmwares compilent proprement ;
* les modes ``model`` et ``emulator`` passent le contrôle série sur cible ;
* une session thermique indépendante produit des métriques archivées avec l'ID
  exact du modèle et le manifeste des données.