# Interface de validation thermique

L'application `test/temperature_validation_gui.py` compare en temps réel la
température mesurée par le D6T et la température estimée par NanoEdge AI sur la
carte STM32. Elle est destinée au firmware de `firmware_validation`, pas au
firmware piloté par le dashboard de datalogging.

## Préparer le firmware

Activer le modèle dans `firmware_validation/Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

Effectuer ensuite un **Clean Project**, reconstruire le projet et reflasher la
carte. Le flux USART1 démarre automatiquement au boot à 115200 bauds, 8N1. Une
trame valide contient exactement deux nombres finis séparés par un point-virgule :

```text
<d6t_temp_c>;<predicted_temp_c>
```

Exemple :

```text
31.400000;30.872314
```

Dans ce firmware, B2 lance désormais un profil moteur à 30 A maximum : départ à
2000 rpm, puis cible pseudo-aléatoire entre 2000 et 4000 rpm, modifiée toutes
les 10 à 30 secondes par pas de 200 à 500 rpm avec une rampe de 300 rpm/s. Ces
changements ne modifient pas le protocole série ci-dessus. Ne les utiliser
qu'après qualification électrique, thermique et mécanique du banc.

## Lancement

Sélection manuelle du port dans l'interface :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
```

Connexion automatique à un port :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
```

Mode de démonstration sans carte :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo
```

Fermer le dashboard, Motor Pilot et tout terminal série avant la connexion : un
port COM ne peut appartenir qu'à une application à la fois.

Sous Windows, une erreur transitoire `ClearCommError` est retentée jusqu'à
100 fois, avec une pause de 150 ms entre les tentatives. La fenêtre de reprise
est donc d'environ 15 secondes, hors durée des opérations série. Une autre
erreur ou l'épuisement des tentatives ferme la liaison et remonte le diagnostic.

## Affichage et calculs

Les deux températures sont affichées avec une décimale. Le graphique conserve
les 90 dernières secondes et jusqu'à 1800 points. Une donnée qui n'a pas été
rafraîchie depuis plus de 2 secondes est considérée comme ancienne par
l'interface.

Pour chaque échantillon :

```text
erreur_signée = prédiction - D6T
erreur_absolue = abs(erreur_signée)
MAE_cumulée = somme(erreurs_absolues) / nombre_échantillons
```

La MAE reste exprimée en degrés Celsius. Les seuils visuels sont :

| Écart absolu | Classe | Couleur |
|---|---|---|
| `< 0,5 °C` | Excellent | vert |
| `0,5 °C à < 1,0 °C` | Bon | bleu |
| `1,0 °C à 1,5 °C` | À surveiller | orange |
| `> 1,5 °C` | Écart élevé | rouge |

Les trames non ASCII, non numériques, non finies ou n'ayant pas exactement deux
champs sont ignorées et comptabilisées comme invalides.

## Enregistrement CSV

Chaque connexion crée automatiquement dans `validation/` un fichier nommé :

```text
validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv
```

Chaque ligne est vidée immédiatement sur disque et contient :

```text
elapsed_s;d6t_temp_c;predicted_temp_c;signed_error_c;absolute_error_c;cumulative_mae_c
```

Les nombres sont enregistrés à leur précision de calcul, avec six décimales.
Le bouton **Exporter CSV** crée une copie ailleurs. **Réinitialiser** efface les
données affichées et remet la MAE à zéro, sans interrompre l'enregistreur
automatique de la connexion en cours.

## Vérification

Les tests unitaires ne nécessitent ni carte ni fenêtre graphique :

```powershell
.\.venv\Scripts\python.exe .\validation\test\test_temperature_validation_gui.py
```

Ils vérifient actuellement trois comportements : la détection d'une erreur
`ClearCommError`, la reprise de lecture après cette erreur et l'écriture avec
flush immédiat d'un échantillon CSV. Ils ne valident pas le rendu visuel, le
port COM réel ni les performances statistiques du modèle.

Le contrôle matériel du protocole se lance depuis la racine du dépôt :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
```
