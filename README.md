# Datalog PMSM et prédiction thermique sur STM32

Ce dépôt regroupe la chaîne complète d'acquisition, de préparation des données
et de validation embarquée d'un modèle NanoEdge AI pour estimer la température
interne d'un moteur PMSM. La cible d'apprentissage est `d6t_temp_c` ; les
variables explicatives proviennent du capteur DS18B20 et de la commande moteur.

Le banc repose sur une carte **B-G473E-ZEST1S**, une carte de puissance
**STDES-LVHP01**, un capteur infrarouge **Omron D6T** et un capteur
**DS18B20**. Les échanges avec le PC utilisent l'USART1 à 115200 bauds.

## Architecture du dépôt

| Chemin | Rôle |
|---|---|
| `datalogging/` | Dashboard Tkinter, profils moteur et journaux CSV bruts |
| `pretraitement/` | Génération des grandeurs physiques et des EWMA pour NanoEdge AI |
| `firmware_acquisition/tets_motor_dewalt/` | Firmware STM32CubeIDE/MCSDK piloté par le dashboard |
| `firmware_validation/` | Firmware autonome de calcul des 55 features et d'inférence NanoEdge AI |
| `validation/` | Interface PC de comparaison entre température mesurée et prédite |
| `inventories/` | Scripts d'inventaire des jeux de données |
| `docs/` | Documentation Sphinx détaillée |
| `dashboard_config.yaml` | Chemins par défaut du dashboard |
| `preprocess_ewma.yaml` | Entrées et sorties par défaut du prétraitement |

La chaîne de traitement est la suivante :

```text
Capteurs + MCSDK
			|
			v
Firmware d'acquisition --USART1--> Dashboard Python --> CSV bruts
																											|
																											v
																					Prétraitement Python
																											|
																											v
																		 CSV cible + 55 features
																											|
																											v
																			NanoEdge AI Studio
																											|
																											v
																			Firmware de validation
																											|
															 +----------------------+------------------+
															 |                                         |
												 Serial Emulator                       Interface de validation
```

## Prérequis

- Windows avec Python 3 et Tkinter ;
- STM32CubeIDE avec une chaîne GNU Arm compatible Cortex-M4 hard-float ;
- STM32CubeProgrammer/ST-LINK pour programmer la carte ;
- NanoEdge AI Studio pour entraîner ou remplacer la bibliothèque embarquée ;
- accès au port série de la B-G473E-ZEST1S.

Créer l'environnement Python depuis la racine du dépôt :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Acquisition et datalogging

Lancer le dashboard :

```powershell
python .\datalogging\motor_datalog_gui_dashboard.py
```

Deux modes sont disponibles :

- **Moteur + collecte** : `SYNC`, `CFG`, puis `START` ;
- **Collecte seule (moteur arrêté)** : `SYNC`, puis `ACQ_START`, notamment
	pour enregistrer une phase de refroidissement.

Dans les deux cas, `STOP` arrête proprement la session. Les profils moteur sont
chargés depuis `datalogging/motor_profiles.json`.

Le CSV brut utilise le point-virgule et conserve huit colonnes :

```text
stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a
```

Les chemins peuvent être configurés dans `dashboard_config.yaml`, avec
`--config`, ou avec les variables d'environnement suivantes :

| Option | Variable d'environnement | Valeur par défaut |
|---|---|---|
| `--log-dir` | `PMSM_DATALOG_LOG_DIR` | `datalogging/logs` |
| `--profile-store` | `PMSM_DATALOG_PROFILE_STORE` | `datalogging/motor_profiles.json` |
| `--csv-path` | `PMSM_DATALOG_CSV_PATH` | nom horodaté dans le dossier des logs |

Exemple :

```powershell
python .\datalogging\motor_datalog_gui_dashboard.py `
	--config .\dashboard_config.yaml `
	--log-dir .\datalogging\logs
```

## Prétraitement NanoEdge AI

Lancer le traitement avec la configuration par défaut :

```powershell
python .\pretraitement\preprocess_logs_ewma.py
```

Le script lit `datalogging/logs/daq_log_*.csv` et écrit les résultats dans
`pretraitement/logs_processed_ewma`. Il :

1. conserve `d6t_temp_c` comme première colonne et cible d'extrapolation ;
2. utilise six mesures brutes comme variables explicatives ;
3. calcule `u_s`, `i_s`, `S_el`, `speed_current` et `speed_power` ;
4. ajoute quatre EWMA à chacune des onze variables explicatives ;
5. produit donc **55 features** en plus de la cible.

Les spans de référence `[1320, 3360, 6360, 9480]` correspondent à 2 Hz. La
fréquence réelle est déduite de la médiane des écarts positifs de
`stm32_time_ms`, puis les spans sont remis à l'échelle. À 10 Hz, ils deviennent
`[6600, 16800, 31800, 47400]`.

Par défaut, le fichier de sortie ne contient ni en-tête ni timestamp. Options
principales :

```powershell
python .\pretraitement\preprocess_logs_ewma.py --header
python .\pretraitement\preprocess_logs_ewma.py --include-time
python .\pretraitement\preprocess_logs_ewma.py --frequency-hz 10
python .\pretraitement\preprocess_logs_ewma.py --config .\preprocess_ewma.yaml
```

Les variables `PMSM_PREPROCESS_INPUT_DIR`, `PMSM_PREPROCESS_OUTPUT_DIR` et
`PMSM_PREPROCESS_PATTERN` remplacent respectivement le dossier d'entrée, le
dossier de sortie et le motif des fichiers.

> **Qualité des données :** l'implémentation actuelle convertit les variables
> explicatives en nombres et remplace leurs valeurs invalides ou infinies par
> `0.0`. La cible `d6t_temp_c` n'est pas convertie : une chaîne `NaN` ou une
> cellule vide reste donc telle quelle dans la sortie. Contrôler et filtrer ces
> lignes avant l'import dans NanoEdge AI Studio.

## Firmware d'acquisition

Le projet à importer dans STM32CubeIDE se trouve dans
`firmware_acquisition/tets_motor_dewalt/STM32CubeIDE`.

Les modules applicatifs principaux sont :

- `app_serial_control.c` : protocole UART, file de réception et validation ;
- `app_motor_control.c` : commande MCSDK, rampes et protections ;
- `app_datalog.c` : planification des capteurs et émission CSV non bloquante ;
- `d6t_ir.c` : lecture I2C du capteur D6T ;
- `ds18b20.c` : lecture 1-Wire du capteur DS18B20.

Le protocole accepte :

```text
SYNC
CFG,<target_rpm>,<iq_limit_a>,<hard_limit_a>,<accel_elec_hz_s>,<datalog_ms>,<ds18b20_ms>
START
ACQ_START,<datalog_ms>,<ds18b20_ms>
STOP
```

Les limites applicatives sont de 4500 rpm, 30 A sur `Iq` et 30 A sur le
courant total. La vitesse minimale est de 100 rpm et l'accélération est
plafonnée à 50 Hz électriques/s dans le dashboard comme dans le firmware.

Le bouton B2 lance un profil autonome variable : départ à 2000 rpm, puis
nouvelle cible aléatoire toutes les 10 à 30 secondes dans la plage
2000–4000 rpm. Chaque variation est limitée à un pas choisi entre 200 et
500 rpm et suit la rampe MCSDK de 10 Hz électriques/s, soit 300 rpm/s avec les
deux paires de pôles configurées. La limite `Iq` et le hard stop valent 30 A.
Un second appui arrête le profil.

La consigne est réappliquée lorsque MCSDK atteint réellement l'état `RUN`. Un
redémarrage demandé pendant l'arrêt attend le retour à `IDLE`, et la protection
de survitesse ne peut jamais dépasser le plafond absolu de 4500 rpm.

> **Qualification obligatoire :** ces valeurs sont des plafonds logiciels, pas
> une certification du banc. Avant un essai à 4500 rpm ou 30 A, vérifier les
> caractéristiques du moteur, de la STDES-LVHP01, de l'alimentation, du câblage,
> du refroidissement et des protections. La polarisation de démarrage reste
> volontairement limitée à 14 A.

## Firmware de validation NanoEdge AI

Le projet `firmware_validation/STM32CubeIDE` recalcule sur la carte les mêmes
55 features que le script Python, à une période fixe de 100 ms. Le mode est
sélectionné dans `firmware_validation/Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U` : exécution du modèle et émission de
	`d6t_temp_c;predicted_temp_c` à 10 Hz ;
- `0U` : émission des 55 valeurs vers le Serial Emulator NanoEdge AI Studio.

L'export inclus est une régression Ridge `1 x 55` pour Cortex-M4 hard-float.
Son identifiant est `6a99400cd097fef61cf265dc`. Les métadonnées exportées
indiquent un score de `0.9827`, un KPI principal de `0.9944`, 464 octets de RAM
estimés et 892 octets de Flash estimés.

Pour remplacer le modèle, remplacer ensemble `libneai.a`, `NanoEdgeAI.h`,
`metadata.json` et `artifacts/` dans `firmware_validation/AI_Model`, conserver
55 entrées et l'API d'extrapolation, puis effectuer un **Clean Project** suivi
d'un **Build Project** dans STM32CubeIDE.

## Validation

Vérifications sans matériel :

```powershell
python .\firmware_validation\tests\validate_neai_export.py
python .\firmware_validation\tests\validate_motor_limits.py
python .\validation\test\test_temperature_validation_gui.py
python .\firmware_validation\tests\validate_preprocess_parity.py
```

Les trois premiers contrôles réussissent avec l'état actuel. Le test global de
parité échoue sur `daq_log_20260827_080523.csv` : `0.000512959` sur
`speed_power_ewma_6600`, pour une tolérance de `0.0005`. Voir la section
validation de `firmware_validation/README.md` avant de modifier le seuil.

Contrôle du contrat série avec une carte connectée :

```powershell
python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
```

Interface de validation thermique :

```powershell
python .\validation\test\temperature_validation_gui.py --port COM5
python .\validation\test\temperature_validation_gui.py --demo
```

L'interface affiche les deux températures, l'erreur signée, l'erreur absolue,
la MAE cumulée et un historique de 90 secondes. Elle peut exporter la session
au format CSV.

## Documentation

Construire la documentation HTML :

```powershell
python -m sphinx -b html .\docs\source .\docs\build\html
```

Le point d'entrée généré est `docs/build/html/index.html`. La documentation
détaille l'architecture, le câblage, le protocole, les traitements, les deux
firmwares, la validation IA et l'API Python. L'audit technique, les risques et
le plan d'action sont conservés dans `docs/source/etat_projet.rst`.

## Limites actuelles

- aucun pipeline d'intégration continue n'est fourni ;
- les firmwares se construisent depuis STM32CubeIDE, sans commande de build
	autonome versionnée ;
- le test série nécessite une carte programmée et un port COM disponible ;
- les builds Debug et Release des deux firmwares réussissent, mais les limites élevées et
	le profil B2 aléatoire n'ont pas été validés sur le banc physique ;
- la parité float32/pandas dépasse légèrement sa tolérance sur le dernier log ;
- les métriques indépendantes de validation doivent rester accompagnées de
	leur CSV source pour être reproductibles.
