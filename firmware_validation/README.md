# Firmware de validation NanoEdge AI

Ce projet STM32CubeIDE autonome reprend les acquisitions moteur et capteurs,
calcule à 10 Hz le même prétraitement EWMA que
`pretraitement/preprocess_logs_ewma.py`, puis expose soit les 55 features, soit
la prédiction thermique NanoEdge AI.

Le mode est sélectionné à la compilation dans `Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U` : modèle embarqué, sortie `température D6T;prédiction` ;
- `0U` : modèle non appelé, sortie des 55 features pour le Serial Emulator.

Le flux démarre automatiquement au boot. Il ne contient ni en-tête, ni préfixe
`DATA`, ni texte de diagnostic. USART1 utilise 115200 bauds, 8N1.

## Organisation

Les éléments propres à la validation se trouvent principalement dans :

| Chemin | Rôle |
|---|---|
| `STM32CubeIDE/Application/User/preprocess_ewma.c` | Calcul float32 des 55 features |
| `STM32CubeIDE/Application/User/app_ai_model.c` | Vérification et appel de NanoEdge AI |
| `STM32CubeIDE/Application/User/app_datalog.c` | Acquisition, cadence et émission USART1 |
| `Inc/app_config.h` | Sélection du mode modèle/émulateur |
| `Inc/preprocess_ewma.h` | Période et dimensions du prétraitement |
| `AI_Model/feature_order.txt` | Ordre contractuel des 55 axes |
| `AI_Model/` | Bibliothèque, en-tête, métadonnées et artefacts du modèle |
| `tests/` | Contrôles hors cible et test du contrat série |

Les dossiers `Drivers`, `MCSDK_v6.4.2-Full`, `Src` et une partie de `Inc` sont
issus des outils STM32. Une régénération CubeMX/Workbench doit être revue avant
d'être intégrée.

## Modèle embarqué actuel

Le contenu de `AI_Model/metadata.json` et `NanoEdgeAI.h` décrit l'export suivant :

| Propriété | Valeur |
|---|---|
| Algorithme | Régression Ridge |
| NanoEdge AI Studio | 5.2.0 |
| ID de bibliothèque | `6a99400cd097fef61cf265dc` |
| Cible | STM32G4, Cortex-M4, hard-float |
| Entrée | 1 échantillon de 55 axes |
| Score NanoEdge | `0.9827` |
| KPI principal des métadonnées | `0.9944` |
| RAM estimée | 464 octets |
| Flash estimée | 892 octets |
| Compilation de l'export | 3 septembre 2026 |

Ces chiffres proviennent de l'export NanoEdge. Ils ne constituent pas à eux
seuls une mesure indépendante sur des données de validation séparées. Les
anciennes valeurs R² `0.8069` et SMAPE `1.55 %` citées dans ce README n'étaient
accompagnées d'aucun script de calcul versionné ; elles ne sont donc plus
présentées comme critères de recette.

Le dossier modèle contient :

```text
AI_Model/
|-- libneai.a
|-- NanoEdgeAI.h
|-- metadata.json
|-- feature_order.txt
`-- artifacts/
    |-- ridge_model_params.json
    |-- ridge_preprocessing_config.json
    `-- ridge_preprocessing_params.json
```

## Mode modèle activé

Avec `APP_NEAI_MODEL_ENABLED == 1U`, `app_ai_model.c` vérifie l'identité et les
dimensions de la bibliothèque, initialise l'extrapolation, copie le vecteur de
55 `float`, puis appelle `neai_extrapolation()`.

Chaque ligne UART valide contient deux températures en degrés Celsius :

```text
<d6t_temp_c>;<predicted_temp_c>
```

Exemple :

```text
31.400000;30.872314
```

Aucune ligne n'est émise tant que le D6T n'a pas fourni de mesure valide, ni si
l'initialisation ou l'inférence NanoEdge échoue. Le bouton B2 démarre ou arrête
le profil moteur autonome sans ajouter de texte au flux de données.

Contrôler le contrat avec une carte connectée :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
```

L'interface graphique correspondante est décrite dans `../validation/README.md`.

## Mode Serial Emulator

Définir :

```c
#define APP_NEAI_MODEL_ENABLED  0U
```

Effectuer ensuite un clean build, reconstruire et reflasher. La bibliothèque
n'est pas appelée et chaque ligne contient exactement 55 nombres finis séparés
par `;`, sans cible D6T, timestamp, en-tête ou préfixe.

```text
27.180000;27.180000;...;31385.884088
```

Dans NanoEdge AI Studio, ouvrir **Validation > Serial Emulator**, sélectionner
le port COM et 115200 bauds. Aucune commande `START` n'est nécessaire.

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
```

## Contrat des 55 features

La période est fixée à 100 ms. Les spans adaptés à 10 Hz sont :

```text
6600, 16800, 31800, 47400
```

Les onze signaux sont ordonnés ainsi :

1. `ds18b20_temp_c`
2. `motor_ud_v`
3. `motor_uq_v`
4. `motor_speed_mech_rpm`
5. `motor_id_a`
6. `motor_iq_a`
7. `u_s = sqrt(ud^2 + uq^2)`
8. `i_s = sqrt(id^2 + iq^2)`
9. `S_el = 1.5 * u_s * i_s`
10. `speed_current = motor_speed_mech_rpm * i_s`
11. `speed_power = motor_speed_mech_rpm * S_el`

Pour chaque signal, le vecteur contient la valeur instantanée puis ses quatre
EWMA, soit `11 x 5 = 55` valeurs. La récurrence reproduit
`pandas.Series.ewm(span=..., adjust=False)`. Les valeurs non finies des features
sont remplacées par zéro dans les deux implémentations.

Le contexte des 44 EWMA est sauvegardé après chaque échantillon dans deux
snapshots alternés de la section SRAM `.noinit`. Une signature, une version, un
numéro de séquence et un CRC32 permettent de restaurer le dernier état complet
après un reset CPU/NRST tant que la carte reste alimentée. Une coupure
d'alimentation ou un snapshot invalide réinitialise le contexte sans écrire en
Flash.

## Remplacer le modèle NanoEdge

Exporter une bibliothèque d'extrapolation depuis NanoEdge AI Studio, puis
remplacer ensemble dans `AI_Model` :

1. `libneai.a` ;
2. `NanoEdgeAI.h` ;
3. `metadata.json` ;
4. le contenu de `artifacts/`.

Vider d'abord `artifacts/` pour ne pas mélanger les paramètres de deux modèles.
Conserver `feature_order.txt`, qui décrit l'ordre imposé par le firmware.

Le nouvel export doit respecter :

- une cible Cortex-M4 STM32G4 compatible avec la carte ;
- l'ABI hard-float et VFPv4-D16 ;
- `NEAI_INPUT_SIGNAL_LENGTH == 1` ;
- `NEAI_INPUT_AXIS_NUMBER == 55` ;
- les symboles `neai_extrapolation_init` et `neai_extrapolation` sans suffixe
  multi-library.

Les assertions de `app_ai_model.c` font échouer la compilation si les dimensions
changent. Un export utilisant des symboles suffixés nécessite une adaptation
explicite de ce module.

## Construction

Importer `firmware_validation/STM32CubeIDE` comme projet existant dans
STM32CubeIDE. Les configurations Debug et Release référencent toutes deux :

- l'en-tête dans `../../AI_Model` ;
- la bibliothèque dans `../../AI_Model` ;
- `:libneai.a` sur la ligne de lien.

Après un changement de mode ou de modèle :

1. exécuter **Project > Clean** ;
2. reconstruire la configuration voulue ;
3. vérifier la présence de `libneai.a` sur la ligne de lien en mode modèle ;
4. programmer `STM32CubeIDE/Debug/firmware_validation.elf` ou
  `STM32CubeIDE/Release/firmware_validation.elf`, selon la configuration ;
5. contrôler le contrat UART correspondant.

## Vérifications automatisées

Vérifier la structure de l'export :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py
```

Ce contrôle réussit avec l'export versionné : ID, dimensions, ABI, symboles,
ordre des features et artefacts Ridge sont cohérents.

Comparer le prétraitement float32 simulé à pandas :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_preprocess_parity.py
```

État vérifié le 8 septembre 2026 : les sept premiers logs passent, mais
`daq_log_20260827_080523.csv` atteint `0.000512959` d'erreur relative mise à
l'échelle sur `speed_power_ewma_6600`, ligne 60913, pour une limite de `0.0005`.
Le test global échoue donc actuellement. Cette faible dérive float32 doit être
qualifiée avant d'ajuster la tolérance ou l'implémentation.

Le contrôle série nécessite une carte réelle. Aucun build STM32 automatisé ou
pipeline d'intégration continue n'est fourni dans le dépôt.
