# Interface de validation thermique

L'application `test/temperature_validation_gui.py` affiche en direct la
temperature reelle mesuree par le D6T et la temperature estimee par le modele
NanoEdge execute dans la carte STM32.

Le firmware doit etre compile avec le modele active dans
`firmware_validation/Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

Le protocole attendu sur USART1 est une ligne a deux nombres, a 115200 bauds :

```text
<d6t_temp_c>;<predicted_temp_c>
```

## Lancement

Selection manuelle du port dans l'interface :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
```

Connexion automatique a un port :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
```

Mode visuel sans carte :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo
```

Fermer le dashboard de datalogging et tout terminal serie avant la connexion :
un port COM ne peut etre ouvert que par une application a la fois.

Sous Windows, une erreur `ClearCommError` ponctuelle est retentee automatiquement
pendant 1,5 seconde. Si la liaison ne repond toujours pas apres dix tentatives,
l'application ferme le port et affiche l'erreur serie.

## Affichage

Les deux valeurs principales sont affichees en grand :

- temperature reelle D6T ;
- temperature estimee par NanoEdge AI.

Toutes les temperatures et erreurs visibles sont arrondies a une decimale. Les
compteurs restent des entiers. Le graphique montre les deux temperatures, leur
intervalle et l'erreur absolue sur les 90 dernieres secondes.

Deux indicateurs d'erreur sont calcules :

- erreur instantanee : `abs(prediction - D6T)` ;
- erreur cumulee : MAE de la session, soit la moyenne de toutes les erreurs
  absolues depuis la derniere reinitialisation.

La MAE est utilisee plutot qu'une somme croissante afin de conserver une valeur
en degres Celsius comparable aux memes seuils :

| Ecart absolu | Couleur |
| --- | --- |
| `< 0,5 °C` | vert |
| `0,5 °C a < 1,0 °C` | bleu |
| `1,0 °C a 1,5 °C` | orange |
| `> 1,5 °C` | rouge |

Chaque connexion cree automatiquement un fichier
`validation_ia_YYYYMMDD_HHMMSS_microsecondes.csv` dans le dossier `validation`.
Chaque mesure est videe immediatement sur disque et le fichier est ferme a la
deconnexion. `Exporter CSV` reste disponible pour enregistrer une copie dans un
autre emplacement.

`Reinitialiser` efface la session et remet la MAE a zero sans interrompre le
fichier automatique de la connexion en cours. Les CSV enregistrent les mesures
brutes a six decimales, les erreurs signee/absolue et la MAE cumulee ; la
limitation a une decimale concerne uniquement l'affichage.

## Verification

Les tests ne necessitent ni carte ni fenetre graphique :

```powershell
.\.venv\Scripts\python.exe .\validation\test\test_temperature_validation_gui.py
```

Ils couvrent le parseur serie, les quatre seuils, la MAE et le formatage a une
decimale.
